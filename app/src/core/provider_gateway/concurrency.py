"""프로세스의 공급자 호출 자리를 FIFO로 배분한다."""

from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Final

from src.core.constants import MAX_CONCURRENT_RUNS


PROVIDER_MAX_CONCURRENT_CALLS_ENV: Final[str] = "PROVIDER_MAX_CONCURRENT_CALLS"
DEFAULT_PROVIDER_MAX_CONCURRENT_CALLS: Final[int] = MAX_CONCURRENT_RUNS
PROVIDER_ADMISSION_POLL_SECONDS: Final[float] = 0.25
SEQUENTIAL_PROVIDER_CALLS: Final[int] = 1


def configured_limit() -> int:
    """기존 서버 용량을 넘기지 않으며 1로 즉시 순차 복귀할 수 있다."""
    raw = os.getenv(PROVIDER_MAX_CONCURRENT_CALLS_ENV)
    if raw is None:
        return DEFAULT_PROVIDER_MAX_CONCURRENT_CALLS
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return SEQUENTIAL_PROVIDER_CALLS
    if not SEQUENTIAL_PROVIDER_CALLS <= value <= DEFAULT_PROVIDER_MAX_CONCURRENT_CALLS:
        return SEQUENTIAL_PROVIDER_CALLS
    return value


class ProviderCallLimiter:
    """대기열 변경만 잠그고 공급자 통신은 허용 자리 수만큼 겹쳐 실행한다."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._waiters: deque[object] = deque()
        self._active = 0

    @contextmanager
    def slot(self, *, check: Callable[[], None]) -> Iterator[None]:
        token = object()
        admitted = False
        with self._condition:
            self._waiters.append(token)
        try:
            while not admitted:
                # 취소·lease·미확정 비용 검사는 전역 잠금 밖에서 수행한다.
                check()
                with self._condition:
                    if self._waiters[0] is token and self._active < configured_limit():
                        self._waiters.popleft()
                        self._active += 1
                        admitted = True
                        self._condition.notify_all()
                    else:
                        self._condition.wait(PROVIDER_ADMISSION_POLL_SECONDS)
            check()
            yield
        finally:
            with self._condition:
                if admitted:
                    self._active -= 1
                else:
                    self._waiters.remove(token)
                self._condition.notify_all()


# 요청마다 생성하면 보고서 수만큼 상한이 곱해지므로 프로세스에서 하나만 쓴다.
PIPELINE_PROVIDER_LIMITER = ProviderCallLimiter()
