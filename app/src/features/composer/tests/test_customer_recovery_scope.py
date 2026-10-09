"""모델의 참 판정 뒤에도 고객유형과 일반 회수관리의 질문을 구분한다."""
import json

import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding


CUSTOMER = "business_model:customer_type"
POLICY = "회사는 회수가 지연되는 금융자산에 대하여 회수지연 현황 및 회수대책이 보고되고 있으며 지연사유에 따라 적절한 조치를 취하고 있다."


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_model_true_cannot_use_management_as_customer_type(wrapped, mixed):
    source = POLICY + (" 회사는 고객사에 콘텐츠를 제공한다." if mixed else "")
    row = {"번호": 1, "결과": "참", "근거": ["98"]}
    raw = json.dumps({"검수결과": {"business_model": [row]}} if wrapped else {"판정": [row]}, ensure_ascii=False)
    problems = {}
    sources = {"98": source}
    result = _apply_grounding(raw, {1: "참"}, {1: (POLICY, sources)},
        diagnostic_contexts={1: ("business_model", "본문", "확인")},
        claim_slots_by_number={1: CUSTOMER}, grounding_problems=problems)
    assert result[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    assert sources == {"98": source}


@pytest.mark.parametrize("claim", [
    "회사는 구독 고객에게 콘텐츠를 제공한다.",
    "회사는 중소기업에 운영자금대출을 제공하고 이자를 수취한다.",
    "회사는 광고주에게 광고 서비스를 제공한다.",
    "회사는 정부부처와 대학을 대상으로 교육 서비스를 제공한다.",
    "회사는 고객사에게 상품을 판매하며 외상판매대금을 회수한다.",
    "회사는 기업 고객에게 금융자산 회수지연 관리 서비스를 제공한다.",
    "당사는 고객사의 신용을 평가해 등급을 제공하고 평가수수료를 받는다.",
])
def test_customer_relation_and_financial_core_services_are_preserved(claim):
    assert business_relation_scope_problem(claim, {"1": POLICY + " " + claim},
        section_id="business_model", claim_slot=CUSTOMER) == ""


@pytest.mark.parametrize("slot", ["business_model:revenue_model", "operations_partners:operating_role", ""])
def test_other_slot_contracts_are_not_extended(slot):
    section = "operations_partners" if slot.startswith("operations_partners") else "business_model"
    assert business_relation_scope_problem(POLICY, {"98": POLICY}, section_id=section, claim_slot=slot) == ""
