"""병렬 본문 작업의 HTTP 캐시 격리와 절대 마감 보존."""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.features.homepage import safe_http


WAIT_SECONDS = 5


@pytest.mark.parametrize("timeout,expected", [(2, 104.0), (30, 110.0)])
def test_worker_cache_is_independent_and_deadline_can_only_shorten(timeout, expected):
    now = [100.0]
    barrier = threading.Barrier(2)
    with safe_http.collection_cache_scope() as cache:
        with safe_http.request_deadline_scope(10, clock=lambda: now[0]) as parent:
            cache.dns_cache[("parent.example", 443)] = ()
            marker = object()
            cache.robots_cache["parent"] = marker
            now[0] = 102.0

            def worker(index):
                with safe_http.isolated_request_scope(timeout) as child:
                    assert child is not parent and child.clock is parent.clock
                    assert child.expires_at == expected
                    assert child.dns_cache == parent.dns_cache
                    assert child.robots_cache["parent"] is marker
                    child.dns_cache[(str(index), 443)] = ()
                    child.robots_cache[str(index)] = marker
                    barrier.wait(WAIT_SECONDS)
                    assert str(1 - index) not in child.robots_cache
                    with safe_http.request_deadline_scope(1000, clock=child.clock) as nested:
                        assert nested is child
                    return child

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(contextvars.copy_context().run, worker, index) for index in range(2)]
                children = [future.result(WAIT_SECONDS) for future in futures]
            assert safe_http.active_deadline_budget() is parent
            assert parent.expires_at == 110.0
            assert len(parent.dns_cache) == len(parent.robots_cache) == 1
            assert children[0].dns_cache is not children[1].dns_cache


def test_expired_parent_cannot_be_revived_and_context_is_restored():
    now = [100.0]
    with safe_http.collection_cache_scope() as cache:
        with safe_http.request_deadline_scope(1, clock=lambda: now[0]) as parent:
            now[0] = 102.0
            with pytest.raises(safe_http.HomepageResponseError):
                with safe_http.isolated_request_scope(30):
                    pytest.fail("만료된 문맥을 다시 시작했습니다")
            assert safe_http.active_deadline_budget() is parent
            with safe_http.collection_cache_scope() as restored:
                assert restored is cache


def test_worker_failure_restores_parent_scope():
    with safe_http.collection_cache_scope() as cache:
        with safe_http.request_deadline_scope(30) as parent:
            with pytest.raises(RuntimeError):
                with safe_http.isolated_request_scope(5) as child:
                    child.robots_cache["worker"] = object()
                    raise RuntimeError("시험용 작업 실패")
            assert safe_http.active_deadline_budget() is parent
            assert not cache.robots_cache


def test_completed_decisions_are_copied_to_next_worker_without_parent_mutation():
    completed = safe_http.IsolatedRequestCache()
    with safe_http.collection_cache_scope() as parent:
        with safe_http.isolated_request_scope(5, completed=completed) as first:
            marker = object()
            first.robots_cache["https://media.example"] = marker
            first.dns_cache[("media.example", 443)] = ()
        with safe_http.isolated_request_scope(5, completed=completed) as second:
            assert second.robots_cache["https://media.example"] is marker
            assert second.dns_cache == first.dns_cache
            assert second.robots_cache is not first.robots_cache
            assert second.dns_cache is not first.dns_cache
        assert not parent.dns_cache and not parent.robots_cache


def test_cache_isolation_alone_keeps_per_transport_deadlines_and_restores_context():
    now = [100.0]
    assert safe_http.active_robots_cache() is None
    with safe_http.isolated_request_scope() as budget:
        assert budget is None and safe_http.active_deadline_budget() is None
        cache = safe_http.active_robots_cache()
        assert cache is not None
        first = safe_http._DeadlineBudget.after(10, clock=lambda: now[0])
        now[0] += 8
        second = safe_http._DeadlineBudget.after(10, clock=lambda: now[0])
        now[0] += 8
        assert first.expires_at == 110 and second.remaining() == 2
        assert first.robots_cache is second.robots_cache is cache
    assert safe_http.active_robots_cache() is None


def test_cache_isolation_without_new_limit_preserves_existing_parent_deadline():
    now = [100.0]
    with safe_http.request_deadline_scope(30, clock=lambda: now[0]) as parent:
        now[0] += 8
        with safe_http.isolated_request_scope() as child:
            assert child is not parent
            assert child.expires_at == parent.expires_at == 130
            assert child.clock is parent.clock
            assert child.robots_cache is not parent.robots_cache
