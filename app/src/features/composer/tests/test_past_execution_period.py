"""최근 36개월 경계는 완료 사건일에만 적용하고 역사 맥락을 보존한다."""

import pytest

from src.features.composer.past_execution_period import past_completed_execution_period_problem
from src.features.composer.past_execution_period_constants import PAST_EXECUTION_OUTSIDE_PERIOD


@pytest.mark.parametrize("text", [
    "회사는 2020년 11월 26일 이사회 승인을 거쳐 2021년 4월 2일 합병등기를 완료하였다.",
    "회사는 2021-04-02 합병등기를 완료했다.",
    "회사는 2021.04.02 합병등기를 완료했다.",
    "회사는 2022년 새 공장을 준공했다.",
    "회사는 2021년 자회사와 합병했다.",
    "회사는 2023년 9월 새 공장을 준공했다.",
    "회사는 2023년 10월 11일 새 공장을 준공했다.",
])
def test_explicit_old_completed_event_is_outside_analysis_period(text):
    assert past_completed_execution_period_problem(text, claim_slot="past_changes:completed_execution", baseline_date="2026-10-12") == PAST_EXECUTION_OUTSIDE_PERIOD


@pytest.mark.parametrize("text", [
    "회사는 2023년 10월 12일 새 공장을 준공했다.",
    "회사는 2025년 새 공장을 준공했다.",
    "회사는 2023년 새 공장을 준공했다.",
    "회사는 2023년 10월 새 공장을 준공했다.",
    "회사는 2021년 설립한 법인을 2025년 인수했다.",
    "회사는 2021년 공장을 준공했으며 2025년 생산라인을 도입했다.",
    "회사는 2021년 설립된 법인을 인수했다.",
    "회사는 2021년 합병한 법인을 인수했다.",
    "회사는 2021년부터 준비한 제품을 출시했다.",
    "2021년 사업보고서에 따르면 회사는 합병등기를 완료했다.",
    "공시일 2021년 4월 2일: 회사는 합병등기를 완료했다.",
    "회사는 2021년 공장 건설을 완료하지 못했다.",
    "회사는 X2021 제품을 출시했다.",
    "회사는 지난해 공장을 준공했다.",
    "회사는 2021년 공장을 준공했으며 이번 분기 생산라인을 도입했다.",
    "회사는 2021년 공장을 준공했다. 회사는 생산라인을 추가 도입했다.",
    "회사는 2021년 계약한 설비의 도입을 완료했다.",
    "회사는 2021년 매입한 부지에 새 공장을 준공했다.",
    "회사는 2021년 발표한 계획에 따라 신공장 건설을 완료했다.",
    "회사는 2021년 10월 공장을 준공했고 2025년 생산라인을 증설했다.",
    "회사는 2021년 공장을 준공했다. 2025년 생산라인을 증설했다.",
    "회사는 2021년 공장을 준공했고 생산라인을 증설했다.",
])
def test_recent_or_ambiguous_or_mixed_history_is_preserved(text):
    assert past_completed_execution_period_problem(text, claim_slot="past_changes:completed_execution", baseline_date="2026-10-12") == ""


@pytest.mark.parametrize("slot,baseline", [
    ("identity:origin", "2026-10-12"),
    ("past_changes:change_interpretation", "2026-10-12"),
    ("past_changes:completed_execution", None),
    ("past_changes:completed_execution", "잘못된 날짜"),
])
def test_other_slots_and_missing_baseline_are_not_reclassified(slot, baseline):
    assert past_completed_execution_period_problem("회사는 2021년 새 공장을 준공했다.", claim_slot=slot, baseline_date=baseline) == ""


def test_leap_year_cutoff_matches_existing_publication_contract():
    assert past_completed_execution_period_problem("회사는 2021년 2월 28일 공장을 준공했다.", claim_slot="past_changes:completed_execution", baseline_date="2024-02-29") == ""


def test_verified_prose_checks_completion_date_in_actual_review_path():
    from src.features.composer.port import (
        CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    )
    from src.features.composer.tests.test_pipeline import _FakeReviewer
    from src.features.composer.verify import verify_report

    old = "회사는 2021년 4월 2일 합병등기를 완료하였다."
    recent = "회사는 2025년 4월 2일 합병등기를 완료하였다."
    fragments = tuple(
        CollectedFragment(str(index), "사업내용", text)
        for index, text in enumerate((old, recent), start=1)
    )
    report = ComposedReport((ComposedSection("past_changes", tuple(
        ComposedSentence(
            fragment.text, (fragment.fragment_id,), "확인",
            planned_claim_slot="past_changes:completed_execution",
        )
        for fragment in fragments
    )),))
    diagnostics = []

    checked = verify_report(
        report, fragments, None, _FakeReviewer(),
        baseline_date="2026-10-12", diagnostics=diagnostics,
    )

    assert tuple(sentence.text for sentence in checked.sections[0].sentences) == (recent,)
    assert checked.sections[0].sentences[0].citations == ("2",)
    assert PAST_EXECUTION_OUTSIDE_PERIOD in str(diagnostics)
