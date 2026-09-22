"""본문 선행 읽기의 독립 시험: 외부 요청 없이 실제 동시 진입과 판정 계약을 확인한다."""

from __future__ import annotations

import contextvars
import datetime as dt
import threading
from collections import Counter
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager
from dataclasses import replace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.body_prefetch import (
    ArticleFetchJob, ArticleFetchOutcome, BodyFetchConcurrency, BodyFetchLane,
    CallBudgetPool, CallLease, HostSlots, fetch_article_body,
)
from src.features.news_intake.models import (
    NewsBodyFetchResult, NewsCandidate, NewsCollectionPolicy, NewsCompanyContext,
)
from src.shared.engine_build_identity import EngineBuildIdentityChangedError
from src.shared.generation_coordination import GenerationWaitCancelled


# 시험 시간·경쟁 규모·입력은 이 파일의 독립 고정값이며 운영 정책을 바꾸지 않는다.
WAIT_SECONDS = 5
RACERS = 12
RACE_CALL_LIMIT = 3
TEST_CALL_LIMIT = 24
DEADLINE = 100.0
AS_OF = dt.date(2026, 9, 22)
SEARCH_DATE = "2026-09-01"
BODY_DATE = "2026-09-02"
BODY = "가나다전자는 산업설비 제조 사업을 운영하며 자동화 설비 공급 계약을 체결했다고 발표했다."
COMPANY = NewsCompanyContext("가나다전자", domain="company.example")
POLICY = NewsCollectionPolicy(trusted_publisher_domains=("media.example",))
PORTAL_URL = "https://n.news.naver.com/article/001/0000000001"


def candidate(number=0, *, host="media.example", portal=False, **changes):
    url = f"https://{host}/article/{number}"
    return replace(NewsCandidate(
        id=f"기사-{number}", title="가나다전자 산업설비 공급", description="산업설비 공급 소식",
        originallink=url, link=PORTAL_URL if portal else url,
        published_on=SEARCH_DATE, publisher="시험언론", priority=c.PRIORITY_OTHER,
        source_url=url, source_category="news_report",
    ), **changes)


def job(fetch_text, *, lane=None, budget=None, **changes):
    return replace(ArticleFetchJob(
        company=COMPANY, policy=POLICY, as_of=AS_OF, fetch_text=fetch_text,
        budget=budget if budget is not None else CallBudgetPool(TEST_CALL_LIMIT),
        deadline=DEADLINE, clock=lambda: 0.0,
        stop_event=lane.stop_event if lane is not None else threading.Event(),
        host_slots=lane.host_slots if lane is not None else None,
    ), **changes)


def wait_for(event):
    assert event.wait(WAIT_SECONDS), "제한시간 안에 동기화 지점에 도달하지 못했습니다"


