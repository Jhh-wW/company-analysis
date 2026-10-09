"""상각 정책은 운영 근거가 아니며 실제 사업 절과 원좌표는 보존한다."""
from hashlib import sha256

import pytest

from src.shared.report_evidence.business_slot_scope import (
    business_slot_scope, business_slot_scope_problem, business_slot_quote_problem,
)
from src.shared.report_evidence.overhead_allocation_scope import (
    amortization_accounting_policy, overhead_allocation_scope,
)

ROLE = "operations_partners:operating_role"
POLICY = "당사는 무형자산으로 계상된 개발비를 관련 제품 등의 판매 또는 사용이 가능한 시점부터 5년 동안 정액법으로 상각하고 있으며, 그 상각액을 제조원가로 계상하고 있습니다."


@pytest.mark.parametrize("source", (
    POLICY,
    "무형자산은 내용연수에 따라 정액법으로 상각한다.",
    "유형자산은 정률법으로 상각합니다.",
    "개발비는 5년 동안 정액법으로 상각하며, 그 상각액을 제조원가로 계상하는 방식으로 운영된다.",
))
def test_순수상각정책은_운영슬롯으로지원하지않는다(source):
    digest = sha256(source.encode()).hexdigest()
    assert amortization_accounting_policy(source)
    assert business_slot_scope_problem(source, ROLE)
    assert business_slot_quote_problem(source, ROLE, 0, len(source))
    assert business_slot_scope(source, "past_changes:performance").score_text == source
    assert sha256(source.encode()).hexdigest() == digest


@pytest.mark.parametrize("actual", (
    "회사는 공장에서 제품을 제조하고 있다.",
    "회사는 신제품을 개발하고 있다.",
    "회사는 고객에게 회계 자문 서비스를 제공하고 있다.",
    "개발비가 증가하여 신규 개발을 중단했다.",
    "올해 감가상각비와 생산원가가 증가했다.",
))
@pytest.mark.parametrize("separator", (" ", ", "))
def test_실제사업과사건은_정책앞뒤에서_사라지지않는다(actual, separator):
    for source in (POLICY + separator + actual, actual + separator + POLICY):
        scope = overhead_allocation_scope(source)
        assert actual in scope.score_text
        start = source.index(actual)
        assert not business_slot_quote_problem(source, ROLE, start, start + len(actual))
        quote = "그 상각액을 제조원가로 계상하고 있습니다"
        start = source.index(quote)
        assert business_slot_quote_problem(source, ROLE, start, start + len(quote))
        assert not amortization_accounting_policy(source)
        for begin, end in scope.excluded_spans:
            assert source[begin:end].strip()
            assert actual not in source[begin:end]


@pytest.mark.parametrize("source", (
    "회사는 고객의 개발비를 5년 동안 정액법으로 상각하며 회계 자문 서비스를 제공하고 있다.",
    "회사는 고객 보유 무형자산을 내용연수에 따라 상각하는 서비스를 제공하고 있다.",
    "회사는 개발비를 투자하여 제품을 제조하고 있으며 개발비는 5년 정액법으로 상각한다.",
    "회사는 제품을 제조하고 있으며 그 상각액을 제조원가로 계상하고 있습니다.",
))
def test_고객회계서비스와같은절의실제개발생산은보존한다(source):
    scope = overhead_allocation_scope(source)
    assert not amortization_accounting_policy(source)
    assert "제공하고" in scope.score_text or "제조하고" in scope.score_text
    assert not business_slot_scope_problem(source, ROLE)


def test_상각정책의좌표는_선행공백과전각문자를원문에연결한다():
    source = "앞 문장.  개발비는 ５년 정액법으로 상각한다. 회사는 제품을 생산하고 있다."
    scope = overhead_allocation_scope(source)
    begin, end = scope.excluded_spans[0]
    assert source[begin:end] == "개발비는 ５년 정액법으로 상각한다"
    assert "회사는 제품을 생산하고 있다." in scope.score_text


@pytest.mark.parametrize("source", (
    "개발비는 올해 증가했으며 5년 정액법으로 상각한다.",
    "개발비는 20백만원이며 5년 정액법으로 상각한다.",
    "회사는 고객을 위해 개발비를 5년 정액법으로 상각하며 회계 서비스를 제공한다.",
    "회사는 고객의 상각액을 제조원가로 계상한다.",
))
def test_정책술어앞의실제증감금액과고객소유를삼키지않는다(source):
    assert overhead_allocation_scope(source).score_text == source
    assert not amortization_accounting_policy(source)
