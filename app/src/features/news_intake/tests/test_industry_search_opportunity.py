"""산업 문제 탐색의 중복·조사 순위를 고정 예산 안에서 검증한다."""

from dataclasses import replace
import pytest

from src.features.news_intake import industry_constants as ic
from src.features.news_intake.search_snapshot import (
    body_ranked_candidates, diverse_candidates, search_plan,
)
from src.features.news_intake.tests.test_industry_context import (
    ANCHOR, AS_OF, CANDIDATE, COMPANY,
)
from src.shared.report_generation.models import exact_text_sha256


def test_단일사업도_국내세계_문제질의_네개가_서로_다르다():
    active = search_plan(COMPANY, AS_OF)
    ordinary = search_plan(replace(COMPANY, business_anchors=()), AS_OF)
    queries = [row for row in active if row[2].startswith(ic.INDUSTRY_TOPIC_PREFIX)]
    assert len(active) == len(ordinary)
    assert active[:ic.INDUSTRY_COMPANY_QUERY_COUNT] == ordinary[:ic.INDUSTRY_COMPANY_QUERY_COUNT]
    assert len(queries) == ic.INDUSTRY_QUERY_COUNT
    assert len({row[0] for row in queries}) == len(queries)
    assert all(ANCHOR.business_item in row[0] for row in queries)
    assert {row[2] for row in queries} == {
        "industry_domestic:" + ANCHOR.anchor_id,
        "industry_global:" + ANCHOR.anchor_id,
    }


