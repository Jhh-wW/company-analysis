"""3장 외부 거래 역할의 자기 원문 결속과 정상 매출 설명을 대조한다."""
import json

import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding


@pytest.mark.parametrize("claim,source", [
    ("금형제작 부문은 외부 수주를 통해 독자적 매출을 얻는다.",
     "금형제작 부문은 생산 설비를 공급하며 금형 수주 증가로 매출이 늘었다."),
    ("금형제작 부문은 외부 고객에게 금형을 공급한다.",
     "금형제작 부문은 내부 생산시설에 금형을 공급한다."),
    ("금형제작 부문은 외부 고객에게 금형을 판매한다.",
     "장비 부문은 외부 고객에게 금형을 판매한다."),
    ("회사는 금형을 외부 고객에게 판매한다.",
     "회사는 펌프를 외부 고객에게 판매한다. 회사는 금형을 내부 생산시설에 공급한다."),
    ("금형제작 부문은 외부 수주로 매출을 얻는다.",
     "외부 수주 현황\n금형제작 부문은 금형 수주 증가로 매출이 늘었다."),
    ("금형제작 부문은 외부 고객에게 금형을 판매한다.",
     "금형제작 부문은 외부 고객에게 금형을 판매할 계획이다."),
    ("금형제작 부문은 외부 고객에게 금형을 판매한다.",
     "금형제작 부문은 외부 고객에게 금형을 판매하지 않는다."),
    ("금형제작 부문은 외부 수주로 매출을 얻는다.",
     "금형제작 부문은 외부 고객에게 금형을 공급한다."),
    ("당사는 시험 설비를 외부 고객에게 판매한다.",
     "당사는 시험 설비를 내부 공장에 공급하고 연구 장비를 외부 고객에게 판매한다."),
    ("당사는 시험 설비의 외부 수주로 매출을 얻는다.",
     "당사는 연구 장비의 외부 수주로 매출을 얻는다."),
    ("당사는 시험 설비를 외부 고객에게 판매한다.",
     "당사는 시험 설비를 내부 공장에 공급하고 다른 회사는 시험 설비를 외부 고객에게 판매한다."),
    ("당사는 시험 설비를 외부 고객에게 판매한다.",
     "당사는 연구 설비를 외부 고객에게 판매한다."),
    ("당사는 시험 설비의 외부 수주로 매출을 얻는다.",
     "당사는 연구 설비의 외부 수주로 매출을 얻는다."),
])
def test_외부범위_추가와_다른주체_제품_활동의_차용을_차단한다(claim, source):
    assert business_relation_scope_problem(claim, {"a": source}, section_id="portfolio") == "scope_condition_unbound"


@pytest.mark.parametrize("claim,source", [
    ("금형제작 부문은 외부 수주로 매출을 얻는다.",
     "금형제작 부문은 외부 수주로 매출을 얻는다."),
    ("금형제작 부문은 내부 생산시설을 지원하며 외부 수주를 통해 매출을 얻는다.",
     "금형제작 부문은 내부 생산시설을 지원하고 외부 수주로 매출을 얻는다."),
    ("회사는 금형을 외부 고객에게 판매한다.",
     "회사는 금형을 외부 고객에게 판매하며 매출을 얻는다."),
    ("당사는 시험 설비의 외부 수주로 매출을 얻는다.",
     "당사는 시험 설비의 외부 수주로 매출을 얻는다."),
    ("당사는 시험 설비를 외부 고객에게 판매한다.",
     "당사는 주요 시험 설비를 외부 고객에게 판매한다."),
    ("당사는 시험 설비를 내부 공장에 공급하고 연구 장비를 외부 고객에게 판매한다.",
     "당사는 시험 설비를 내부 공장에 공급하고 연구 장비를 외부 고객에게 판매한다."),
    ("금형제작 부문은 수주 증가로 매출이 늘었다.",
     "금형제작 부문은 금형 수주 증가로 매출이 늘었다."),
    ("금형제작 부문은 외부 매출 여부가 확인되지 않는다.",
     "금형제작 부문은 금형 수주 증가로 매출이 늘었다."),
    ("금형제작 부문은 외부 고객에게 금형을 판매할 계획이다.",
     "금형제작 부문은 금형 수주 증가로 매출이 늘었다."),
])
def test_자기원문의_외부거래_내외부혼합과_일반수주매출을_보존한다(claim, source):
    assert business_relation_scope_problem(claim, {"a": source}, section_id="portfolio") == ""


