"""회수관리의 고객 키워드가 고객유형 지원이나 AI 재유입이 되지 않는다."""
import pytest

from features.evidence_collection.business_slot_scope import business_slot_scope, business_slot_scope_problem
from features.evidence_collection.relevance import score_fragment_slots_with_signal


CUSTOMER = "business_model:customer_type"
POLICY = "회사는 거래처 관련 금융자산의 회수지연 현황 및 회수대책을 정기 보고하고 지연사유에 따라 적절한 조치를 취한다."


def test_recovery_management_remains_observed_without_customer_support():
    scores, observed = score_fragment_slots_with_signal(POLICY)
    assert observed
    assert CUSTOMER not in {score.slot_id for score in scores}
    assert business_slot_scope_problem(POLICY, CUSTOMER)


def test_customer_word_in_credit_evaluation_does_not_reenter():
    text = "일반거래처의 경우 고객의 재무상태와 과거 경험을 고려하여 신용을 평가한다. " + POLICY
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert CUSTOMER not in {score.slot_id for score in scores}


@pytest.mark.parametrize("fact", [
    "주요 고객사는 기업과 대학이다.",
    "회사는 기업 고객사에 대출 서비스를 제공한다.",
    "회사는 고객사에게 상품을 판매하며 외상판매대금을 회수한다.",
    "당사는 고객사의 신용을 평가해 등급을 제공하고 평가수수료를 받는다.",
])
def test_actual_customer_relation_survives_mixed_management(fact):
    scores, observed = score_fragment_slots_with_signal(POLICY + " " + fact)
    assert observed
    assert CUSTOMER in {score.slot_id for score in scores}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("customer", ["고객", "고객사"])
def test_loan_service_and_recovery_reporting_have_separate_support(reverse, customer):
    service = f"은행은 중소기업 {customer}에게 대출상품을 제공하고 이자를 수취한다"
    management = "대출채권의 회수대책을 보고한다"
    text = ", ".join([management, service] if reverse else [service, management]) + "."
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    # 기존 채점 어휘는 고객사이며, 일반 고객 표기의 새 지원칸은 만들지 않는다.
    if customer == "고객사":
        assert CUSTOMER in {score.slot_id for score in scores}
    assert service in business_slot_scope(text, CUSTOMER).score_text
    assert not business_slot_scope_problem(text, CUSTOMER)