def start_thread(task):
    """주 스레드로 예외를 전달하며 종료 대기를 제한할 수 있는 시험용 스레드."""
    settled = Future()

    def run():
        try:
            settled.set_result(task())
        except BaseException as error:
            settled.set_exception(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return settled


@pytest.fixture
def lanes():
    active = []
    releases = []

    def make(width=None, *, per_host=1):
        lane = BodyFetchLane(None if width is None else BodyFetchConcurrency(width, per_host))
        active.append(lane)
        return lane

    def gate():
        event = threading.Event()
        releases.append(event)
        return event

    make.gate = gate
    yield make
    for lane in active:
        lane.request_stop()
    for event in releases:
        event.set()
    for lane in active:
        start_thread(lane.close).result(timeout=WAIT_SECONDS)


@pytest.mark.parametrize("width", [2, 3])
def test_actual_body_io_overlaps_at_configured_width_and_never_exceeds_it(lanes, width):
    lane = lanes(width)
    release = lanes.gate()
    full = threading.Event()
    lock = threading.Lock()
    active = peak = 0
    calls = []
    items = [candidate(number, host=f"h{number}.media.example") for number in range(width * 2)]

    def fetch(url):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(url)
            if active == width:
                full.set()
        try:
            wait_for(release)
            return BODY
        finally:
            with lock:
                active -= 1

    shared = job(fetch, lane=lane)
    futures = [lane.submit(lambda item=item: fetch_article_body(shared, item, CallLease())) for item in items]
    wait_for(full)
    with lock:
        assert active == peak == width
        assert len(calls) == width
    assert not any(future.done() for future in futures)
    release.set()
    outcomes = [future.result(timeout=WAIT_SECONDS) for future in futures]
    assert peak == width
    assert active == 0
    assert [outcome.candidate for outcome in outcomes] == items
    assert all(outcome.full_body == BODY for outcome in outcomes)
    assert shared.budget.made == len(items)


def test_same_host_serializes_while_another_host_can_finish(lanes):
    lane = lanes(3)
    first = candidate(0)
    second = candidate(1, host="www.MEDIA.example")
    other = candidate(2, host="other.media.example")
    first_entered, second_waiting = threading.Event(), threading.Event()
    release = lanes.gate()
    lock = threading.Lock()
    active = peak = 0

    class ObservedSlots:
        @contextmanager
        def slot(self, url):
            if url == second.originallink:
                second_waiting.set()
            with lane.host_slots.slot(url):
                yield

    def fetch(url):
        nonlocal active, peak
        if url == other.originallink:
            return BODY
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if url == first.originallink:
                first_entered.set()
                wait_for(release)
            return BODY
        finally:
            with lock:
                active -= 1

    shared = job(fetch, lane=lane, host_slots=ObservedSlots())
    pending_first = lane.submit(lambda: fetch_article_body(shared, first, CallLease()))
    wait_for(first_entered)
    pending_second = lane.submit(lambda: fetch_article_body(shared, second, CallLease()))
    wait_for(second_waiting)
    pending_other = lane.submit(lambda: fetch_article_body(shared, other, CallLease()))
    assert pending_other.result(timeout=WAIT_SECONDS).full_body == BODY
    assert not pending_second.done()
    release.set()
    assert pending_first.result(timeout=WAIT_SECONDS).full_body == BODY
    assert pending_second.result(timeout=WAIT_SECONDS).full_body == BODY
    assert peak == 1


def test_completion_can_reverse_priority_without_rebinding_results(lanes):
    lane = lanes(3)
    entered = [threading.Event() for _ in range(3)]
    release = [lanes.gate() for _ in range(3)]
    items = [candidate(number, host=f"rank{number}.media.example", priority=number + 1) for number in range(3)]
    completion = []

    def fetch(url):
        number = [item.originallink for item in items].index(url)
        entered[number].set()
        wait_for(release[number])
        completion.append(number)
        return BODY + str(number)

    shared = job(fetch, lane=lane)
    futures = [lane.submit(lambda item=item: fetch_article_body(shared, item, CallLease())) for item in items]
    for event in entered:
        wait_for(event)
    for number in reversed(range(3)):
        release[number].set()
        assert futures[number].result(timeout=WAIT_SECONDS).full_body == BODY + str(number)
    assert completion == [2, 1, 0]
    assert [future.result().candidate for future in futures] == items


def test_same_host_opt_in_two_allows_exactly_two_body_calls(lanes):
    lane = lanes(3, per_host=2)
    release, full = lanes.gate(), threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def fetch(url):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                full.set()
        try:
            wait_for(release)
            return BODY
        finally:
            with lock:
                active -= 1

    shared = job(fetch, lane=lane)
    futures = [lane.submit(lambda number=number: fetch_article_body(shared, candidate(number), CallLease()))
               for number in range(3)]
    wait_for(full)
    with lock:
        assert active == peak == 2
    release.set()
    assert all(future.result(timeout=WAIT_SECONDS).full_body == BODY for future in futures)
    assert peak == 2 and active == 0


@pytest.mark.parametrize("width", [None, 1])
def test_default_and_explicit_one_run_on_calling_thread(lanes, width):
    lane = lanes(width)
    observed = []
    shared = job(lambda url: observed.append(threading.get_ident()) or BODY, lane=lane)
    future = lane.submit(lambda: fetch_article_body(shared, candidate(), CallLease()))
    assert future.done() and not lane.concurrent
    assert future.result().full_body == BODY
    assert observed == [threading.get_ident()]


def test_context_is_copied_per_submission_and_worker_writes_do_not_escape(lanes):
    lane = lanes(2)
    local = contextvars.ContextVar("본문시험_문맥", default="기본")
    barrier = threading.Barrier(2)
    seen = []
    parent = threading.get_ident()
    token = local.set("첫 제출")

    def fetch(url):
        before = local.get()
        local.set("작업 내부")
        barrier.wait(timeout=WAIT_SECONDS)
        seen.append((before, local.get(), threading.get_ident()))
        return BODY

    shared = job(fetch, lane=lane)
    try:
        first = lane.submit(lambda: fetch_article_body(shared, candidate(host="one.media.example"), CallLease()))
        local.set("둘째 제출")
        second = lane.submit(lambda: fetch_article_body(shared, candidate(host="two.media.example"), CallLease()))
        local.set("부모 변경")
        assert first.result(timeout=WAIT_SECONDS).full_body == BODY
        assert second.result(timeout=WAIT_SECONDS).full_body == BODY
        assert sorted(value[0] for value in seen) == sorted(["첫 제출", "둘째 제출"])
        assert all(value[1] == "작업 내부" and value[2] != parent for value in seen)
        assert local.get() == "부모 변경"
    finally:
        local.reset(token)


@pytest.mark.parametrize("reserved", [False, True])
def test_call_count_budget_is_atomic_under_barrier_contention(reserved):
    pool = CallBudgetPool(RACE_CALL_LIMIT)
    barrier = threading.Barrier(RACERS)

    def compete():
        lease = CallLease()
        barrier.wait(timeout=WAIT_SECONDS)
        if reserved and not pool.try_reserve(1, lease):
            return False
        try:
            return pool.consume(lease)
        finally:
            pool.release(lease)

    futures = [start_thread(compete) for _ in range(RACERS)]
    assert sum(future.result(timeout=WAIT_SECONDS) for future in futures) == RACE_CALL_LIMIT
    assert pool.made == RACE_CALL_LIMIT
    assert pool.unreserved == 0
    assert not pool.available(CallLease())


def test_unused_reservations_return_without_inflating_actual_calls():
    pool = CallBudgetPool(3)
    owner, outsider = CallLease(), CallLease()
    assert pool.try_reserve(3, owner)
    assert pool.made == 0 and pool.unreserved == 0
    assert not pool.try_reserve(1, outsider)
    assert not pool.consume(outsider)
    assert pool.consume(owner)
    pool.release(owner)
    pool.release(owner)
    assert owner.reserved == 0 and pool.made == 1 and pool.unreserved == 2
    assert pool.try_reserve(2, outsider)
    assert pool.consume(outsider) and pool.consume(outsider)
    pool.release(outsider)
    assert pool.made == 3 and pool.unreserved == 0


def test_article_success_returns_unused_fallback_capacity():
    pool, lease = CallBudgetPool(2), CallLease()
    assert pool.try_reserve(2, lease)
    shared = job(lambda url: BODY, budget=pool)
    result = fetch_article_body(shared, candidate(portal=True), lease)
    assert result.calls_made == pool.made == 1
    assert lease.reserved == 0 and pool.unreserved == 1


def test_competing_article_fallbacks_share_actual_call_cap(lanes):
    lane = lanes(3)
    barrier = threading.Barrier(3)
    pool = CallBudgetPool(3)
    calls = []

    def fetch(url):
        calls.append(url)
        barrier.wait(timeout=WAIT_SECONDS)
        return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_TIMEOUT)

    shared = job(fetch, lane=lane, budget=pool)
    items = [candidate(number, host=f"cap{number}.media.example", portal=True) for number in range(3)]
    futures = [lane.submit(lambda item=item: fetch_article_body(shared, item, CallLease())) for item in items]
    outcomes = [future.result(timeout=WAIT_SECONDS) for future in futures]
    assert set(calls) == {item.originallink for item in items}
    assert all(outcome.budget_exhausted and outcome.calls_made == 1 for outcome in outcomes)
    assert pool.made == 3 and pool.unreserved == 0


