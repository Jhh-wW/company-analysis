"""산업·업계 질의는 탐색 기회이며 사업·문제·지역 승인 근거가 아니다."""
from dataclasses import asdict, replace

import pytest

from src.features.news_intake.search_snapshot import search_plan
from src.features.news_intake import industry_constants as ic
from src.features.news_intake.industry_context import extend_prompt
from src.features.news_intake.tests.test_industry_context import (
    ANCHOR, AS_OF, BODY, CANDIDATE, COMPANY, response, split,
)
from src.shared.report_generation.models import exact_text_sha256


def current_anchor(item, anchor_id):
    text = f"예제법인은 {item} 사업을 운영한다."
    return replace(ANCHOR, anchor_id=anchor_id, business_item=item,
                   exact_text=text, text_sha256=exact_text_sha256(text))


def test_두사업에_국내세계_탐색을_배분하고_회사몫과원앵커를_보존한다():
    first = current_anchor("정밀부품제조", "manufacturing")
    second = current_anchor("설비공사", "construction")
    company = replace(COMPANY, business_anchors=(first, second))
    before = asdict(company)
    ordinary = search_plan(replace(company, business_anchors=()), AS_OF)
    active = search_plan(company, AS_OF)
    assert len(active) == len(ordinary)
    assert active[:2] == ordinary[:2]
    assert [(q, mode, topic) for q, mode, topic, _ in active[2:6]] == [
        ("정밀부품 제조 산업 과제", "sim", "industry_domestic:manufacturing"),
        ("정밀부품 제조 세계 산업 동향", "sim", "industry_global:manufacturing"),
        ("설비 공사 국내 업계 문제", "sim", "industry_domestic:construction"),
        ("설비 공사 글로벌 업계 위기", "sim", "industry_global:construction"),
    ]
    assert len({row[0] for row in active[2:6]}) == 4
    assert asdict(company) == before


@pytest.mark.parametrize("business,body,industry,problem", [
    ("타이어 제조", "한국의 상용차 운송 업계는 공영 차고지 부족으로 불법 주차 문제를 겪고 있다.",
     "상용차 운송", "차고지 부족"),
    ("설비공사", "한국의 산업설비 제품 시장은 부품 공급 부족으로 납품이 지연되는 문제를 겪고 있다.",
     "산업설비 제품 시장", "공급 부족"),
])
def test_산업질의의_주제는_타사업이라는_실제판정을_덮어쓰지않는다(business, body, industry, problem):
    anchor = current_anchor(business, ANCHOR.anchor_id)
    company = replace(COMPANY, business_anchors=(anchor,))
    topic = search_plan(company, AS_OF)[2][2]
    candidate = replace(CANDIDATE, topics=(topic,))
    # 타사업은 통제된 의미 판정이다. 이 시험이 임의 본문의 사업 의미를 입증하지는 않는다.
    raw = response(text=body, industry=industry, problem=problem,
                   applicability_quote=industry, same_business=False)
    _, problems, rejected = split(raw, body=body, company=company, candidate=candidate)
    assert not problems and rejected == {"industry_unbound_problem": 1}


@pytest.mark.parametrize("query_index,body,changes", [
    (2, "산업설비 제조 산업은 핵심 부품의 공급 지연으로 생산 일정이 늦어지는 문제를 겪고 있다.", {}),
    (3, BODY, {"geography": "global", "geography_detail": "세계", "geography_evidence": "세계"}),
    (2, "한국의 산업설비 제조 산업은 향후 공급 지연 문제를 겪을 수 있다.", {}),
])
def test_지역무지정_세계검색과위기어는_없는지역과미래문제를_승인하지않는다(query_index, body, changes):
    query = search_plan(COMPANY, AS_OF)[query_index]
    candidate = replace(CANDIDATE, topics=(query[2],))
    _, problems, rejected = split(response(text=body, **changes), body=body, candidate=candidate)
    assert not problems and rejected == {"industry_unbound_problem": 1}


def test_새질의의_정상현재산업근거도_회사직접사실로_이동하지않는다():
    query = search_plan(COMPANY, AS_OF)[2]
    candidate = replace(CANDIDATE, topics=(query[2],))
    direct, problems, rejected = split(candidate=candidate)
    assert not rejected and len(problems) == 1
    assert problems[0].exact_text == BODY
    assert problems[0].text_sha256 == exact_text_sha256(BODY)
    assert problems[0].business_anchor_id == ANCHOR.anchor_id
    assert direct["items"][0]["same_company"] is False


def test_산업안내는_사용사업과제조공급업_차이와명시영향의범위를_요구한다():
    prompt = extend_prompt("원본문 입력", COMPANY)
    assert prompt.count(ic.INDUSTRY_BUSINESS_ACTIVITY_GUIDE) == 1
    assert "same_business" in prompt
    assert "제조·공급업과 이를 사용하는 운송·시공·운영업" in prompt
    assert "공식 앵커와 같은 사업 활동에 미치는 영향이 기사에 명시되면 그 범위" in prompt
    assert extend_prompt("원본문 입력", replace(COMPANY, business_anchors=())) == "원본문 입력"
