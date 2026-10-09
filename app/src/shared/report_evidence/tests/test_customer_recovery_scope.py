"""회수관리와 실제 제공물의 고객 관계를 같은 원문에서 분리한다."""
import hashlib

import pytest

from src.shared.report_evidence.business_slot_scope import (
    business_slot_quote_problem, business_slot_scope, business_slot_scope_problem,
)


CUSTOMER = "business_model:customer_type"
POLICY = "회사는 회수가 지연되는 금융자산에 대하여 회수지연 현황 및 회수대책이 보고되고 있으며 지연사유에 따라 적절한 조치를 취하고 있다."


@pytest.mark.parametrize("text", [
    POLICY,
    "회사는 거래처 관련 금융자산의 회수지연 현황을 정기 보고한다.",
    "회사는 수취채권의 회수대책을 보고하고 지연사유를 관리한다.",
    "회사는 미수금의 회수지연을 점검하는 정책을 운영한다.",
    "일반거래처의 경우 고객의 재무상태와 과거 경험 등을 고려하여 신용을 평가한다.",
    "당사는 고객사의 신용을 평가해 거래한도를 정하고 담보를 수취한다.",
    "당사는 고객사의 신용을 평가해 등급을 제공하고 평가수수료를 받을 계획이다.",
])
def test_recovery_management_is_not_a_customer_type(text):
    assert business_slot_scope_problem(text, CUSTOMER) == "business_slot_scope_unsupported"


@pytest.mark.parametrize("text", [
    "회사는 구독 고객에게 콘텐츠를 제공한다.",
    "회사는 중소기업에 운영자금대출을 제공하고 이자를 수취한다.",
    "회사는 광고주에게 광고 서비스를 제공한다.",
    "회사는 정부부처와 대학을 대상으로 교육 서비스를 제공한다.",
    "회사는 고객사에게 상품을 판매하며 그 외상판매대금을 회수한다.",
    "회사는 기업 고객에게 금융자산 회수지연 관리 서비스를 제공한다.",
    "회사는 고객사에게 신용을 평가하는 서비스를 제공한다.",
    "당사는 고객사의 신용을 평가해 등급을 제공하고 평가수수료를 받는다.",
    "회사의 주요 고객은 개인 차주이며 대출채권 회수대책을 보고한다.",
])
def test_actual_customers_and_receivable_services_are_preserved(text):
    assert not business_slot_scope_problem(text, CUSTOMER)
    assert business_slot_scope(text, CUSTOMER).score_text.strip()


@pytest.mark.parametrize("separator", [" ", "\n", "; ", ", "])
def test_mixed_source_keeps_sales_but_rejects_management_quote(separator):
    sale = "회사는 고객사에 콘텐츠를 제공한다."
    text = POLICY + separator + sale
    digest = hashlib.sha256(text.encode()).hexdigest()
    assert not business_slot_scope_problem(text, CUSTOMER)
    assert "고객사에콘텐츠를제공한다" in "".join(business_slot_scope(text, CUSTOMER).score_text.split())
    assert business_slot_quote_problem(text, CUSTOMER, 0, len(POLICY))
    assert not business_slot_quote_problem(text, CUSTOMER, len(POLICY + separator), len(text))
    assert hashlib.sha256(text.encode()).hexdigest() == digest


@pytest.mark.parametrize("slot", ["business_model:revenue_model", "business_model:value_exchange", "operations_partners:operating_role"])
def test_customer_only_restriction_preserves_other_slots(slot):
    scoped = business_slot_scope(POLICY, slot)
    # 운영역할은 기존 절 분할을 사용하므로 원문 문자열 대신 제외 여부를 대조한다.
    assert not scoped.excluded_clauses
    assert not business_slot_scope_problem(POLICY, slot)


@pytest.mark.parametrize("reverse", [False, True])
def test_recovery_context_does_not_remove_an_independent_loan_service(reverse):
    service = "은행은 중소기업 고객에게 대출상품을 제공하고 이자를 수취한다"
    management = "대출채권의 회수대책을 보고한다"
    text = ", ".join([management, service] if reverse else [service, management]) + "."
    scoped = business_slot_scope(text, CUSTOMER)
    assert service in scoped.score_text
    assert management not in scoped.score_text
    assert not business_slot_scope_problem(text, CUSTOMER)
    start = text.index(management)
    assert business_slot_quote_problem(text, CUSTOMER, start, start + len(management))
