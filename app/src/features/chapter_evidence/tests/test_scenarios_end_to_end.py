"""오프라인 5종 시나리오 — 생산부 출력이 최종 생성 게이트까지 그대로 통과하는지.

여기서 검증하는 것은 candidate_readiness(진단값)가 아니라, 그 candidate를
``build_section_bundle`` + ``assess_generation_gate``(계약 로직)에 실제로
흘려서 나오는 «최종» 판정이다. 진단이 최종 로직과 다른 방향이면 여기서
드러난다.
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
    make_attempt,
    make_document,
    make_fragment,
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
    collector_slots_for,
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
            company_id=fixture["documents"][0]["company_id"],
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


_SCENARIO_FIXTURES = (
    ("corp-wisely", "audit_only", build_wisely_type_fixture),
    ("corp-listed", "listed", build_listed_fixture),
    ("corp-financial", "financial", build_financial_fixture),
    ("corp-no-homepage", "audit_only", build_no_homepage_fixture),
    ("corp-js-render", "audit_only", build_javascript_render_failure_fixture),
)


@pytest.mark.parametrize(("company_id", "company_type", "fixture_builder"), _SCENARIO_FIXTURES)
def test_진단이_INSUFFICIENT인_장은_계약도_UNKNOWN이_아니다(
    company_id: str, company_type: str, fixture_builder
) -> None:
    """진단(producer diagnostic)이 계약보다 «덜 조심»하지 않음을 고정한다.

    진단이 INSUFFICIENT라고 판단한 장은 최종 계약 판정(build_section_bundle)도
    반드시 UNKNOWN이 아니어야 한다 — 진단이 조회 장애를 자료 부재로 축소해
    사용자에게 잘못된 확신을 주면 안 된다. 다섯 시나리오 전부에서 위반이
    0건임을 고정한다.
    """

    fixture = fixture_builder()
    candidates = produce_chapter_evidence_candidates(
        company_id=company_id, company_type=company_type, **fixture
    )
    for candidate in candidates:
        if candidate.candidate_readiness is not EvidenceReadiness.INSUFFICIENT:
            continue
        bundle = build_section_bundle(
            candidate,
            required_slot_ids=required_slots_for(candidate.section_id),
            injected_slot_facts=injected_facts_for(candidate.section_id),
        )
        assert bundle.readiness is not EvidenceReadiness.UNKNOWN, (
            f"{candidate.section_id}: 진단은 INSUFFICIENT인데 계약은 UNKNOWN입니다"
        )


def test_혼합_회사_입력이_섞여도_대상회사_후보와_판정에_영향없다() -> None:
    """혼합 회사 입력 종단시험 — documents·fragments·attempts 세 곳 모두에 다른

    회사 값을 섞어도 대상 회사의 후보·준비 판정이 한 건도 바뀌지 않아야
    한다. 특히 웹 필수 장이 조회 장애(FAILED)로 UNKNOWN인 상태에서, 다른
    회사의 «정상 확인»(REQUIRED·MISSING) attempt가 그 슬롯을 «우리도
    확인했다»로 위장해 UNKNOWN을 INSUFFICIENT로 바꾸지 못해야 한다 —
    바로 그 실패 방향이 생산부가 장애를 자료 부재로 축소하는 결함이다.
    """

    target_company_id = "corp-js-render"
    foreign_company_id = "corp-other"
    fixture = build_javascript_render_failure_fixture(company_id=target_company_id)

    baseline_candidates = produce_chapter_evidence_candidates(
        company_id=target_company_id, company_type="audit_only", **fixture
    )
    baseline_by_section = {c.section_id: c for c in baseline_candidates}

    # UNKNOWN인 웹 필수 장(예: future_strategy) 슬롯을 다른 회사가
    # «정상 확인(MISSING)»한 것처럼 attempt를 심는다 — 진짜 이 회사가
    # 확인한 게 아니므로 UNKNOWN이 유지돼야 한다.
    unknown_section_id = "future_strategy"
    foreign_attempt = make_attempt(
        company_id=foreign_company_id,
        attempt_id="attempt-foreign-mixed",
        source_kind="official_homepage",
        slot_ids=collector_slots_for(unknown_section_id),
        state=CollectionState.MISSING.value,
        reason_code="official_homepage_missing",
    )

    # READY인 장(예: business_model)의 빈 슬롯이 없는 상태에 다른 회사의
    # 조각·문서를 더 얹어도 대상 회사 조각 구성은 그대로여야 한다.
    ready_section_id = "business_model"
    foreign_document = make_document(
        company_id=foreign_company_id,
        document_id="foreign-doc-mixed",
        source_kind="dart_business_report",
        exact_evidence_hashes=None,
    )
    foreign_fragment = make_fragment(
        company_id=foreign_company_id,
        fragment_id="frag-foreign-mixed",
        document_id="foreign-doc-mixed",
        section_id=ready_section_id,
        slot_id="business_model:revenue_model",
        text="다른 회사가 섞어 넣으려는 매출 구조 서술.",
        score_millis=999,
    )

    mixed_documents = [*fixture["documents"], foreign_document]
    mixed_fragments = [*fixture["fragments"], foreign_fragment]
    mixed_attempts = [*fixture["attempts"], foreign_attempt]

    mixed_candidates = produce_chapter_evidence_candidates(
        company_id=target_company_id,
        company_type="audit_only",
        documents=mixed_documents,
        fragments=mixed_fragments,
        attempts=mixed_attempts,
    )
    mixed_by_section = {c.section_id: c for c in mixed_candidates}

    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        baseline = baseline_by_section[section_id]
        mixed = mixed_by_section[section_id]
        assert mixed.candidate_readiness is baseline.candidate_readiness, (
            f"{section_id}: 혼합 입력이 준비 판정을 바꿨습니다"
        )
        assert {f.fragment_id for f in mixed.fragments} == {
            f.fragment_id for f in baseline.fragments
        }, f"{section_id}: 혼합 입력이 근거 조각 구성을 바꿨습니다"
        assert all(document.company_id == target_company_id for document in mixed.documents)
        assert all(fragment.company_id == target_company_id for fragment in mixed.fragments)
        assert all(attempt.company_id == target_company_id for attempt in mixed.attempts)

    # 특히 UNKNOWN 유지가 핵심 주장이다 — 다른 회사 attempt가 이걸 뒤집으면
    # 안 된다.
    assert mixed_by_section[unknown_section_id].candidate_readiness is EvidenceReadiness.UNKNOWN
    assert any(
        code.startswith("attempt_company_mismatch:")
        for code in mixed_by_section[unknown_section_id].reason_codes
    )
    assert any(
        code.startswith("fragment_company_mismatch:")
        for code in mixed_by_section[ready_section_id].reason_codes
    )
