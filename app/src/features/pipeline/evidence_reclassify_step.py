"""공식 근거 preflight 직전의 단일 재판정 단계."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, ContextManager, Final

from src.core.evidence_reclassify_switch import evidence_reclassify_enabled
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.evidence_reclassify.constants import RECLASSIFY_PROMPT_VERSION
from src.features.evidence_reclassify.logic import (
    apply_removals,
    build_reclassify_request,
    parse_and_verify,
    to_typed_fragments,
)
from src.features.evidence_reclassify.models import ReclassifyAssignment, ReclassifyResult
from src.features.pipeline.collection_recovery import raise_if_request_interrupted
from src.shared import generation_coordination
from src.shared.generation_cache_identity import GenerationCacheNamespace
from src.features.pipeline.official_evidence_preflight import empty_collector_sections
from src.features.storage import evidence_reclassify_cache
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


RECLASSIFY_STEP_NAME: Final[str] = "6_근거_재판정"
# structured output 전체가 검증 JSON 한 건에 머물도록 호출별 상한을 고정한다.
RECLASSIFY_MAX_TOKENS: Final[int] = 4_096
# 결정론 점수(직접 일치 250, 제목 일치 200)의 저신뢰 꼬리만 다시 본다.
LOW_SCORE_MAX_MILLIS: Final[int] = 500
_RECEIPT_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"(?<![0-9])[0-9]{14}(?![0-9])")
_SOURCE_BINDING_REJECTION: Final[str] = "source_binding_failed"


@dataclass(frozen=True)
class ReclassifySource:
    """adapter가 이미 검증한 원시 envelope의 요청 수명 사본."""

    company_type: str
    dart_envelope: Mapping[str, object] = field(repr=False, compare=False)
    wide_envelope: Mapping[str, object] = field(repr=False, compare=False)


@dataclass(frozen=True)
class ReclassifiableOfficialEvidenceCollectionResult(OfficialEvidenceCollectionResult):
    """preflight 전 한 번만 소비하는 재판정 원문 차선을 함께 나른다."""

    reclassify_source: ReclassifySource | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def plain_official_evidence(
    result: OfficialEvidenceCollectionResult,
) -> OfficialEvidenceCollectionResult:
    """재판정 원문 차선을 실은 하위 타입을 «정확한» 기본 타입으로 되돌린다.

    ★ 왜 필요한가 (2026-09-17 운영 실측) — 스위치가 켜지면 수집 결과가
      `ReclassifiableOfficialEvidenceCollectionResult`가 되는데, 재판정을 건너뛰거나
      (빈 칸 없음·소유권 미확정·호출 실패) 실패한 경로는 그 하위 타입을 그대로
      돌려줬다. 뒤의 보완조사 최종검사는 `type(x) is OfficialEvidenceCollectionResult`
      로 «정확한 타입»만 받으므로 보고서 전체가 `invalid_report_dto(official_evidence_type)`
      으로 멈췄다. 재판정 원문은 이 단계 밖에서 쓸 일이 없으니 여기서 떼어 낸다.
    """

    if type(result) is OfficialEvidenceCollectionResult:
        return result
    return OfficialEvidenceCollectionResult(
        company_id=result.company_id,
        candidates=result.candidates,
        unclassified_evidence=result.unclassified_evidence,
        comparison_candidates=result.comparison_candidates,
        provenance_documents=result.provenance_documents,
    )


def attach_reclassify_source(
    result: OfficialEvidenceCollectionResult,
    *,
    company_type: str,
    dart_envelope: Mapping[str, object],
    wide_envelope: Mapping[str, object],
) -> OfficialEvidenceCollectionResult:
    """스위치가 켜진 수집 결과에만 검증된 원문 envelope를 붙인다."""

    return ReclassifiableOfficialEvidenceCollectionResult(
        company_id=result.company_id,
        candidates=result.candidates,
        unclassified_evidence=result.unclassified_evidence,
        comparison_candidates=result.comparison_candidates,
        provenance_documents=result.provenance_documents,
        reclassify_source=ReclassifySource(
            company_type=str(company_type),
            dart_envelope=dart_envelope,
            wide_envelope=wide_envelope,
        ),
    )


def _mapping_rows(envelope: Mapping[str, object], key: str) -> list[dict[str, Any]]:
    values = envelope.get(key, ())
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"재판정 원문 {key} 배열 형식이 올바르지 않습니다")
    rows: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError(f"재판정 원문 {key} 항목이 Mapping이 아닙니다")
        rows.append(dict(value))
    return rows


def _candidate_paragraphs(source: ReclassifySource) -> list[dict[str, Any]]:
    dart_envelope = source.dart_envelope
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for is_unclassified, key in (
        (True, "unclassified_fragments"),
        (False, "fragments"),
    ):
        for fragment in _mapping_rows(dart_envelope, key):
            score = fragment.get("score_millis", 0)
            if (
                not is_unclassified
                and (
                    isinstance(score, bool)
                    or not isinstance(score, int)
                    or score > LOW_SCORE_MAX_MILLIS
                )
            ):
                continue
            paragraph_id = str(fragment.get("fragment_id") or "").strip()
            if not paragraph_id or paragraph_id in seen_ids:
                continue
            candidate = dict(fragment)
            candidate["paragraph_id"] = paragraph_id
            candidate["is_unclassified"] = is_unclassified
            candidates.append(candidate)
            seen_ids.add(paragraph_id)
    return candidates


def _included_candidates(
    candidates: Sequence[Mapping[str, Any]],
    paragraph_ids: Sequence[str],
) -> list[dict[str, Any]]:
    by_id = {
        str(candidate.get("paragraph_id") or "").strip(): dict(candidate)
        for candidate in candidates
    }
    return [by_id[paragraph_id] for paragraph_id in paragraph_ids]


def _input_paragraph_hash(candidates: Sequence[Mapping[str, Any]]) -> str:
    payload = [
        {
            "document_id": str(candidate.get("document_id") or ""),
            "location": str(candidate.get("location") or ""),
            "paragraph_id": str(candidate.get("paragraph_id") or ""),
            "text_sha256": str(candidate.get("text_sha256") or ""),
        }
        for candidate in candidates
    ]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _receipt_numbers(
    source: ReclassifySource,
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    document_rows = {
        str(document.get("document_id") or "").strip(): document
        for key in ("documents", "unclassified_documents")
        for document in _mapping_rows(source.dart_envelope, key)
    }
    receipts: set[str] = set()
    for candidate in candidates:
        document_id = str(candidate.get("document_id") or "").strip()
        document = document_rows.get(document_id, {})
        for value in (document_id, str(document.get("canonical_url") or "")):
            receipts.update(_RECEIPT_NUMBER_RE.findall(value))
    return tuple(sorted(receipts))


def _response_json(response: object) -> object:
    if isinstance(response, Mapping):
        return dict(response)
    blocks = getattr(response, "content", None)
    if not isinstance(blocks, (list, tuple)):
        raise ValueError("재판정 응답 본문 배열이 없습니다")
    text_block = next(
        (
            block
            for block in blocks
            if getattr(block, "type", "text") == "text"
            and isinstance(getattr(block, "text", None), str)
        ),
        None,
    )
    if text_block is None:
        raise ValueError("재판정 응답에 JSON 텍스트가 없습니다")
    return json.loads(text_block.text)


def _validated_payload(result: ReclassifyResult) -> dict[str, object]:
    return {
        "assignments": [
            {
                "paragraph_id": item.paragraph_id,
                "section_id": item.section_id,
                "slot_id": item.slot_id,
                "quote": item.exact_quote,
            }
            for item in result.assignments
        ],
        "removals": [
            {
                "paragraph_id": item.paragraph_id,
                "section_id": item.section_id,
                "reason": item.reason,
            }
            for item in result.removals
        ],
    }


def _cached_diagnostics(result: ReclassifyResult) -> dict[str, object]:
    return {
        "total_items": result.diagnostics.total_items,
        "rejected_items": result.diagnostics.rejected_items,
        "rejected_by_reason": dict(result.diagnostics.rejected_by_reason),
    }


def _diagnostics_from_cache(
    value: object,
    result: ReclassifyResult,
) -> tuple[int, Counter[str]]:
    if not isinstance(value, Mapping):
        return result.diagnostics.rejected_items, Counter(
            result.diagnostics.rejected_by_reason
        )
    rejected = value.get("rejected_items", 0)
    raw_reasons = value.get("rejected_by_reason", {})
    if isinstance(rejected, bool) or not isinstance(rejected, int) or rejected < 0:
        rejected = 0
    reasons = Counter()
    if isinstance(raw_reasons, Mapping):
        for reason, count in raw_reasons.items():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                reasons[str(reason)] += count
    return rejected, reasons


def _typed_additions(
    result: ReclassifyResult,
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], tuple[ReclassifyAssignment, ...], int]:
    grouped: dict[tuple[str, str], list[ReclassifyAssignment]] = defaultdict(list)
    for assignment in result.assignments:
        grouped[(assignment.paragraph_id, assignment.section_id)].append(assignment)

    additions: list[dict[str, Any]] = []
    accepted: list[ReclassifyAssignment] = []
    rejected = 0
    for assignments in grouped.values():
        group_result = replace(result, assignments=tuple(assignments))
        try:
            converted = to_typed_fragments(group_result, candidates)
        except (KeyError, TypeError, ValueError):
            rejected += len(assignments)
            continue
        additions.extend(converted)
        accepted.extend(assignments)
    return additions, tuple(accepted), rejected


def _merged_dart_envelope(
    source: ReclassifySource,
    *,
    result: ReclassifyResult,
    additions: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    dart = copy.deepcopy(dict(source.dart_envelope))
    classified_documents = _mapping_rows(dart, "documents")
    unclassified_documents = _mapping_rows(dart, "unclassified_documents")
    documents_by_id = {
        str(document.get("document_id") or "").strip(): document
        for document in classified_documents
    }
    unclassified_by_id = {
        str(document.get("document_id") or "").strip(): document
        for document in unclassified_documents
    }

    removal_inputs: list[dict[str, Any]] = []
    for fragment in _mapping_rows(dart, "fragments"):
        slot_id = str(fragment.get("slot_id") or "").strip()
        section_id = str(fragment.get("section_id") or "").strip()
        fragment.setdefault("covered_slot_ids", (slot_id,) if slot_id else ())
        fragment.setdefault("supported_claim_slots", (slot_id,) if slot_id else ())
        fragment.setdefault("section_ids", (section_id,) if section_id else ())
        removal_inputs.append(fragment)
    updated_fragments = apply_removals(removal_inputs, result)
    # 빼기는 원문을 폐기하지 않고 분류만 지운다. 후보 생산기는 분류된 조각만
    # 받으므로 빈 조각은 원시 envelope의 무분류 차선에 남기고 여기서는 제외한다.
    classified_fragments = [
        fragment
        for fragment in updated_fragments
        if str(fragment.get("section_id") or "").strip()
        and str(fragment.get("slot_id") or "").strip()
    ]

    bound_additions: list[dict[str, Any]] = []
    for raw_addition in additions:
        addition = dict(raw_addition)
        document_id = str(addition.get("document_id") or "").strip()
        document = documents_by_id.get(document_id)
        if document is None:
            unclassified_document = unclassified_by_id.get(document_id)
            if unclassified_document is None:
                continue
            document = copy.deepcopy(unclassified_document)
            document["exact_evidence_hashes"] = []
            document["exact_evidence_bindings"] = []
            documents_by_id[document_id] = document
            classified_documents.append(document)

        evidence_hash = str(addition.get("text_sha256") or "").strip()
        location = str(addition.get("location") or "").strip()
        raw_hashes = document.get("exact_evidence_hashes", [])
        raw_bindings = document.get("exact_evidence_bindings", [])
        if not isinstance(raw_hashes, (list, tuple)) or not isinstance(
            raw_bindings, (list, tuple)
        ):
            continue
        hashes = list(raw_hashes)
        bindings = [dict(item) for item in raw_bindings if isinstance(item, Mapping)]
        document["exact_evidence_hashes"] = hashes
        document["exact_evidence_bindings"] = bindings
        if evidence_hash not in hashes:
            hashes.append(evidence_hash)
            hashes.sort()
        binding = {"location": location, "text_sha256": evidence_hash}
        if binding not in bindings:
            bindings.append(binding)
            bindings.sort(
                key=lambda item: (
                    str(item["location"]),
                    str(item["text_sha256"]),
                )
            )
        bound_additions.append(addition)

    dart["documents"] = classified_documents
    dart["fragments"] = [*classified_fragments, *bound_additions]
    return dart


def _merge_result(
    original: OfficialEvidenceCollectionResult,
    source: ReclassifySource,
    *,
    result: ReclassifyResult,
    additions: Sequence[Mapping[str, Any]],
) -> OfficialEvidenceCollectionResult:
    dart_envelope = _merged_dart_envelope(
        source,
        result=result,
        additions=additions,
    )
    candidates = produce_from_collection_envelopes(
        company_id=original.company_id,
        company_type=source.company_type,
        collection_envelopes=(dart_envelope, source.wide_envelope),
    )
    return OfficialEvidenceCollectionResult(
        company_id=original.company_id,
        candidates=candidates,
        unclassified_evidence=original.unclassified_evidence,
        comparison_candidates=original.comparison_candidates,
        provenance_documents=original.provenance_documents,
    )


def _step(
    *,
    empty_sections: Sequence[Mapping[str, object]],
    candidate_count: int,
    prompt_chars: int,
    cache_state: str,
    adopted: int,
    rejected: int,
    rejected_by_reason: Mapping[str, int],
    removals: int,
    ai_calls: int,
    degraded_sections: Sequence[str] = (),
    failure: str = "",
    save_failure: str = "",
) -> dict[str, object]:
    record: dict[str, object] = {
        "step": RECLASSIFY_STEP_NAME,
        "빈장": [str(item.get("section_id") or "") for item in empty_sections],
        "후보수": candidate_count,
        "프롬프트글자": prompt_chars,
        "캐시": cache_state,
        "채택": adopted,
        "폐기": rejected,
        "폐기사유": dict(sorted(rejected_by_reason.items())),
        "빼기": removals,
        "AI호출": ai_calls,
    }
    if degraded_sections:
        record["강등"] = list(degraded_sections)
    if failure:
        record["실패"] = failure
    if save_failure:
        record["캐시저장실패"] = save_failure
    return record


@contextmanager
def _reclassification_connection(
    *,
    connect_db: Callable[[], ContextManager[sqlite3.Connection]],
    cache_key: str,
    paragraph_hash: str,
    included: Sequence[Mapping[str, Any]],
    company_id: str,
    preparation_namespace: Callable[[], GenerationCacheNamespace | None] | None,
    input_digest: str,
) -> Iterator[tuple[sqlite3.Connection, bool, evidence_reclassify_cache.Cached | None]]:
    """무료 캐시를 먼저 보고, 대기 중에는 DB 연결·거래를 잡지 않는다."""

    with connect_db() as conn:
        cached = evidence_reclassify_cache.load(conn, cache_key)
        if cached is not None and cached.input_paragraph_hash == paragraph_hash:
            try:
                parse_and_verify(cached.validated_items, included)
            except (KeyError, TypeError, ValueError):
                pass
            else:
                yield conn, False, cached
                return
    with generation_coordination.paid_preparation(
        corp_id=company_id,
        cache_namespace=(
            preparation_namespace()
            if preparation_namespace is not None and generation_coordination.is_active()
            else None
        ),
        input_digest=input_digest,
    ) as owner:
        # 먼저 끝난 owner가 저장했을 수 있으므로 같은 입력의 캐시를 다시 읽는다.
        with connect_db() as conn:
            yield conn, owner, evidence_reclassify_cache.load(conn, cache_key)


def reclassify_official_evidence(
    official_evidence: OfficialEvidenceCollectionResult,
    *,
    client: object,
    connect_db: Callable[[], ContextManager[sqlite3.Connection]],
    model: str,
    steps: list[dict[str, Any]],
    generated_at: str,
    preparation_namespace: Callable[[], GenerationCacheNamespace | None] | None = None,
) -> OfficialEvidenceCollectionResult:
    """빈 수집 칸이 있을 때만 캐시 또는 계량 client로 한 번 재판정한다."""

    if not evidence_reclassify_enabled():
        # 스위치 OFF는 «바이트 경계까지 그대로»가 계약이다 — 어댑터가 하위 타입을 붙이지도
        # 않으므로 되돌릴 것도 없다.
        return official_evidence

    empty_sections = empty_collector_sections(official_evidence)
    if not empty_sections:
        return plain_official_evidence(official_evidence)

    source = getattr(official_evidence, "reclassify_source", None)
    if not isinstance(source, ReclassifySource):
        steps.append(
            _step(
                empty_sections=empty_sections,
                candidate_count=0,
                prompt_chars=0,
                cache_state="miss",
                adopted=0,
                rejected=0,
                rejected_by_reason={},
                removals=0,
                ai_calls=0,
                failure="재판정 원문 차선 없음",
            )
        )
        return plain_official_evidence(official_evidence)

    try:
        candidates = _candidate_paragraphs(source)
        request = build_reclassify_request(empty_sections, candidates)
        included = _included_candidates(candidates, request.candidate_paragraph_ids)
        # 같은 문단이라도 빈 칸·기존 배정·원문 결속이 바뀌면 재사용하지 않는다.
        # 수집 시도/스캔 완료 상태는 AI 입력이 아니며 현재 envelope를 병합할 때
        # 다시 적용한다. 그 상태만 달라졌다고 같은 분류를 유료로 반복하지 않는다.
        paragraph_hash = hashlib.sha256(json.dumps(
            [_input_paragraph_hash(included), request.prompt,
             official_evidence.company_id, source.company_type,
             {key: _mapping_rows(source.dart_envelope, key) for key in (
                 "documents", "unclassified_documents", "fragments", "unclassified_fragments",
             )}],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        receipts = _receipt_numbers(source, included)
        receipt_model_key = evidence_reclassify_cache.key_for(
            receipts,
            RECLASSIFY_PROMPT_VERSION,
            model,
        )
        # 같은 공시의 다른 빈 칸·배정 입력끼리 단일 캐시행을 덮어쓰지 않는다.
        # 준비 lease가 아직 결과를 공유하는 동안에도 각 입력의 결과가 남아야 한다.
        cache_key = hashlib.sha256(
            f"{receipt_model_key}:{paragraph_hash}".encode("utf-8")
        ).hexdigest()
    except (KeyError, TypeError, ValueError) as error:
        steps.append(
            _step(
                empty_sections=empty_sections,
                candidate_count=0,
                prompt_chars=0,
                cache_state="miss",
                adopted=0,
                rejected=0,
                rejected_by_reason={},
                removals=0,
                ai_calls=0,
                failure=f"입력:{type(error).__name__}",
            )
        )
        return plain_official_evidence(official_evidence)

    cache_state = "miss"
    ai_calls = 0
    cached_diagnostics: object = {}
    try:
        # 준비 owner와 캐시가 같은 입력 경계를 사용해야 원문 위치·문서 결속까지
        # 다른 요청이 같은 lease 아래서 서로의 결과를 기다리는 일이 없다.
        input_digest = cache_key
        with _reclassification_connection(
            connect_db=connect_db, cache_key=cache_key,
            paragraph_hash=paragraph_hash, included=included,
            company_id=official_evidence.company_id,
            preparation_namespace=preparation_namespace, input_digest=input_digest,
        ) as (conn, owner, cached):
            if cached is not None and cached.input_paragraph_hash == paragraph_hash:
                try:
                    parsed = parse_and_verify(cached.validated_items, included)
                except (KeyError, TypeError, ValueError):
                    cached = None
                else:
                    cache_state = "hit"
                    cached_diagnostics = cached.rejection_diagnostics

            if cache_state == "miss":
                if not owner:
                    steps.append(_step(
                        empty_sections=empty_sections, candidate_count=len(included),
                        prompt_chars=request.diagnostics.prompt_chars,
                        cache_state=cache_state, adopted=0, rejected=0,
                        rejected_by_reason={}, removals=0, ai_calls=0,
                        failure="준비 결과 캐시 없음:중복 호출 방지",
                    ))
                    return plain_official_evidence(official_evidence)
                ai_calls = 1
                try:
                    response = client.messages.create(
                        model=model,
                        max_tokens=RECLASSIFY_MAX_TOKENS,
                        temperature=0,
                        messages=[{"role": "user", "content": request.prompt}],
                        output_config={
                            "format": {
                                "type": "json_schema",
                                "schema": request.schema,
                            }
                        },
                    )
                    parsed = parse_and_verify(_response_json(response), included)
                except Exception as error:  # noqa: BLE001 - 차선 실패는 보고서를 막지 않는다
                    raise_if_request_interrupted(error)
                    steps.append(
                        _step(
                            empty_sections=empty_sections,
                            candidate_count=len(included),
                            prompt_chars=request.diagnostics.prompt_chars,
                            cache_state=cache_state,
                            adopted=0,
                            rejected=0,
                            rejected_by_reason={},
                            removals=0,
                            ai_calls=ai_calls,
                            failure=f"호출또는응답:{type(error).__name__}",
                        )
                    )
                    return plain_official_evidence(official_evidence)
                cached_diagnostics = _cached_diagnostics(parsed)

            additions, accepted_assignments, binding_rejected = _typed_additions(
                parsed,
                included,
            )
            effective = replace(parsed, assignments=accepted_assignments)
            rejected, rejection_reasons = _diagnostics_from_cache(
                cached_diagnostics,
                parsed,
            )
            if binding_rejected:
                rejected += binding_rejected
                rejection_reasons[_SOURCE_BINDING_REJECTION] += binding_rejected

            try:
                merged = _merge_result(
                    official_evidence,
                    source,
                    result=effective,
                    additions=additions,
                )
            except Exception as error:  # noqa: BLE001 - 차선 병합 실패는 원결과로 격리한다
                raise_if_request_interrupted(error)
                steps.append(
                    _step(
                        empty_sections=empty_sections,
                        candidate_count=len(included),
                        prompt_chars=request.diagnostics.prompt_chars,
                        cache_state=cache_state,
                        adopted=0,
                        rejected=rejected,
                        rejected_by_reason=rejection_reasons,
                        removals=0,
                        ai_calls=ai_calls,
                        failure=f"병합:{type(error).__name__}",
                    )
                )
                return plain_official_evidence(official_evidence)

            save_failure = ""
            if cache_state == "miss":
                try:
                    evidence_reclassify_cache.save(
                        conn,
                        cache_key,
                        evidence_reclassify_cache.Cached(
                            validated_items=_validated_payload(effective),
                            rejection_diagnostics={
                                "total_items": parsed.diagnostics.total_items,
                                "rejected_items": rejected,
                                "rejected_by_reason": dict(rejection_reasons),
                            },
                            generated_at=generated_at,
                            input_paragraph_hash=paragraph_hash,
                        ),
                    )
                    # 캐시 store는 commit을 호출자에게 맡긴다. 명시 commit 연결은
                    # 미확정 transaction이 남으면 종료 시 예외로 채택 결과까지 버리므로
                    # 여기서 확정한다.
                    conn.commit()
                except Exception as error:  # noqa: BLE001 - cache 장애는 채택 결과를 버리지 않는다
                    raise_if_request_interrupted(error)
                    save_failure = type(error).__name__
                    try:
                        conn.rollback()
                    except Exception:  # noqa: BLE001 - rollback 실패도 채택 결과를 막지 않는다
                        pass
                    if generation_coordination.is_active():
                        try:
                            saved = evidence_reclassify_cache.load(conn, cache_key)
                        except Exception as verification_error:
                            raise generation_coordination.GenerationCoordinationError(
                                "재판정 캐시 commit 결과를 확인하지 못했습니다"
                            ) from verification_error
                        # waiter가 복원할 수 없는 새 배정을 owner만 작성 입력에 넣으면
                        # 서로 다른 최종 신원으로 유료 작성이 중복될 수 있다.
                        # commit 성공 뒤 응답만 잃었으면 저장된 결과를 그대로 쓴다.
                        if not (
                            saved is not None
                            and saved.input_paragraph_hash == paragraph_hash
                            and saved.validated_items == _validated_payload(effective)
                        ):
                            merged = plain_official_evidence(official_evidence)
                            effective = replace(effective, assignments=(), removals=())
    except Exception as error:  # noqa: BLE001 - DB 차선 실패는 보고서를 막지 않는다
        raise_if_request_interrupted(error)
        steps.append(
            _step(
                empty_sections=empty_sections,
                candidate_count=len(included),
                prompt_chars=request.diagnostics.prompt_chars,
                cache_state=cache_state,
                adopted=0,
                rejected=0,
                rejected_by_reason={},
                removals=0,
                ai_calls=ai_calls,
                failure=f"캐시:{type(error).__name__}",
            )
        )
        return plain_official_evidence(official_evidence)

    before_empty = {
        str(item.get("section_id") or "") for item in empty_sections
    }
    after_empty = {
        str(item.get("section_id") or "")
        for item in empty_collector_sections(merged)
    }
    steps.append(
        _step(
            empty_sections=empty_sections,
            candidate_count=len(included),
            prompt_chars=request.diagnostics.prompt_chars,
            cache_state=cache_state,
            adopted=len(effective.assignments),
            rejected=rejected,
            rejected_by_reason=rejection_reasons,
            removals=len(effective.removals),
            ai_calls=ai_calls,
            degraded_sections=tuple(sorted(after_empty - before_empty)),
            save_failure=save_failure,
        )
    )
    return merged