def test_모델의_해석승인후에도_외부범위는_자기원문에_묶는다():
    claim = "금형제작 부문은 내부 생산시설을 지원하며 외부 수주를 통해 독자적 매출을 얻는다."
    sources = {"a": "금형제작 부문은 생산 설비를 공급하며 금형 수주 증가로 매출이 늘었다."}
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["a"]}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: "참"}, {1: (claim, sources)},
        diagnostic_contexts={1: ("portfolio", "본문", "해석")}, grounding_problems=problems)
    assert result[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    assert sources == {"a": "금형제작 부문은 생산 설비를 공급하며 금형 수주 증가로 매출이 늘었다."}


def test_다른3장관계규칙은_켜지않고_외부범위의_빈근거는_통과시키지않는다():
    assert business_relation_scope_problem("금형은 OEM 방식으로 공급된다.", {"a": "별도 자료"}, section_id="portfolio") == ""
    assert business_relation_scope_problem("금형은 외부 고객에게 공급된다.", {}, section_id="portfolio") == "scope_condition_unbound"


@pytest.mark.parametrize("amount", ["7", "1,234", "0.25"])
def test_같은부문의_명시_외부매출열_양수는_본문근거로_보존한다(amount):
    claim = "계측 부문은 외부 고객 매출을 창출한다."
    source = f"사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | {amount} | 0\n지원 부문 | 0 | 9"
    sources = {"a": source}
    assert business_relation_scope_problem(claim, sources, section_id="portfolio") == ""
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["a"]}]}, ensure_ascii=False)
    problems = {}
    assert _apply_grounding(raw, {1: "참"}, {1: (claim, sources)},
        diagnostic_contexts={1: ("portfolio", "본문", "해석")}, grounding_problems=problems)[1] == "참"
    assert problems == {}
    assert sources == {"a": source}


@pytest.mark.parametrize("source", [
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 0 | 9\n지원 부문 | 7 | 0",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | -7 | 9",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | (7) | 9",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | - | 9",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 미확인 | 9",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7(검토) | 9",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 예정 | 9",
    "사업부문 | 내부매출 | 매출\n계측 부문 | 7 | 9",
    "사업부문 | 외부고객 매출 | 내부매출\n지원 부문 | 7 | 0",
    "사업부문 | 외부고객 매출 | 내부매출\n합계 | 7 | 0",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7 | 0\n계측 부문 | 0 | 9",
    "사업부문 | 외부고객 매출 | 외부고객 매출\n계측 부문 | 7 | 0",
    "외부고객 매출 계획\n사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7 | 0",
    "사업부문 | 외부고객 매출 | 상태\n계측 부문 | 7 | 검토 중",
    "외부 고객 매출\n사업부문 | 매출 | 내부매출\n계측 부문 | 7 | 0",
    "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7",
    "다른 회사의 부문별 매출입니다.\n사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7 | 0",
])
def test_외부매출표의_다른행_내부열_불확정값_중복을_차용하지않는다(source):
    assert business_relation_scope_problem("계측 부문은 외부 고객 매출을 창출한다.",
        {"a": source}, section_id="portfolio") == "scope_condition_unbound"


@pytest.mark.parametrize("claim", [
    "계측 부문은 외부 수주로 매출을 얻는다.",
    "계측 부문은 외부 고객에게 장비를 공급한다.",
    "계측 부문은 외부 고객에게 장비를 판매한다.",
    "계측 부문은 특정 고객사의 외부 매출을 창출한다.",
    "계측 부문은 외부 고객을 주요 고객으로 한다.",
    "지원 부문은 외부 고객 매출을 창출한다.",
    "당사는 외부 고객 매출을 창출한다.",
])
def test_외부매출표는_수주_공급_고객명과_다른부문_전사주장을_지원하지않는다(claim):
    source = "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7 | 0\n지원 부문 | 0 | 9"
    assert business_relation_scope_problem(claim, {"a": source}, section_id="portfolio") == "scope_condition_unbound"


def test_다른조각의_외부매출_헤더와_자기행을_합쳐_지원하지않는다():
    sources = {"a": "사업부문 | 외부고객 매출 | 내부매출", "b": "계측 부문 | 7 | 0"}
    assert business_relation_scope_problem("계측 부문은 외부 고객 매출을 창출한다.",
        sources, section_id="portfolio") == "scope_condition_unbound"


def test_다른회사표_뒤_명시_자기소유_표제로_외부매출표_지원을_복구한다():
    source = ("다른 회사의 부문별 매출입니다.\n"
              "사업부문 | 외부고객 매출 | 내부매출\n지원 부문 | 7 | 0\n"
              "당사의 부문별 매출입니다.\n"
              "사업부문 | 외부고객 매출 | 내부매출\n계측 부문 | 7 | 0")
    assert business_relation_scope_problem("계측 부문은 외부 고객 매출을 창출한다.",
        {"a": source}, section_id="portfolio") == ""


def test_부문명의_계획은_미래상태로_오인하지않는다():
    source = ("도시계획 부문 매출표\n"
              "사업부문 | 외부고객 매출 | 내부매출\n도시계획 부문 | 7 | 0")
    assert business_relation_scope_problem("도시계획 부문은 외부 고객 매출을 창출한다.",
        {"a": source}, section_id="portfolio") == ""
