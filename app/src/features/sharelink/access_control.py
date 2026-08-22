"""공유 capability GET의 비식별·bounded 애플리케이션 요청 제한."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from src.core import clock
from src.features.sharelink import constants


_CAPABILITY_DOMAIN = b"company-analysis/sharelink/access/capability/v1\x00"
_REQUESTER_DOMAIN = b"company-analysis/sharelink/access/requester/v1\x00"
_UNKNOWN_CLIENT = "unknown-client"


def _normalized_client_host(client_host: str) -> str:
    """IP만 정규화한다. 호스트명·깨진 값은 하나의 익명 통장으로 묶는다."""

    raw = str(client_host or "").strip()
    try:
        return ipaddress.ip_address(raw).compressed
    except ValueError:
        return _UNKNOWN_CLIENT


def _window_number(now_iso: str) -> int:
    parsed = dt.datetime.fromisoformat(str(now_iso or "").strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=clock.KST)
    return int(parsed.timestamp()) // constants.ACCESS_WINDOW_SECONDS


def _identifiers(capability: str, client_host: str) -> tuple[str, str]:
    """원문을 보유하지 않는 capability·요청자 도메인 식별자를 만든다."""

    normalized = str(capability or "").strip().lower().encode("utf-8")
    capability_digest = hashlib.sha256(_CAPABILITY_DOMAIN + normalized).digest()
    requester_digest = hmac.new(
        capability_digest,
        _REQUESTER_DOMAIN + _normalized_client_host(client_host).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return capability_digest.hex(), requester_digest


def requester_hash_of(capability: str, client_host: str) -> str:
    """DB의 짧은 구간 통장에 쓸 비가역·capability별 요청자 식별자."""

    return _identifiers(capability, client_host)[1]


@dataclass
class AccessLimiter:
    """프로세스 메모리를 고정 상한 안에서만 쓰는 2단계 limiter."""

    per_requester_limit: int = constants.ACCESS_PER_REQUESTER_LIMIT
    per_capability_limit: int = constants.ACCESS_PER_CAPABILITY_LIMIT
    max_entries: int = constants.ACCESS_LIMITER_MAX_ENTRIES
    _entries: OrderedDict[tuple[str, str, int], int] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def allow(self, capability: str, client_host: str, now_iso: str) -> bool:
        """두 통장이 모두 남았을 때만 원자적으로 한 칸을 사용한다."""

        try:
            window = _window_number(now_iso)
        except (OverflowError, TypeError, ValueError):
            return False
        capability_id, requester_id = _identifiers(capability, client_host)
        capability_key = ("capability", capability_id, window)
        requester_key = ("requester", requester_id, window)

        with self._lock:
            capability_count = self._entries.get(capability_key, 0)
            requester_count = self._entries.get(requester_key, 0)
            if (
                capability_count >= self.per_capability_limit
                or requester_count >= self.per_requester_limit
            ):
                return False
            self._entries[capability_key] = capability_count + 1
            self._entries.move_to_end(capability_key)
            self._entries[requester_key] = requester_count + 1
            self._entries.move_to_end(requester_key)
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


def allow_request(capability: str, client_host: str, now_iso: str) -> bool:
    return _LIMITER.allow(capability, client_host, now_iso)


def reset_for_tests() -> None:
    """독립 시험 사이에 프로세스 통장이 새지 않게 한다."""

    _LIMITER.clear()
