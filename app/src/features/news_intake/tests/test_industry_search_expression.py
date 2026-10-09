"""검색 표현·정보성 제목 순위는 원문 및 산업 승인 계약과 분리한다."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from src.features.news_intake import industry_constants as ic
from src.features.news_intake.search_snapshot import (
    _industry_candidates, body_ranked_candidates, collect_search_snapshot,
    industry_search_expression, search_plan,
)
from src.features.news_intake.tests.test_industry_context import (
    ANCHOR, AS_OF, BODY, CANDIDATE, COMPANY, response, split,
)
from src.features.news_intake.tests.test_industry_minimum_opportunity import candidates
from src.shared.report_generation.models import exact_text_sha256


@pytest.mark.parametrize("original,expected", [
    ("정밀부품제조", "정밀부품 제조"), ("설비공사", "설비 공사"),
    ("운송서비스", "운송 서비스"), ("콘텐츠판매", "콘텐츠 판매"),
    ("장비유통", "장비 유통"), ("설비 공사", "설비 공사"),
    ("공사", "공사"), ("시공", "시공"), ("전자상거래", "전자상거래"),
    ("공공 서비스", "공공 서비스"), ("A서비스", "A서비스"),
    ("광고·콘텐츠판매", "광고·콘텐츠판매"), ("제조/서비스", "제조/서비스"),
])
def test_활동경계만_분리하고_위험하거나_알수없는_표현은_유지한다(original, expected):
    expression = industry_search_expression(original)
    assert expression == expected
    assert expression.replace(" ", "") == original.replace(" ", "")


def test_실제전송질의와_원앵커의_위치해시는_별도로_관측한다():
    text = "예제법인은 정밀부품제조 사업을 운영한다."
    anchor = replace(ANCHOR, business_item="정밀부품제조", exact_text=text,
                     text_sha256=exact_text_sha256(text), location=f"chars:0-{len(text)}")
    company = replace(COMPANY, business_anchors=(anchor,))
    before = asdict(company)
    queries = []
    observed = []

    def search(query, **options):
        queries.append((query, options))
        return SimpleNamespace(state="success", reason_code="news_search_ok", items=(),
                               transport_attempts=1, retry_recovered=False,
                               attempt_reason_codes=("news_search_ok",))

    snapshot = collect_search_snapshot(search_news=search, company=company, as_of=AS_OF,
                                      observer=lambda event, value: observed.append((event, value)))
    ordinary = search_plan(replace(company, business_anchors=()), AS_OF)
    assert len(snapshot.query_attempts) == len(ordinary)
    assert len(queries) == len(ordinary)
    rows = [row for row in observed[0][1]["queries"] if row["query_derivation"] is not None]
    assert [row["attempt"]["query"] for row in rows] == [
        "정밀부품 제조 산업 과제", "정밀부품 제조 세계 산업 동향",
        "정밀부품 제조 국내 업계 문제", "정밀부품 제조 글로벌 업계 위기",
    ]
    for row in rows:
        derivation = row["query_derivation"]
        assert derivation["original_business_item"] == anchor.business_item
        assert derivation["search_expression"] == "정밀부품 제조"
        assert derivation["anchor_location"] == anchor.location
        assert derivation["anchor_text_sha256"] == anchor.text_sha256
        assert derivation["query"] == row["attempt"]["query"]
    assert asdict(company) == before
    assert observed[0][1]["business_anchors"] == [asdict(anchor)]
    assert {row["attempt"]["topic"].split(":", 1)[0] for row in rows} == {
        "industry_domestic", "industry_global",
    }


@pytest.mark.parametrize("title", ["[Who Is ?] 예제 대표이사", "관련주 목록", "장비 관련주 총정리"])
def test_사업명과요약문제어가있는_프로필목록도_사건제목을_앞서지_않는다(title):
    profile = replace(CANDIDATE, id="profile", title=title,
                      description=f"{ANCHOR.business_item} 사업 소개. 다른 업종 공급 부족과 규제", published_on="2026-09-29",
                      topics=("industry_domestic:" + ANCHOR.anchor_id,))
    event = replace(profile, id="event", title="현장 생산 차질", description="조사 결과",
                    published_on="2026-08-01")
    before = [profile, event]
    assert _industry_candidates(before, company=COMPANY) == [event, profile]
    assert before == [profile, event]


def test_프로필형식이라도_제목자체의_문제사건은_기존사업연결순위를_유지한다():
    event = replace(CANDIDATE, id="event", title=f"[Who Is ?] {ANCHOR.business_item} 공급 차질",
                    topics=("industry_domestic:" + ANCHOR.anchor_id,))
    other = replace(event, id="other", title="다른 산업의 규제 문제")
    assert _industry_candidates([other, event], company=COMPANY) == [event, other]


def test_프로필이라도_요약의같은사업문제는_강한순위를_보존한다():
    profile = replace(CANDIDATE, id="profile", title="[Who Is ?] 대표이사",
                      description=f"{ANCHOR.business_item} 생산 차질",
                      topics=("industry_domestic:" + ANCHOR.anchor_id,))
    other = replace(profile, id="other", title="다른 업종의 규제 문제", description="")
    assert _industry_candidates([other, profile], company=COMPANY) == [profile, other]


@pytest.mark.parametrize("budget", [0, 1, 5, 6, 12, 22])
def test_순위보완은_총예산과회사후보몫과양지역을_유지한다(budget):
    ordinary = [item for item in candidates() if item.metadata_name_match]
    issues = [replace(CANDIDATE, id=region, title="현장 생산 차질", metadata_name_match=False,
                      topics=(f"industry_{region}:" + ANCHOR.anchor_id,))
              for _, region in ic.INDUSTRY_QUERY_REGIONS]
    original = [*issues, *ordinary]
    ranked = body_ranked_candidates(original, attempt_budget=budget, probe_budget=3, company=COMPANY)
    assert len(ranked) == len(original) and set(ranked) == set(original)
    assert len({item.id for item in ranked[:budget]}) <= budget
    if budget == 22:
        assert {item.id for item in ranked[:2]} == {"domestic", "global"}
        assert sum(item.metadata_name_match for item in ranked[:budget]) == 20


@pytest.mark.parametrize("changes", [
    {"same_business": False}, {"problem_present": False},
    {"geography": "global"}, {"geography_supported": False},
])
def test_파생질의와순위는_타사업_문제없음_지역없음의_실제검수를_대신하지않는다(changes):
    assert not split(response(**changes), body=BODY)[1]


def test_공식앵커가없거나_미지앵커이면_순위보완으로_산업기회가생기지않는다():
    item = replace(CANDIDATE, title="생산 차질", topics=("industry_domestic:unknown",))
    assert _industry_candidates([item], company=COMPANY) == []
    assert not any(row[2].startswith(ic.INDUSTRY_TOPIC_PREFIX)
                   for row in search_plan(replace(COMPANY, business_anchors=()), AS_OF))
