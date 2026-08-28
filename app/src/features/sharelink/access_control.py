"""공유 capability GET의 개인 식별 없는 bounded 애플리케이션 요청 제한.

링크를 연 사람의 IP나 그 파생값은 받지도, 만들지도, 보관하지도 않는다. 비용
경계에는 사람별 추적이 필요하지 않다. 링크 하나 전체를 1분 통장으로 묶으면 된다.
영속 정본은 :mod:`src.features.sharelink.store`의 SQLite 원자적 구간 집계이고, 이
모듈은 명백한 초과 요청을 DB 앞에서 값싸게 거르는 프로세스 보조 장치다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from src.core import clock
from src.features.sharelink import constants


_CAPABILITY_DOMAIN = b"company-analysis/sharelink/access/capability/v1\x00"


def _window_number(now_iso: str) -> int:
    parsed = dt.datetime.fromisoformat(str(now_iso or "").strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=clock.KST)
    return int(parsed.timestamp()) // constants.ACCESS_WINDOW_SECONDS


def _capability_identifier(capability: str) -> str:
    """메모리 통장에만 쓸 capability 지문을 만든다."""

    normalized = str(capability or "").strip().lower().encode("utf-8")
    return hashlib.sha256(_CAPABILITY_DOMAIN + normalized).hexdigest()


@dataclass
class AccessLimiter:
    """프로세스 메모리를 고정 상한 안에서만 쓰는 링크별 limiter."""

    per_capability_limit: int = constants.ACCESS_PER_CAPABILITY_LIMIT
    max_entries: int = constants.ACCESS_LIMITER_MAX_ENTRIES
    _entries: OrderedDict[tuple[str, int], int] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def allow(self, capability: str, now_iso: str) -> bool:
        """링크 전체 통장이 남았을 때만 원자적으로 한 칸을 사용한다."""

        try:
            window = _window_number(now_iso)
        except (OverflowError, TypeError, ValueError):
            return False
        capability_key = (_capability_identifier(capability), window)

        with self._lock:
            capability_count = self._entries.get(capability_key, 0)
            if capability_count >= self.per_capability_limit:
                return False
            self._entries[capability_key] = capability_count + 1
            self._entries.move_to_end(capability_key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)


_LIMITER = AccessLimiter()


def allow_request(capability: str, now_iso: str) -> bool:
    return _LIMITER.allow(capability, now_iso)


def reset_for_tests() -> None:
    """독립 시험 사이에 프로세스 통장이 새지 않게 한다."""

    _LIMITER.clear()
