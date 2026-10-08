"""같은 계약의 체결과 미래 이행을 구분하는 합성 반례."""
import datetime as dt

import pytest

from src.features.news_intake.contract_execution_context import contract_execution_context_problem
from src.features.news_intake.contract_execution_context_constants import CONTRACT_CONTEXT_REASON
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.tests.test_self_contained_news_quote import _response

AS_OF = dt.date(2026, 10, 9)
COMPANY = NewsCompanyContext('가상기술', identity_context='산업용 센서 제조')
HEADER = "2027년부터 '미래 대회'에 센서·기술 지원\n"
PERIOD = '가상기술은 2027년부터 5년간 행사에 센서를 공급한다. '
SIGNED = '가상기술은 행사 운영사와 공급 계약을 체결했다고 밝혔다. '
ACTION = "가상기술은 국제 산업용 센서 경연 행사인 '미래 대회'를 새로 후원한다."
BODY = HEADER + PERIOD + SIGNED + ACTION


def problem(text=ACTION, body=BODY, temporal='completed'):
    return contract_execution_context_problem(text, body, COMPANY, AS_OF,
                                              temporal_status=temporal)


def test_missing_future_fulfillment_period_is_rejected():
    assert problem() == CONTRACT_CONTEXT_REASON


def test_current_contract_completion_is_preserved():
    assert problem(SIGNED.strip()) == ''


def test_future_period_and_fulfillment_are_preserved_as_plan():
    text = "가상기술은 2027년부터 '미래 대회'를 후원할 예정이다."
    assert problem(text, text, 'planned') == ''


@pytest.mark.parametrize('action', [
    "가상기술은 현재 '다른 대회'를 후원하고 있다.",
    "가상기술은 이미 '미래 대회'를 후원하고 있다.",
    "가상기술은 '미래 대회'를 후원했다.",
    "가상기술은 '다른 대회'를 새로 후원한다.",
])
def test_separate_event_and_realized_actions_are_preserved(action):
    assert problem(action, HEADER + PERIOD + SIGNED + action) == ''


@pytest.mark.parametrize('body', [
    HEADER + PERIOD + ACTION,
    HEADER + PERIOD.replace('가상기술은', '다른기술은') + SIGNED + ACTION,
    HEADER + PERIOD + SIGNED + '다른기술은 행사 공급을 담당한다. ' + ACTION,
    HEADER.replace('2027', '2028') + PERIOD + SIGNED + ACTION,
    HEADER + PERIOD + SIGNED + '설명. ' * 300 + ACTION,
])
def test_unbound_contract_context_cannot_reject_other_action(body):
    assert problem(body=body) == ''


def test_production_excerpt_validation_enforces_period_constraint():
    # 원문·선택 좌표를 수정하는 대신 기존 검증 경로가 거절 사유를 낸다.
    excerpts, rejected = _response(BODY, ACTION, identity_evidence=PERIOD.strip())
    assert not excerpts
    assert rejected == {CONTRACT_CONTEXT_REASON: 1}


def test_contract_completion_survives_same_production_validation():
    excerpts, rejected = _response(BODY, SIGNED.strip(), identity_evidence=PERIOD.strip())
    assert len(excerpts) == 1
    assert not rejected


@pytest.mark.parametrize('current', [
    "가상기술은 현재 '다른 대회'를 후원하고 있다. ",
    "다른기술은 현재 '미래 대회'를 후원하고 있다. ",
    "가상기술은 현재 '미래 대회'에 센서를 공급하고 있다. ",
    "가상기술은 현재 '미래 대회'를 후원하고 있지 않다. ",
    "가상기술은 현재 '미래 대회'를 후원하고 있다. 가상기술은 후원을 중단했다. ",
    "가상기술은 2023년에 '미래 대회'를 후원하고 있다. ",
    "가상기술은 현재 '미래 대회 부대행사'를 후원하고 있다. ",
])
def test_existing_support_must_match_actor_event_action_and_remain_active(current):
    assert problem(body=current + BODY) == CONTRACT_CONTEXT_REASON


def test_explicit_future_period_cannot_be_completed_execution():
    text = "가상기술은 2027년부터 '미래 대회'를 후원한다."
    assert problem(text, text) == CONTRACT_CONTEXT_REASON


def test_one_existing_event_does_not_cover_another_future_event():
    text = "가상기술은 '기존 대회'와 '미래 대회'를 후원한다."
    current = "가상기술은 현재 '기존 대회'를 후원하고 있다. "
    assert problem(text, current + HEADER + PERIOD + SIGNED + text) == CONTRACT_CONTEXT_REASON


def test_other_event_termination_does_not_erase_current_fulfillment():
    current = "가상기술은 현재 '미래 대회'를 후원하고 있다. "
    end = "가상기술은 '다른 대회'의 후원을 중단했다. "
    assert problem(body=current + end + BODY) == ''
