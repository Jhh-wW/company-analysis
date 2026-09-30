"""지원쌍 사이의 구체 사업 관계 차용과 정상 관계 요약을 대조한다."""
import json
import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding


@pytest.mark.parametrize("claim,source", [
    ("부품은 OEM 방식으로 공급된다.", "부품은 주문자 상표 부착 생산 방식으로 완성차 업체에 공급된다."),
    ("부품은 주문자 상표 생산 방식으로 공급된다.", "부품은 OEM 방식으로 공급되고 있다."),
    ("원형사업은 주요 고객사를 상대로 한다.", "원형사업은 가온전자와 누리자동차 등 주요 고객사에게 제품을 판매한다."),
    ("시제품은 위탁 제작 서비스로 제공한다.", "시제품은 외주 제작을 통해 고객에게 서비스를 제공한다."),
    ("회사는 소프트웨어 라이선스를 판매한다.", "회사는 소프트웨어 사용권을 판매하고 매출을 얻는다."),
    ("플랫폼은 구독 서비스로 제공한다.", "플랫폼은 월정액 서비스를 고객에게 제공한다."),
    ("금융회사는 개인 차주를 고객으로 한다.", "금융회사는 개인 대출자 고객에게 금융 서비스를 제공한다."),
    ("보험회사는 보험 가입자를 고객으로 한다.", "보험회사는 보험 계약자를 고객으로 하여 보험을 판매한다."),
    ("회계회사는 평가 서비스를 제공한다.", "회계회사는 고객에게 평가 서비스를 제공하고 수수료 매출을 얻는다."),
    ("산업장비는 제조하고 판매한다.", "산업장비는 제조하여 고객에게 판매한다."),
    ("자동차 부품은 개발, 생산, 판매한다.", "자동차 부품은 개발하고 생산하며 판매한다."),
    ("회사는 타이어 판매대금을 현금과 어음으로 회수한다.", "국내 판매대금은 현금, 어음으로 회수한다."),
    ("프로토타입 부문은 CNC 정밀가공기술과 QDM 서비스를 주력으로 한다.", "프로토타입 부문은 CNC 정밀가공기술과 QDM 서비스를 제공하는 사업을 주력으로 한다."),
    ("회사는 부품을 생산할 계획이다.", "회사는 부품을 생산할 계획이다."),
    ("회사는 부품을 제조하지 않는다.", "회사는 부품을 제조하지 않는다."),
])
def test_정상_관계와_동의어를_보존한다(claim, source):
    assert business_relation_scope_problem(claim, {"a": source}, section_id="business_model") == ""


@pytest.mark.parametrize("claim,source", [
    ("자동차 부품은 OEM 방식으로 공급된다.", "회사는 전 세계 브랜드에 타이어를 공급하며 생산기지를 확장했다."),
    ("열관리 부문은 완성차업체 및 자동차 메이커를 고객으로 한다.", "열관리 부문은 고객사 물량 증가로 매출이 늘었으며 전기차 솔루션을 개발할 계획이다."),
    ("시제품은 위탁 제작 서비스를 제공한다.", "시제품은 고객에게 제품을 판매하는 영업조직을 갖추고 있다."),
    ("플랫폼은 구독 서비스를 제공한다.", "플랫폼은 소프트웨어 라이선스를 판매한다."),
    ("회사는 OEM 방식으로 장비를 제조한다.", "회사는 OEM 방식으로 장비를 제조하지 않는다."),
    ("회사는 OEM 방식으로 부품을 생산한다.", "회사는 OEM 방식으로 부품을 생산할 계획이다."),
    ("밸브는 최종 소비자를 대상으로 판매한다.", "펌프는 최종 소비자를 대상으로 판매하며 밸브는 기업 고객에게 판매한다."),
    ("밸브는 OEM 방식으로 공급된다.", "펌프는 OEM 방식으로 공급되며 밸브는 일반 대리점에 공급된다."),
    ("회사는 자동차 고객에게 OEM 방식으로 부품을 공급한다.", "회사는 자동차 고객에게 부품을 공급한다. 경쟁기업은 OEM 방식으로 같은 부품을 제조한다."),
    ("회사는 개인 고객에게 서비스를 제공한다.", "회사는 기업 고객에게 서비스를 제공한다."),
    ("밸브는 OEM 방식으로 공급한다.", "제품 | 판매방식\n펌프 | OEM 공급\n밸브 | 대리점 공급"),
    ("회사는 기업 고객에게 구독 서비스를 제공한다.", "회사 | 기업 고객 | 산업장비 판매 ; 다른 법인 | 개인 고객 | 구독 서비스"),
])
def test_자기인용밖_관계와_다른_사업_고객을_빌리지_못한다(claim, source):
    assert business_relation_scope_problem(claim, {"a": source}, section_id="operations_partners") == "scope_condition_unbound"


def test_다른장과_빈근거의_기존계약은_유지한다():
    claim = "부품은 OEM 방식으로 공급된다."
    assert business_relation_scope_problem(claim, {"a": "별도 자료"}, section_id="portfolio") == ""
    assert business_relation_scope_problem(claim, {}, section_id="operations_partners") == ""


def test_같은_문장의_다른행동_계획으로_현재관계를_지우지_않는다():
    source = "회사는 현재 OEM 방식으로 제품을 공급하며 향후 신제품을 개발할 계획이다."
    assert business_relation_scope_problem("회사는 OEM 방식으로 제품을 공급한다.", {"a": source}, section_id="operations_partners") == ""
    source = "제품 | 판매방식\n밸브 | 주문자 상표 생산(OEM) 공급\n펌프 | 대리점 판매"
    assert business_relation_scope_problem("밸브는 OEM 방식으로 공급한다.", {"a": source}, section_id="operations_partners") == ""


def test_일반_동사의_동의어는_기존_의미검수에_맡긴다():
    assert business_relation_scope_problem("회사는 구체 서비스를 제공한다.", {"a": "회사는 고객의 문제를 해결하는 용역 사업을 한다."}, section_id="business_model") == ""


def test_기존_검수_반환과_재구성_경로에서도_같은_본문을_차단한다():
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["a"]}]}, ensure_ascii=False)
    verdicts = {1: "참"}
    candidates = {1: ("부품은 OEM 방식으로 공급된다.", {"a": "회사는 타이어를 공급한다."})}
    problems = {}
    result = _apply_grounding(raw, verdicts, candidates,
        diagnostic_contexts={1: ("operations_partners", "본문", "확인")}, grounding_problems=problems)
    assert result[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    # 같은 후보가 고쳐쓰기에서 돌아와도 같은 입구를 통과하지 못한다.
    assert _apply_grounding(raw, verdicts, candidates,
        diagnostic_contexts={1: ("operations_partners", "본문", "확인")}) == result
    assert _apply_grounding(raw, verdicts, candidates,
        diagnostic_contexts={1: ("portfolio", "본문", "확인")})[1] == "참"
