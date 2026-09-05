"""근거 재판정을 운영에서 켜고 끄는 프로세스 동결 스위치.

근거 재판정은 결정론 문지기가 놓친 필수 의미 칸을 AI 호출 한 번으로
보충한다. 아직 운영 검증 전인 경로이므로 엔진 모드나 typed 수집기와 따로
되돌릴 수 있어야 한다.

값을 요청마다 다시 읽으면 한 프로세스 안의 조사들이 서로 다른 계약을
따를 수 있다. 그래서 최초 조회 때 환경을 한 번만 읽고 프로세스 수명 동안
동결한다.
"""

from __future__ import annotations

import os
import threading
from enum import Enum
from typing import Final


#: 근거 재판정을 켜는 환경변수. 선언하지 않으면 꺼진다.
EVIDENCE_RECLASSIFY_ENV_NAME: Final[str] = "EVIDENCE_RECLASSIFY"
#: 오타나 관용 표기로 미검증 경로가 켜지지 않도록 정확히 이 값만 받는다.
EVIDENCE_RECLASSIFY_ENV_ON: Final[str] = "1"


class EvidenceReclassifySwitch(Enum):
    """근거 재판정 경로 선택에 쓰는 정확한 스위치 값."""

    OFF = "off"
    ON = "on"


class EvidenceReclassifySwitchChangedError(RuntimeError):
    """이미 시작한 프로세스의 근거 재판정 스위치를 바꾸려 했다."""


_PROCESS_SWITCH_LOCK = threading.RLock()
_PROCESS_SWITCH: EvidenceReclassifySwitch | None = None


def require_exact_evidence_reclassify_switch(
    switch: EvidenceReclassifySwitch,
) -> EvidenceReclassifySwitch:
    """문자열·bool처럼 겉모양만 닮은 값을 스위치로 받지 않는다."""

    if type(switch) is not EvidenceReclassifySwitch:
        raise TypeError("정확한 EvidenceReclassifySwitch 값이 필요합니다")
    return switch


def _capture_evidence_reclassify_switch_from_environment(
) -> EvidenceReclassifySwitch:
    """동결 lock 안에서만 환경변수를 한 번 읽는다."""

    return (
        EvidenceReclassifySwitch.ON
        if os.environ.get(EVIDENCE_RECLASSIFY_ENV_NAME)
        == EVIDENCE_RECLASSIFY_ENV_ON
        else EvidenceReclassifySwitch.OFF
    )


def freeze_process_evidence_reclassify_switch(
    switch: EvidenceReclassifySwitch | None = None,
) -> EvidenceReclassifySwitch:
    """최초 조회에서 값을 정하고 프로세스 수명 동안 동결한다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        if switch is None and _PROCESS_SWITCH is not None:
            return _PROCESS_SWITCH
        candidate = (
            _capture_evidence_reclassify_switch_from_environment()
            if switch is None
            else require_exact_evidence_reclassify_switch(switch)
        )
        if _PROCESS_SWITCH is None:
            _PROCESS_SWITCH = candidate
        elif _PROCESS_SWITCH is not candidate:
            raise EvidenceReclassifySwitchChangedError(
                "이미 동결된 근거 재판정 스위치를 바꿀 수 없습니다"
            )
        return _PROCESS_SWITCH


def process_evidence_reclassify_switch() -> EvidenceReclassifySwitch:
    """프로세스가 동결한 값. 아직 없으면 지금 동결한다."""

    with _PROCESS_SWITCH_LOCK:
        current = _PROCESS_SWITCH
    return freeze_process_evidence_reclassify_switch() if current is None else current


def frozen_evidence_reclassify_switch() -> EvidenceReclassifySwitch | None:
    """환경을 읽거나 동결하지 않고 이미 동결된 값만 조회한다."""

    with _PROCESS_SWITCH_LOCK:
        return _PROCESS_SWITCH


def evidence_reclassify_enabled() -> bool:
    """이 프로세스에서 근거 재판정 경로를 써도 되는가."""

    return (
        process_evidence_reclassify_switch()
        is EvidenceReclassifySwitch.ON
    )


def _reset_process_evidence_reclassify_switch_for_tests() -> None:
    """pytest에서 프로세스 재시작을 모사할 때만 쓴다."""

    global _PROCESS_SWITCH
    with _PROCESS_SWITCH_LOCK:
        _PROCESS_SWITCH = None
