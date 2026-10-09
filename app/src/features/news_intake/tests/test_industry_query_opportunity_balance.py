"""사업 탐색 메타 순위와 고정 질의별 조사 기회를 본문 승인과 분리한다."""

from dataclasses import replace

import pytest

from src.features.news_intake.search_snapshot import (
    _industry_business_linked, _industry_candidates, _industry_exploration_linked,
    _reserved_industry_candidates, body_ranked_candidates, search_plan,
)
from src.features.news_intake.tests.test_industry_context import (
    ANCHOR, AS_OF, BODY, CANDIDATE, COMPANY, response, split,
)
from src.shared.report_generation.models import exact_text_sha256


def company():
    anchors = []
    for identifier, business in (("content", "뉴스콘텐츠"), ("advertising", "광고 사업")):
        text = f"당사는 {business}을 현재 제공하고 있다."
        anchors.append(replace(ANCHOR, anchor_id=identifier, business_item=business,
                               exact_text=text, text_sha256=exact_text_sha256(text),
                               location=f"chars:0-{len(text)}"))
    return replace(COMPANY, business_anchors=tuple(anchors))


def candidate(identifier, topic, title="시장 소식", description="새 소식", *, date="2026-09-01"):
    return replace(CANDIDATE, id=identifier, title=title, description=description,
                   topics=(topic,), metadata_name_match=False, published_on=date,
                   source_url=f"https://media.example/{identifier}")


@pytest.mark.parametrize("topic,title", [
    ("industry_domestic:content", "뉴스 콘텐츠 업계의 공급 부족"),
    ("industry_domestic:advertising", "광고료 수익에 의존하는 업체의 위기"),
    ("industry_domestic:advertising", "광고비 감소와 업계 위기"),
])
def test_spacing_and_business_suffix_are_exploratory_only(topic, title):
    item = candidate("spacing", topic, title)
    assert not _industry_business_linked(item, company())
    assert _industry_exploration_linked(item, company())
    assert item.title == title


@pytest.mark.parametrize("title", ["광고판 공급 부족", "허위광고 위기", "뉴스콘텐츠코드 규제"])
def test_prefix_and_suffix_names_do_not_match_core(title):
    item = candidate("suffix", "industry_domestic:advertising", title)
    assert not _industry_exploration_linked(item, company())


def test_full_business_signal_precedes_exploratory_problem():
    exact = candidate("exact", "industry_domestic:advertising", "광고 사업 시장 소식")
    weak = candidate("weak", "industry_domestic:advertising", "광고료 수익 감소 위기")
    assert _industry_candidates([weak, exact], company=company())[0] == exact


def test_linked_summary_problem_precedes_unrelated_title_problem():
    linked = candidate("linked", "industry_global:advertising", "신기술과 창작",
                       "광고료 수익에 의존하는 미디어 기업이 경영 위기를 겪는다")
    other = candidate("other", "industry_global:advertising", "주류 업계 위기")
    assert _industry_candidates([other, linked], company=company())[0] == linked


def test_other_anchor_and_other_clause_do_not_borrow_problem_signal():
    other_anchor = candidate("wrong", "industry_global:content", "광고료 수익 위기")
    separate = candidate("separate", "industry_global:advertising", "광고 소식",
                         "광고비는 안정적이다. 주류 업계 위기가 심해졌다")
    from src.features.news_intake.search_snapshot import _industry_exploration_problem
    assert not _industry_exploration_problem(other_anchor, company())
    assert not _industry_exploration_problem(separate, company())


def four_groups():
    return [candidate(f"{anchor}-{region}-{index}", f"industry_{region}:{anchor}",
                      "시장 위기", date=f"2026-09-{20 + index:02d}")
            for anchor in ("content", "advertising")
            for region in ("domestic", "global") for index in range(3)]


def reserve(items, budget):
    return _reserved_industry_candidates(_industry_candidates(items, company=company()),
                                          budget, company=company())


def test_four_places_cover_four_query_groups_without_mutating_metadata():
    items = four_groups()
    before = tuple(items)
    selected = reserve(items, 4)
    assert len(selected) == len({item.id for item in selected}) == 4
    assert {item.topics[0] for item in selected} == {
        f"industry_{region}:{anchor}" for anchor in ("content", "advertising")
        for region in ("domestic", "global")}
    assert tuple(items) == before and all(item in items for item in selected)


def test_multi_query_article_does_not_replace_another_fresh_opportunity():
    shared = replace(four_groups()[0], id="shared", title="뉴스콘텐츠 위기",
                     topics=("industry_domestic:content", "industry_global:content"))
    global_item = candidate("fresh-global", "industry_global:content", "시장 소식")
    other_groups = [item for item in four_groups() if "advertising" in item.topics[0]]
    selected = reserve([shared, global_item, *other_groups], 4)
    assert len({item.id for item in selected}) == 4
    assert shared in selected and global_item in selected
    assert len([item for item in selected if "advertising" in item.topics[0]]) == 2


def test_empty_or_duplicate_only_groups_reallocate_unique_places():
    shared = replace(four_groups()[0], id="shared", title="뉴스콘텐츠 위기",
                     topics=("industry_domestic:content", "industry_global:content"))
    others = [item for item in four_groups() if item.topics == ("industry_domestic:advertising",)]
    selected = reserve([shared, *others], 4)
    assert len(selected) == len({item.id for item in selected}) == 4
    assert shared in selected and all(item in [shared, *others] for item in selected)


@pytest.mark.parametrize("budget", [0, 1, 2, 3, 4, 8])
def test_reservation_budget_and_two_region_priority_are_preserved(budget):
    selected = reserve(four_groups(), budget)
    assert len(selected) == len({item.id for item in selected}) == budget
    if budget >= 2:
        assert {item.topics[0].split(":")[0] for item in selected[:2]} == {
            "industry_domestic", "industry_global"}


def test_company_top_article_and_total_candidate_budget_are_preserved():
    ordinary = [replace(candidate(f"company-{index}", "business"),
                        metadata_name_match=True, published_on=f"2026-09-{30-index:02d}")
                for index in range(20)]
    items = ordinary + four_groups()
    before = tuple(items)
    ranked = body_ranked_candidates(items, attempt_budget=24, probe_budget=3, company=company())
    assert len(ranked) == len({item.id for item in ranked}) == len(items)
    assert [item for item in ranked if item.metadata_name_match] == ordinary
    assert ordinary[0] in ranked[:24]
    assert sum(item.metadata_name_match for item in ranked[:24]) == 20
    assert tuple(items) == before


def test_unknown_anchor_and_absent_anchors_never_reserve():
    unknown = candidate("unknown", "industry_domestic:unknown", "광고료 위기")
    assert _industry_candidates([unknown], company=company()) == []
    assert _industry_candidates(four_groups(), company=replace(company(), business_anchors=())) == []


def test_query_contract_and_source_validation_are_unchanged():
    before = search_plan(COMPANY, AS_OF)
    assert len(before) == len(search_plan(replace(COMPANY, business_anchors=()), AS_OF))
    other = replace(CANDIDATE, topics=("industry_global:" + ANCHOR.anchor_id,))
    payload = response(same_business=False)
    assert not split(payload, candidate=other, body=BODY)[1]
    payload = response(geography="global")
    assert not split(payload, candidate=other, body=BODY)[1]
