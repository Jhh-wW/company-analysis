"""신용관리·보험·조사가 고객·운영 역할을 거짓 준비시키지 않는다."""
import hashlib

import pytest

from features.evidence_collection.business_slot_scope import business_slot_scope, business_slot_scope_problem
from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.tests.test_streaming_collection import _collect

CUSTOMER = "business_model:customer_type"
ROLE = "operations_partners:operating_role"
POLICIES = (
    (CUSTOMER, "회사는 거래처의 신용등급을 결정할 목적으로 재무정보와 거래실적을 사용하고 있습니다."),
    (ROLE, "회사는 제조물의 결함으로 인한 생명, 신체 또는 재산의 피해에 대비해 보험계약을 체결한다."),
    (CUSTOMER, "회사는 신용도가 일정 수준 이상인 거래처와 거래하고 담보를 수취하는 정책을 채택한다."),
    (CUSTOMER, "신용위험 관리\n매출채권의 거래처 신용등급을 검토하고 신용보증보험으로 손실을 관리한다."),
    (CUSTOMER, "회사는 매출채권에 신용평가를 수행하며, 거래처의 신용등급을 지속적으로 검토한다."),
    (ROLE, "제조물책임법에 따라 제조업자에게 손해배상책임을 부과하므로 회사는 보험계약을 체결한다."),
    (ROLE, "회사는 공정거래위원회로부터 금형 제조위탁 거래 조사를 받았으며 조사결과에 이의를 제기했다."),
    (ROLE, "제조물책임보험\n회사는 제조물책임보험계약을 체결한다."),
)


@pytest.mark.parametrize("slot,text", POLICIES)
def test_policy_or_investigation_cannot_fill_business_slot_or_become_no_signal(slot, text):
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert slot not in {score.slot_id for score in scores}
    assert business_slot_scope_problem(text, slot) == "business_slot_scope_unsupported"
    assert business_slot_scope_problem(text, "current_challenges:issue") == ""


@pytest.mark.parametrize("slot,text", [
    (CUSTOMER, "회사의 주요 고객사는 산업장비 업체와 의료기관이다."),
    (CUSTOMER, "회사는 금융 거래처에게 신용위험 관리 서비스를 제공한다."),
    (CUSTOMER, "회사는 기업 고객사에 대출 서비스를 제공하며 신용등급을 평가한다."),
    (ROLE, "회사는 산업장비를 제조하고 고객사에 공급한다."),
    (ROLE, "회사는 제조물책임보험 상품을 고객사에 제공한다."),
    (ROLE, "회사는 공정위 조사를 받았지만 제품을 생산하고 고객사에 납품한다."),
    (ROLE, "회사는 자동차 부품 제조를 외주 업체에 위탁하고 공급망을 운영한다."),
    (CUSTOMER, "회사는 신용등급을 평가하며, 은행의 고객은 기업과 개인 차주이다."),
    (CUSTOMER, "은행의 고객은 기업과 개인 차주이며 신용위험 관리정책을 운영한다."),
    (ROLE, "보험사는 제조물책임보험을 설계하고 판매한다."),
    (ROLE, "보험사는 제조물책임보험 계약을 인수한다."),
])
def test_actual_customers_financial_services_and_operating_role_are_preserved(slot, text):
    assert not business_slot_scope_problem(text, slot)
    assert business_slot_scope(text, slot).score_text.strip()


@pytest.mark.parametrize("policy_slot,policy", POLICIES[:2])
@pytest.mark.parametrize("separator", ["\n", " ", ", ", "; "])
def test_mixed_paragraph_masks_only_policy_clause_and_preserves_exact_source(policy_slot, policy, separator):
    fact = ("회사의 주요 고객사는 의료기관이다." if policy_slot == CUSTOMER
            else "회사는 산업장비를 제조하고 생산한다.")
    text = policy + separator + fact
    scoped = business_slot_scope(text, policy_slot)
    assert fact.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, policy_slot)
    # 제한은 점수 입력뿐이다. 수집 원문·좌표·지문은 변경되지 않는다.
    original = policy + " " + fact
    harvest = _collect(original)
    assert any(fact in fragment.text for fragment in harvest.fragments)
    for document in (*harvest.documents, *harvest.unclassified_documents):
        assert document.content_sha256 == hashlib.sha256(original.encode()).hexdigest()
    for fragment in (*harvest.fragments, *harvest.unclassified_fragments):
        start, end = map(int, fragment.location.split("-"))
        assert original[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()


def test_pure_excluded_policy_is_not_retained_for_ai_reclassification():
    text = POLICIES[0][1]
    harvest = _collect(text)
    assert not harvest.fragments
    assert not harvest.unclassified_fragments
