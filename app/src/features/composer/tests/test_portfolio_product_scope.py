"""명시 자기 품목 표의 나열·별칭·부문 결속 회귀."""
import json

import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.portfolio_product_scope import portfolio_product_scope_problem
from src.features.composer.verify import _apply_grounding


def table(item, purpose="측정용", owner="계측 부문"):
    return f"[{owner}]\n사업부문 | 매출유형 | 품 목 | 구체적용도\n{owner} | 제품 | {item} | {purpose}"


@pytest.mark.parametrize("claim,sources", [
    ("계측 부문은 센서, 펌프, 정비 용역을 공급한다.", {"a": table("센서"), "b": table("펌프")}),
    ("계측 부문은 센서, 펌프를 공급한다.", {"a": table("센서"), "b": table("펌프", owner="지원 부문")}),
    ("계측 부문은 펌프를 공급한다.", {"a": table("센서", "펌프 산업에 사용")}),
    ("계측 부문은 센서, 펌프를 공급한다.", {"a": table("센서") + "\n펌프 공급 현황"}),
    ("계측 부문은 센서, 펌프를 공급한다.", {"a": table("센서") + "\n지원 부문은 펌프를 공급한다."}),
    ("계측 부문은 센서를 공급한다.", {"a": "다른 회사의 제품 현황입니다.\n" + table("센서")}),
    ("계측 부문은 센서를 공급한다.", {"a": "다른 회사의 부문별 매출입니다.\n" + table("센서")}),
    ("계측 부문은 센서를 공급한다.", {"a": "[계측 부문]\n사업부문 | 품목 | 용도\n지원 부문 | 센서 | 측정용"}),
    ("계측 부문은 센서를 공급한다.", {"a": table("센서정비 용역")}),
    ("지원 부문 외에 기타 부문으로 계측 및 금형제작 사업이 운영되고 있으며, 계측 부문은 센서, 펌프, 정비 용역을 공급하고, 금형제작 부문은 측정을 위한 생산설비인 ABC&DEF를 주문제작 방식으로 공급한다.",
     {"a": table("센서"), "b": table("ABC&DEF", owner="금형제작 부문")}),
])
def test_missing_items_and_other_rows_or_titles_are_rejected(claim, sources):
    assert portfolio_product_scope_problem(claim, sources) == "scope_condition_unbound"
    assert business_relation_scope_problem(claim, sources, section_id="portfolio") == "scope_condition_unbound"


@pytest.mark.parametrize("claim,sources", [
    ("계측 부문은 센서, 펌프, 정비 용역을 공급한다.", {"a": table("센서"), "b": table("펌프"), "c": table("정비 용역")}),
    ("계측 부문은 센서, 펌프를 공급한다.", {"a": table("센서") + "\n계측 부문은 펌프를 공급한다."}),
    ("계측 부문은 센서 및 펌프를 공급한다.", {"a": table("센서") + "\n계측 부문 | 제품 | 펌프 | 이송용"}),
    ("계측 부문은 생산 장비, 정비 용역을 공급한다.", {"a": table("제조장비(생산장비)", "생산용"), "b": table("설비보전 용역(정비 용역)")}),
    ("계측 부문은 측정관련 생산설비, ABC&DEF를 공급한다.", {"a": table("생산설비", "측정을 위한 생산설비"), "b": table("abc & def")}),
    ("계측 부문은 측정을 위한 생산설비인 센서, 펌프를 공급한다.", {"a": table("센서"), "b": table("펌프")}),
    ("계측 부문은 센서를 공급하고, 지원 부문은 펌프를 공급한다.", {"a": table("센서"), "b": table("펌프", owner="지원 부문")}),
    ("당사는 센서, 펌프를 공급한다.", {"a": table("센서"), "b": table("펌프", owner="지원 부문")}),
    ("계측 부문은 센서를 공급하지 않는다.", {"a": table("펌프")}),
    ("계측 부문은 센서, 펌프를 공급한다.", {"a": "계측 부문은 센서 및 펌프를 공급한다."}),
    ("계측 부문은 센서를 공급한다.", {"a": "다른 회사의 부문별 매출입니다.\n" + table("펌프") + "\n당사의 제품 현황입니다.\n" + table("센서")}),
    ("계측 부문은 센서, 정비 용역을 공급한다.", {"a": table("센서"),
     "b": "[계측 부문]\n사업부문 | 매출유형 | 품목 | 구체적용도\n계측 부문 | 용 역 | 정비 | 설비 유지보수"}),
])
def test_multiple_own_quotes_explicit_aliases_and_spelling_are_preserved(claim, sources):
    assert portfolio_product_scope_problem(claim, sources) == ""


def test_approved_prose_list_still_requires_each_own_item():
    claim = "계측 부문은 센서, 펌프, 정비 용역을 공급한다."
    sources = {"a": table("센서"), "b": table("펌프")}
    before = dict(sources)
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["a", "b"]}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: "참"}, {1: (claim, sources)},
        diagnostic_contexts={1: ("portfolio", "본문", claim)}, grounding_problems=problems)
    assert result[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    assert sources == before


def test_other_sections_keep_their_contract():
    assert business_relation_scope_problem("계측 부문은 센서, 펌프를 공급한다.",
        {"a": table("센서")}, section_id="business_model") == ""
