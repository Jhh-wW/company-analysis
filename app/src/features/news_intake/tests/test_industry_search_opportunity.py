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
    assert ranked[0].id == "award"
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
    assert all(query.endswith(("산업 문제", "공급난")) for query in queries)
    assert not any("공급 수요" in query or "차질 규제" in query for query in queries)
