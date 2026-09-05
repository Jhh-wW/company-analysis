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
    company_id: str = "corp-1",
    attempt_id: str,
    source_kind: str,
    slot_ids: tuple[str, ...],
    state: CollectionState,
    requirement: SourceRequirement = SourceRequirement.REQUIRED,
) -> CollectionAttempt:
    # diagnose_candidate_readiness는 company_id를 스스로 확인하지 않는다
    # (그건 produce.py의 몫이다) — 이 시험 helper의 기본값은 순수 자리
    # 채우기용이다.
    return CollectionAttempt(
        company_id=company_id,
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
    # (계약 기준으로는 REQUIRED 조회가 있고 실패도 아니라 «정상 확인 후 부재»
    # 이지만, 기대 경로 미관측이라는 가산 확인이 진단을 UNKNOWN 쪽으로 더
    # 조심하게 튼다 — 그래서 사유 코드도 계약과 다른 expected_path_unobserved다.)
    wrong_path_attempt = _attempt(
        attempt_id="a1",
        source_kind="official_web_page",
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
    assert "expected_path_unobserved:identity:corporate_identity" in reasons


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


def test_필수경로가_MISSING이어도_같은슬롯_site_probe_게이트가_전부막히면_unknown이다() -> None:
    """P1-B — build_section_bundle과 같은 site_probe_gate_* 분기.

    REQUIRED 조회(DART)는 정상적으로 MISSING을 돌려줬지만, 같은 슬롯을
    겨냥한 site-probe 게이트(robots.txt 차단)가 FAILED면 «확인을 마쳤다»고
    단정하지 않는다. 진단이 계약보다 덜 조심하면 안 된다는 이 모듈의
    불변식을 지키려면 build_section_bundle과 같은 조건에서 같은 방향
    (UNKNOWN)으로 판정해야 한다.
    """

    required_attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("identity:corporate_identity",),
        state=CollectionState.MISSING,
    )
    site_probe_failed_attempt = _attempt(
        attempt_id="a2",
        source_kind="robots_txt",
        slot_ids=("identity:corporate_identity",),
        state=CollectionState.FAILED,
        requirement=SourceRequirement.OPTIONAL,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="identity",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(required_attempt, site_probe_failed_attempt),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert "site_probe_gate_failed:identity:corporate_identity" in reasons


def test_site_probe_게이트가_다른_host에서_한번이라도_성공하면_단정을_막지_않는다() -> None:
    """P0 회귀 방지 — www/apex 대체 host 하나가 robots 차단이어도, 다른
    host의 robots가 정상 확인됐다면(그 출처를 실제로 열어본 것) 진단이
    UNKNOWN으로 끌려가지 않는다. past_changes는 수집 슬롯이
    completed_execution 하나뿐이라(historical_performance는 주입 몫) 다른
    빈 슬롯이 섞여 UNKNOWN으로 끌려가지 않는다."""

    required_attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("past_changes:completed_execution",),
        state=CollectionState.MISSING,
    )
    root_host_robots_ok = _attempt(
        attempt_id="a2",
        source_kind="robots_txt",
        slot_ids=("past_changes:completed_execution",),
        state=CollectionState.OK,
        requirement=SourceRequirement.OPTIONAL,
    )
    alt_host_robots_failed = _attempt(
        attempt_id="a3",
        source_kind="robots_txt",
        slot_ids=("past_changes:completed_execution",),
        state=CollectionState.FAILED,
        requirement=SourceRequirement.OPTIONAL,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="past_changes",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(required_attempt, root_host_robots_ok, alt_host_robots_failed),
    )

    assert readiness is EvidenceReadiness.INSUFFICIENT
    assert "evidence_absent_after_check:past_changes:completed_execution" in reasons


def test_site_probe_게이트가_아닌_보조경로_실패는_확인완료_단정을_막지_않는다() -> None:
    """P0 회귀 방지 — IR PDF 없음처럼 site-probe 게이트가 아닌 흔한 개별
    경로 실패는 «확인 마침» 단정을 막지 않는다(원래 P0: IR 1건 실패가
    9장을 다 죽이던 회귀). past_changes는 수집 슬롯이 completed_execution
    하나뿐이다."""

    required_attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("past_changes:completed_execution",),
        state=CollectionState.MISSING,
    )
    ir_failed_attempt = _attempt(
        attempt_id="a2",
        source_kind="official_ir_pdf",
        slot_ids=("past_changes:completed_execution",),
        state=CollectionState.FAILED,
        requirement=SourceRequirement.OPTIONAL,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="past_changes",
        company_type=CompanyType.LISTED,
        filled_slot_ids=frozenset(),
        attempts=(required_attempt, ir_failed_attempt),
    )

    assert readiness is EvidenceReadiness.INSUFFICIENT
    assert "evidence_absent_after_check:past_changes:completed_execution" in reasons
    assert "evidence_absent_after_check:identity:corporate_identity" not in reasons


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
        slot_ids=(
            "competitive_position:self_context",
            "competitive_position:stated_differentiator",
        ),
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
        source_kind="official_web_page",
        slot_ids=(
            "competitive_position:self_context",
            "competitive_position:stated_differentiator",
        ),
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


