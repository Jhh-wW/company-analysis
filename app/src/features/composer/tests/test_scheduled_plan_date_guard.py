"""지난 예정일 검사와 정상 미래·과거 설명의 경계를 확인한다."""

import json
import pytest

from src.features.composer.grounding import constrain_verdicts
from src.features.composer.plan_timing_guard import plan_timing_problem

BASELINE = "2026-10-09"
REASON = "plan_target_year_outdated"
STALE = "회사는 2025년 3월 31일 개최 예정인 제20기 정기주주총회에서 감사 선임의 건을 안건으로 상정하여 결의를 진행할 예정이다."
UNDATED = "감사 선임과 관련하여 같은 정기주주총회에서 중임 건을 안건으로 상정하여 결의를 진행할 예정이다."


@pytest.mark.parametrize("claim,sources", [
    (STALE, {"1": STALE}),
    (UNDATED, {"1": STALE}),
    ("회사는 2026년 10월 8일까지 신공장을 완공할 예정이다.", {}),
    ("회사는 2026-10-08 신공장을 완공할 예정이다.", {}),
    ("회사는 2026년 9월 신제품을 출시할 예정이다.", {}),
    ("회사는 2026년 3분기 신제품을 출시할 예정이다.", {}),
    ("회사는 2025년 신제품을 출시할 예정이다.", {}),
])
def test_expired_scheduled_period_is_rejected(claim, sources):
    assert plan_timing_problem(claim, sources, baseline_date=BASELINE) == REASON


@pytest.mark.parametrize("claim", [
    "회사는 2026년 10월 9일 신공장을 완공할 예정이다.",
    "회사는 2026년 10월 신제품을 출시할 예정이다.",
    "회사는 2026년 4분기 신제품을 출시할 예정이다.",
    "회사는 2026년 신제품을 출시할 예정이다.",
    "회사는 2027년 3월 31일 정기주주총회에서 결의를 진행할 예정이다.",
    "회사는 2025년 3월 31일 주주총회에서 결의를 진행했다.",
    "회사는 2025년 3월 31일 개최 예정인 주주총회에서 결의를 진행했다.",
    "회사는 당시 2025년 3월 31일 개최 예정인 주주총회에서 결의를 진행할 예정이라고 밝혔다.",
    "회사는 2025년 3월 31일 주주총회 개최 계획을 취소했다.",
    "회사는 2025년 3월 31일 주주총회 개최를 연기하고 현재 일정을 조정 중이다.",
    "회사는 2025년부터 신제품을 출시할 예정이다.",
    "회사는 2025년 매출을 분석하고 신제품을 출시할 예정이다.",
    "회사는 2025년 13월 31일 신제품을 출시할 예정이다.",
])
def test_future_uncertain_period_and_history_are_preserved(claim):
    assert plan_timing_problem(claim, {"1": claim}, baseline_date=BASELINE) == ""


def test_other_source_event_does_not_supply_candidate_date():
    claim = "회사는 같은 제품설명회에서 결의를 진행할 예정이다."
    assert plan_timing_problem(claim, {"1": STALE}, baseline_date=BASELINE) == ""


def test_other_action_does_not_supply_candidate_date():
    claim = "회사는 같은 정기주주총회에서 신제품을 출시할 예정이다."
    assert plan_timing_problem(claim, {"1": STALE}, baseline_date=BASELINE) == ""


def test_table_cells_do_not_borrow_dates():
    cells = ("2025년 3월 31일", "정기주주총회에서 결의를 진행할 예정이다")
    assert plan_timing_problem(" ".join(cells), {}, cells, baseline_date=BASELINE) == ""


def test_latest_own_future_event_does_not_become_past_from_other_event():
    current = STALE.replace("2025년 3월 31일", "2027년 3월 31일")
    assert plan_timing_problem(UNDATED, {"1": STALE, "2": current}, baseline_date=BASELINE) == ""


@pytest.mark.parametrize("verdict", ["참", "애매"])
def test_actual_grounding_path_rejects_current_future_interpretation(verdict):
    raw = json.dumps({"판정": [{"번호": 1, "결과": verdict}]}, ensure_ascii=False)
    result, problems = constrain_verdicts(raw, {1: verdict}, {1: (UNDATED, {"1": STALE})}, baseline_date=BASELINE)
    assert result == {1: "근거결속실패"}
    assert problems == {1: REASON}


@pytest.mark.parametrize("baseline", [None, "unknown"])
def test_missing_baseline_is_not_guessed(baseline):
    assert plan_timing_problem(STALE, {"1": STALE}, baseline_date=baseline) == ""


def test_exact_current_reaffirmation_is_preserved_without_inferring_completion():
    source = "2026년 10월 9일 현재 " + STALE
    assert plan_timing_problem(STALE, {"1": source}, baseline_date=BASELINE) == ""


@pytest.mark.parametrize("prefix", ["2026년 현재 ", "2026년 10월 10일 현재 ", "2025년 10월 9일 현재 "])
def test_missing_or_future_reaffirmation_is_not_used(prefix):
    assert plan_timing_problem(STALE, {"1": prefix + STALE}, baseline_date=BASELINE) == REASON


def test_future_event_after_unrelated_old_date_is_preserved():
    claim = "회사는 2025년 3월 31일 회의를 진행하고 2027년 3월 31일 공장을 준공할 예정이다."
    assert plan_timing_problem(claim, {"1": claim}, baseline_date=BASELINE) == ""
