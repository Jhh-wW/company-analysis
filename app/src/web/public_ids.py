"""외부에 노출되는 작업·결과 bearer를 충돌 없이 예약한다."""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Mapping, MutableMapping
from typing import Final, TypeVar

from src.features.budget.constants import JOB_KEEP_SEC
from src.features.budget.sharing import REPORT_ID_HEX_CHARS
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store


PUBLIC_ID_BYTES: Final[int] = 16
PUBLIC_ID_ALLOCATION_ATTEMPTS: Final[int] = 8
_PUBLIC_ID_RE = re.compile(rf"^[0-9a-f]{{{REPORT_ID_HEX_CHARS}}}$")
_RESERVATION_LOCK = threading.Lock()
_RESERVED_IDS: dict[str, float] = {}
_T = TypeVar("_T")


class PublicIdUnavailable(RuntimeError):
    """고유한 공개 bearer를 안전하게 예약할 수 없음."""


def _drop_stale_reservations(now: float) -> None:
    stale = [
        identifier
        for identifier, reserved_at in _RESERVED_IDS.items()
        if now - reserved_at > JOB_KEEP_SEC
    ]
    for identifier in stale:
        _RESERVED_IDS.pop(identifier, None)


def reserve(jobs: Mapping[str, object]) -> str:
    """메모리 작업과 저장 보고서에 없는 128비트 bearer를 원자 예약한다."""
    with _RESERVATION_LOCK:
        now = time.monotonic()
        _drop_stale_reservations(now)
        for _attempt in range(PUBLIC_ID_ALLOCATION_ATTEMPTS):
            candidate = secrets.token_hex(PUBLIC_ID_BYTES)
            if not isinstance(candidate, str) or not _PUBLIC_ID_RE.fullmatch(candidate):
                continue
            if candidate in _RESERVED_IDS or candidate in jobs:
                continue
            try:
                with storage_db.connect() as conn:
                    if report_store.exists(conn, candidate):
                        continue
            except Exception as exc:  # noqa: BLE001 — 존재 여부 불명은 fail-closed
                raise PublicIdUnavailable from exc
            _RESERVED_IDS[candidate] = now
            return candidate
    raise PublicIdUnavailable


def register(
    jobs: MutableMapping[str, _T], identifier: str, value: _T
) -> bool:
    """기존 작업을 덮지 않고 잠금 안에서 새 작업을 등록한다."""
    with _RESERVATION_LOCK:
        if identifier in jobs:
            return False
        jobs[identifier] = value
        _RESERVED_IDS.pop(identifier, None)
        return True


def release(identifier: str) -> None:
    """작업으로 승격하지 못했거나 폐기한 예약을 반환한다."""
    if not identifier:
        return
    with _RESERVATION_LOCK:
        _RESERVED_IDS.pop(identifier, None)
