"""본문을 다시 읽은 뒤에만 재사용하는 동일 분석 묶음 캐시.

키에는 입력 해시만, 값에는 검증된 분석과 원문 범위만 보관한다. 본문·prompt·
회사 정보·provider usage/비밀은 캐시에 저장하지 않는다. 조회·저장만 잠그며
provider 호출은 잠그지 않는다. 동시 cold miss는 각각 기존 호출 예산을 쓴다.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterator

from src.features.news_intake import analysis_cache_constants as c
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.models import NewsCandidate, NewsCollectionPolicy, NewsCompanyContext
from src.shared.news_analysis_port import (
    AnalysisNamespace, ProviderAnalysis, analyze_with_cache, news_analysis_scope,
)


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def cache_enabled() -> bool:
    value = os.environ.get(c.ANALYSIS_CACHE_ENV)
    return c.ANALYSIS_CACHE_DEFAULT if value is None else value == "1"


@dataclass
class AnalysisRequest:
    company: NewsCompanyContext
    as_of: dt.date
    policy: NewsCollectionPolicy
    articles: list[tuple[NewsCandidate, str]]
    full_body_hashes: dict[str, str]
    prompt: str
    schema: dict[str, Any]
    max_tokens: int
    cache_hits: int = 0
    provider_calls: int | None = None

    def key(self, namespace: AnalysisNamespace | None) -> str | None:
        if type(namespace) is not AnalysisNamespace or not namespace.usable():
            return None
        try:
            hashes = [self.full_body_hashes[candidate.source_url] for candidate, _ in self.articles]
            if not hashes or any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
                                 for value in hashes):
                return None
            return _hash(_bytes({
                "version": c.ANALYSIS_CACHE_VERSION, "model": namespace.model,
                "build": namespace.build.wire, "company": asdict(self.company),
                "as_of": self.as_of.isoformat(), "policy": asdict(self.policy),
                "articles": [{"candidate": asdict(candidate), "full_body_sha256": full_hash,
                              "input_body_sha256": _hash(body.encode("utf-8"))}
                             for (candidate, body), full_hash in zip(self.articles, hashes)],
                "prompt": self.prompt, "schema": self.schema, "max_tokens": self.max_tokens,
            }))
        except (TypeError, ValueError, KeyError):
            return None

    def valid(self, payload: Any) -> bool:
        # 부정 판정·부분 채택도 저장하지 않는다. 적중 역시 현재 검증을 통과해야 한다.
        if type(payload) is not dict:
            return False
        try:
            excerpts, rejected = validate_grounded_response(
                payload, articles=self.articles, company=self.company, as_of=self.as_of,
            )
            return bool(excerpts) and not rejected
        except (TypeError, ValueError, KeyError):
            return False


@contextmanager
def analysis_request(**kwargs: Any) -> Iterator[AnalysisRequest]:
    request = AnalysisRequest(**kwargs)
    def handler(provider: Callable, namespace: AnalysisNamespace | None, reserve_hit: Callable) -> Any:
        if not cache_enabled():
            return provider().payload
        return PROCESS_ANALYSIS_CACHE.run(request, namespace, provider, reserve_hit)
    def counter(count: int) -> None:
        request.provider_calls = count
    with news_analysis_scope(handler, counter):
        yield request


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    encoded: bytes
    checksum: str


def _entry_checksum(key: str, expires_at: float, encoded: bytes) -> str:
    return _hash(_bytes([key, expires_at]) + encoded)


def _span(text: str, body: str) -> list[int]:
    if not isinstance(text, str):
        raise ValueError("원문 범위는 문자열이어야 합니다")
    start = body.find(text)
    if start < 0:
        raise ValueError("현재 본문에 없는 근거는 캐시하지 않습니다")
    return [start, start + len(text)]


def _project(payload: dict, request: AnalysisRequest, *, restore: bool) -> dict:
    """원문 근거는 정수 범위로 저장하고 새로 읽은 본문에서만 복원한다."""
    value = copy.deepcopy(payload)
    bodies = {candidate.id: body for candidate, body in request.articles}
    for item in value["items"]:
        body = bodies[item["id"]]

        def convert(raw: Any) -> Any:
            if not restore:
                return _span(raw, body)
            if (type(raw) is not list or len(raw) != 2 or any(type(i) is not int for i in raw)
                    or not 0 <= raw[0] <= raw[1] <= len(body)):
                raise ValueError("손상된 원문 범위")
            return body[raw[0]:raw[1]]

        item["entity_evidence"] = convert(item["entity_evidence"])
        for excerpt in item["excerpts"]:
            for name in c.ANALYSIS_CACHE_SOURCE_FIELDS:
                excerpt[name] = convert(excerpt[name])
            # 사건명·대상명 자체가 본문 범위이면 현재 본문에서 복원한다.
            for name in ("event_key", "subject"):
                raw = excerpt[name]
                if restore:
                    if type(raw) is list:
                        excerpt[name] = convert(raw)
                elif raw and raw in body:
                    excerpt[name] = convert(raw)
    return value


def _contains_full_body(value: Any, bodies: tuple[str, ...]) -> bool:
    """JSON 이스케이프 전의 모든 문자열·키에서 배치 원문 잔존을 확인한다."""
    if isinstance(value, str):
        return any(body and body in value for body in bodies)
    if isinstance(value, dict):
        return any(_contains_full_body(key, bodies) or _contains_full_body(item, bodies)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_full_body(item, bodies) for item in value)
    return False


class AnalysisResultCache:
    def __init__(self, *, ttl: float = c.ANALYSIS_CACHE_TTL_SECONDS,
                 max_entries: int = c.ANALYSIS_CACHE_MAX_ENTRIES,
                 max_bytes: int = c.ANALYSIS_CACHE_MAX_BYTES,
                 entry_max_bytes: int = c.ANALYSIS_CACHE_ENTRY_MAX_BYTES,
                 clock: Callable[[], float] = time.monotonic):
        if (not math.isfinite(ttl) or ttl <= 0
                or any(type(v) is not int or v <= 0 for v in (max_entries, max_bytes, entry_max_bytes))):
            raise ValueError("캐시 경계는 양의 유한한 수여야 합니다")
        self._ttl, self._max_entries = ttl, max_entries
        self._max_bytes, self._entry_max_bytes = max_bytes, entry_max_bytes
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def _prune(self) -> None:
        now = self._clock()
        for key, entry in list(self._entries.items()):
            if (type(entry) is not _Entry or type(entry.expires_at) not in (int, float)
                    or not math.isfinite(entry.expires_at) or entry.expires_at <= now
                    or type(entry.encoded) is not bytes):
                self._entries.pop(key, None)

    def discard(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def _get(self, key: str, request: AnalysisRequest) -> dict | None:
        with self._lock:
            self._prune()
            entry = self._entries.get(key)
            if entry is None:
                return None
            try:
                if (type(entry.encoded) is not bytes or len(entry.encoded) > self._entry_max_bytes
                        or _entry_checksum(key, entry.expires_at, entry.encoded) != entry.checksum):
                    raise ValueError("손상된 분석 캐시")
                payload = _project(json.loads(entry.encoded), request, restore=True)
            except (TypeError, ValueError, KeyError, IndexError, AttributeError, RecursionError):
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
        if not request.valid(payload):
            self.discard(key)
            return None
        return payload

    def _put(self, key: str, request: AnalysisRequest, payload: dict) -> None:
        try:
            projected = _project(payload, request, restore=False)
            # 접두·접미 metadata와 중첩 추가 필드에도 어느 기사 원문도 남기지 않는다.
            # 분석 결과는 그대로 반환하고 이 응답의 캐시 저장만 생략한다.
            if _contains_full_body(projected, tuple(body for _, body in request.articles)):
                return
            encoded = _bytes(projected)
        except (TypeError, ValueError, KeyError, IndexError, RecursionError):
            return
        if len(encoded) > min(self._entry_max_bytes, self._max_bytes):
            return
        with self._lock:
            self._prune()
            expires_at = self._clock() + self._ttl
            self._entries[key] = _Entry(expires_at, encoded, _entry_checksum(key, expires_at, encoded))
            self._entries.move_to_end(key)
            while (len(self._entries) > self._max_entries
                   or sum(len(item.encoded) for item in self._entries.values()) > self._max_bytes):
                self._entries.popitem(last=False)

    def run(self, request: AnalysisRequest, namespace: AnalysisNamespace | None,
            provider: Callable[[], ProviderAnalysis], reserve_hit: Callable[[], None]) -> Any:
        key = request.key(namespace)
        if key is not None:
            payload = self._get(key, request)
            if payload is not None:
                # 실제 provider 시도/비용은 만들지 않고 기존 요청의 논리 몫만 보존한다.
                request.provider_calls = 0
                reserve_hit()
                request.cache_hits += 1
                return payload
        result = provider()
        if key is not None and result.complete is True and request.valid(result.payload):
            self._put(key, request, result.payload)
        return result.payload


PROCESS_ANALYSIS_CACHE = AnalysisResultCache()
