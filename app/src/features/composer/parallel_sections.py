"""독립 장 작업을 제한된 수만 실행하고 목차 순서로 합친다."""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import TypeVar

from src.features.composer.parallel_section_constants import (
    MAX_PARALLEL_SECTION_CALLS,
    SERIAL_SECTION_CALLS,
    SECTION_THREAD_NAME_PREFIX,
)

_Result = TypeVar("_Result")


def section_worker_count(ask: Callable) -> int:
    """호출 경계가 명시적으로 보증한 동시성만 허용한다."""
    workers = getattr(ask, "max_parallel_calls", SERIAL_SECTION_CALLS)
    if getattr(ask, "parallel_safe", False) is not True or type(workers) is not int:
        return SERIAL_SECTION_CALLS
    return max(SERIAL_SECTION_CALLS, min(workers, MAX_PARALLEL_SECTION_CALLS))


def run_section_jobs(
    jobs: Sequence[Callable[[], _Result]], *, max_workers: int,
) -> list[_Result]:
    """완료 순서와 무관하게 합치며 치명적 오류 후 새 작업은 보내지 않는다.

    이미 진행 중인 호출은 모두 정산될 때까지 합류한다. 각 작업은 독립된
    Context를 받지만, 그 안의 가변 예약 장부는 공급자 경계에서 보호해야 한다.
    """
    if type(max_workers) is not int or not SERIAL_SECTION_CALLS <= max_workers <= MAX_PARALLEL_SECTION_CALLS:
        raise ValueError("장별 작성 동시 실행 수가 허용 범위를 벗어났습니다")
    if max_workers == SERIAL_SECTION_CALLS:
        return [job() for job in jobs]
    results: dict[int, _Result] = {}
    iterator = iter(enumerate(jobs))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=SECTION_THREAD_NAME_PREFIX) as pool:
        pending = {}

        def submit_next() -> bool:
            item = next(iterator, None)
            if item is None:
                return False
            index, job = item
            future = pool.submit(contextvars.copy_context().run, job)
            pending[future] = index
            return True

        for _ in range(min(max_workers, len(jobs))):
            submit_next()
        try:
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                # 같은 관측 묶음에 실패가 있으면 후속 유료 호출보다 먼저 전파한다.
                for future in sorted(completed, key=pending.__getitem__):
                    results[pending[future]] = future.result()
                for future in completed:
                    del pending[future]
                for _ in completed:
                    if not submit_next():
                        break
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return [results[index] for index in range(len(jobs))]
