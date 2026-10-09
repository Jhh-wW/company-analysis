"""날짜 없는 행사 지시어가 자기 원문의 지난 예정일을 지우지 못한다."""
import pytest

from src.features.composer.scheduled_plan_date_guard import scheduled_plan_date_problem


BASELINE = "2026-10-09"
PAST = "2025년 3월 31일 개최 예정인 제12기 정기주주총회에서 감사 선임 안건을 상정하여 결의를 진행할 예정입니다."
CLAIM = "{} 주주총회에서 감사 선임 안건을 상정하여 결의를 진행할 예정이다."


@pytest.mark.parametrize("reference", ["같은", "해당", "이번"])
def test_reference_keeps_past_event_date(reference):
    assert scheduled_plan_date_problem(CLAIM.format(reference), {"a": PAST}, baseline_date=BASELINE) == "plan_target_year_outdated"


def test_same_explicit_event_term_is_bound():
    claim = CLAIM.format("같은 제12기")
    assert scheduled_plan_date_problem(claim, {"a": PAST}, baseline_date=BASELINE) == "plan_target_year_outdated"


def test_other_explicit_event_term_does_not_borrow_date():
    assert scheduled_plan_date_problem(CLAIM.format("이번 제13기"), {"a": PAST}, baseline_date=BASELINE) == ""


def test_direct_other_term_does_not_borrow_date():
    claim = "제13기 정기주주총회에서 감사 선임 안건을 상정하여 결의를 진행할 예정이다."
    assert scheduled_plan_date_problem(claim, {"a": PAST}, baseline_date=BASELINE) == ""


@pytest.mark.parametrize("agenda", ["정관 개정", "이사 선임"])
def test_other_agenda_does_not_borrow_date(agenda):
    claim = CLAIM.format("같은").replace("감사 선임", agenda)
    assert scheduled_plan_date_problem(claim, {"a": PAST}, baseline_date=BASELINE) == ""


def test_current_future_event_in_own_sources_is_preserved():
    future = PAST.replace("2025년 3월 31일", "2027년 3월 31일")
    assert scheduled_plan_date_problem(CLAIM.format("해당"), {"a": PAST, "b": future}, baseline_date=BASELINE) == ""


def test_completed_event_does_not_date_separate_future_activity():
    source = "2025년 3월 31일 주주총회를 개최했고, 회사는 신규 서비스를 출시할 예정이다."
    claim = "이번 주주총회에서 발표한 신규 서비스를 출시할 예정이다."
    assert scheduled_plan_date_problem(claim, {"a": source}, baseline_date=BASELINE) == ""


def test_conditional_separate_future_activity_is_preserved():
    claim = "같은 주주총회에서 승인한 사업은 허가를 받으면 신규 서비스를 출시할 예정이다."
    assert scheduled_plan_date_problem(claim, {"a": PAST, "b": claim}, baseline_date=BASELINE) == ""


def test_historical_plan_report_is_preserved():
    claim = "당시 같은 주주총회에서 감사 선임 안건을 상정하여 결의를 진행할 예정이라고 밝혔다."
    assert scheduled_plan_date_problem(claim, {"a": PAST}, baseline_date=BASELINE) == ""


def test_different_event_does_not_borrow_past_date():
    claim = "이번 임원회의에서 감사 선임 안건을 상정하여 결의를 진행할 예정이다."
    assert scheduled_plan_date_problem(claim, {"a": PAST}, baseline_date=BASELINE) == ""


def test_same_event_different_scheduled_action_is_preserved():
    claim = "같은 주주총회에서 공개한 신규 서비스를 출시할 예정이다."
    assert scheduled_plan_date_problem(claim, {"a": PAST}, baseline_date=BASELINE) == ""