@pytest.mark.parametrize("anchor_count", [2, 3])
def test_복수사업은_한도내_우선사업마다_국내와_세계를_조사한다(anchor_count):
    text = "회사는 사업0, 사업1, 사업2를 운영한다."
    anchors = tuple(replace(ANCHOR, anchor_id=f"anchor-{index}",
                            business_item=f"사업{index}", exact_text=text,
                            text_sha256=exact_text_sha256(text), location=f"chars:0-{len(text)}")
                    for index in range(anchor_count))
    plan = search_plan(replace(COMPANY, business_anchors=anchors), AS_OF)
    queries = [row for row in plan if row[2].startswith(ic.INDUSTRY_TOPIC_PREFIX)]
    assert len(queries) == ic.INDUSTRY_QUERY_COUNT
    covered_anchors = anchors[:ic.INDUSTRY_QUERY_COUNT // len(ic.INDUSTRY_QUERY_REGIONS)]
    for anchor in covered_anchors:
        linked = [row for row in queries if row[2].split(":", 1)[1] == anchor.anchor_id]
        assert linked and all(anchor.business_item in row[0] for row in linked)
        assert {row[2].split(":", 1)[0] for row in linked} == {"industry_domestic", "industry_global"}
    assert {row[2].split(":", 1)[1] for row in queries} == {anchor.anchor_id for anchor in covered_anchors}


def _candidates():
    award = replace(CANDIDATE, id="award", title="산업설비 디자인상 수상",
                    description="기술력을 인정받았다", published_on="2026-09-29",
                    metadata_name_match=True)
    problem = replace(CANDIDATE, id="problem", title="산업설비 제조 부품 공급 지연",
                      description="생산 차질이 이어진다", published_on="2026-09-01",
                      source_url="https://media.example/problem", metadata_name_match=True,
                      topics=("industry_domestic:" + ANCHOR.anchor_id,))
    ordinary = [replace(award, id=f"ordinary-{index}", topics=("business",),
                        source_url=f"https://media.example/{index}") for index in range(4)]
    return [award, problem, *ordinary]


def test_최신수상보다_문제후보에_본문예약기회를_주되_다른후보도_보존한다():
    candidates = _candidates()
    ranked = diverse_candidates(candidates, len(candidates), company=COMPANY)
    assert ranked[0].id == "problem"
    assert {item.id for item in ranked} == {item.id for item in candidates}
    body = body_ranked_candidates(candidates, attempt_budget=6, probe_budget=1, company=COMPANY)
    assert body[0].id == "problem"
    assert len(body) == len(candidates)
    assert next(item for item in body if item.id == "award").topics == ("industry_domestic",)


def test_문제신호없는_산업후보도_기존순위로_조사하며_회사기사순위는_유지한다():
    candidates = [replace(item, title="산업설비 소식", description="새 소식")
                  for item in _candidates()]
    ranked = diverse_candidates(candidates, len(candidates), company=COMPANY)
    # 실제 앵커에 연결된 약한 산업 후보에도 기존 예약 몫을 준다.
    assert ranked[0].id == "problem"
    ordinary = [replace(item, topics=("business",)) for item in candidates]
    assert list(diverse_candidates(ordinary, len(ordinary))) == sorted(
        ordinary, key=lambda item: (item.published_on, item.source_url), reverse=True)


def test_일반문제낱말만으로_다른사업기사를_우선하지_않는다():
    candidates = _candidates()
    unrelated = replace(candidates[0], id="unrelated", title="주차장 공급 부족 위기",
                        topics=("industry_domestic:" + ANCHOR.anchor_id,))
    candidates[0] = unrelated
    ranked = diverse_candidates(candidates, len(candidates), company=COMPANY)
    assert ranked[0].id == "problem"
    assert "unrelated" in {item.id for item in ranked}
    wrong_anchor = replace(candidates[1], topics=("industry_domestic:unknown",))
    ranked = diverse_candidates([unrelated, wrong_anchor, *candidates[2:]], 6, company=COMPANY)
    assert ranked[0].id == "unrelated"


def test_질의에_문제조건을_여럿_AND로_쌓지_않는다():
    queries = [row[0] for row in search_plan(COMPANY, AS_OF)
               if row[2].startswith(ic.INDUSTRY_TOPIC_PREFIX)]
    # 특정 공급·수요 문제를 미리 단정하거나 조건을 여러 개 AND로 붙이지 않는다.
    assert all(query.endswith(ic.INDUSTRY_QUERY_THEMES) for query in queries)
    assert not any("공급 수요" in query or "차질 규제" in query for query in queries)


def _industry_candidate(identifier, title, description, region="domestic", date="2026-09-01"):
    # 기존 공식 앵커는 제품의 전체 사업명 '산업설비 제조'를 요구한다.
    title = title.replace("산업설비", ANCHOR.business_item)
    description = description.replace("산업설비", ANCHOR.business_item)
    return replace(CANDIDATE, id=identifier, title=title, description=description,
                   published_on=date, source_url=f"https://media.example/{identifier}",
                   metadata_name_match=False, topics=(f"industry_{region}:{ANCHOR.anchor_id}",))


def test_사업명이_제목에_있는_문제후보를_최신_일반경제_요약보다_먼저_조사한다():
    macro = _industry_candidate("macro", "생산 소비 투자 감소", "산업설비 수요 감소가 나타났다", date="2026-09-30")
    direct = _industry_candidate("direct", "산업설비 업계 운임 상승", "수출 기업 원가 부담이 커졌다", date="2026-06-11")
    candidates = [macro, direct, *_candidates()[2:]]
    originals = tuple(candidates)
    ranked = body_ranked_candidates(candidates, attempt_budget=6, probe_budget=1, company=COMPANY)
    assert ranked[0].id == direct.id
    assert {item.id for item in ranked} == {item.id for item in candidates}
    assert tuple(candidates) == originals
    assert all(next(item for item in ranked if item.id == original.id) == original for original in originals)


def test_국내와_세계_문제후보에_같은_예약몫_안에서_각각_기회를_준다():
    domestic = [_industry_candidate(f"domestic-{index}", "산업설비 공급 차질", "생산 중단", date="2026-09-30")
                for index in range(3)]
    global_candidate = _industry_candidate("global", "산업설비 원가 부담", "제조 생산 차질", "global", "2026-06-01")
    candidates = [*domestic, global_candidate, *_candidates()[2:]]
    # 후보 12개를 구성해 기존 예산 12의 예약몫 2개 안에서 두 탐색군을 확인한다.
    ordinary = [replace(_candidates()[2], id=f"ordinary-extra-{i}", source_url=f"https://media.example/extra-{i}")
                for i in range(4)]
    candidates.extend(ordinary)
    ranked = body_ranked_candidates(candidates, attempt_budget=12, probe_budget=2, company=COMPANY)
    assert {item.id for item in ranked[:2]} == {domestic[-1].id, global_candidate.id}
    assert len(ranked) == len(candidates) == 12
    assert {item.id for item in ranked} == {item.id for item in candidates}


def test_두_지역에_속한_한_기사는_예약몫을_중복_소비하지_않는다():
    shared = replace(_industry_candidate("shared", "산업설비 공급 차질", "제조 생산 중단"),
                     topics=("industry_domestic:" + ANCHOR.anchor_id, "industry_global:" + ANCHOR.anchor_id))
    other = _industry_candidate("other", "산업설비 원가 부담", "공급 차질")
    candidates = [shared, other, *_candidates()[2:]]
    ranked = body_ranked_candidates(candidates, attempt_budget=6, probe_budget=1, company=COMPANY)
    assert ranked[0].id == shared.id
    assert len({item.id for item in ranked}) == len(candidates)


@pytest.mark.parametrize("title,description", [
    ("산업설비코드 공급 부족", "화학재료 공장 차질"),
    ("폐산업설비 재활용 공급 부족", "정유 공장 차질"),
    ("주차장 공급 부족", "산업설비는 안정적이다. 주차장 공급 부족이 심해졌다"),
])
def test_세계주제만으로_다른사업이나_다른절_문제를_예약하지_않는다(title, description):
    domestic = _industry_candidate("domestic", "산업설비 공급 차질", "산업설비 생산 중단")
    unrelated = _industry_candidate("unrelated", title, description, "global", "2026-09-30")
    candidates = [domestic, unrelated, *_candidates()[2:]]
    ranked = body_ranked_candidates(candidates, attempt_budget=6, probe_budget=1, company=COMPANY)
    assert ranked[0].id == domestic.id
    assert next(item for item in ranked if item.id == unrelated.id) == unrelated


def test_지역라벨은_후보의_기사메타와_실제검수지역을_바꾸지_않는다():
    labelled_global = _industry_candidate("labelled-global", "산업설비 국내 공장 생산 차질", "한국 공장 공급 중단", "global")
    candidates = [labelled_global, *_candidates()[1:]]
    ranked = body_ranked_candidates(candidates, attempt_budget=6, probe_budget=1, company=COMPANY)
    assert next(item for item in ranked if item.id == labelled_global.id) == labelled_global
    assert labelled_global.topics == ("industry_global:" + ANCHOR.anchor_id,)


@pytest.mark.parametrize("budget", [0, 1, 5, 6])
def test_작은_예산은_지역보장을_위해_증가시키지_않는다(budget):
    domestic = _industry_candidate("domestic", "산업설비 공급 차질", "산업설비 생산 중단")
    global_candidate = _industry_candidate("global", "산업설비 공급 부족", "산업설비 수요 감소", "global")
    candidates = [domestic, global_candidate, *_candidates()[2:]]
    ranked = body_ranked_candidates(candidates, attempt_budget=budget, probe_budget=1, company=COMPANY)
    assert len(ranked) == len(candidates)
    assert len({item.id for item in ranked[:budget]}) <= budget


@pytest.mark.parametrize("global_anchor", ["anchor-two", "unknown-anchor"])
def test_다른_앵커의_국내문제를_세계탐색기회로_빌리지_않는다(global_anchor):
    other_text = "회사는 냉각장치 제조 사업을 운영한다."
    other_anchor = replace(ANCHOR, anchor_id="anchor-two", business_item="냉각장치 제조",
                           exact_text=other_text, text_sha256=exact_text_sha256(other_text),
                           location=f"chars:0-{len(other_text)}")
    company = replace(COMPANY, business_anchors=(ANCHOR, other_anchor))
    mixed_topics = replace(_industry_candidate("mixed", "산업설비 공급 차질", "산업설비 생산 중단"),
                           topics=("industry_domestic:" + ANCHOR.anchor_id, "industry_global:" + global_anchor))
    domestic = _industry_candidate("domestic", "산업설비 공급 지연", "산업설비 생산 차질", date="2026-08-30")
    actual_global = replace(_industry_candidate("global", "냉각장치 제조 공급 부족", "냉각장치 제조 생산 중단",
                                                "global", "2026-06-01"),
                           topics=("industry_global:" + other_anchor.anchor_id,))
    ordinary = [replace(_candidates()[2], id=f"ordinary-{index}", source_url=f"https://media.example/{index}")
                for index in range(9)]
    candidates = [mixed_topics, domestic, actual_global, *ordinary]
    ranked = body_ranked_candidates(candidates, attempt_budget=12, probe_budget=2, company=company)
    assert [item.id for item in ranked[:2]] == [mixed_topics.id, actual_global.id]
    assert next(item for item in ranked if item.id == mixed_topics.id).topics == mixed_topics.topics
    assert len(ranked) == len(candidates)
