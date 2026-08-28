"""공개 readiness가 단일 이벤트 루프를 멈추지 않는 운영 회귀 시험."""

from __future__ import annotations

import asyncio
import threading
import time

from src.web import runtime
from src.web.routers import health


def test_느린_SQLite_readiness가_healthz와_웹_event_loop를_막지_않는다(
    monkeypatch,
) -> None:
    """SQLite 잠금 대기는 worker thread에서 일어나야 한다.

    Uvicorn worker가 하나이므로 async 함수 안에서 동기 SQLite를 기다리면
    readiness 한 건만으로 liveness를 포함한 모든 HTTP 응답이 함께 멈춘다.
    """

    entered = threading.Event()
    release = threading.Event()

    def slow_storage_check() -> None:
        entered.set()
        assert release.wait(timeout=2.0)

    monkeypatch.setattr(runtime, "_check_storage_read_ready", slow_storage_check)
    monkeypatch.setenv("PIPELINE", "demo")
    monkeypatch.setenv("BETA_ADMIN_ONLY", "0")

    async def scenario() -> None:
        ready_task = asyncio.create_task(health.readyz())
        assert await asyncio.to_thread(entered.wait, 1.0)
        started = time.monotonic()
        live = await asyncio.wait_for(health.healthz(), timeout=0.1)
        elapsed = time.monotonic() - started
        assert live.status_code == 200
        assert elapsed < 0.1
        assert not ready_task.done()
        release.set()
        ready = await asyncio.wait_for(ready_task, timeout=1.0)
        assert ready.status_code == 200

    try:
        asyncio.run(scenario())
    finally:
        release.set()
