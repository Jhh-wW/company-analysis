"""모델의 참 판정도 계정각주를 사업 수익방식으로 승격하지 못한다."""
import json

import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding


REVENUE = "business_model:revenue_model"
ACCOUNT = (
    "기타 수익 항목에는 수입기술료, 수입수수료, 대여금에 대한 이자수익 등이 포함되며, "
    "배당금수익은 이 항목에 포함되지 않는다."
)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_참_판정된_계정정의는_혼합원문에서도_수익칸을_채우지_못한다(wrapped, mixed):
    source = ACCOUNT + (" 회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다." if mixed else "")
    row = {"번호": 1, "결과": "참", "근거": ["1"]}
    raw = json.dumps({"검수결과": {"business_model": [row]}} if wrapped else {"판정": [row]}, ensure_ascii=False)
    problems = {}
    sources = {"1": source}
    result = _apply_grounding(raw, {1: "참"}, {1: (ACCOUNT, sources)},
        diagnostic_contexts={1: ("business_model", "본문", "확인")},
        claim_slots_by_number={1: REVENUE}, grounding_problems=problems)
    assert result[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    assert sources == {"1": source}


@pytest.mark.parametrize("claim", [
    "회사는 수입수수료로 수익을 얻는다.",
    "회사의 수익원은 수입기술료와 수입수수료이다.",
])
@pytest.mark.parametrize("source", [
    ACCOUNT,
    "(*) 기타에는 수입기술료, 수입수수료, 대여금에 대한 이자수익 등이 포함되어 있습니다. 단, 배당금수익은 포함되어 있지 않습니다.",
])
def test_계정정의만_인용한_수익원_재서술도_사업관계를_만들지_못한다(claim, source):
    assert business_relation_scope_problem(claim, {"1": source},
        section_id="business_model", claim_slot=REVENUE) == "scope_condition_unbound"


def test_회계각주의_금액과_존대활용이_수익방식으로_승격되지_않는다():
    claim = "기타수익 항목에는 수입수수료 300억원과 수입기술료 100억원이 포함됩니다."
    assert business_relation_scope_problem(claim, {"1": claim},
        section_id="business_model", claim_slot=REVENUE) == "scope_condition_unbound"


@pytest.mark.parametrize("claim", [
    "금융수익은 기업대출 고객 이자와 대출취급 수수료로 구성된다.",
    "금융수익에는 고객에게 대출 서비스를 제공하고 받은 수수료가 포함된다.",
    "기타수익에는 보험상품 판매 수수료가 포함된다.",
    "금융수익에는 신탁상품 운용 수수료가 포함된다.",
    "회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다.",
    "주된 영업수익의 형태는 용역 매출, 콘텐츠 매출 등으로 구성됩니다.",
])
@pytest.mark.parametrize("mixed", [False, True])
def test_정상_상품수익과_계정정의_혼합원문의_실제거래절은_보존한다(claim, mixed):
    source = (ACCOUNT + " " if mixed else "") + claim
    assert not business_relation_scope_problem(claim, {"1": source},
        section_id="business_model", claim_slot=REVENUE)


@pytest.mark.parametrize("slot", ["business_model:regional_mix", "business_model:customer_type", ""])
def test_다른_의미칸의_원문을_수익칸_제약으로_삭제하지_않는다(slot):
    assert not business_relation_scope_problem(ACCOUNT, {"1": ACCOUNT},
        section_id="business_model", claim_slot=slot)
