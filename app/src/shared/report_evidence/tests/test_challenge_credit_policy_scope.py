"""신용 측정·평상시 관리와 실제 고객 사건을 같은 절에서 구분한다."""
import hashlib

import pytest

from src.shared.report_evidence.business_slot_scope import business_slot_quote_problem, business_slot_scope
from src.shared.report_evidence.challenge_eligibility import challenge_eligibility_problem, challenge_eligibility_scope

POLICIES = (
    "회사는 기대신용손실 측정 시 미래전망정보를 반영하고 있다.",
    "회사는 거시경제변수와 측정요소 간 모델링으로 기대신용손실을 산출한다.",
    "회사는 신용위험의 유의적 증가 여부를 판단하기 위해 연체와 자산건전성 정보를 활용한다.",
    "채무불이행은 부도와 동일하게 정의하며 연체한 경우로 간주한다.",
    "회사는 금융자산의 회수지연 현황 및 회수대책이 정기적으로 보고되고 지연사유에 따라 적절한 조치를 취한다.",
    "회사는 금융기관에 예치금을 예치하고 신용등급이 우수한 금융기관과 거래하므로 신용위험은 제한적이다.",
)
ACTUALS = (
    "은행 대출 고객의 연체율이 상승했고 현재 상환 지원을 제공하고 있다.",
    "보험 고객의 보상 지급 지연이 발생했으며 대응하고 있다.",
    "결제 대행 서비스 고객의 결제 장애가 발생했다.",
    "거래처가 파산했으며 매출채권의 회수가 지연됐다.",
    "금융자산의 회수가 중단됐으며 현재 고객 회수 지원을 진행하고 있다.",
)
SLOTS = ("current_challenges:issue", "current_challenges:response")


@pytest.mark.parametrize("policy", POLICIES)
def test_credit_policy_does_not_fill_issue_or_response_and_raw_is_preserved(policy):
    digest = hashlib.sha256(policy.encode()).hexdigest()
    assert challenge_eligibility_problem(policy) == "challenge_business_relation_unbound"
    for slot in SLOTS:
        assert not business_slot_scope(policy, slot).score_text.strip()
        assert business_slot_quote_problem(policy, slot, 0, len(policy))
    assert hashlib.sha256(policy.encode()).hexdigest() == digest
    assert not business_slot_quote_problem(policy, "business_model:revenue_model", 0, len(policy))


@pytest.mark.parametrize("actual", ACTUALS)
def test_actual_financial_customer_problem_remains_for_semantic_review(actual):
    assert challenge_eligibility_scope(actual).score_text == actual
    assert not challenge_eligibility_problem(actual)
    for slot in SLOTS:
        assert not business_slot_quote_problem(actual, slot, 0, len(actual))


@pytest.mark.parametrize("separator", (" ", "\n"))
def test_policy_crop_cannot_borrow_actual_problem_in_mixed_source(separator):
    policy = POLICIES[0]
    actual = ACTUALS[2]
    source = policy + separator + actual
    scope = challenge_eligibility_scope(source)
    assert actual in scope.score_text
    assert policy not in scope.score_text
    for slot in SLOTS:
        assert business_slot_quote_problem(source, slot, 0, len(policy))
        assert not business_slot_quote_problem(source, slot, len(policy + separator), len(source))


@pytest.mark.parametrize("actual", ACTUALS)
def test_same_clause_actual_event_is_kept_but_policy_only_crop_is_restricted(actual):
    policy = POLICIES[0][:-1]
    source = policy + "며 " + actual
    assert actual in challenge_eligibility_scope(source).score_text
    for slot in SLOTS:
        assert business_slot_quote_problem(source, slot, 0, len(policy))
        assert not business_slot_quote_problem(source, slot, len(policy + "며 "), len(source))


@pytest.mark.parametrize("service", (
    "은행은 고객에게 기대신용손실 측정 서비스를 제공하고 있다.",
    "은행은 기대신용손실 측정 서비스를 고객에게 제공하고 있다.",
    "금융사는 고객에게 신용위험 평가 솔루션을 제공하고 있다.",
))
def test_customer_credit_service_is_not_removed_as_internal_measurement(service):
    assert challenge_eligibility_scope(service).score_text == service
    assert not challenge_eligibility_problem(service)


def test_conditional_definition_does_not_assert_an_actual_loan_default():
    source = "회사는 신용위험의 유의적 증가 여부를 판단하기 위해 고객이 연체한 경우의 정보를 활용한다."
    assert challenge_eligibility_problem(source)


@pytest.mark.parametrize("condition", (
    "고객 연체가 급증할 경우",
    "고객 연체율이 상승했다고 가정한 경우",
))
def test_policy_condition_does_not_become_an_actual_customer_event(condition):
    source = "회사는 기대신용손실을 측정하며 " + condition + " 손실충당금을 인식한다."
    assert challenge_eligibility_problem(source)


def test_short_measurement_crop_cannot_borrow_actual_event_in_same_clause():
    source = "회사는 기대신용손실 측정 시 미래전망정보를 반영하며 결제 서비스 고객의 결제 장애가 발생했다."
    quote = "미래전망정보를 반영"
    actual = "결제 서비스 고객의 결제 장애가 발생했다"
    for slot in SLOTS:
        start = source.index(quote)
        assert business_slot_quote_problem(source, slot, start, start + len(quote))
        start = source.index(actual)
        assert not business_slot_quote_problem(source, slot, start, start + len(actual))