@pytest.mark.parametrize("first_result,excluded,failure", [
    (NewsBodyFetchResult(reason_code="fetch_http_403"), "", "fetch_http_403"),
    (NewsBodyFetchResult(reason_code="알수없는실패"), "", c.EXCLUDED_FETCH_FAILED),
    (NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_META_DESCRIPTION), "metadata_only_not_body", ""),
    (NewsBodyFetchResult(text="<article>" + BODY + "</article>", stage=c.BODY_STAGE_PROVIDED), "unparsed_html_not_body", ""),
    (NewsBodyFetchResult(text="\ufffd" * c.GROUNDED_MIN_EXCERPT_CHARS, stage=c.BODY_STAGE_PROVIDED), "", c.EXCLUDED_FETCH_DECODE_ERROR),
    (NewsBodyFetchResult(text="짧은 본문", stage=c.BODY_STAGE_PROVIDED), "body_too_short", ""),
    (NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_PROVIDED, effective_url="https://media.example.evil.test/story"), "untrusted_effective_url", ""),
    (NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_PROVIDED, published_on="날짜오류"), "invalid_body_published_date", ""),
    (NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_PROVIDED, published_on="2026-09-23"), "body_published_outside_window", ""),
    (NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_PROVIDED, published_on="2023-09-21"), "body_published_outside_window", ""),
], ids=["http403", "unknown_failure", "meta", "html", "decode", "short", "untrusted_redirect", "invalid_date", "future_date", "old_date"])
def test_rejected_original_falls_back_to_portal_preserving_reason_and_metadata(first_result, excluded, failure):
    item = candidate(portal=True)
    calls = []

    def fetch(url):
        calls.append(url)
        if url == item.originallink:
            return first_result
        return NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_ARTICLE_TAG, published_on=BODY_DATE)

    shared = job(fetch)
    result = fetch_article_body(shared, item, CallLease())
    assert calls == [item.originallink, item.link]
    assert result.full_body == BODY and result.stage == c.BODY_STAGE_ARTICLE_TAG
    assert result.candidate == item
    assert result.read_candidate.source_url == PORTAL_URL
    assert result.read_candidate.published_on == BODY_DATE
    assert result.read_candidate.published_on_source == "article_metadata"
    assert result.read_candidate.publisher == item.publisher
    assert result.excluded == Counter({excluded: 1} if excluded else {})
    assert result.article_failures == ((failure,) if failure else ())
    assert result.warnings == Counter({"search_body_date_corrected": 1, **({failure: 1} if failure else {})})
    assert result.calls_made == result.urls_examined == shared.budget.made == 2


