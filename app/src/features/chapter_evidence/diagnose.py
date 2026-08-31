"""수집 생산부가 남기는 장별 후보 진단(candidate_readiness).

여기서 만드는 판정은 «진단값»일 뿐이다. 최종 판정은
``src.shared.report_evidence.logic.build_section_bundle``이 다시 한다
(주입 슬롯까지 포함해서). 이 모듈은 그 최종 판정보다 «덜 조심»할 수 없도록,
먼저 build_section_bundle과 완전히 같은 규칙(접두어 무관)으로 슬롯별 상태를
계산하고, 그 위에 회사유형별 기대 경로 확인을 «더 조심하는 방향으로만»
얹는다.

한 슬롯당 판정 순서:
1) 그 슬롯을 대상으로 한 REQUIRED 조회 기록이 하나도 없다 → UNKNOWN
   (``required_path_unobserved``, build_section_bundle과 동일한 문구)
2) REQUIRED 조회 기록 중 FAILED·TRUNCATED가 하나라도 있다 → UNKNOWN
   (``required_path_{state}``, build_section_bundle과 동일한 문구)
3) 위 두 경우가 아니면(계약 기준 «정상 확인 후 부재») 회사유형이 기대하는
   출처 접두어가 그 REQUIRED 조회 기록 중에 실제로 있었는지 한 겹 더 본다.
   한 번도 관측되지 않았다면 → UNKNOWN(``expected_path_unobserved``) —
   계약은 INSUFFICIENT 방향이지만 진단은 여기서 더 조심한다.
   관측됐다면 → INSUFFICIENT(``evidence_absent_after_check``)

회사유형을 아직 모르는(``CompanyType.UNDECIDED``) 경우 3)의 가산 확인을
아예 적용하지 않는다 — 기대할 접두어 자체가 없기 때문이다. 이 경우 슬롯별
판정은 1)·2)만으로 build_section_bundle과 완전히 같은 결과를 낸다.

이 순서 덕분에 「진단이 INSUFFICIENT인데 계약이 UNKNOWN」인 경우는 나오지
않는다 — 계약이 UNKNOWN이라고 볼 조건(1·2)은 진단에서도 그대로 UNKNOWN을
강제하고, 진단만의 추가 조건(3)은 UNKNOWN 쪽으로만 작동하기 때문이다.
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

    # 회사유형을 아직 모르면 기대할 접두어 자체가 없다 — 가산 확인(3단계)을
    # 건너뛴다. expected_required_path_prefix는 undecided를 지원하지 않으므로
    # (알 수 없는 회사 유형으로 거부한다) 아예 호출하지 않는다.
    expected_prefix = (
        None
        if company_type is CompanyType.UNDECIDED
        else expected_required_path_prefix(company_type, section_id)
    )

    reasons: list[str] = []
    any_unknown = False
    for slot_id in missing:
        # 1)·2) — build_section_bundle과 완전히 같은 규칙. 접두어와 무관하게
        # 그 슬롯을 대상으로 한 REQUIRED 조회 기록 전체를 본다.
        required_attempts = tuple(
            attempt
            for attempt in attempts
            if attempt.requirement is SourceRequirement.REQUIRED
            and slot_id in attempt.slot_ids
        )
        if not required_attempts:
            any_unknown = True
            reasons.append(f"required_path_unobserved:{slot_id}")
            continue
        failed_states = {
            attempt.state
            for attempt in required_attempts
            if attempt.state in FAILURE_COLLECTION_STATES
        }
        if failed_states:
            any_unknown = True
            for state in sorted(failed_states, key=lambda item: item.value):
                reasons.append(f"required_path_{state.value.lower()}:{slot_id}")
            continue

        # 3) — 계약 기준으로는 «정상 확인 후 부재». 회사유형별 기대 경로가
        # 있다면 그 경로가 실제로 관측됐는지 한 겹 더 확인해 더 조심하는
        # 쪽으로만 튼다(진단이 계약보다 덜 조심하게 두지 않는다).
        if expected_prefix is not None and not any(
            attempt.source_kind.startswith(expected_prefix)
            for attempt in required_attempts
        ):
            any_unknown = True
            reasons.append(f"expected_path_unobserved:{slot_id}")
            continue
        reasons.append(f"evidence_absent_after_check:{slot_id}")

    readiness = EvidenceReadiness.UNKNOWN if any_unknown else EvidenceReadiness.INSUFFICIENT
    return readiness, tuple(reasons)
