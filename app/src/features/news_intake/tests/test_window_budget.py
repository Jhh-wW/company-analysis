"""후보가 적은 기간의 남는 기사 시도 예산을 최근부터 재사용하는지 검증한다."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake import collection as collection_service
from src.features.news_intake.body_prefetch import BodyFetchConcurrency
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.grounded import build_grounded_prompt
from src.features.news_intake.models import NewsBodyFetchResult
from src.features.news_intake.search_snapshot import candidate_window, collect_search_snapshot, diverse_candidates
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
    # 주입 콜백은 내부 변형의 명시적 차단을 증명하지 않는다. 미관측 접근 실패는
    # 대체 슬롯 자격이 없으므로 기존 기본 24건 계약이 그대로 남는다.
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


@pytest.mark.parametrize("recent_count", [30, 55])
def test_one_archive_candidate_reserves_one_attempt_and_releases_the_rest(recent_count):
    snapshot = paged_snapshot([item(number) for number in range(recent_count)]
                              + [item(recent_count, date="2025-01-01")])
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=unique_body,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert result.diagnostics["기간별"]["12"]["시도상한"] == 23
    assert result.diagnostics["기간별"]["12"]["시도"] == 23
    assert result.diagnostics["기간별"]["12"]["미시도"] == recent_count - 23
    assert result.diagnostics["기간별"]["24"]["시도"] == 1
    assert result.diagnostics["본문시도기사"] == 24


@pytest.mark.parametrize("counts,expected", [
    ((30, 1, 1), (22, 1, 1)),
    ((2, 30, 1), (2, 21, 1)),
    ((1, 2, 30), (1, 2, 21)),
    ((3, 2, 1), (3, 2, 1)),
    ((1, 30, 30), (1, 19, 4)),
    ((30, 1, 30), (19, 1, 4)),
    ((30, 30, 1), (19, 4, 1)),
])
def test_sparse_windows_release_only_attempts_they_cannot_use(counts, expected):
    dates = ("2026-09-01", "2025-01-01", "2024-01-01")
    items = [item(window * 100 + number, date=dates[window])
             for window, count in enumerate(counts) for number in range(count)]
    snapshot = paged_snapshot(items)
    calls = []

    def fetch(url):
        calls.append(url)
        return unique_body(url)

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert tuple(result.diagnostics["기간별"][str(month)]["시도"] for month in c.WINDOW_MONTHS) == expected
    assert len(calls) == len(set(calls)) == sum(expected)
    window_by_url = {candidate.source_url: candidate.published_on for candidate in snapshot.candidates}
    assert [window_by_url[url] for url in calls] == sorted(
        (window_by_url[url] for url in calls), reverse=True,
    )
    assert result.diagnostics["본문호출"] <= POLICY.max_body_calls
    assert result.diagnostics["분석AI호출"] <= POLICY.max_analysis_calls


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


@pytest.mark.parametrize("corrected_date,target_months", [("2025-01-01", 24), ("2024-01-01", 36)])
def test_deferred_bodies_preserve_sparse_archive_attempts_without_refetch(corrected_date, target_months):
    snapshot = paged_snapshot([item(number) for number in range(30)] + [item(30, date="2025-01-01")])
    recent = [candidate for candidate in snapshot.candidates if candidate.published_on == "2026-09-01"]
    carried_urls = {candidate.source_url for candidate in diverse_candidates(recent, 2)}
    calls, analysis = [], []

    def fetch(url):
        calls.append(url)
        return NewsBodyFetchResult(text=unique_body(url), stage=c.BODY_STAGE_ARTICLE_TAG,
                                   published_on=corrected_date if url in carried_urls else "")

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(calls=analysis), policy=POLICY)
    analyzed_urls = [article["url"] for batch in analysis for article in batch["articles"]]
    assert len(calls) == len(set(calls)) == len(analyzed_urls) == 24
    assert all(analyzed_urls.count(url) == 1 for url in carried_urls)
    assert tuple(result.diagnostics["기간별"][str(month)]["시도"] for month in c.WINDOW_MONTHS) == (23, 1, 0)
    assert result.diagnostics["기간별"]["12"]["이월"] == 2
    assert result.diagnostics["기간별"][str(target_months)]["본문"] >= 2
    assert result.diagnostics["분석AI호출"] <= POLICY.max_analysis_calls


def test_redistribution_preserves_article_and_fallback_call_limits():
    items = [item(number) for number in range(55)] + [item(55, date="2025-01-01")]
    for number, article in enumerate(items):
        article.link = f"https://n.news.naver.com/mnews/article/001/{number}"
    snapshot = paged_snapshot(items)
    calls = []

    def fetch(url):
        calls.append(url)
        return NewsBodyFetchResult(reason_code="fetch_http_403")

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert len(calls) == len(set(calls)) == result.diagnostics["본문호출"] == 48
    assert result.diagnostics["본문시도기사"] == 24
    assert tuple(result.diagnostics["기간별"][str(month)]["시도"] for month in c.WINDOW_MONTHS) == (23, 1, 0)
    assert result.diagnostics["분석AI호출"] == 0


def test_redistribution_stops_requests_when_collection_deadline_expires(monkeypatch):
    snapshot = paged_snapshot([item(number) for number in range(30)] + [item(30, date="2025-01-01")])
    elapsed = 0
    calls, analysis = [], []
    monkeypatch.setattr(collection_service, "time", SimpleNamespace(monotonic=lambda: elapsed))

    def fetch(url):
        nonlocal elapsed
        calls.append(url)
        elapsed = POLICY.max_collection_seconds
        return unique_body(url)

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(calls=analysis), policy=POLICY)
    assert len(calls) == result.diagnostics["본문시도기사"] == result.diagnostics["본문호출"] == 1
    assert not analysis and result.diagnostics["분석AI호출"] == 0
    assert "body_budget_exhausted" in result.diagnostics["상한사유"]


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


@pytest.mark.parametrize("concurrent", [False, True])
def test_confirmed_access_denial_backfills_after_all_base_windows(concurrent):
    items = [item(number) for number in range(15)]
    items += [item(number, date="2025-01-01") for number in range(15, 47)]
    items += [item(number, date="2024-01-01") for number in range(47, 50)]
    search_snapshot = paged_snapshot(items)
    by_window = {
        months: diverse_candidates(
            [candidate for candidate in search_snapshot.candidates
             if candidate_window(candidate, AS_OF) == months],
            len(search_snapshot.candidates),
        )
        for months in c.WINDOW_MONTHS
    }
    denied_urls = {
        candidate.source_url
        for candidate in (*by_window[12], *by_window[24][:6], *by_window[36])
    }
    calls = []

    def fetch(url):
        calls.append(url)
        if url in denied_urls:
            return NewsBodyFetchResult(
                reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED,
                all_variants_access_denied=True,
            )
        return unique_body(url)

    result = collect_from_snapshot(
        search_snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
        analyze_grounded=analyzer(), policy=POLICY,
        body_fetch=BodyFetchConcurrency(max_in_flight=3) if concurrent else None,
    )
    diagnostics = result.diagnostics
    assert diagnostics["기본시도기사"] == POLICY.max_body_articles
    assert diagnostics["접근거절기사"] == POLICY.max_body_articles
    assert diagnostics["대체시도상한"] > 0
    assert diagnostics["대체시도기사"] > 0
    assert diagnostics["대체기간별"]["24"]["본문"] > 0
    assert diagnostics["본문시도기사"] == diagnostics["기본시도기사"] + diagnostics["대체시도기사"]
    assert diagnostics["본문호출"] == diagnostics["기본본문호출"] + diagnostics["대체본문호출"]
    assert diagnostics["본문호출"] <= POLICY.max_body_calls
    assert diagnostics["분석AI호출"] <= POLICY.max_analysis_calls
    assert result.fragments
    assert len(calls) == diagnostics["본문호출"]
    assert len(set(calls)) == len(calls)
    assert [diagnostics["기간별"][str(months)]["시도"] for months in c.WINDOW_MONTHS] == [15, 6, 3]


@pytest.mark.parametrize("fallback_result,confirmed", [
    (NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_PROVIDED), False),
    (NewsBodyFetchResult(reason_code="fetch_http_429"), False),
    (NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED), False),
    (NewsBodyFetchResult(reason_code=c.FETCH_HTTP_FORBIDDEN_CODE,
                         all_variants_access_denied=True), True),
])
def test_access_denial_requires_every_callback_and_internal_variant(fallback_result, confirmed):
    candidate = item(1)
    candidate.link = "https://n.news.naver.com/mnews/article/001/1"
    search_snapshot = paged_snapshot([candidate, item(2)])
    source_url = candidate.originallink
    calls = []

    def fetch(url):
        calls.append(url)
        if url == source_url:
            return NewsBodyFetchResult(
                reason_code=c.FETCH_HTTP_FORBIDDEN_CODE,
                all_variants_access_denied=True,
            )
        if "n.news.naver.com" in url:
            return fallback_result
        return unique_body(url)

    result = collect_from_snapshot(
        search_snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
        analyze_grounded=analyzer(), policy=POLICY,
    )
    assert result.diagnostics["접근거절기사"] == int(confirmed)
    assert result.diagnostics["대체시도기사"] == 0
    assert result.diagnostics["본문호출"] <= POLICY.max_body_calls
    assert calls.count(source_url) == 1


@pytest.mark.parametrize("concurrent", [False, True])
def test_replacement_failures_do_not_create_more_slots(concurrent):
    snapshot = paged_snapshot([item(number) for number in range(50)])
    calls = []

    def fetch(url):
        calls.append(url)
        return NewsBodyFetchResult(
            reason_code=c.FETCH_HTTP_FORBIDDEN_CODE,
            all_variants_access_denied=True,
        )

    result = collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
        analyze_grounded=analyzer(), policy=POLICY,
        body_fetch=BodyFetchConcurrency(max_in_flight=3) if concurrent else None,
    )
    diagnostics = result.diagnostics
    assert diagnostics["접근거절기사"] == POLICY.max_body_articles
    assert diagnostics["대체시도상한"] == POLICY.max_body_articles
    assert diagnostics["기본시도기사"] == POLICY.max_body_articles
    assert diagnostics["대체시도기사"] == POLICY.max_body_articles
    assert diagnostics["본문시도기사"] == diagnostics["본문호출"] == POLICY.max_body_calls
    assert len(calls) == len(set(calls)) == POLICY.max_body_calls
    assert diagnostics["분석AI호출"] == 0


def test_confirmed_denials_cannot_extend_expired_collection_deadline(monkeypatch):
    snapshot = paged_snapshot([item(number) for number in range(50)])
    elapsed = 0
    calls = []
    monkeypatch.setattr(collection_service, "time", SimpleNamespace(monotonic=lambda: elapsed))

    def fetch(url):
        nonlocal elapsed
        calls.append(url)
        elapsed = POLICY.max_collection_seconds
        return NewsBodyFetchResult(
            reason_code=c.FETCH_HTTP_FORBIDDEN_CODE,
            all_variants_access_denied=True,
        )

    result = collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
        analyze_grounded=analyzer(), policy=POLICY,
    )
    assert result.diagnostics["본문호출"] == len(calls) == 1
    assert result.diagnostics["대체시도기사"] == 0
    assert result.diagnostics["분석AI호출"] == 0


def test_publisher_or_search_title_name_does_not_spend_ai_without_body_identity():
    policy = replace(POLICY, batch_size=1)
    snapshot = paged_snapshot([item(0), item(1)], policy)
    analysis = []

    def fetch(url):
        if url.endswith("/0"):
            return "다른 법인은 산업 설비 사업에서 국내 고객과 공급 계약을 체결했다. " * 3
        return unique_body(url)

    result = collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
        analyze_grounded=analyzer(calls=analysis), policy=policy,
    )
    assert result.diagnostics["본문읽기"] == 2
    assert result.diagnostics["제외"]["body_target_name_missing"] == 1
    assert result.diagnostics["분석AI호출"] == len(analysis) == 1
    assert len(result.fragments) == 1
    assert analysis[0]["articles"][0]["url"].endswith("/1")


def test_only_name_beyond_ai_body_cutoff_cannot_pass_identity():
    policy = replace(POLICY, max_body_chars=90)
    snapshot = paged_snapshot([item(0)], policy)
    analysis = []
    body = "다른 법인은 산업 설비 사업에서 공급 계약을 체결했다. " * 4 + COMPANY.company_name
    assert COMPANY.company_name not in body[:policy.max_body_chars]

    result = collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, fetch_text=lambda url: body,
        analyze_grounded=analyzer(calls=analysis), policy=policy,
    )
    assert result.diagnostics["본문읽기"] == 1
    assert result.diagnostics["제외"]["body_target_name_missing"] == 1
    assert result.diagnostics["분석AI호출"] == 0
    assert not analysis and not result.fragments


def test_grounded_prompt_separates_publisher_from_company_business_event():
    snapshot = paged_snapshot([item(0)])
    prompt = build_grounded_prompt(COMPANY, [(snapshot.candidates[0], BODY)], AS_OF)
    assert "발행처·기자·저작권 표기의 회사명" in prompt
    assert "그 언론사가 보도한 타사의 사건은 same_company=false" in prompt
    assert "그 언론사 자신의 사업·조직·계약" in prompt
