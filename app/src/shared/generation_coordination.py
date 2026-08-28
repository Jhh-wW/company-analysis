"""웹 실행기와 조사 pipeline 사이의 요청 로컬 단일 실행 계약.

pipeline은 DB나 billing bucket을 알지 못하고, 웹 실행기는 DART 출처
snapshot을 알지 못한다. 두 계층이 서로를 import하지 않고 요청 한 건의
callback만 주입하도록 이 작은 계약을 shared에 둔다.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from src.shared.generation_cache_identity import GenerationCacheNamespace


class GenerationCoordinationError(RuntimeError):
    """중복 과금을 막는 조정 계약을 지킬 수 없다."""


class GenerationOwnerFailed(GenerationCoordinationError):
    """먼저 시작한 생성이 실패해 waiter에게도 같은 실패를 전파한다."""


class GenerationWaitCancelled(GenerationCoordinationError):
    """대기자의 요청이 취소됐고 owner의 작업은 건드리지 않는다."""


class GenerationWaitTimedOut(GenerationCoordinationError):
    """대기 총한도를 넘겼고 owner의 작업은 건드리지 않는다."""


class GenerationExecutionDeadlineExceeded(GenerationCoordinationError):
    """한 owner가 가질 수 있는 유한한 실행 시간을 모두 썼다."""


@dataclass(frozen=True)
class ReusedGeneration:
    """owner가 완료한 불변 content를 waiter가 읽을 때의 결과."""

    content_snapshot_id: str
    artifact_id: str
    report: Any
    actual_models: tuple[str, ...] = ()
    #: 정식 장기 캐시에서 온 결과인지, 동시 waiter에게만 짧게 공유한 결과인지.
    generation_cache_eligible: bool = False


@dataclass(frozen=True)
class GenerationCallbacks:
    """paid 요청 하나에만 설치되는 조정 callback."""

    coordinate: Callable[
        [str, GenerationCacheNamespace | None, str],
        ReusedGeneration | None,
    ]
    ensure_paid_phase: Callable[[], None]


_CURRENT: contextvars.ContextVar[GenerationCallbacks | None] = (
    contextvars.ContextVar("generation_coordination_callbacks", default=None)
)


def is_active() -> bool:
    """현재 실제 paid 경로가 불변 cache/lease 계약을 주입했는가."""

    return _CURRENT.get() is not None


@contextlib.contextmanager
def activate(callbacks: GenerationCallbacks) -> Iterator[GenerationCallbacks]:
    """현재 paid 작업에 통장·lease·지연 예약 callback을 설치한다."""

    if not isinstance(callbacks, GenerationCallbacks):
        raise TypeError("보고서 단일 실행 callback 형식이 올바르지 않습니다")
    token = _CURRENT.set(callbacks)
    try:
        yield callbacks
    finally:
        _CURRENT.reset(token)


def coordinate(
    *,
    corp_id: str,
    cache_namespace: GenerationCacheNamespace | None,
    preflight_identity_digest: str,
) -> ReusedGeneration | None:
    """완전한 사전 신원이면 캐시를 읽거나 owner를 정한다.

    callback이 없는 demo·단위시험은 예전처럼 독립 실행한다. paid 실제
    경로에서는 웹 실행기가 반드시 callback을 설치한다.
    """

    callbacks = _CURRENT.get()
    if callbacks is None:
        return None
    return callbacks.coordinate(
        str(corp_id),
        cache_namespace,
        str(preflight_identity_digest),
    )


def ensure_paid_phase() -> None:
    """첫 provider 호출 직전에 owner만 유료 phase를 예약하게 한다."""

    callbacks = _CURRENT.get()
    if callbacks is not None:
        callbacks.ensure_paid_phase()
