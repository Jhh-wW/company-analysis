"""분석할 수 없는 본문 요청을 보내지 않고 기존 채택 결과를 보존하는지 확인한다."""

from dataclasses import replace

import pytest

from src.features.news_intake.body_prefetch import BodyFetchConcurrency
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.tests.test_collection import AS_OF, COMPANY, POLICY, analyzer, item
from src.features.news_intake.tests.test_window_budget import paged_snapshot, unique_body


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("analysis_limit, expected_reads", [(0, 0), (1, 4), (3, 12)])
def test_analysis_budget_stops_body_requests_before_next_batch(concurrency, analysis_limit, expected_reads):
    policy = replace(POLICY, max_analysis_calls=analysis_limit)
    snapshot = paged_snapshot([item(number) for number in range(20)], policy)
    fetched, analysis = [], []

    def fetch(url):
        fetched.append(url)
        return unique_body(url)

    result = collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, policy=policy,
        fetch_text=fetch, analyze_grounded=analyzer(calls=analysis),
        body_fetch=BodyFetchConcurrency(max_in_flight=concurrency),
    )

    assert len(fetched) == result.diagnostics["본문호출"] == expected_reads
    assert result.diagnostics["본문읽기"] == expected_reads
    assert len(analysis) == result.diagnostics["분석AI호출"] == analysis_limit
    assert result.diagnostics["관련성통과"] == expected_reads
    assert result.diagnostics["상한사유"] == ("analysis_budget_exhausted",)
    assert result.diagnostics["자료부족"] is False
    assert result.diagnostics["캐시재사용가능"] is False


@pytest.mark.parametrize("concurrency", [1, 3])
def test_exact_budget_completion_preserves_article_evidence(concurrency):
    items = [item(number) for number in range(4)]
    limited_policy = replace(POLICY, max_analysis_calls=1)

    def collect(policy):
        return collect_from_snapshot(
            paged_snapshot(items, policy), company=COMPANY, as_of=AS_OF, policy=policy,
            fetch_text=unique_body, analyze_grounded=analyzer(),
            body_fetch=BodyFetchConcurrency(max_in_flight=concurrency),
        )

    limited, unlimited = collect(limited_policy), collect(POLICY)
    assert limited.fragments == unlimited.fragments
    assert limited.articles == unlimited.articles
    assert limited.diagnostics["상한사유"] == ()
    assert limited.diagnostics["캐시재사용가능"] is True
