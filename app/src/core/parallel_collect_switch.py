"""경쟁사 비교와 뉴스 검색 스냅샷을 동시에 돌릴지 정하는 프로세스 동결 스위치.

값을 요청마다 다시 읽으면 같은 보고서의 두 갈래가 서로 다른 실행 순서를 볼 수
있다. 최초 조회에서 정확히 ``"1"``만 켜고 프로세스 수명 동안 동결한다.

★ 이 스위치는 «순서»만 바꾼다 — 수집 호출 수·근거 수·검증 관문은 켜든 끄든
  같다. 다만 비교 갈래가 실패하는 실행에서는 차이가 하나 생긴다. 차례로 돌 때는
  비교가 먼저 멈춰 뉴스 검색까지 가지 않지만, 동시에 돌 때는 이미 떠난 뉴스 검색
  호출을 되돌릴 수 없다. 즉 «실패한 실행에서 외부 검색 호출이 더 나갈 수 있다»가
  이 스위치가 만드는 유일한 호출 증가 지점이다.
"""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final


PARALLEL_COLLECT_ENV_NAME: Final[str] = "PARALLEL_COLLECT"
PARALLEL_COLLECT_ENV_ON: Final[str] = "1"


class ParallelCollectSwitch(Enum):
    """비교·뉴스 검색 동시 실행 선택에 쓰는 정확한 스위치 값."""

    OFF = "off"
    ON = "on"


class ParallelCollectSwitchChangedError(RuntimeError):
    """이미 시작한 프로세스의 동시 수집 스위치를 바꾸려 했다."""


_PROCESS_SWITCH_LOCK = threading.RLock()
_PROCESS_SWITCH: ParallelCollectSwitch | None = None


def require_exact_parallel_collect_switch(
    switch: ParallelCollectSwitch,
) -> ParallelCollectSwitch:
    """문자열·bool·속성만 닮은 객체를 스위치 값으로 받지 않는다."""

    if type(switch) is not ParallelCollectSwitch:
        raise TypeError("정확한 ParallelCollectSwitch 값이 필요합니다")
    return switch


def _capture_parallel_collect_switch_from_environment() -> ParallelCollectSwitch:
    """bootstrap 전용 raw 환경 읽기. 동결 lock 안에서만 호출한다."""

    return (
        ParallelCollectSwitch.ON
        if os.environ.get(PARALLEL_COLLECT_ENV_NAME) == PARALLEL_COLLECT_ENV_ON
        else ParallelCollectSwitch.OFF
    )


def freeze_process_parallel_collect_switch(
    switch: ParallelCollectSwitch | None = None,
) -> ParallelCollectSwitch:
    """최초 조회에서 값을 한 번 정하고 프로세스 수명 동안 동결한다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        if switch is None and _PROCESS_SWITCH is not None:
            return _PROCESS_SWITCH
        candidate = (
            _capture_parallel_collect_switch_from_environment()
            if switch is None
            else require_exact_parallel_collect_switch(switch)
        )
        if _PROCESS_SWITCH is None:
            _PROCESS_SWITCH = candidate
        elif _PROCESS_SWITCH is not candidate:
            raise ParallelCollectSwitchChangedError(
                "이미 동결된 동시 수집 스위치를 바꿀 수 없습니다"
            )
        return _PROCESS_SWITCH


def process_parallel_collect_switch() -> ParallelCollectSwitch:
    """프로세스가 동결한 값. 아직 없으면 지금 동결한다."""

    with _PROCESS_SWITCH_LOCK:
        current = _PROCESS_SWITCH
    return (
        freeze_process_parallel_collect_switch() if current is None else current
    )


def frozen_parallel_collect_switch() -> ParallelCollectSwitch | None:
    """환경을 읽거나 값을 동결하지 않고 현재 값만 돌려준다."""

    with _PROCESS_SWITCH_LOCK:
        return _PROCESS_SWITCH


def enabled() -> bool:
    """비교·뉴스 검색을 이 프로세스에서 동시에 돌려도 되는가."""

    return process_parallel_collect_switch() is ParallelCollectSwitch.ON


def _reset_process_parallel_collect_switch_for_tests() -> None:
    """pytest process 재시작 모사 전용. production에서는 호출하지 않는다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        _PROCESS_SWITCH = None
