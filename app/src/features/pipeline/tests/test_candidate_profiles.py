"""주소 조회의 실제 병렬 폭·기한·요청 문맥 보존 시험."""

import contextvars
import threading
import time

import pytest

from src.features.pipeline.candidate_profiles import fetch_candidate_profiles
from src.features.pipeline.candidate_profile_constants import DART_PROFILE_WORKERS


def test_profile_fetch_preserves_context_order_and_concurrency_bound():
    request = contextvars.ContextVar("회사검색요청", default="")
    request.set("요청별사용량")
    barrier = threading.Barrier(DART_PROFILE_WORKERS)
    lock = threading.Lock()
    active = 0
    peak = 0

    def fetch(item):
        nonlocal active, peak
        assert request.get() == "요청별사용량"
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=3)
        with lock:
            active -= 1
        return str(item)

    rows = fetch_candidate_profiles(tuple(range(9)), fetch, deadline=time.monotonic() + 5)
    assert rows == [(i, str(i)) for i in range(9)]
    assert peak == DART_PROFILE_WORKERS


def test_expired_batch_stops_further_queries_and_partial_success():
    calls = []

    def fetch(item):
        calls.append(item)
        time.sleep(0.08)
        return item

    with pytest.raises(TimeoutError):
        fetch_candidate_profiles(tuple(range(9)), fetch, deadline=time.monotonic() + 0.03)
    assert 0 < len(calls) <= DART_PROFILE_WORKERS
    assert all(item < DART_PROFILE_WORKERS for item in calls)