def test_failed_original_exception_keeps_search_date_on_portal_success():
    item = candidate(portal=True)
    calls = []

    def fetch(url):
        calls.append(url)
        if url == item.originallink:
            raise OSError("시험용 원문 연결 실패")
        return BODY

    result = fetch_article_body(job(fetch), item, CallLease())
    assert calls == [item.originallink, item.link]
    assert result.read_candidate.published_on == SEARCH_DATE
    assert result.read_candidate.published_on_source == "search_index"
    assert result.warnings == Counter({c.EXCLUDED_FETCH_FAILED: 1})


@pytest.mark.parametrize("published", ["2023-09-22", "2025-09-22", "2026-09-22"])
def test_valid_date_boundaries_and_effective_original_are_preserved(published):
    effective = "https://media.example/canonical"
    result = fetch_article_body(job(lambda url: NewsBodyFetchResult(
        text=BODY, stage=c.BODY_STAGE_JSON_LD, effective_url=effective, published_on=published,
    )), candidate(), CallLease())
    assert result.full_body == BODY
    assert result.read_candidate.source_url == effective
    assert result.read_candidate.published_on == published
    assert result.read_candidate.published_on_source == "article_metadata"


@pytest.mark.parametrize("category", ["", "official_release"])
def test_portal_does_not_invent_trusted_publisher(category):
    item = candidate(portal=True, host="untrusted.test", source_category=category)
    calls = []
    result = fetch_article_body(job(lambda url: calls.append(url) or BODY), item, CallLease())
    assert calls == []
    assert result.full_body == "" and result.read_candidate is None
    assert result.excluded == Counter({"untrusted_body_url": 2})
    assert result.calls_made == 0 and result.urls_examined == 2


def test_untrusted_original_is_skipped_without_spending_portal_call():
    item = candidate(portal=True, host="untrusted.test")
    calls = []
    shared = job(lambda url: calls.append(url) or BODY, budget=CallBudgetPool(1))
    result = fetch_article_body(shared, item, CallLease())
    assert calls == [PORTAL_URL]
    assert result.full_body == BODY
    assert result.excluded == Counter({"untrusted_body_url": 1})
    assert result.calls_made == shared.budget.made == 1


