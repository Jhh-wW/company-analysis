"""서로 다른 호스트를 실제로 겹쳐 읽어도 마지막 분석 뒤에는 새 본문을 보내지 않는다."""

from dataclasses import replace
import threading

import pytest

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.search_snapshot import diverse_candidates, domain_host
from src.features.news_intake.tests.test_collection import AS_OF, COMPANY, analyzer
from src.features.news_intake.tests.test_collection_body_concurrency import (
    GatedFetch, THREE_WIDE, WAIT_SECONDS, WIDE_POLICY,
    analysis_order, comparable, distinct_host_items, finish, run_in_thread,
)
from src.features.news_intake.tests.test_window_budget import paged_snapshot, unique_body
from src.shared.report_generation.models import exact_text_sha256


ANALYSIS_LIMIT = 1
LAST_BATCH_SIZE = THREE_WIDE.max_in_flight
RECENT_DATE = "2026-09-01"
ARCHIVE_DATE = "2025-01-01"


@pytest.mark.parametrize(
    "remaining_date",
    [RECENT_DATE, ARCHIVE_DATE, None],
    ids=["same-window-unfinished", "next-window-unfinished", "exact-completion"],
)
def test_last_analysis_waits_for_all_bodies_and_never_fetches_another_batch(remaining_date):
    policy = replace(WIDE_POLICY, batch_size=LAST_BATCH_SIZE, max_analysis_calls=ANALYSIS_LIMIT)
    items = distinct_host_items(LAST_BATCH_SIZE)
    if remaining_date is not None:
        remaining = distinct_host_items(LAST_BATCH_SIZE * 2)[LAST_BATCH_SIZE:]
        for row in remaining:
            row.pubDate = remaining_date
        items.extend(remaining)
    snapshot = paged_snapshot(items, policy)
    recent = [candidate for candidate in snapshot.candidates if candidate.published_on == RECENT_DATE]
    expected_batch = [candidate.source_url for candidate in diverse_candidates(recent, len(recent))][:LAST_BATCH_SIZE]
    expected_unused = {candidate.source_url for candidate in snapshot.candidates} - set(expected_batch)
    assert bool(expected_unused) is (remaining_date is not None)

    sequential_calls = []
    sequential = collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, policy=policy,
        fetch_text=unique_body, analyze_grounded=analyzer(calls=sequential_calls),
    )

    fetch = GatedFetch()
    analysis_entered = threading.Event()
    release_analysis = threading.Event()
    concurrent_calls = []
    analysis_observations = []
    base_analyzer = analyzer(calls=concurrent_calls)

    def analyze(prompt, schema, max_tokens):
        # 분석 중 요청도 붙잡아 두므로 중간에 잘못 보낸 본문이 관측에서 빠지지 않는다.
        with fetch.lock:
            observation = {
                "active_at_start": fetch.active,
                "started_at_start": tuple(fetch.started),
                "finished_at_start": tuple(fetch.finished),
            }
        analysis_observations.append(observation)
        analysis_entered.set()
        observation["released"] = release_analysis.wait(WAIT_SECONDS)
        with fetch.lock:
            observation["active_at_end"] = fetch.active
            observation["started_at_end"] = tuple(fetch.started)
        return base_analyzer(prompt, schema, max_tokens)

    thread, box = run_in_thread(lambda: collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, policy=policy,
        fetch_text=fetch, analyze_grounded=analyze, body_fetch=THREE_WIDE,
    ))
    try:
        started = fetch.wait_started(LAST_BATCH_SIZE)
        # 콜백 진입 순서는 OS가 정한다. 후보 순위와 비교할 것은 분석 소비 순서다.
        assert set(started) == set(expected_batch)
        with fetch.lock:
            assert fetch.active == fetch.max_active == LAST_BATCH_SIZE
            assert len({domain_host(url) for url in fetch.started}) == LAST_BATCH_SIZE
            assert max(fetch.max_active_by_host.values()) == 1
        assert not analysis_entered.is_set(), "본문이 끝나기 전에 분석이 시작됐습니다"

        # 선두 후보를 마지막에 해제해 완료 순서와 후보 소비 순서를 확실히 다르게 만든다.
        for completed_count, url in enumerate(reversed(expected_batch), start=1):
            fetch.release(url)
            fetch.wait_finished(completed_count)
        assert analysis_entered.wait(WAIT_SECONDS), "마지막 묶음의 분석이 시작되지 않았습니다"
        with fetch.lock:
            assert fetch.active == 0, "분석 중 본문 콜백이 남아 있습니다"
            assert set(fetch.started) == set(expected_batch)
            assert fetch.finished == list(reversed(expected_batch))
        release_analysis.set()
        result = finish(thread, box)
    finally:
        # 실패한 시험도 요청·분석 스레드를 남기지 않는다. 이후 요청은 즉시 돌려보낸다.
        release_analysis.set()
        with fetch.lock:
            fetch.gated = False
            gates = tuple(fetch.gates.values())
        for gate in gates:
            gate.set()
        thread.join(WAIT_SECONDS)
        assert not thread.is_alive(), "시험 정리 뒤 수집 스레드가 남았습니다"

    assert len(analysis_observations) == ANALYSIS_LIMIT
    observation = analysis_observations[0]
    assert observation["released"] is True
    assert observation["active_at_start"] == observation["active_at_end"] == 0
    assert observation["started_at_start"] == observation["started_at_end"]
    assert observation["finished_at_start"] == tuple(reversed(expected_batch))
    assert analysis_order(concurrent_calls) == analysis_order(sequential_calls) == [expected_batch]
    assert len(fetch.started) == LAST_BATCH_SIZE, "마지막 분석 이후 다음 묶음 본문이 전송됐습니다"
    assert expected_unused.isdisjoint(fetch.started)
    assert fetch.finished == list(reversed(expected_batch))
    assert fetch.max_active >= 2

    assert comparable(result) == comparable(sequential)
    expected_article_order = sorted(expected_batch)
    assert [article.candidate.source_url for article in result.articles] == expected_article_order
    assert [fragment.url for fragment in result.fragments] == expected_article_order
    expected_hashes = {url: exact_text_sha256(unique_body(url)) for url in expected_batch}
    assert result.document_hashes == expected_hashes
    for article, fragment in zip(result.articles, result.fragments):
        body = unique_body(article.candidate.source_url)
        assert article.document_content_sha256 == expected_hashes[article.candidate.source_url]
        assert len(article.excerpts) == 1
        excerpt = article.excerpts[0]
        assert excerpt.text == body[excerpt.span_start:excerpt.span_end] == fragment.text == body
        assert fragment.text_sha256 == exact_text_sha256(body)

    diagnostics = result.diagnostics
    assert diagnostics["본문호출"] == diagnostics["본문읽기"] == LAST_BATCH_SIZE
    assert diagnostics["관련성통과"] == diagnostics["독립기사"] == LAST_BATCH_SIZE
    assert diagnostics["분석AI호출"] == ANALYSIS_LIMIT
    assert diagnostics["분석잔여호출"] == 0
    assert diagnostics["본문동시수집"]["출발"] == LAST_BATCH_SIZE
    assert diagnostics["본문동시수집"]["선행미사용호출"] == 0
    if expected_unused:
        assert diagnostics["상한사유"] == ("analysis_budget_exhausted",)
        assert diagnostics["완전성"] == "partial"
        assert diagnostics["자료부족"] is False
        assert diagnostics["캐시재사용가능"] is False
    else:
        assert diagnostics["상한사유"] == ()
        assert diagnostics["캐시재사용가능"] is True

    print(
        f"동시성 관측: 잔여기간={remaining_date or '없음'}, 최대동시={fetch.max_active}, "
        f"분석시진행={observation['active_at_start']}/{observation['active_at_end']}, "
        f"본문={len(fetch.started)}, 분석={len(concurrent_calls)}, "
        f"역순완료={fetch.finished == list(reversed(expected_batch))}, "
        f"미시도={len(expected_unused)}, 캐시={diagnostics['캐시재사용가능']}"
    )
