"""중첩 표의 부문 승격과 회사 포지셔닝의 제품 역할 승격을 막는다."""
import json

import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding

SLOT = "portfolio:product_role"
SOURCE = "[장비 부문]\n사업부문 | 매출유형 | 품목 | 구체적용도\n기 타 | 공임 | 장비정비 | 유지보수"
POSITIONING = "우리는 하이테크 기술력으로 미래 산업에 대한 다양한 솔루션을 제시합니다. 변화하는 산업 속에서 시장을 이끌어가고 있습니다."


@pytest.mark.parametrize("claim", [
    "기타 사업부문에서는 장비정비 서비스가 운영되고 있다.",
    "기타 부문은 장비정비 용역을 제공한다.",
    "지원 부문에서는 장비정비가 서비스 매출로 계상된다.",
    "기타 부문에서는 장비정비 서비스가 운영되고 있으며 추가 주문은 없다.",
])
def test_부모표_하위행을_독립부문으로_승격하지_못한다(claim):
    assert business_relation_scope_problem(claim, {"a": SOURCE}, section_id="portfolio", claim_slot=SLOT) == "scope_condition_unbound"


@pytest.mark.parametrize("other_source", [
    "기타 부문에서는 계측 서비스가 운영되고 있다.",
    "기타 부문은 계측 서비스를 제공한다.",
    "[기타 부문]\n사업부문 | 품목 | 구체적용도\n기타 부문 | 계측 서비스 | 측정",
])
@pytest.mark.parametrize("claim", [
    "기타 부문에서는 장비정비 서비스가 운영되고 있다.",
    "기타 부문에서는 장비정비 서비스가 운영되고 있으며 추가 주문은 없다.",
])
def test_다른인용의_부문이_맞아도_제품을_빌리지_못한다(other_source, claim):
    assert business_relation_scope_problem(claim, {"a": SOURCE, "b": other_source},
        section_id="portfolio", claim_slot=SLOT) == "scope_condition_unbound"


@pytest.mark.parametrize("claim,sources", [
    ("장비 부문에서는 장비정비 서비스가 운영되고 있다.", {"a": SOURCE}),
    ("장비 부문의 기타 사업에서는 장비정비 서비스가 운영되고 있다.", {"a": SOURCE}),
    ("기타 부문에서는 장비정비 서비스가 운영되고 있다.", {"a": SOURCE.replace("[장비 부문]", "[기타 부문]")}),
    ("지원 부문에서는 장비정비 서비스가 운영되고 있다.", {"a": SOURCE, "b": "지원 부문에서는 장비정비 서비스가 운영되고 있다."}),
    ("당사는 장비정비를 제공한다.", {"a": SOURCE}),
    ("기타 사업부문에서는 장비정비 서비스가 운영되고 있다.", {"a": "기타 사업부문에서는 장비정비 서비스가 운영되고 있다."}),
    ("기타 부문에서는 장비정비 서비스를 운영하지 않는다.", {"a": SOURCE}),
])
def test_실제_부모하위제품과_독립부문_산문을_보존한다(claim, sources):
    assert business_relation_scope_problem(claim, sources, section_id="portfolio", claim_slot=SLOT) == ""


@pytest.mark.parametrize("claim,sources", [
    (POSITIONING, {"a": POSITIONING}),
    ("회사는 설비진단솔루션으로 고장을 감지한다.", {"a": POSITIONING}),
    (POSITIONING, {"a": POSITIONING + " 회사는 산업장비를 제조한다."}),
])
def test_슬로건_후보와_순수슬로건에서_만든_제품역할을_최종차단한다(claim, sources):
    before = dict(sources)
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": list(sources)}]}, ensure_ascii=False)
    problems = {}
    verdict = _apply_grounding(raw, {1: "참"}, {1: (claim, sources)},
        diagnostic_contexts={1: ("portfolio", "본문", claim)},
        claim_slots_by_number={1: SLOT}, grounding_problems=problems)
    assert verdict[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    assert sources == before


@pytest.mark.parametrize("text", [
    "회사는 하이테크 기술력으로 산업장비를 제조한다.",
    "설비진단솔루션은 진동을 측정하여 고장을 감지하며 시장을 선도한다.",
    "회사는 시장을 선도하는 고객 관리 서비스를 제공한다.",
    "시장 선도 기술력을 갖췄으며, 회사는 의료기기를 생산하고 있다.",
    "기술력으로 시장을 선도한다. 공정 관리를 위한 솔루션을 제공한다.",
])
def test_구체제품_솔루션과_혼합원문의_실제역할은_남긴다(text):
    assert business_relation_scope_problem(text, {"a": text}, section_id="portfolio", claim_slot=SLOT) == ""


def test_슬로건은_다른의미칸의_원문까지_삭제하지_않는다():
    assert business_relation_scope_problem(POSITIONING, {"a": POSITIONING}, section_id="identity", claim_slot="identity:business_definition") == ""
    assert business_relation_scope_problem(POSITIONING, {"a": POSITIONING}, section_id="portfolio", claim_slot="portfolio:lifecycle_stage") == ""
