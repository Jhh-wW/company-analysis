"""접근 실패와 빈 기간이 남은 기사 시도 예산을 버리지 않는지 검증한다."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsBodyFetchResult
from src.features.news_intake.search_snapshot import collect_search_snapshot, diverse_candidates
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, POLICY, analyzer, item


def paged_snapshot(items, policy=POLICY):
    calls = 0

    def search(query, **options):
        nonlocal calls
        start = calls * options["display"]
        calls += 1
        return SimpleNamespace(
            state="success", reason_code="news_search_ok", items=items[start:start + options["display"]],
            transport_attempts=1, retry_recovered=False, attempt_reason_codes=("news_search_ok",),
        )

    return collect_search_snapshot(search_news=search, company=COMPANY, as_of=AS_OF, policy=policy)


def unique_body(url):
    return BODY + " 기사 식별자 " + url.rsplit("/", 1)[-1]


def test_empty_archive_windows_release_attempts_within_global_limits():
    policy = replace(POLICY, max_analysis_calls=5)
    snapshot = paged_snapshot([item(number) for number in range(45)], policy)
    ranked = diverse_candidates(list(snapshot.candidates), len(snapshot.candidates))
    unavailable = {candidate.source_url for candidate in ranked[:8]}
    calls = []

    def fetch(url):
        calls.append(url)
        if url in unavailable:
            return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED)
        return unique_body(url)

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(), policy=policy)
    assert len(calls) == policy.max_body_articles == 24
    assert result.diagnostics["본문읽기"] == 16
    assert result.diagnostics["분석AI호출"] == 4
    assert result.diagnostics["기간별"]["12"]["시도상한"] == 24
    assert result.diagnostics["기간별"]["12"]["미시도"] == 21
    assert result.diagnostics["상한사유"] == ("body_budget_exhausted",)
    assert result.diagnostics["분석잔여호출"] == 1
    assert result.diagnostics["시도경고"][c.EXCLUDED_FETCH_ROBOTS_BLOCKED] == 8


def test_existing_archive_candidates_keep_both_window_allocations():
    items = [item(number) for number in range(24)]
    items += [item(number, date="2025-01-01") for number in range(24, 28)]
    items += [item(number, date="2024-01-01") for number in range(28, 32)]
    snapshot = paged_snapshot(items)
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=unique_body,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert [result.diagnostics["기간별"][str(month)]["시도"] for month in c.WINDOW_MONTHS] == [16, 4, 4]
    assert result.diagnostics["본문시도기사"] == 24


def test_one_archive_candidate_keeps_its_base_allocation():
    snapshot = paged_snapshot([item(number) for number in range(30)] + [item(30, date="2025-01-01")])
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=unique_body,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert result.diagnostics["기간별"]["12"]["시도상한"] == 20
    assert result.diagnostics["기간별"]["24"]["시도"] == 1


def test_corrected_dates_defer_bodies_without_refetch_within_five_analysis_calls():
    policy = replace(POLICY, max_analysis_calls=5)
    snapshot = paged_snapshot([item(number) for number in range(30)], policy)
    ranked = diverse_candidates(list(snapshot.candidates), len(snapshot.candidates))
    carried_urls = {candidate.source_url for candidate in ranked[:2]}
    unavailable = {candidate.source_url for candidate in ranked[2:10]}
    calls, analysis = [], []

    def fetch(url):
        calls.append(url)
        if url in unavailable:
            return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED)
        return NewsBodyFetchResult(text=unique_body(url), stage=c.BODY_STAGE_ARTICLE_TAG,
                                   published_on="2024-01-01" if url in carried_urls else "2026-09-01")

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(calls=analysis), policy=policy)
    analyzed_urls = [article["url"] for batch in analysis for article in batch["articles"]]
    assert len(calls) == len(set(calls)) == 24
    assert carried_urls <= set(analyzed_urls)
    assert result.diagnostics["기간별"]["12"]["이월"] == 2
    assert result.diagnostics["분석AI호출"] == 5
    assert result.diagnostics["기간별"]["36"]["본문"] == 2


@pytest.mark.parametrize("article_limit,call_limit", [(1, 48), (24, 3), (7, 5)])
def test_redistribution_respects_reduced_article_and_body_call_limits(article_limit, call_limit):
    policy = replace(POLICY, max_body_articles=article_limit, max_body_calls=call_limit)
    snapshot = paged_snapshot([item(number) for number in range(30)], policy)
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=unique_body,
                                   analyze_grounded=analyzer(), policy=policy)
    assert result.diagnostics["본문시도기사"] <= article_limit
    assert result.diagnostics["본문호출"] <= call_limit


def test_publisher_observation_separates_domains_from_search_rows():
    snapshot = paged_snapshot([item(number, host="unverified.example") for number in range(3)])
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=unique_body,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert result.diagnostics["출처정책"] == {
        "방식": "확인도메인허용목록", "등록도메인수": len(POLICY.trusted_publisher_domains),
        "미확인도메인수": 1, "미확인검색반환행수": 3,
    }
    assert result.diagnostics["본문호출"] == result.diagnostics["분석AI호출"] == 0