def test_undecided_회사유형은_기대경로_가산규칙을_적용하지_않는다() -> None:
    # competitive_position 은 수집 슬롯이 self_context 하나뿐이라(비교 5칸은
    # 주입 몫) 다른 빈 슬롯이 섞여 UNKNOWN으로 끌려가지 않는다. 회사 유형을
    # 아직 모르면(undecided) 기대할 접두어 자체가 없다. LISTED라면 dart_가
    # 기대 경로지만, 여기서는 official_로만 조회해도 «기대 경로 미관측» 가산
    # 확인 없이 계약과 완전히 같은 판정(정상 확인 후 부재)만 내야 한다.
    attempt = _attempt(
        attempt_id="a1",
        source_kind="official_web_page",
        slot_ids=(
            "competitive_position:self_context",
            "competitive_position:stated_differentiator",
        ),
        state=CollectionState.MISSING,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="competitive_position",
        company_type=CompanyType.UNDECIDED,
        filled_slot_ids=frozenset(),
        attempts=(attempt,),
    )

    assert readiness is EvidenceReadiness.INSUFFICIENT
    assert reasons == (
        "evidence_absent_after_check:competitive_position:self_context",
        "evidence_absent_after_check:competitive_position:stated_differentiator",
    )


def test_undecided_회사유형도_조회_실패는_여전히_unknown이다() -> None:
    # 가산 확인만 안 쓸 뿐, 계약과 동일한 1)·2) 규칙(REQUIRED 조회 자체가
    # 없거나 실패·절단)은 undecided에서도 그대로 적용된다.
    attempt = _attempt(
        attempt_id="a1",
        source_kind="dart_business_report",
        slot_ids=("competitive_position:self_context",),
        state=CollectionState.FAILED,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="competitive_position",
        company_type=CompanyType.UNDECIDED,
        filled_slot_ids=frozenset(),
        attempts=(attempt,),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert "required_path_failed:competitive_position:self_context" in reasons


def test_접두어만_닮은_미등록조회는_정상_필수경로가_아니다() -> None:
    fake_attempt = _attempt(
        attempt_id="fake-source",
        source_kind="official_fake",
        slot_ids=("competitive_position:self_context",),
        state=CollectionState.MISSING,
    )

    readiness, reasons = diagnose_candidate_readiness(
        section_id="competitive_position",
        company_type=CompanyType.AUDIT_ONLY,
        filled_slot_ids=frozenset(),
        attempts=(fake_attempt,),
    )

    assert readiness is EvidenceReadiness.UNKNOWN
    assert "expected_path_unobserved:competitive_position:self_context" in reasons