@pytest.mark.parametrize("boundary", ["stop", "deadline", "budget"])
def test_preflight_boundary_prevents_any_transmission_and_returns_reservation(boundary):
    calls = []
    pool, lease = CallBudgetPool(0 if boundary == "budget" else 2), CallLease()
    if boundary != "budget":
        assert pool.try_reserve(2, lease)
    shared = job(lambda url: calls.append(url) or BODY, budget=pool)
    if boundary == "stop":
        shared.stop_event.set()
    if boundary == "deadline":
        shared = replace(shared, clock=lambda: DEADLINE)
    result = fetch_article_body(shared, candidate(portal=True), lease)
    assert calls == [] and pool.made == 0 and lease.reserved == 0
    assert result.calls_made == result.urls_examined == 0
    assert result.stop_requested == (boundary == "stop")
    assert result.budget_exhausted == (boundary != "stop")
    assert pool.unreserved == (0 if boundary == "budget" else 2)


@pytest.mark.parametrize("boundary", ["stop", "deadline", "budget"])
def test_boundary_is_rechecked_after_waiting_for_host_slot(lanes, boundary):
    lane = lanes(2)
    item = candidate(portal=True)
    attempted = threading.Event()
    calls = []
    now = [0.0]
    pool = CallBudgetPool(1)

    class ObservedSlots(HostSlots):
        @contextmanager
        def slot(self, url):
            attempted.set()
            with super().slot(url):
                yield

    slots = ObservedSlots(1)
    shared = job(lambda url: calls.append(url) or BODY, lane=lane, budget=pool,
                 host_slots=slots, clock=lambda: now[0])
    with slots.slot(item.originallink):
        attempted.clear()
        pending = lane.submit(lambda: fetch_article_body(shared, item, CallLease()))
        wait_for(attempted)
        if boundary == "stop":
            lane.request_stop()
        elif boundary == "deadline":
            now[0] = DEADLINE
        else:
            assert pool.consume(CallLease())
    result = pending.result(timeout=WAIT_SECONDS)
    assert calls == [] and result.calls_made == 0
    assert result.stop_requested == (boundary == "stop")
    assert result.budget_exhausted == (boundary != "stop")


@pytest.mark.parametrize("failed", [False, True])
def test_stop_during_active_fetch_prevents_followup_portal_transmission(lanes, failed):
    lane = lanes(2)
    entered, release = threading.Event(), lanes.gate()
    calls = []
    pool, lease = CallBudgetPool(2), CallLease()
    assert pool.try_reserve(2, lease)

    def fetch(url):
        calls.append(url)
        entered.set()
        wait_for(release)
        if failed:
            raise OSError("중단 직후 시험용 전송 실패")
        return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_TIMEOUT)

    item = candidate(portal=True)
    shared = job(fetch, lane=lane, budget=pool)
    pending = lane.submit(lambda: fetch_article_body(shared, item, lease))
    wait_for(entered)
    lane.request_stop()
    release.set()
    result = pending.result(timeout=WAIT_SECONDS)
    assert calls == [item.originallink]
    assert result.stop_requested and not result.budget_exhausted
    assert result.calls_made == pool.made == 1
    assert pool.unreserved == 1 and lease.reserved == 0


def test_fatal_fetch_releases_lease_and_host_without_fallback(lanes):
    class FatalFetch(BaseException):
        pass

    lane = lanes(2)
    item = candidate(portal=True)
    calls = []
    pool, lease = CallBudgetPool(2), CallLease()
    assert pool.try_reserve(2, lease)

    def fetch(url):
        calls.append(url)
        raise FatalFetch("시험용 치명 중단")

    shared = job(fetch, lane=lane, budget=pool)
    pending = lane.submit(lambda: fetch_article_body(shared, item, lease))
    with pytest.raises(FatalFetch):
        pending.result(timeout=WAIT_SECONDS)
    assert calls == [item.originallink]
    assert pool.made == 1 and pool.unreserved == 1 and lease.reserved == 0
    recovery = replace(shared, fetch_text=lambda url: BODY)
    next_result = lane.submit(lambda: fetch_article_body(recovery, candidate(1), CallLease()))
    assert next_result.result(timeout=WAIT_SECONDS).full_body == BODY


