"""공동 주어가 거래별 결제 조건을 넓히는 경우와 정상 합집합을 구분한다."""
import json

import pytest

from src.features.composer.payment_method_scope import payment_method_scope_problem
from src.features.composer.verify import _apply_grounding

SOURCE = (
    "1) 보관거래 : 현재 D/A, D/P, O/A거래 등으로 진행 "
    "2) 재청구(RBC)거래 : 보관 거래와 마찬가지로 모두 D/A, D/P거래하고 있음"
)


@pytest.mark.parametrize("claim", [
    "보관거래 및 재청구(RBC)거래는 D/A·D/P·O/A거래 등으로 진행된다.",
    "재청구(RBC)거래 및 보관거래는 D/A, D/P, O/A로 진행된다.",
    "재청구(RBC)거래는 O/A 방식으로 진행된다.",
])
def test_closed_methods_cannot_be_borrowed_from_another_trade(claim):
    sources = {"a": SOURCE}
    before = sources.copy()
    assert payment_method_scope_problem(claim, sources, section_id="business_model") == "scope_condition_unbound"
    assert sources == before


@pytest.mark.parametrize("claim", [
    "보관거래는 D/A·D/P·O/A로 진행된다.",
    "재청구(RBC)거래는 D/A·D/P로 진행된다.",
    "보관거래 및 재청구(RBC)거래는 D/A·D/P로 진행된다.",
    "보관거래와 재청구(RBC)거래를 포함한 전체 해외거래에는 D/A·D/P·O/A가 사용된다.",
    "재청구(RBC)거래는 D/A·D/P를 사용하며 O/A는 제외된다.",
    "재청구(RBC)거래는 D/A·D/P를 사용한다. 보관거래는 O/A를 사용한다.",
    "다른거래는 O/A를 사용한다.",
    "재청구(RBC)거래는 O/A를 사용하지 않는다.",
])
def test_supported_methods_union_and_nonassertion_are_preserved(claim):
    assert payment_method_scope_problem(claim, {"a": SOURCE}, section_id="business_model") == ""


def test_open_list_does_not_establish_closed_exclusion():
    source = "재청구(RBC)거래 : D/A, D/P 등을 이용한다."
    assert payment_method_scope_problem("재청구(RBC)거래는 O/A를 이용한다.", {"a": source}, section_id="business_model") == ""


@pytest.mark.parametrize("section", ["business_model", "operations_partners"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_model_true_cannot_publish_closed_method_expansion(section, wrapped):
    claim = "보관거래 및 재청구(RBC)거래는 D/A·D/P·O/A거래 등으로 진행된다."
    row = {"번호": 1, "결과": "참", "근거": ["a"]}
    raw = json.dumps({"검수결과": {section: [row]}} if wrapped else {"판정": [row]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: "참"}, {1: (claim, {"a": SOURCE})},
        diagnostic_contexts={1: (section, "본문", "확인")}, grounding_problems=problems)
    assert result[1] != "참"
    assert problems[1] == "scope_condition_unbound"


def test_different_sections_are_outside_this_contract():
    assert payment_method_scope_problem("재청구(RBC)거래는 O/A를 이용한다.", {"a": SOURCE}, section_id="identity") == ""
