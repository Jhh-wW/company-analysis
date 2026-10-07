"""수집·AI 부분인용의 신용 측정 정책과 실제 고객 사건 경계를 검증한다."""
import pytest

from features.evidence_collection.business_slot_scope import business_slot_scope, business_slot_quote_problem
from features.evidence_collection.challenge_eligibility import challenge_eligibility_problem, challenge_eligibility_scope
from features.evidence_collection.relevance import score_fragment_slots_with_signal

@pytest.mark.parametrize("source", (
    "회사는 기대신용손실 측정 시 미래전망정보를 반영하고 있다.",
    "회사는 신용위험의 유의적 증가 여부를 판단하기 위해 연체 정보를 활용한다.",
    "회사는 금융자산의 회수지연 현황 및 회수대책이 보고되고 지연사유에 따라 적절한 조치를 취한다.",
    "회사는 금융기관에 예치금을 예치하고 신용등급이 우수한 금융기관과 거래하므로 신용위험은 제한적이다.",
))
def test_credit_policy_is_not_a_current_business_slot(source):
    assert challenge_eligibility_problem(source)
    for slot in ("current_challenges:issue", "current_challenges:response"):
        assert not business_slot_scope(source, slot).score_text.strip()
        assert business_slot_quote_problem(source, slot, 0, len(source))
    scores, observed = score_fragment_slots_with_signal(source, allowed_slot_ids=frozenset(("current_challenges:issue", "current_challenges:response")))
    assert not scores
    assert observed


@pytest.mark.parametrize("source", (
    "은행 대출 고객의 연체율이 상승했고 현재 상환 지원을 제공하고 있다.",
    "결제 대행 서비스 고객의 결제 장애가 발생했다.",
    "거래처가 파산했으며 매출채권의 회수가 지연됐다.",
    "은행은 고객에게 기대신용손실 측정 서비스를 제공하고 있다.",
))
def test_actual_financial_customer_problem_or_service_is_not_removed(source):
    assert challenge_eligibility_scope(source).score_text == source
    assert not challenge_eligibility_problem(source)


def test_measurement_crop_cannot_borrow_same_clause_actual_event():
    source = "회사는 기대신용손실 측정 시 미래전망정보를 반영하며 결제 서비스 고객의 결제 장애가 발생했다."
    for slot in ("current_challenges:issue", "current_challenges:response"):
        policy = "미래전망정보를 반영"
        actual = "결제 서비스 고객의 결제 장애가 발생했다"
        start = source.index(policy)
        assert business_slot_quote_problem(source, slot, start, start + len(policy))
        start = source.index(actual)
        assert not business_slot_quote_problem(source, slot, start, start + len(actual))
