"""회사 공식 원문에 적힌 자기 선언형 차별점을 결정론적으로 선별한다.

이 모듈은 선언의 사실 여부나 타사 대비 우열을 판단하지 않는다. 공식 회사
발행 문장에서 회사가 주어이고 닫힌 선언 표지가 있을 때만 원문 표현을 9장
근거로 옮긴다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from src.features.company_comparison.official_sources import (
    OfficialCandidateSentence,
)
from src.features.pipeline.port import FactRecord, ReportSection
from src.features.provenance.sources import (
    Source,
    evidence_text_hash,
    exact_evidence_text_hash,
    is_canonical_official_with_registry,
    seal_collected_source,
)
from src.features.report_standard.constants import SECTION_BY_ID
from src.features.report_standard.publish import fact_evidence_binding
from src.shared.company_identity import exact_company_name_key
from src.shared.report_evidence.models import ChapterEvidenceCandidates, EvidenceFragment
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import (
    FormalSourceKindContractError,
    document_slots_for_formal_source_kind,
)
from src.shared.report_quality.constants import STATED_DIFFERENTIATOR_CLAIM_TYPE
from src.shared.report_quality.comparison_claims import (
    STATED_DIFFERENTIATOR_MARKERS,
    clean_stated_differentiator_sentence as _clean_sentence,
    stated_differentiator_claim as _claim_sentence,
    stated_differentiator_subject_prefix as _subject_prefix,
)


COMPETITIVE_SECTION_ID = "competitive_position"
STATED_DIFFERENTIATOR_SLOT = "competitive_position:stated_differentiator"
#: 파이프라인 진단 기록에 쓰는 단계 이름. 호출부의 실패 기록과 같은 이름이어야
#: 한 단계의 성공·실패가 같은 열에서 읽힌다.
STATED_DIFFERENTIATOR_PROMOTION_STEP = "9장_자기선언_승격"
FORBIDDEN_DIFFERENTIATOR_JUDGMENT_TERMS = (
    "우위",
    "열위",
    "더 낫",
    "경쟁력이 높",
    "경쟁력이 낮",
)
MAX_STATED_DIFFERENTIATORS = 4

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")


@dataclass(frozen=True)
class StatedDifferentiatorResult:
    """9장에 붙일 회사 자기 선언 사실과 출처."""

    section: ReportSection
    facts: tuple[FactRecord, ...]
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class StatedDifferentiatorPromotion:
    """9장 승격을 마친 수집 결과와 무엇을 건너뛰었는지의 요약."""

    result: OfficialEvidenceCollectionResult
    promoted: int
    #: 자기 선언 문장이 있었지만 그 문서 종류가 9장 칸을 소유하지 않아 건너뛴
    #: 조각 수. 종류 이름순으로 고정해 진단 기록이 실행마다 흔들리지 않게 한다.
    skipped_by_source_kind: tuple[tuple[str, int], ...] = ()

    def step_record(self) -> dict[str, object]:
        """파이프라인 진단(steps)에 그대로 넣을 수 있는 한 단계 기록."""

        return {
            "step": STATED_DIFFERENTIATOR_PROMOTION_STEP,
            "승격": self.promoted,
            "건너뜀": {kind: count for kind, count in self.skipped_by_source_kind},
        }


def stated_differentiator_sentence_is_eligible(
    sentence: str,
    *,
    company_name: str,
    company_aliases: Iterable[str] = (),
    publisher: str,
) -> bool:
    """회사 공식 발행 문장의 닫힌 주어·선언 표지 계약을 검사한다."""

    clean = _clean_sentence(sentence)
    del publisher  # 공식 발행 주체 결속은 typed 문서·Source 등록부가 검증한다.
    return bool(
        clean
        and _subject_prefix(
            clean,
            company_name=company_name,
            company_aliases=company_aliases,
        )
        and any(marker in clean for marker in STATED_DIFFERENTIATOR_MARKERS)
        and not any(term in clean for term in FORBIDDEN_DIFFERENTIATOR_JUDGMENT_TERMS)
    )


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(
        candidate
        for candidate in (
            _clean_sentence(item)
            for item in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", str(text or ""))
        )
        if candidate
    )


def stated_differentiator_sentences(
    text: str,
    *,
    company_name: str,
    company_aliases: Iterable[str] = (),
    publisher: str,
) -> tuple[str, ...]:
    """한 공식 원문에서 최대 네 개의 자기 선언 문장을 순서대로 고른다."""

    return tuple(
        sentence
        for sentence in _sentences(text)
        if stated_differentiator_sentence_is_eligible(
            sentence,
            company_name=company_name,
            company_aliases=company_aliases,
            publisher=publisher,
        )
    )[:MAX_STATED_DIFFERENTIATORS]


def _owns_stated_differentiator_slot(document: object) -> bool:
    """이 문서 종류가 9장 자기 선언 칸을 소유하는지 정책 표에 직접 묻는다."""

    try:
        allowed = document_slots_for_formal_source_kind(
            getattr(document, "source_kind", "")
        )
    except FormalSourceKindContractError:
        # 공식 문서 표에 없는 종류는 애초에 승격 대상이 아니다. 그 문서가 장
        # 후보에 실제로 들어 있다면 수집 결과 자료형 계약이 따로 거절한다.
        return False
    return STATED_DIFFERENTIATOR_SLOT in allowed


def promote_stated_differentiator_fragments(
    result: OfficialEvidenceCollectionResult,
    *,
    company_name: str,
    company_aliases: Iterable[str] = (),
) -> StatedDifferentiatorPromotion:
    """사전검사 전에 공식 조각의 선언 문장을 9장 typed 조각으로 승격한다.

    9장 칸을 소유하지 않는 문서 종류(반기·분기보고서, 채용 페이지 등)의 선언
    문장은 «조용히 건너뛰고» 요약에만 센다. 한 조각의 소유권 결핍 때문에 승격
    전체가 계약 예외로 죽으면, 소유권 있는 사업보고서 선언까지 같이 사라진다
    (2026-09-16·17 운영 실행에서 실제로 9장이 통째로 비었다).
    """

    candidates_by_section = {item.section_id: item for item in result.candidates}
    target = candidates_by_section[COMPETITIVE_SECTION_ID]
    sanitized_target_fragments: list[EvidenceFragment] = []
    for fragment in target.fragments:
        remaining_slots = tuple(
            slot_id
            for slot_id in fragment.covered_slot_ids
            if slot_id != STATED_DIFFERENTIATOR_SLOT
        )
        if len(remaining_slots) == len(fragment.covered_slot_ids):
            sanitized_target_fragments.append(fragment)
        elif remaining_slots:
            sanitized_target_fragments.append(
                replace(
                    fragment,
                    slot_id=remaining_slots[0],
                    covered_slot_ids=remaining_slots,
                )
            )
    documents_by_id = {
        document.document_id: document
        for candidate in result.candidates
        for document in candidate.documents
    }
    selected: list[tuple[EvidenceFragment, object]] = []
    skipped_counts: dict[str, int] = {}
    seen_hashes: set[str] = set()
    remaining_chars = target.max_chars - sum(
        len(item.text) for item in sanitized_target_fragments
    )
    for candidate in result.candidates:
        for fragment in candidate.fragments:
            document = documents_by_id.get(fragment.document_id)
            if document is None:
                continue
            if not stated_differentiator_sentences(
                fragment.text,
                company_name=company_name,
                company_aliases=company_aliases,
                publisher=document.publisher,
            ):
                continue
            if not _owns_stated_differentiator_slot(document):
                source_kind = str(getattr(document, "source_kind", "") or "unknown")
                skipped_counts[source_kind] = skipped_counts.get(source_kind, 0) + 1
                continue
            if fragment.text_sha256 in seen_hashes or len(fragment.text) > remaining_chars:
                continue
            seen_hashes.add(fragment.text_sha256)
            selected.append((fragment, document))
            remaining_chars -= len(fragment.text)
            if len(selected) >= MAX_STATED_DIFFERENTIATORS:
                break
        if len(selected) >= MAX_STATED_DIFFERENTIATORS:
            break
    existing_ids = {item.fragment_id for item in sanitized_target_fragments}
    additions: list[EvidenceFragment] = []
    added_documents = {item.document_id: item for item in target.documents}
    for fragment, document in selected:
        suffix = hashlib.sha256(
            f"{fragment.fragment_id}\x1f{STATED_DIFFERENTIATOR_SLOT}".encode("utf-8")
        ).hexdigest()[:16]
        fragment_id = f"{fragment.fragment_id}-stated-{suffix}"
        if fragment_id in existing_ids:
            continue
        existing_ids.add(fragment_id)
        additions.append(
            replace(
                fragment,
                fragment_id=fragment_id,
                section_id=COMPETITIVE_SECTION_ID,
                slot_id=STATED_DIFFERENTIATOR_SLOT,
                covered_slot_ids=(STATED_DIFFERENTIATOR_SLOT,),
                reason_codes=("deterministic.company_stated_differentiator",),
            )
        )
        added_documents[document.document_id] = document

    updated_target = replace(
        target,
        documents=tuple(added_documents.values()),
        fragments=(*sanitized_target_fragments, *additions),
    )
    updated_candidates = tuple(
        updated_target if item.section_id == COMPETITIVE_SECTION_ID else item
        for item in result.candidates
    )
    # 새 객체를 만들지 않고 replace 로 바꾼다 — 파이프라인이 넘긴 하위 타입
    # (재판정 원문 차선 reclassify_source 를 실은 결과)의 다른 필드를 보존해야 한다.
    return StatedDifferentiatorPromotion(
        result=replace(result, candidates=updated_candidates),
        promoted=len(additions),
        skipped_by_source_kind=tuple(sorted(skipped_counts.items())),
    )


def add_stated_differentiator_fragments(
    result: OfficialEvidenceCollectionResult,
    *,
    company_name: str,
    company_aliases: Iterable[str] = (),
) -> OfficialEvidenceCollectionResult:
    """승격 결과만 필요한 호출부를 위한 얇은 위임 — 판정은 승격 함수 한 곳뿐."""

    return promote_stated_differentiator_fragments(
        result,
        company_name=company_name,
        company_aliases=company_aliases,
    ).result


def register_stated_differentiator_sentence_evidence(
    fragments: Mapping[int, Mapping[str, object]],
    *,
    company_name: str,
    company_aliases: Iterable[str] = (),
) -> dict[int, dict[str, object]]:
    """원문 안의 적격 자기 선언 문장을 기존 ``근거원문`` 목록에 등록한다."""

    copied = {number: dict(fragment) for number, fragment in fragments.items()}
    for fragment in copied.values():
        if str(fragment.get("종류") or "") == "뉴스":
            continue
        sentences = stated_differentiator_sentences(
            str(fragment.get("원문") or ""),
            company_name=company_name,
            company_aliases=company_aliases,
            publisher=str(fragment.get("발행처") or ""),
        )
        if not sentences:
            continue
        existing = fragment.get("근거원문") or []
        if isinstance(existing, str):
            existing = [existing]
        fragment["근거원문"] = list(
            dict.fromkeys(
                [
                    *(str(item).strip() for item in existing if str(item).strip()),
                    *sentences,
                ]
            )
        )
    return copied


def _support_terms(sentence: str, claim: str, company_name: str) -> list[str]:
    marker = next(
        (item for item in STATED_DIFFERENTIATOR_MARKERS if item in sentence), ""
    )
    excluded = {
        exact_company_name_key(company_name),
        "당사는",
        "우리는",
        "우리회사는",
        marker,
    }
    second = next(
        (
            token
            for token in _TOKEN.findall(sentence)
            if token in claim
            and token not in excluded
            and marker not in token
        ),
        "",
    )
    return [item for item in (marker, second) if item]


def build_stated_differentiator_result(
    *,
    company_name: str,
    company_aliases: Iterable[str] = (),
    official_candidate_sentences: Sequence[OfficialCandidateSentence],
    candidate_source_registry: Sequence[Source],
) -> StatedDifferentiatorResult | None:
    """봉인된 공식 문장에서 9장 자기 선언 사실을 최대 네 건 만든다."""

    registry = tuple(candidate_source_registry)
    selected: list[tuple[str, Source]] = []
    seen: set[str] = set()
    for candidate in official_candidate_sentences:
        source = candidate.source
        sentence = _clean_sentence(candidate.evidence_text)
        if (
            sentence in seen
            or not is_canonical_official_with_registry(source, registry)
            or evidence_text_hash(sentence) not in source.evidence_hashes
            or exact_evidence_text_hash(sentence) not in source.exact_evidence_hashes
            or not stated_differentiator_sentence_is_eligible(
                sentence,
                company_name=company_name,
                company_aliases=company_aliases,
                publisher=source.publisher,
            )
        ):
            continue
        claim = _claim_sentence(sentence, company_name, company_aliases)
        support_terms = _support_terms(sentence, claim, company_name)
        if len(support_terms) < 2:
            continue
        selected.append((sentence, source))
        seen.add(sentence)
        if len(selected) >= MAX_STATED_DIFFERENTIATORS:
            break
    if not selected:
        return None

    facts: list[FactRecord] = []
    sources_by_id: dict[str, Source] = {}
    prose_lines: list[tuple[str, str]] = []
    registry_by_id = {source.source_id: source for source in registry}
    for sentence, original_source in selected:
        source = seal_collected_source(
            replace(
                original_source,
                used_in=sorted({*original_source.used_in, COMPETITIVE_SECTION_ID}),
            )
        )
        sources_by_id[source.source_id] = source
        attester_id = source.domain_attestation_source_id.strip()
        if attester_id and attester_id in registry_by_id:
            sources_by_id[attester_id] = registry_by_id[attester_id]
        claim = _claim_sentence(sentence, company_name, company_aliases)
        support_terms = _support_terms(sentence, claim, company_name)
        source_date = source.published_at or source.disclosed_at or source.collected_at
        fact = FactRecord(
            fact_id="fact-stated-differentiator-"
            + hashlib.sha256(
                f"{company_name}\x1f{source.source_id}\x1f{sentence}".encode("utf-8")
            ).hexdigest()[:20],
            legal_entity=company_name,
            subject_scope="회사 공식 자기 선언",
            relationship_or_action="회사가 밝힌 차별점",
            claim=claim,
            claim_type=STATED_DIFFERENTIATOR_CLAIM_TYPE,
            section_owner=COMPETITIVE_SECTION_ID,
            time_state="standing",
            as_of=source_date,
            source_id=source.source_id,
            source_type=source.source_type,
            source_title=source.title or source.label,
            source_publisher=source.publisher,
            source_host=source.host,
            source_url=source.url,
            source_document_id=source.document_id,
            location=source.location,
            status="verified",
            fact_status="actual",
            verification_status="verified",
            state_evidence=sentence,
            source_date=source_date,
            evidence_support_terms=support_terms,
            limitations=(
                "회사가 공식 자료에서 직접 밝힌 표현이며 사실 여부나 타사 비교를 "
                "별도로 판정하지 않습니다."
            ),
            limitation=(
                "회사가 공식 자료에서 직접 밝힌 표현이며 사실 여부나 타사 비교를 "
                "별도로 판정하지 않습니다."
            ),
            supports_causality=False,
            claim_slot=STATED_DIFFERENTIATOR_SLOT,
        )
        fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
        facts.append(fact)
        prose_lines.append((claim, str(source.number)))

    spec = SECTION_BY_ID[COMPETITIVE_SECTION_ID]
    return StatedDifferentiatorResult(
        section=ReportSection(
            cell=spec.section_id,
            title=spec.title,
            display_number=spec.display_number,
            tag=spec.tag,
            prose_lines=prose_lines,
            fact_ids=[fact.fact_id for fact in facts],
        ),
        facts=tuple(facts),
        sources=tuple(sources_by_id.values()),
    )


__all__ = [
    "FORBIDDEN_DIFFERENTIATOR_JUDGMENT_TERMS",
    "MAX_STATED_DIFFERENTIATORS",
    "STATED_DIFFERENTIATOR_CLAIM_TYPE",
    "STATED_DIFFERENTIATOR_MARKERS",
    "STATED_DIFFERENTIATOR_PROMOTION_STEP",
    "STATED_DIFFERENTIATOR_SLOT",
    "StatedDifferentiatorPromotion",
    "StatedDifferentiatorResult",
    "add_stated_differentiator_fragments",
    "build_stated_differentiator_result",
    "promote_stated_differentiator_fragments",
    "register_stated_differentiator_sentence_evidence",
    "stated_differentiator_sentence_is_eligible",
    "stated_differentiator_sentences",
]
