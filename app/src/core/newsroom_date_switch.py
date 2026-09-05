"""뉴스룸 날짜 AI 예비 단계를 운영에서 켜고 끄는 프로세스 동결 스위치.

프로그램 날짜 판정은 항상 켜져 있고 비용이 드는 AI 예비 단계만 이 스위치가
막는다. 값을 요청마다 다시 읽으면 한 프로세스의 수집 결과가 환경 변경 시점에
따라 갈리므로 최초 조회에서 정확히 한 번 동결한다.
"""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final


NEWSROOM_DATE_AI_ENV_NAME: Final[str] = "NEWSROOM_DATE_AI"
NEWSROOM_DATE_AI_ENV_ON: Final[str] = "1"


class NewsroomDateSwitch(Enum):
    """뉴스룸 날짜 AI 예비 단계의 정확한 스위치 값."""

    OFF = "off"
    ON = "on"


class NewsroomDateSwitchChangedError(RuntimeError):
    """이미 시작한 프로세스의 뉴스룸 날짜 스위치를 바꾸려 했다."""


_PROCESS_SWITCH_LOCK = threading.RLock()
_PROCESS_SWITCH: NewsroomDateSwitch | None = None


def require_exact_newsroom_date_switch(
    switch: NewsroomDateSwitch,
) -> NewsroomDateSwitch:
    """문자열·bool·속성만 닮은 객체를 스위치 값으로 받지 않는다."""

    if type(switch) is not NewsroomDateSwitch:
        raise TypeError("정확한 NewsroomDateSwitch 값이 필요합니다")
    return switch


def _capture_newsroom_date_switch_from_environment() -> NewsroomDateSwitch:
    """bootstrap 전용 raw 환경 읽기. 동결 lock 안에서만 호출한다."""

    return (
        NewsroomDateSwitch.ON
        if os.environ.get(NEWSROOM_DATE_AI_ENV_NAME) == NEWSROOM_DATE_AI_ENV_ON
        else NewsroomDateSwitch.OFF
    )


def freeze_process_newsroom_date_switch(
    switch: NewsroomDateSwitch | None = None,
) -> NewsroomDateSwitch:
    """최초 조회 값을 프로세스 수명 동안 동결한다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        if switch is None and _PROCESS_SWITCH is not None:
            return _PROCESS_SWITCH
        candidate = (
            _capture_newsroom_date_switch_from_environment()
            if switch is None
            else require_exact_newsroom_date_switch(switch)
        )
        if _PROCESS_SWITCH is None:
            _PROCESS_SWITCH = candidate
        elif _PROCESS_SWITCH is not candidate:
            raise NewsroomDateSwitchChangedError(
                "이미 동결된 뉴스룸 날짜 AI 스위치를 바꿀 수 없습니다"
            )
        return _PROCESS_SWITCH


def process_newsroom_date_switch() -> NewsroomDateSwitch:
    """프로세스가 동결한 스위치 값. 아직 없으면 지금 동결한다."""

    with _PROCESS_SWITCH_LOCK:
        current = _PROCESS_SWITCH
    return freeze_process_newsroom_date_switch() if current is None else current


def frozen_newsroom_date_switch() -> NewsroomDateSwitch | None:
    """환경을 읽거나 동결하지 않고 현재 값만 본다."""

    with _PROCESS_SWITCH_LOCK:
        return _PROCESS_SWITCH


def newsroom_date_ai_enabled() -> bool:
    """이 프로세스에서 뉴스룸 날짜 AI 예비 단계를 써도 되는가."""

    return process_newsroom_date_switch() is NewsroomDateSwitch.ON


def _reset_process_newsroom_date_switch_for_tests() -> None:
    """pytest process 재시작 모사 전용. production에서는 호출하지 않는다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        _PROCESS_SWITCH = None
