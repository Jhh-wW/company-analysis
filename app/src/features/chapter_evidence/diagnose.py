"""수집 생산부가 남기는 장별 후보 진단(candidate_readiness).

여기서 만드는 판정은 «진단값»일 뿐이다. 최종 판정은
``src.shared.report_evidence.logic.build_section_bundle``이 다시 한다
(주입 슬롯까지 포함해서). 이 모듈은 그 최종 판정과 «같은 방향»으로
판단하도록 build_section_bundle과 같은 3단계 규칙을 따른다.

- 수집 슬롯이 모두 찼다 → READY
- 빈 슬롯이 있고, 그 슬롯의 기대 REQUIRED 경로 조회 기록이 없거나
  FAILED/TRUNCATED다 → UNKNOWN (수집 장애를 「자료 없음」으로 단정하지 않는다)
- 빈 슬롯의 기대 REQUIRED 경로 조회가 전부 OK/MISSING(정상 확인 후 부재)
  다 → INSUFFICIENT
"""

from __future__ import annotations

from src.features.chapter_evidence.constants import (
    FAILURE_COLLECTION_STATES,
    CompanyType,
    expected_required_path_prefix,
)
from src.shared.report_evidence.constants import EvidenceReadiness, SourceRequirement
from src.shared.report_evidence.models import CollectionAttempt
from src.shared.report_evidence.policy import collector_slots_for


def diagnose_candidate_readiness(
    *,
    section_id: str,
    company_type: CompanyType,
    filled_slot_ids: frozenset[str],
    attempts: tuple[CollectionAttempt, ...],
) -> tuple[EvidenceReadiness, tuple[str, ...]]:
    """이 장의 수집 슬롯만 보고 후보 준비 상태를 진단한다."""

    collector_slot_order = collector_slots_for(section_id)
    missing = [slot_id for slot_id in collector_slot_order if slot_id not in filled_slot_ids]
    if not missing:
        return EvidenceReadiness.READY, ()

    expected_prefix = expected_required_path_prefix(company_type, section_id)
    reasons: list[str] = []
    any_unknown = False
    for slot_id in missing:
        relevant_attempts = tuple(
            attempt
            for attempt in attempts
            if attempt.requirement is SourceRequirement.REQUIRED
            and slot_id in attempt.slot_ids
            and attempt.source_kind.startswith(expected_prefix)
        )
        if not relevant_attempts:
            any_unknown = True
            reasons.append(f"required_path_unobserved:{slot_id}")
            continue
        failed_states = {
            attempt.state
            for attempt in relevant_attempts
            if attempt.state in FAILURE_COLLECTION_STATES
        }
        if failed_states:
            any_unknown = True
            for state in sorted(failed_states, key=lambda item: item.value):
                reasons.append(f"required_path_{state.value.lower()}:{slot_id}")
            continue
        reasons.append(f"evidence_absent_after_check:{slot_id}")

    readiness = EvidenceReadiness.UNKNOWN if any_unknown else EvidenceReadiness.INSUFFICIENT
    return readiness, tuple(reasons)
