"""typed DART 수집기를 운영에서 켜고 끄는 프로세스 동결 스위치.

★ 왜 별도 스위치인가 — typed 수집기(``analysis_engine/src/features/
  evidence_collection``)는 자기 docstring으로 「실제 네트워크로 시험하지
  않았다(LIVE_COLLECTION_UNVERIFIED)」고 선언한 코드다. 엔진 모드
  (``ENGINE_V2``)나 release mode와 «따로» 끌 수 있어야, 새 수집기 하나 때문에
  이미 정상 완료되던 회사가 못 받는 일이 생겼을 때 다른 계약을 건드리지 않고
  되돌릴 수 있다.

★ 왜 동결인가 — 값을 요청마다 다시 읽으면 한 프로세스 안에서 같은 조사가
  반쯤은 typed, 반쯤은 legacy로 갈린다. ``features/pipeline/engine_mode.py``의
  ``freeze_process_engine_mode()``와 **같은 패턴**을 그대로 쓴다(새 메커니즘을
  발명하지 않는다).

★ v1 경로는 이 값을 읽지 않는다. 읽지 않으면 동결도 되지 않으므로
  ``frozen_typed_collector_switch()``가 ``None``으로 남는다 — 「v1이 스위치를
  보지 않았다」를 시험이 기계적으로 확인하는 통로다.
"""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final

#: 운영에서 typed 수집기를 켜는 환경변수. 기본값은 «꺼짐»이다.
TYPED_DART_COLLECTOR_ENV_NAME: Final[str] = "TYPED_DART_COLLECTOR"
#: 정확히 이 값일 때만 켜진다. true·yes·on 같은 관용 표기를 받아 주면 오타
#: 하나로 미검증 수집기가 운영에서 켜진다.
TYPED_DART_COLLECTOR_ENV_ON: Final[str] = "1"


class TypedCollectorSwitch(Enum):
    """수집 경로 선택에 쓰는 정확한 스위치 값."""

    OFF = "off"
    ON = "on"


class TypedCollectorSwitchChangedError(RuntimeError):
    """이미 시작한 프로세스의 typed 수집기 스위치를 바꾸려 했다."""


_PROCESS_SWITCH_LOCK = threading.RLock()
_PROCESS_SWITCH: TypedCollectorSwitch | None = None


def require_exact_typed_collector_switch(
    switch: TypedCollectorSwitch,
) -> TypedCollectorSwitch:
    """문자열·bool·속성만 닮은 객체를 스위치 값으로 받지 않는다."""

    if type(switch) is not TypedCollectorSwitch:
        raise TypeError("정확한 TypedCollectorSwitch 값이 필요합니다")
    return switch


def _capture_typed_collector_switch_from_environment() -> TypedCollectorSwitch:
    """bootstrap 전용 raw 환경 읽기. 동결 lock 안에서만 호출한다."""

    return (
        TypedCollectorSwitch.ON
        if os.environ.get(TYPED_DART_COLLECTOR_ENV_NAME)
        == TYPED_DART_COLLECTOR_ENV_ON
        else TypedCollectorSwitch.OFF
    )


def freeze_process_typed_collector_switch(
    switch: TypedCollectorSwitch | None = None,
) -> TypedCollectorSwitch:
    """최초 조회에서 스위치를 딱 한 번 정하고 프로세스 수명 동안 동결한다.

    Args:
        switch: 명시 동결 값. 생략하면 환경변수를 lock 안에서 한 번 읽는다.

    Returns:
        이 프로세스가 끝까지 쓸 스위치 값.

    Raises:
        TypedCollectorSwitchChangedError: 이미 동결된 값과 다른 값을 명시했다.
    """

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        # 인자를 생략한 재호출은 raw 환경을 다시 보지 않는다. 최초 요청끼리
        # 경합해도 capture를 lock 안에서 해서 서로 다른 값을 만들 틈이 없다.
        if switch is None and _PROCESS_SWITCH is not None:
            return _PROCESS_SWITCH
        candidate = (
            _capture_typed_collector_switch_from_environment()
            if switch is None
            else require_exact_typed_collector_switch(switch)
        )
        if _PROCESS_SWITCH is None:
            _PROCESS_SWITCH = candidate
        elif _PROCESS_SWITCH is not candidate:
            raise TypedCollectorSwitchChangedError(
                "이미 동결된 typed 수집기 스위치를 바꿀 수 없습니다"
            )
        return _PROCESS_SWITCH


def process_typed_collector_switch() -> TypedCollectorSwitch:
    """프로세스가 동결한 스위치 값. 아직 없으면 지금 동결한다."""

    with _PROCESS_SWITCH_LOCK:
        current = _PROCESS_SWITCH
    return freeze_process_typed_collector_switch() if current is None else current


def frozen_typed_collector_switch() -> TypedCollectorSwitch | None:
    """동결된 값을 «읽기만» 한다 — 아직 아무도 안 봤으면 ``None``.

    이 함수는 환경변수를 읽지 않고 동결도 하지 않는다. 「이 경로가 스위치를
    한 번도 조회하지 않았다」를 시험이 확인하는 데 쓴다.
    """

    with _PROCESS_SWITCH_LOCK:
        return _PROCESS_SWITCH


def typed_dart_collector_enabled() -> bool:
    """typed DART 수집기를 이 프로세스에서 써도 되는가."""

    return process_typed_collector_switch() is TypedCollectorSwitch.ON


def _reset_process_typed_collector_switch_for_tests() -> None:
    """pytest process 재시작 모사 전용. production에서는 호출하지 않는다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        _PROCESS_SWITCH = None
