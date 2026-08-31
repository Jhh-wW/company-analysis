from __future__ import annotations

import pytest

from src.features.chapter_evidence.constants import CompanyType
from src.features.chapter_evidence.diagnose import diagnose_candidate_readiness
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    SourceRequirement,
)
from src.shared.report_evidence.models import CollectionAttempt


def _attempt(
    *,
    attempt_id: str,
    source_kind: str,
    slot_ids: tuple[str, ...],
    state: CollectionState,
    requirement: SourceRequirement = SourceRequirement.REQUIRED,
) -> CollectionAttempt:
    return CollectionAttempt(
        attempt_id=attempt_id,
        source_kind=source_kind,
        requirement=requirement,
        state=state,
        slot_ids=slot_ids,
        reason_code=f"{source_kind}_{state.value.lower()}",
    )


def test_수집슬롯이_모두_찼으면_ready다() -> None:
    readiness, reasons = diagnose_candidate_readiness(
        section_id="business_model",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(
            {
                "business_model:revenue_model",
                "business_model:customer_type",
                "business_model:value_exchange",
            }
        ),
        attempts=(),
    )

    assert readiness is EvidenceReadiness.READY
    assert reasons == ()


def test_확인기록이_전혀_없는_빈슬롯은_unknown이다() -> None:
    readiness, reasons = diagnose_candidate_readiness(
        section_id="identity",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert "required_path_unobserved:identity:corporate_identity" in reasons


def test_기대경로가_아닌_출처의_시도는_확인으로_치지_않는다() -> None:
    # listed 는 identity 의 기대 경로가 dart_ 다. official_ 로만 조회했다면
    # «기대 경로를 아직 확인 안 한 것»이라 INSUFFICIENT가 아니라 UNKNOWN이다.
    wrong_path_attempt = _attempt(
        attempt_id="a1",
        source_kind="official_homepage",
        slot_ids=("identity:corporate_identity",),
        state=CollectionState.MISSING,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="identity",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(wrong_path_attempt,),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert "required_path_unobserved:identity:corporate_identity" in reasons


def test_기대경로를_정상확인했지만_부재면_insufficient다() -> None:
    # past_changes 는 수집 슬롯이 completed_execution 하나뿐이라(historical_
    # performance는 주입 몫) 다른 빈 슬롯이 섞여 UNKNOWN으로 끌려가지 않는다.
    attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("past_changes:completed_execution",),
        state=CollectionState.MISSING,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="past_changes",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(attempt,),
    )

    assert readiness is EvidenceReadiness.INSUFFICIENT
    assert "evidence_absent_after_check:past_changes:completed_execution" in reasons


@pytest.mark.parametrize("state", [CollectionState.FAILED, CollectionState.TRUNCATED])
def test_기대경로_조회_실패나_절단은_unknown이다(state: CollectionState) -> None:
    attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("identity:corporate_identity",),
        state=state,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="identity",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(attempt,),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert f"required_path_{state.value.lower()}:identity:corporate_identity" in reasons


def test_optional_시도는_기대경로_확인으로_치지_않는다() -> None:
    optional_attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("identity:corporate_identity",),
        state=CollectionState.MISSING,
        requirement=SourceRequirement.OPTIONAL,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="identity",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(optional_attempt,),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert "required_path_unobserved:identity:corporate_identity" in reasons


def test_회사유형에_따라_기대경로가_달라진다() -> None:
    # competitive_position 은 수집 슬롯이 self_context 하나뿐이라(비교 5칸은
    # 주입 몫) 슬롯 개수 문제 없이 경로만 비교할 수 있다. audit_only 는
    # 기대 경로가 official_ 이다. 같은 dart_ 확인 기록만으로는 여전히
    # UNKNOWN이어야 한다.
    dart_attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_audit_report",
        slot_ids=("competitive_position:self_context",),
        state=CollectionState.MISSING,
    )

    readiness, _ = diagnose_candidate_readiness(
        section_id="competitive_position",
        company_type=CompanyType.AUDIT_ONLY,
        filled_slot_ids=frozenset(),
        attempts=(dart_attempt,),
    )

    assert readiness is EvidenceReadiness.UNKNOWN

    official_attempt = _attempt(
        attempt_id="a2",
        source_kind="official_homepage",
        slot_ids=("competitive_position:self_context",),
        state=CollectionState.MISSING,
    )

    readiness_official, reasons_official = diagnose_candidate_readiness(
        section_id="competitive_position",
        company_type=CompanyType.AUDIT_ONLY,
        filled_slot_ids=frozenset(),
        attempts=(official_attempt,),
    )

    assert readiness_official is EvidenceReadiness.INSUFFICIENT
    assert (
        "evidence_absent_after_check:competitive_position:self_context"
        in reasons_official
    )


def test_일부만_찬_장은_빈슬롯만_진단한다() -> None:
    attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("business_model:customer_type",),
        state=CollectionState.MISSING,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="business_model",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(
            {"business_model:revenue_model", "business_model:value_exchange"}
        ),
        attempts=(attempt,),
    )

    assert readiness is EvidenceReadiness.INSUFFICIENT
    assert reasons == ("evidence_absent_after_check:business_model:customer_type",)