def test_cancelled_queued_future_never_calls_fetch_and_owner_returns_lease(lanes):
    lane = lanes(2)
    entered = threading.Barrier(3)
    release = lanes.gate()
    calls = []

    def occupy():
        entered.wait(timeout=WAIT_SECONDS)
        wait_for(release)
        return ArticleFetchOutcome(candidate())

    running = [lane.submit(occupy) for _ in range(2)]
    entered.wait(timeout=WAIT_SECONDS)
    pool, lease = CallBudgetPool(2), CallLease()
    assert pool.try_reserve(2, lease)
    shared = job(lambda url: calls.append(url) or BODY, lane=lane, budget=pool)
    queued = lane.submit(lambda: fetch_article_body(shared, candidate(portal=True), lease))
    assert queued.cancel()
    # 실행되지 않은 작업의 예약은 제출자가 반납한다. 모듈 작업의 finally는 실행되지 않는다.
    pool.release(lease)
    lane.request_stop()
    release.set()
    for future in running:
        future.result(timeout=WAIT_SECONDS)
    lane.close()
    assert queued.cancelled() and calls == []
    assert pool.made == 0 and pool.unreserved == 2 and lease.reserved == 0


def test_close_cancels_queued_work_before_waiting_for_running_work(lanes):
    lane = lanes(2)
    entered = threading.Barrier(3)
    release, cancelled = lanes.gate(), threading.Event()
    calls = []

    def occupy():
        entered.wait(timeout=WAIT_SECONDS)
        wait_for(release)
        return ArticleFetchOutcome(candidate())

    running = [lane.submit(occupy) for _ in range(2)]
    entered.wait(timeout=WAIT_SECONDS)
    shared = job(lambda url: calls.append(url) or BODY, lane=lane)
    queued = lane.submit(lambda: fetch_article_body(shared, candidate(), CallLease()))
    queued.add_done_callback(lambda future: cancelled.set())
    lane.request_stop()
    closing = start_thread(lane.close)
    wait_for(cancelled)
    assert queued.cancelled() and not closing.done()
    release.set()
    for future in running:
        future.result(timeout=WAIT_SECONDS)
    closing.result(timeout=WAIT_SECONDS)
    assert calls == [] and shared.budget.made == 0


@pytest.mark.parametrize("width,per_host", [(0, 1), (4, 1), (True, 1), (2.0, 1), (2, 0), (2, True), (2, 3), (1, 2)])
def test_invalid_concurrency_never_creates_a_lane(width, per_host):
    with pytest.raises(ValueError):
        BodyFetchConcurrency(width, per_host)


@pytest.mark.parametrize("width", [None, 1, 2, 3])
def test_closed_lane_rejects_new_io_and_close_is_idempotent(lanes, width):
    lane = lanes(width)
    calls = []
    lane.close()
    lane.close()
    with pytest.raises(RuntimeError, match="종료된 본문 수집 실행기"):
        lane.submit(lambda: calls.append("전송"))
    assert calls == [] and lane.stop_event.is_set()
    assert not lane.concurrent


def test_close_itself_stops_active_fetch_before_portal_fallback(lanes):
    lane = lanes(2)
    entered, release = threading.Event(), lanes.gate()
    calls = []

    def fetch(url):
        calls.append(url)
        entered.set()
        wait_for(release)
        return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_TIMEOUT)

    item = candidate(portal=True)
    shared = job(fetch, lane=lane)
    pending = lane.submit(lambda: fetch_article_body(shared, item, CallLease()))
    wait_for(entered)
    closing = start_thread(lane.close)
    wait_for(lane.stop_event)
    release.set()
    result = pending.result(timeout=WAIT_SECONDS)
    closing.result(timeout=WAIT_SECONDS)
    assert calls == [item.originallink] and result.stop_requested


@pytest.mark.parametrize("error_type", [GenerationWaitCancelled, EngineBuildIdentityChangedError, CancelledError])
def test_request_stop_error_is_not_swallowed_as_article_failure(lanes, error_type):
    lane = lanes(2)
    item = candidate(portal=True)
    calls = []
    error = error_type("시험용 요청 전체 중단")

    def fetch(url):
        calls.append(url)
        raise error

    pool, lease = CallBudgetPool(2), CallLease()
    assert pool.try_reserve(2, lease)
    shared = job(fetch, lane=lane, budget=pool)
    pending = lane.submit(lambda: fetch_article_body(shared, item, lease))
    with pytest.raises(error_type) as caught:
        pending.result(timeout=WAIT_SECONDS)
    assert caught.value is error
    assert calls == [item.originallink] and lease.reserved == 0
