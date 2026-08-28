"""OAuth 콜백의 서버 발급·비차단·고정 동시성 통합 계약.

진짜 Google 네트워크는 한 번도 사용하지 않는다. 모든 provider 경계는 가짜 함수로
바꾸고, 서버 원장이 거부한 요청에는 그 가짜조차 0회인지 확인한다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager

import httpx
from fastapi import FastAPI

from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.auth import state_store
from src.features.storage import db as storage_db
from src.web.routers import auth as auth_router


def _state(char: str) -> str:
    assert len(char) == 1 and char.isascii() and char.isalnum()
    return char * auth_constants.STATE_TOKEN_CHARS


def _issue(raw: str, *, now: float | None = None) -> None:
    with storage_db.connect() as conn:
        state_store.issue_state(
            conn,
            raw,
            now=auth_router._wall_now() if now is None else now,
        )


def _result(previous_session_token: str | None = None) -> auth_google.LoginResult:
    session = auth_logic.rotate_session(
        "admin@example.com",
        is_admin=True,
        subject="google:oauth-safety-test-person",
        previous_token=previous_session_token,
    )
    return auth_google.LoginResult(
        email=session.email,
        is_admin=True,
        session=session,
    )


def _test_app() -> FastAPI:
    # 전체 lifespan의 복구 작업과 무관하게 OAuth event-loop 계약만 실제 ASGI로 본다.
    application = FastAPI()
    application.include_router(auth_router.router)

    @application.get("/ordinary-probe")
    async def ordinary_probe():
        return {"ok": True}

    return application


def _client(application: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="https://service.example",
    )


def _callback_path(raw: str, *, code: str = "auth-code") -> str:
    return f"/auth/callback?code={code}&state={raw}"


def _cookie(raw: str) -> dict[str, str]:
    return {"Cookie": f"{auth_constants.STATE_COOKIE_NAME}={raw}"}


def test_자기선택_replay_만료_불일치_형식오류는_provider_0회(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def forbidden_provider(*args, **kwargs):
        calls.append("called")
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(auth_google, "handle_callback", forbidden_provider)
    self_chosen = _state("a")
    replayed = _state("b")
    expired = _state("c")
    issued_for_wrong_cookie = _state("d")
    wrong_cookie = _state("e")
    _issue(replayed)
    with storage_db.connect() as conn:
        assert state_store.consume_state(
            conn, replayed, now=auth_router._wall_now()
        )
    _issue(
        expired,
        now=(
            auth_router._wall_now()
            - auth_constants.STATE_MAX_AGE_SEC
            - 1
        ),
    )
    _issue(issued_for_wrong_cookie)

    async def scenario() -> list[int]:
        async with _client(_test_app()) as client:
            responses = [
                await client.get(
                    _callback_path(self_chosen),
                    headers=_cookie(self_chosen),
                    follow_redirects=False,
                ),
                await client.get(
                    _callback_path(replayed),
                    headers=_cookie(replayed),
                    follow_redirects=False,
                ),
                await client.get(
                    _callback_path(expired),
                    headers=_cookie(expired),
                    follow_redirects=False,
                ),
                await client.get(
                    _callback_path(issued_for_wrong_cookie),
                    headers=_cookie(wrong_cookie),
                    follow_redirects=False,
                ),
                await client.get(
                    _callback_path("short"),
                    headers=_cookie("short"),
                    follow_redirects=False,
                ),
                await client.get(
                    _callback_path("x" * (auth_constants.STATE_TOKEN_CHARS + 1)),
                    headers=_cookie(
                        "x" * (auth_constants.STATE_TOKEN_CHARS + 1)
                    ),
                    follow_redirects=False,
                ),
            ]
        return [response.status_code for response in responses]

    statuses = asyncio.run(scenario())
    assert statuses[:5] == [303, 303, 303, 303, 303]
    assert statuses[5] == 422
    assert calls == []


def test_정상_callback만_한번_provider와_session으로_이어진다(
    monkeypatch,
) -> None:
    raw = _state("f")
    _issue(raw)
    calls = 0

    def successful_provider(
        code,
        state_received,
        state_expected,
        *,
        previous_session_token=None,
        provider_deadline_monotonic=None,
    ):
        nonlocal calls
        calls += 1
        assert code == "auth-code"
        assert state_received == state_expected == raw
        assert provider_deadline_monotonic > auth_router._monotonic_now()
        return _result(previous_session_token)

    monkeypatch.setattr(auth_google, "handle_callback", successful_provider)

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with _client(_test_app()) as client:
            first = await client.get(
                _callback_path(raw),
                headers=_cookie(raw),
                follow_redirects=False,
            )
            replay = await client.get(
                _callback_path(raw),
                headers=_cookie(raw),
                follow_redirects=False,
            )
            return first, replay

    first, replay = asyncio.run(scenario())
    assert first.status_code == 303
    assert auth_constants.SESSION_COOKIE_NAME in first.cookies
    assert auth_logic.get_session(first.cookies[auth_constants.SESSION_COOKIE_NAME])
    assert replay.status_code == 303
    assert auth_constants.SESSION_COOKIE_NAME not in replay.cookies
    assert calls == 1


def test_state_DB장애는_fail_closed_503이고_provider_0회(
    monkeypatch, caplog
) -> None:
    raw = _state("g")
    _issue(raw)
    calls = 0

    def forbidden_provider(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _result()

    @contextmanager
    def broken_store(*_args, **_kwargs):
        raise sqlite3.OperationalError("private-storage-path-and-detail")
        yield  # pragma: no cover

    monkeypatch.setattr(auth_google, "handle_callback", forbidden_provider)
    monkeypatch.setattr(auth_router.storage_db, "connect", broken_store)
    caplog.set_level(logging.DEBUG)

    async def scenario() -> httpx.Response:
        async with _client(_test_app()) as client:
            return await client.get(
                _callback_path(raw),
                headers=_cookie(raw),
                follow_redirects=False,
            )

    response = asyncio.run(scenario())
    assert response.status_code == 503
    assert response.headers["retry-after"] == str(
        auth_constants.OAUTH_OVERLOAD_RETRY_AFTER_SEC
    )
    assert calls == 0
    app_logs = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == auth_router.logger.name
    )
    assert raw not in app_logs
    assert "auth-code" not in app_logs
    assert "private-storage-path-and-detail" not in app_logs


def test_느린_provider_중에도_ordinary_probe는_즉시_응답한다(
    monkeypatch,
) -> None:
    raw = _state("h")
    _issue(raw)
    started = threading.Event()

    def slow_provider(*_args, **_kwargs):
        started.set()
        time.sleep(0.3)
        return _result()

    monkeypatch.setattr(auth_google, "handle_callback", slow_provider)

    async def scenario() -> tuple[float, int, int]:
        async with _client(_test_app()) as client:
            callback = asyncio.create_task(
                client.get(
                    _callback_path(raw),
                    headers=_cookie(raw),
                    follow_redirects=False,
                )
            )
            assert await asyncio.to_thread(started.wait, 1.0)
            before = time.perf_counter()
            probe = await client.get("/ordinary-probe")
            elapsed = time.perf_counter() - before
            completed = await callback
            return elapsed, probe.status_code, completed.status_code

    elapsed, probe_status, callback_status = asyncio.run(scenario())
    assert probe_status == 200
    assert callback_status == 303
    assert elapsed < 0.15


def test_provider_동시수는_고정되고_N_plus_1은_대기열없이_429(
    monkeypatch,
) -> None:
    assert auth_constants.OAUTH_PROVIDER_MAX_CONCURRENCY == 2
    states = [_state(char) for char in ("i", "j", "k")]
    for raw in states:
        _issue(raw)
    release = threading.Event()
    both_started = threading.Event()
    lock = threading.Lock()
    calls = 0
    active = 0
    maximum_active = 0

    def slow_provider(*_args, **_kwargs):
        nonlocal calls, active, maximum_active
        with lock:
            calls += 1
            active += 1
            maximum_active = max(maximum_active, active)
            if active == auth_constants.OAUTH_PROVIDER_MAX_CONCURRENCY:
                both_started.set()
        assert release.wait(2.0)
        try:
            return _result()
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(auth_google, "handle_callback", slow_provider)

    async def scenario() -> tuple[list[int], httpx.Response]:
        async with _client(_test_app()) as client:
            running = [
                asyncio.create_task(
                    client.get(
                        _callback_path(raw),
                        headers=_cookie(raw),
                        follow_redirects=False,
                    )
                )
                for raw in states[:2]
            ]
            assert await asyncio.to_thread(both_started.wait, 1.0)
            overflow = await client.get(
                _callback_path(states[2]),
                headers=_cookie(states[2]),
                follow_redirects=False,
            )
            release.set()
            completed = await asyncio.gather(*running)
            return [response.status_code for response in completed], overflow

    completed_statuses, overflow = asyncio.run(scenario())
    assert completed_statuses == [303, 303]
    assert overflow.status_code == 429
    assert overflow.headers["retry-after"] == str(
        auth_constants.OAUTH_OVERLOAD_RETRY_AFTER_SEC
    )
    assert calls == maximum_active == auth_constants.OAUTH_PROVIDER_MAX_CONCURRENCY


def test_전체_deadline은_응답을_끝내도_worker_slot을_끝까지_유지한다(
    monkeypatch,
) -> None:
    first, second, overflow = (_state("l"), _state("m"), _state("n"))
    for raw in (first, second, overflow):
        _issue(raw)
    started = threading.Event()
    workers_finished = threading.Event()
    started_count = 0
    finished_count = 0
    lock = threading.Lock()

    def ignores_deadline(*_args, **_kwargs):
        nonlocal started_count, finished_count
        with lock:
            started_count += 1
            if started_count == 2:
                started.set()
        try:
            time.sleep(0.3)
            return _result()
        finally:
            with lock:
                finished_count += 1
                if finished_count == 2:
                    workers_finished.set()

    monkeypatch.setattr(auth_google, "handle_callback", ignores_deadline)
    monkeypatch.setattr(auth_constants, "OAUTH_PROVIDER_TOTAL_DEADLINE_SEC", 0.05)
    monkeypatch.setattr(auth_constants, "OAUTH_DEADLINE_RETURN_GRACE_SEC", 0.02)

    async def scenario() -> tuple[list[httpx.Response], httpx.Response, float]:
        async with _client(_test_app()) as client:
            before = time.perf_counter()
            running = [
                asyncio.create_task(
                    client.get(
                        _callback_path(raw),
                        headers=_cookie(raw),
                        follow_redirects=False,
                    )
                )
                for raw in (first, second)
            ]
            assert await asyncio.to_thread(started.wait, 1.0)
            completed = await asyncio.gather(*running)
            deadline_elapsed = time.perf_counter() - before
            # 응답은 deadline에 끝났지만 실제 두 thread는 아직 자는 중이다.
            third = await client.get(
                _callback_path(overflow),
                headers=_cookie(overflow),
                follow_redirects=False,
            )
            return completed, third, deadline_elapsed

    completed, third, deadline_elapsed = asyncio.run(scenario())
    assert [response.status_code for response in completed] == [503, 503]
    assert deadline_elapsed < 0.2
    assert third.status_code == 429
    assert started_count == 2
    # 다음 시험에 실행 중 worker를 넘기지 않는다.
    assert workers_finished.wait(1.0)


def test_거부로그에는_state_cookie와_인가코드가_남지않는다(
    monkeypatch, caplog
) -> None:
    raw = _state("o")
    authorization_code = "authorization-code-must-not-be-logged"
    monkeypatch.setattr(
        auth_google,
        "handle_callback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must be zero")
        ),
    )
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with _client(_test_app()) as client:
            response = await client.get(
                _callback_path(raw, code=authorization_code),
                headers=_cookie(raw),
                follow_redirects=False,
            )
            assert response.status_code == 303

    asyncio.run(scenario())
    # httpx 시험 client는 자신의 요청 URL을 INFO로 남길 수 있다. 제품 앱 로거만
    # 검사하며, 실제 Uvicorn 접근 로그는 access_log 전용 회귀 시험이 지킨다.
    joined = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("src.")
    )
    assert raw not in joined
    assert authorization_code not in joined
