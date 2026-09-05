"""1단계 «매출표 범용 파서 + 3장 카드 복구»를 운영에서 켜고 끄는 프로세스 동결 스위치.

★ 왜 별도 스위치인가 — 매출 구성표 파서를 제목 화이트리스트에서 «표 모양»
  탐지로 바꾸고, 3장 카드 작가 안내문의 이중 조건을 완화하는 1단계 변경은
  아홉 장 출고 계약을 건드리지 않지만, 표가 새로 생기는 회사가 늘어난다.
  엔진 모드(``ENGINE_V2``)·release mode·typed 수집기 스위치와 «따로» 끌 수
  있어야, 새 표 하나 때문에 이미 정상 완료되던 회사가 달라졌을 때 다른
  계약을 건드리지 않고 되돌릴 수 있다.

★ 왜 동결인가 — 값을 요청마다 다시 읽으면 한 프로세스 안에서 같은 조사가
  반쯤은 새 파서, 반쯤은 옛 파서로 갈린다. ``app/src/core/typed_collector_switch.py``
  와 **같은 패턴**을 그대로 쓴다(새 메커니즘을 발명하지 않는다).

★ «선언하지 않는 것»이 off다. ``render.yaml``에 키를 적지 않으면 옛 동작이다.
"""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final

#: 운영에서 1단계 매출표·3장 카드 경로를 켜는 환경변수. 기본값은 «꺼짐»이다.
REVENUE_TABLE_V2_ENV_NAME: Final[str] = "REVENUE_TABLE_V2"
#: 정확히 이 값일 때만 켜진다. true·yes·on 같은 관용 표기를 받아 주면 오타
#: 하나로 미검증 경로가 운영에서 켜진다.
REVENUE_TABLE_V2_ENV_ON: Final[str] = "1"


class RevenueTableSwitch(Enum):
    """매출표 파서·3장 카드 경로 선택에 쓰는 정확한 스위치 값."""

    OFF = "off"
    ON = "on"


class RevenueTableSwitchChangedError(RuntimeError):
    """이미 시작한 프로세스의 매출표 스위치를 바꾸려 했다."""


_PROCESS_SWITCH_LOCK = threading.RLock()
_PROCESS_SWITCH: RevenueTableSwitch | None = None


def require_exact_revenue_table_switch(
    switch: RevenueTableSwitch,
) -> RevenueTableSwitch:
    """문자열·bool·속성만 닮은 객체를 스위치 값으로 받지 않는다."""

    if type(switch) is not RevenueTableSwitch:
        raise TypeError("정확한 RevenueTableSwitch 값이 필요합니다")
    return switch


def _capture_revenue_table_switch_from_environment() -> RevenueTableSwitch:
    """bootstrap 전용 raw 환경 읽기. 동결 lock 안에서만 호출한다."""

    return (
        RevenueTableSwitch.ON
        if os.environ.get(REVENUE_TABLE_V2_ENV_NAME) == REVENUE_TABLE_V2_ENV_ON
        else RevenueTableSwitch.OFF
    )


def freeze_process_revenue_table_switch(
    switch: RevenueTableSwitch | None = None,
) -> RevenueTableSwitch:
    """최초 조회에서 스위치를 딱 한 번 정하고 프로세스 수명 동안 동결한다.

    Args:
        switch: 명시 동결 값. 생략하면 환경변수를 lock 안에서 한 번 읽는다.

    Returns:
        이 프로세스가 끝까지 쓸 스위치 값.

    Raises:
        RevenueTableSwitchChangedError: 이미 동결된 값과 다른 값을 명시했다.
    """

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        if switch is None and _PROCESS_SWITCH is not None:
            return _PROCESS_SWITCH
        candidate = (
            _capture_revenue_table_switch_from_environment()
            if switch is None
            else require_exact_revenue_table_switch(switch)
        )
        if _PROCESS_SWITCH is None:
            _PROCESS_SWITCH = candidate
        elif _PROCESS_SWITCH is not candidate:
            raise RevenueTableSwitchChangedError(
                "이미 동결된 매출표 스위치를 바꿀 수 없습니다"
            )
        return _PROCESS_SWITCH


def process_revenue_table_switch() -> RevenueTableSwitch:
    """프로세스가 동결한 스위치 값. 아직 없으면 지금 동결한다."""

    with _PROCESS_SWITCH_LOCK:
        current = _PROCESS_SWITCH
    return freeze_process_revenue_table_switch() if current is None else current


def frozen_revenue_table_switch() -> RevenueTableSwitch | None:
    """동결된 값을 «읽기만» 한다 — 아직 아무도 안 봤으면 ``None``.

    환경변수를 읽지 않고 동결도 하지 않는다. 「이 경로가 스위치를 한 번도
    조회하지 않았다」를 시험이 확인하는 데 쓴다.
    """

    with _PROCESS_SWITCH_LOCK:
        return _PROCESS_SWITCH


def revenue_table_v2_enabled() -> bool:
    """1단계 매출표 범용 파서·3장 카드 새 안내문을 이 프로세스에서 써도 되는가."""

    return process_revenue_table_switch() is RevenueTableSwitch.ON


def _reset_process_revenue_table_switch_for_tests() -> None:
    """pytest process 재시작 모사 전용. production에서는 호출하지 않는다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        _PROCESS_SWITCH = None
