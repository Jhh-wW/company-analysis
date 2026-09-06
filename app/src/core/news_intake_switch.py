"""언론 보조 근거 경로를 운영에서 켜고 끄는 프로세스 동결 스위치.

값을 요청마다 다시 읽으면 같은 보고서의 수집·인용·부록 단계가 서로 다른
정책을 볼 수 있다. 최초 조회에서 정확히 ``"1"``만 켜고 프로세스 수명 동안
동결한다.
"""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final


NEWS_INTAKE_ENV_NAME: Final[str] = "NEWS_INTAKE"
NEWS_INTAKE_ENV_ON: Final[str] = "1"


class NewsIntakeSwitch(Enum):
    """언론 보조 근거 경로 선택에 쓰는 정확한 스위치 값."""

    OFF = "off"
    ON = "on"


class NewsIntakeSwitchChangedError(RuntimeError):
    """이미 시작한 프로세스의 언론 근거 스위치를 바꾸려 했다."""


_PROCESS_SWITCH_LOCK = threading.RLock()
_PROCESS_SWITCH: NewsIntakeSwitch | None = None


def require_exact_news_intake_switch(switch: NewsIntakeSwitch) -> NewsIntakeSwitch:
    """문자열·bool·속성만 닮은 객체를 스위치 값으로 받지 않는다."""

    if type(switch) is not NewsIntakeSwitch:
        raise TypeError("정확한 NewsIntakeSwitch 값이 필요합니다")
    return switch


def _capture_news_intake_switch_from_environment() -> NewsIntakeSwitch:
    """bootstrap 전용 raw 환경 읽기. 동결 lock 안에서만 호출한다."""

    return (
        NewsIntakeSwitch.ON
        if os.environ.get(NEWS_INTAKE_ENV_NAME) == NEWS_INTAKE_ENV_ON
        else NewsIntakeSwitch.OFF
    )


def freeze_process_news_intake_switch(
    switch: NewsIntakeSwitch | None = None,
) -> NewsIntakeSwitch:
    """최초 조회에서 값을 한 번 정하고 프로세스 수명 동안 동결한다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        if switch is None and _PROCESS_SWITCH is not None:
            return _PROCESS_SWITCH
        candidate = (
            _capture_news_intake_switch_from_environment()
            if switch is None
            else require_exact_news_intake_switch(switch)
        )
        if _PROCESS_SWITCH is None:
            _PROCESS_SWITCH = candidate
        elif _PROCESS_SWITCH is not candidate:
            raise NewsIntakeSwitchChangedError(
                "이미 동결된 언론 근거 스위치를 바꿀 수 없습니다"
            )
        return _PROCESS_SWITCH


def process_news_intake_switch() -> NewsIntakeSwitch:
    """프로세스가 동결한 값. 아직 없으면 지금 동결한다."""

    with _PROCESS_SWITCH_LOCK:
        current = _PROCESS_SWITCH
    return freeze_process_news_intake_switch() if current is None else current


def frozen_news_intake_switch() -> NewsIntakeSwitch | None:
    """환경을 읽거나 값을 동결하지 않고 현재 값만 돌려준다."""

    with _PROCESS_SWITCH_LOCK:
        return _PROCESS_SWITCH


def news_intake_enabled() -> bool:
    """언론 보조 근거 경로를 이 프로세스에서 써도 되는가."""

    return process_news_intake_switch() is NewsIntakeSwitch.ON


def _reset_process_news_intake_switch_for_tests() -> None:
    """pytest process 재시작 모사 전용. production에서는 호출하지 않는다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        _PROCESS_SWITCH = None
