"""뉴스 수집과 provider adapter 사이의 요청 로컬 분석 포트.

캐시 저장·키·검증 정책은 소유하지 않는다. 수집기가 설치한 실행 callback과
실제 provider 전송 계수 관측만 현재 호출 문맥으로 전달한다.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from src.shared.engine_build_identity import EngineBuildIdentity, require_exact_engine_build_identity


@dataclass(frozen=True)
class AnalysisNamespace:
    model: str
    build: EngineBuildIdentity

    def usable(self) -> bool:
        try:
            build = require_exact_engine_build_identity(self.build)
            return (build.cache_usable and type(self.model) is str
                    and bool(self.model.strip()) and self.model == self.model.strip()
                    and self.model.casefold() not in {"unknown", "none", "null", "unavailable"})
        except (TypeError, ValueError):
            return False


@dataclass(frozen=True)
class ProviderAnalysis:
    payload: Any
    # 실제 전송 1회·정상 종료·확정 usage까지 확인한 adapter만 True를 준다.
    complete: bool = False


_HANDLER: contextvars.ContextVar[Callable | None] = contextvars.ContextVar("news_analysis_handler", default=None)
_COUNTER: contextvars.ContextVar[Callable[[int], None] | None] = contextvars.ContextVar("news_analysis_counter", default=None)


@contextmanager
def news_analysis_scope(handler: Callable, counter: Callable[[int], None]) -> Iterator[None]:
    handler_token = _HANDLER.set(handler)
    counter_token = _COUNTER.set(counter)
    try:
        yield
    finally:
        _COUNTER.reset(counter_token)
        _HANDLER.reset(handler_token)


def record_news_provider_calls(count: int) -> None:
    counter = _COUNTER.get()
    if counter is not None and type(count) is int and count >= 0:
        counter(count)


def analyze_with_cache(provider: Callable[[], ProviderAnalysis], *, namespace: AnalysisNamespace | None,
                       reserve_hit: Callable[[], None]) -> Any:
    handler = _HANDLER.get()
    if handler is None:
        return provider().payload
    return handler(provider, namespace, reserve_hit)
