"""한 프로세스가 끝까지 운반하는 조사 엔진 모드 계약."""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final


ENGINE_V2_ENV_NAME: Final[str] = "ENGINE_V2"
ENGINE_V2_ENV_ON: Final[str] = "1"


class EngineMode(Enum):
    """캐시 열쇠·조회·생성·저장이 함께 쓰는 정확한 엔진 모드."""

    V1 = "v1"
    V2 = "v2"


class EngineModeChangedError(RuntimeError):
    """이미 시작한 프로세스의 엔진 모드를 바꾸려 했다."""


_PROCESS_MODE_LOCK = threading.RLock()
_PROCESS_MODE: EngineMode | None = None


def require_exact_engine_mode(mode: EngineMode) -> EngineMode:
    """문자열·bool·속성만 닮은 객체를 엔진 모드로 받지 않는다."""

    if type(mode) is not EngineMode:
        raise TypeError("정확한 EngineMode 값이 필요합니다")
    return mode


def _capture_engine_mode_from_environment() -> EngineMode:
    """bootstrap 전용 raw 환경 읽기. 요청 본체에서는 호출하지 않는다."""

    return (
        EngineMode.V2
        if os.environ.get(ENGINE_V2_ENV_NAME) == ENGINE_V2_ENV_ON
        else EngineMode.V1
    )


def freeze_process_engine_mode(mode: EngineMode | None = None) -> EngineMode:
    """최초 bootstrap/요청에서 모드를 딱 한 번 정하고 영구 동결한다."""

    global _PROCESS_MODE
    with _PROCESS_MODE_LOCK:
        # 인자를 생략한 재호출은 raw 환경을 다시 보지 않는다. 최초 요청끼리
        # 경합해도 capture를 lock 안에서 해서 서로 다른 값을 만들 틈이 없다.
        if mode is None and _PROCESS_MODE is not None:
            return _PROCESS_MODE
        candidate = (
            _capture_engine_mode_from_environment()
            if mode is None
            else require_exact_engine_mode(mode)
        )
        if _PROCESS_MODE is None:
            _PROCESS_MODE = candidate
        elif _PROCESS_MODE is not candidate:
            raise EngineModeChangedError(
                "이미 동결된 프로세스 엔진 모드를 바꿀 수 없습니다"
            )
        return _PROCESS_MODE


def process_engine_mode() -> EngineMode:
    """프로세스 시작 때 동결한 모드만 돌려준다."""

    with _PROCESS_MODE_LOCK:
        current = _PROCESS_MODE
    return freeze_process_engine_mode() if current is None else current


def assert_engine_mode_current(mode: EngineMode) -> EngineMode:
    """요청이 운반한 exact 모드가 현재 process 정본과 같은지 확인한다."""

    exact = require_exact_engine_mode(mode)
    current = process_engine_mode()
    if current is not exact:
        raise EngineModeChangedError(
            "요청의 엔진 모드와 현재 프로세스 엔진 모드가 다릅니다"
        )
    return current


def _reset_process_engine_mode_for_tests() -> None:
    """pytest process 재시작 모사 전용. production에서는 호출하지 않는다."""

    global _PROCESS_MODE
    with _PROCESS_MODE_LOCK:
        _PROCESS_MODE = None
