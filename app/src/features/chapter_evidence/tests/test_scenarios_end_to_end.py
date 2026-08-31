"""오프라인 5종 시나리오 — 생산부 출력이 Codex 최종 게이트까지 그대로 통과하는지.

여기서 검증하는 것은 candidate_readiness(진단값)가 아니라, 그 candidate를
``build_section_bundle`` + ``assess_generation_gate``(계약 로직, Codex가
그대로 쓸 함수)에 실제로 흘려서 나오는 «최종» 판정이다. 진단이 최종 로직과
다른 방향이면 여기서 드러난다.
"""

from __future__ import annotations

import pytest

from src.features.chapter_evidence.produce import produce_chapter_evidence_candidates
from src.features.chapter_evidence.tests.fixtures import (
    build_financial_fixture,
    build_javascript_render_failure_fixture,
    build_listed_fixture,
    build_no_homepage_fixture,
    build_unfilled_channel,
    build_wisely_type_fixture,
    injected_facts_for,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    ReportExecutionOutcome,
)
from src.shared.report_evidence.logic import assess_generation_gate, build_section_bundle
from src.shared.report_evidence.models import GenerationGateDecision
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    required_slots_for,
)


def _run_gate(candidates, *, company_id: str) -> GenerationGateDecision:
    bundles = tuple(
        build_section_bundle(
            candidate,
            required_slot_ids=required_slots_for(candidate.section_id),
            injected_slot_facts=injected_facts_for(candidate.section_id),
        )
        for candidate in candidates
    )
    return assess_generation_gate(
        company_id=company_id,
        bundles=bundles,
        required_section_ids=REQUIRED_EVIDENCE_SECTION_IDS,
    )


def _corrupt_single_section(fixture: dict, *, section_id: str, state: str, reason_code: str) -> dict:
    """listed 정상 fixture에서 한 장만 골라 조회 실패/부재로 바꾼다(음성 대조)."""

    fragments = [
        fragment for fragment in fixture["fragments"] if fragment["section_id"] != section_id
    ]
    attempts = [
        attempt
        for attempt in fixture["attempts"]
        if not all(slot_id.startswith(f"{section_id}:") for slot_id in attempt["slot_ids"])
    ]
    attempts.extend(
        build_unfilled_channel(
            section_id=section_id,
            source_kind="dart_business_report",
            state=state,
            reason_code=reason_code,
        )
    )
    return {"documents": fixture["documents"], "fragments": fragments, "attempts": attempts}


def test_시나리오1_와이즐리형_감사보고서_빈약_공식웹_다호스트는_전장_ready() -> None:
    fixture = build_wisely_type_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-wisely", company_type="audit_only", **fixture
    )
    decision = _run_gate(candidates, company_id="corp-wisely")

    assert all(
        candidate.candidate_readiness is EvidenceReadiness.READY for candidate in candidates
    )
    assert decision.status is GenerationGateStatus.READY_FOR_GENERATION
    assert decision.can_call_ai is True


def test_시나리오2_사업보고서형은_공시_경로로_전장_ready() -> None:
    fixture = build_listed_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed", company_type="listed", **fixture
    )
    decision = _run_gate(candidates, company_id="corp-listed")

    assert decision.status is GenerationGateStatus.READY_FOR_GENERATION


def test_시나리오3_금융형은_매출액_대신_대체지표_원문으로_ready() -> None:
    fixture = build_financial_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-financial", company_type="financial", **fixture
    )
    decision = _run_gate(candidates, company_id="corp-financial")

    past_changes = next(c for c in candidates if c.section_id == "past_changes")
    completed_execution_fragment = next(
        fragment
        for fragment in past_changes.fragments
        if fragment.slot_id == "past_changes:completed_execution"
    )
    assert "이자수익" in completed_execution_fragment.text
    assert "매출액" not in completed_execution_fragment.text
    assert decision.status is GenerationGateStatus.READY_FOR_GENERATION


def test_시나리오4_홈페이지없음은_웹필수장이_insufficient다() -> None:
    fixture = build_no_homepage_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-no-homepage", company_type="audit_only", **fixture
    )
    decision = _run_gate(candidates, company_id="corp-no-homepage")

    by_section = {candidate.section_id: candidate for candidate in candidates}
    for section_id in ("identity", "portfolio", "future_strategy", "culture", "competitive_position"):
        assert by_section[section_id].candidate_readiness is EvidenceReadiness.INSUFFICIENT
    for section_id in ("business_model", "past_changes", "current_challenges", "operations_partners"):
        assert by_section[section_id].candidate_readiness is EvidenceReadiness.READY

    assert decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert decision.outcome is ReportExecutionOutcome.INSUFFICIENT_EVIDENCE
    assert decision.can_call_ai is False
    assert set(decision.insufficient_section_ids) == {
        "identity",
        "portfolio",
        "future_strategy",
        "culture",
        "competitive_position",
    }
    assert decision.unknown_section_ids == ()


def test_시나리오5_자바스크립트형은_웹필수장이_unknown이다() -> None:
    fixture = build_javascript_render_failure_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-js-render", company_type="audit_only", **fixture
    )
    decision = _run_gate(candidates, company_id="corp-js-render")

    by_section = {candidate.section_id: candidate for candidate in candidates}
    for section_id in ("identity", "portfolio", "future_strategy", "culture", "competitive_position"):
        assert by_section[section_id].candidate_readiness is EvidenceReadiness.UNKNOWN

    assert decision.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE
    assert decision.outcome is ReportExecutionOutcome.TRANSIENT_FAILURE
    assert decision.can_call_ai is False
    assert set(decision.unknown_section_ids) == {
        "identity",
        "portfolio",
        "future_strategy",
        "culture",
        "competitive_position",
    }


@pytest.mark.parametrize(
    ("state", "expected_readiness"),
    [
        (CollectionState.FAILED.value, EvidenceReadiness.UNKNOWN),
        (CollectionState.TRUNCATED.value, EvidenceReadiness.UNKNOWN),
        (CollectionState.MISSING.value, EvidenceReadiness.INSUFFICIENT),
    ],
)
def test_한_장만_조회장애여도_다른_여덟_장은_영향받지_않는다(
    state: str, expected_readiness: EvidenceReadiness
) -> None:
    """겹마다 따로 — 한 층(한 장)의 조회 장애가 다른 층까지 초록불을 끄지 않는지 확인한다."""

    fixture = build_listed_fixture()
    corrupted = _corrupt_single_section(
        fixture,
        section_id="future_strategy",
        state=state,
        reason_code=f"dart_business_report_{state.lower()}",
    )

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed", company_type="listed", **corrupted
    )
    by_section = {candidate.section_id: candidate for candidate in candidates}

    assert by_section["future_strategy"].candidate_readiness is expected_readiness
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        if section_id == "future_strategy":
            continue
        assert by_section[section_id].candidate_readiness is EvidenceReadiness.READY
