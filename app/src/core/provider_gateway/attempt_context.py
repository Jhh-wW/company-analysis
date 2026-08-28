"""paid runtime이 저수준 provider 경계에 주입하는 요청 로컬 callback."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from src.core.provider_gateway.types import ProviderObservation


class ProviderAttemptContextUnavailable(RuntimeError):
    """유료 provider 호출에 영속 attempt callback이 설치되지 않음."""


@dataclass(frozen=True)
class ProviderAttemptCallbacks:
    """budget·web을 import하지 않고 attempt 상태를 영속화하는 주입 경계."""

    begin_attempt: Callable[[str, str, float], Any]
    heartbeat: Callable[[Any], None]
    mark_dispatch_intent: Callable[[Any], None]
    record_observation: Callable[[Any, ProviderObservation], None]


_CURRENT: contextvars.ContextVar[ProviderAttemptCallbacks | None] = (
    contextvars.ContextVar("provider_attempt_callbacks", default=None)
)


@contextlib.contextmanager
def activate(callbacks: ProviderAttemptCallbacks) -> Iterator[ProviderAttemptCallbacks]:
    """한 유료 phase 실행 문맥에 DB callback 묶음을 설치한다."""
    if not isinstance(callbacks, ProviderAttemptCallbacks):
        raise TypeError("provider attempt callback 형식이 올바르지 않습니다")
    token = _CURRENT.set(callbacks)
    try:
        yield callbacks
    finally:
        _CURRENT.reset(token)


def current() -> ProviderAttemptCallbacks:
    callbacks = _CURRENT.get()
    if callbacks is None:
        raise ProviderAttemptContextUnavailable(
            "유료 provider 호출 전에 attempt 기록 문맥이 설치되지 않았습니다"
        )
    return callbacks
