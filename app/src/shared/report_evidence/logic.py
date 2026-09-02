"""수집기의 자기 판정과 분리된 장별·전체 근거 준비 판정."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    ReportExecutionOutcome,
    SITE_PROBE_GATE_SOURCE_KINDS,
    SourceRequirement,
)


_MAX_REASON_CODE_CHARS = 120
_MACHINE_CODE = re.compile(r"^[A-Za-z0-9_.:-]+$")
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    GenerationGateDecision,
    InjectedSlotFacts,
    SectionEvidenceBundle,
)


def _unique(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    result = tuple(str(value).strip() for value in values)
    if not result or any(not value for value in result):
        raise ValueError(f"{label}에는 빈 값을 넣을 수 없습니다")
    if len(set(result)) != len(result):
        raise ValueError(f"{label}에는 중복 값을 넣을 수 없습니다")
    return result


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _scoped_reason_code(scope: str, reason_code: str) -> str:
    """장 이름을 붙인 사유 코드가 저장 계약 120자를 넘지 않게 보존한다.

    단순 절단은 끝부분만 다른 두 장애를 같은 코드로 만든다. 전체 원문 기계 코드의
    SHA-256 일부를 붙여 서로 다른 원인은 계속 구분하고, 사용자 문장이나 원문은
    새로 싣지 않는다. 정책 밖 장 이름이 들어와도 전체 게이트가 예외로 죽지 않게
    장 이름 자체는 안전한 해시 표식으로 바꾼다.
    """

    clean_scope = str(scope).strip()
    if not clean_scope or _MACHINE_CODE.fullmatch(clean_scope) is None:
        scope_digest = hashlib.sha256(clean_scope.encode("utf-8")).hexdigest()[:16]
        clean_scope = f"section_sha256_{scope_digest}"
    combined = f"{clean_scope}:{reason_code}"
    if len(combined) <= _MAX_REASON_CODE_CHARS:
        return combined
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]
    suffix = f":sha256_{digest}"
    prefix = combined[: _MAX_REASON_CODE_CHARS - len(suffix)].rstrip("_.:-")
    return f"{prefix}{suffix}"


def build_section_bundle(
    candidate: ChapterEvidenceCandidates,
    *,
    required_slot_ids: Iterable[str],
    injected_slot_facts: Iterable[InjectedSlotFacts] = (),
) -> SectionEvidenceBundle:
    """수집 후보와 검증된 구조화 사실을 합쳐 한 장의 최종 준비 상태를 판정한다.

    수집기가 적은 ``candidate_readiness``는 진단값일 뿐이다. 필수 의미 칸별
    조회 결과를 다시 보지 않으면 선택형 자료 한 곳의 장애가 전체를 막거나,
    반대로 확인하지 않은 빈 칸을 실제 자료 부재라고 오판하게 된다.
    """

    required = _unique(required_slot_ids, label="필수 의미 칸")
    injected = tuple(injected_slot_facts)
    injected_slot_ids = tuple(item.slot_id for item in injected)
    if len(set(injected_slot_ids)) != len(injected_slot_ids):
        raise ValueError("같은 의미 칸에 주입 사실 묶음을 두 번 넣을 수 없습니다")
    unknown_injected = sorted(set(injected_slot_ids) - set(required))
    if unknown_injected:
        raise ValueError(
            "필수 정책에 없는 의미 칸으로 사실을 주입할 수 없습니다: "
            + ", ".join(unknown_injected)
        )

    fragment_slots = {
        slot_id
        for fragment in candidate.fragments
        for slot_id in fragment.covered_slot_ids
    }
    filled_set = (fragment_slots | set(injected_slot_ids)) & set(required)
    filled = tuple(slot_id for slot_id in required if slot_id in filled_set)
    missing = tuple(slot_id for slot_id in required if slot_id not in filled_set)

    generated_reasons: list[str] = []
    has_unknown = False
    for slot_id in missing:
        required_attempts = tuple(
            attempt
            for attempt in candidate.attempts
            if attempt.requirement is SourceRequirement.REQUIRED
            and slot_id in attempt.slot_ids
        )
        if not required_attempts:
            has_unknown = True
            generated_reasons.append(f"required_path_unobserved:{slot_id}")
            continue
        failed_states = {
            attempt.state
            for attempt in required_attempts
            if attempt.state in {CollectionState.FAILED, CollectionState.TRUNCATED}
        }
        if failed_states:
            has_unknown = True
            for state in sorted(failed_states, key=lambda item: item.value):
                generated_reasons.append(
                    f"required_path_{state.value.lower()}:{slot_id}"
                )
            continue
        # required 경로는 전부 정상 확인됐다(OK/MISSING). 그러나 그것만으로
        # «확인을 마쳤다»고 단정하지 않는다 — requirement(«이 경로가 유일한
        # 확인 길인가»)와 outcome-kind(«막힌 것인가, 없는 것인가»)는 다른
        # 질문이다. site-probe 게이트(robots.txt 등, SITE_PROBE_GATE_
        # SOURCE_KINDS)가 이 슬롯이 걸린 출처 전부에서 막혔다면 그 출처를
        # 아예 못 열어본 것이라, 같은 슬롯의 required 경로 하나가 정상
        # 확인됐다고 해도 다른 잠재적 근거(그 출처의 다른 페이지들)까지 다
        # 살펴봤다고 볼 수 없다(P1-B).
        #
        # ⚠️ 이 확인은 두 겹으로 좁힌다 — 그러지 않으면 OPTIONAL 강등이
        # 막았던 「IR 1건 실패가 9장을 다 죽이던 P0」가 되살아난다(결합
        # 종단시험 test_combined_collectors_end_to_end.py가 이 경계를
        # 잠근다).
        #   1) source_kind가 site-probe 게이트로 좁다 — 흔한 개별 후보
        #      페이지 실패(IR PDF 없음·특정 후보 URL 404 등, 정상 운영에서도
        #      자주 있는 일)는 포함하지 않는다.
        #   2) 같은 게이트가 «전부 막혔을 때»만 — www/apex 대체 호스트처럼
        #      게이트가 여러 host에 걸쳐 여러 번 시도됐을 수 있다. 그중
        #      하나라도 정상 확인(OK)됐다면 그 출처는 실제로 열어봤다는
        #      뜻이므로 단정을 막을 이유가 없다.
        # OPTIONAL 강등 자체는 건드리지 않는다 — required_attempts가
        # 비었을 때(→ unobserved)나 이미 failed_states가 있을 때
        # (→ required_path_*)는 그대로다.
        site_probe_attempts = tuple(
            attempt
            for attempt in candidate.attempts
            if attempt.requirement is not SourceRequirement.REQUIRED
            and attempt.source_kind in SITE_PROBE_GATE_SOURCE_KINDS
            and slot_id in attempt.slot_ids
        )
        site_probe_ever_succeeded = any(
            attempt.state not in {CollectionState.FAILED, CollectionState.TRUNCATED}
            for attempt in site_probe_attempts
        )
        site_probe_failed_states = {
            attempt.state
            for attempt in site_probe_attempts
            if attempt.state in {CollectionState.FAILED, CollectionState.TRUNCATED}
        }
        if site_probe_failed_states and not site_probe_ever_succeeded:
            has_unknown = True
            for state in sorted(site_probe_failed_states, key=lambda item: item.value):
                generated_reasons.append(
                    f"site_probe_gate_{state.value.lower()}:{slot_id}"
                )
            continue
        generated_reasons.append(f"evidence_absent_after_check:{slot_id}")

    if not missing:
        readiness = EvidenceReadiness.READY
    elif has_unknown:
        readiness = EvidenceReadiness.UNKNOWN
    else:
        readiness = EvidenceReadiness.INSUFFICIENT

    if candidate.candidate_readiness is not readiness:
        generated_reasons.append(
            "producer_readiness_disagreed:"
            f"{candidate.candidate_readiness.value.lower()}_to_{readiness.value.lower()}"
        )

    return SectionEvidenceBundle(
        company_id=candidate.company_id,
        section_id=candidate.section_id,
        required_slot_ids=required,
        filled_slot_ids=filled,
        missing_slot_ids=missing,
        documents=candidate.documents,
        fragments=candidate.fragments,
        injected_slot_facts=injected,
        readiness=readiness,
        reason_codes=_dedupe((*candidate.reason_codes, *generated_reasons)),
        estimated_tokens=candidate.estimated_tokens,
        max_chars=candidate.max_chars,
        max_estimated_tokens=candidate.max_estimated_tokens,
    )


def assess_generation_gate(
    *,
    company_id: str,
    bundles: Iterable[SectionEvidenceBundle],
    required_section_ids: Iterable[str],
) -> GenerationGateDecision:
    """필수 장 하나라도 확인하지 못했으면 AI를 부르지 않는 전체 게이트."""

    clean_company_id = str(company_id).strip()
    if not clean_company_id:
        raise ValueError("회사 식별자는 비워 둘 수 없습니다")
    required = _unique(required_section_ids, label="필수 장")
    bundle_tuple = tuple(bundles)
    bundle_ids = tuple(bundle.section_id for bundle in bundle_tuple)
    if len(set(bundle_ids)) != len(bundle_ids):
        raise ValueError("같은 장의 근거 묶음을 두 번 넣을 수 없습니다")
    if any(bundle.company_id != clean_company_id for bundle in bundle_tuple):
        raise ValueError("다른 회사의 근거 묶음을 한 생성 게이트에 섞을 수 없습니다")
    by_id = {bundle.section_id: bundle for bundle in bundle_tuple}

    ready: list[str] = []
    insufficient: list[str] = []
    unknown: list[str] = []
    reasons: list[str] = []
    for section_id in required:
        bundle = by_id.get(section_id)
        if bundle is None:
            unknown.append(section_id)
            reasons.append(f"section_bundle_missing:{section_id}")
            continue
        if bundle.readiness is EvidenceReadiness.READY:
            ready.append(section_id)
        elif bundle.readiness is EvidenceReadiness.INSUFFICIENT:
            insufficient.append(section_id)
        else:
            unknown.append(section_id)
        reasons.extend(
            _scoped_reason_code(section_id, reason_code)
            for reason_code in bundle.reason_codes
        )

    if unknown:
        status = GenerationGateStatus.STOP_TRANSIENT_FAILURE
        outcome: ReportExecutionOutcome | None = (
            ReportExecutionOutcome.TRANSIENT_FAILURE
        )
    elif insufficient:
        status = GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
        outcome = ReportExecutionOutcome.INSUFFICIENT_EVIDENCE
    else:
        status = GenerationGateStatus.READY_FOR_GENERATION
        outcome = None

    return GenerationGateDecision(
        company_id=clean_company_id,
        status=status,
        outcome=outcome,
        required_section_ids=required,
        ready_section_ids=tuple(ready),
        insufficient_section_ids=tuple(insufficient),
        unknown_section_ids=tuple(unknown),
        reason_codes=_dedupe(reasons),
    )
