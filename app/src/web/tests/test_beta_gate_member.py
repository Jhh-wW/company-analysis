"""관리자 전용 로그인 벽에도 초대 명단(allowlist) 회원은 들어온다는 것을 못 박는다.

배포 계약(포트폴리오 링크)을 새로 만들면서 넣었다. ``main.py``의 ``beta_admin_gate``에
넣은 MEMBER 예외는 **배포 계약과 무관하게** 적용한다 — ``BETA_ADMIN_ONLY``는 로그인
벽을 켤지만 정할 뿐, 어느 forwarded-header 신뢰 모델을 쓰는지와는 다른 축이기
때문이다(주석 근거: ``src/web/main.py``의 ``beta_admin_gate``). 이 시험은 계약을
아예 설정하지 않은 «일반 배포 + BETA_ADMIN_ONLY=1» 상황에서 그 예외 하나만 본다.

★ 지키는 것
  ① 초대 명단에 «활성» 상태로 있는 회원은 로그인만으로 관리자 화면 밖을 연다.
  ② `/admin`과 그 하위 경로는 이 예외로도 절대 열리지 않는다 — 관리자 세션만.
  ③ 명단 밖 구글 로그인은 여전히 `/auth/not-admin`으로 간다 (기존 회귀 보존).
  ④ 로그인하지 않았으면 `/auth/login`으로 간다 (기존 회귀 보존).
  ⑤ 로그인 콜백 직후 도착지도 관리자·회원이면 홈, 그 외에는 관리자 전용 안내다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.auth import state_store
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.storage import db as storage_db
from src.web import main, runtime
from src.web.routers import auth as auth_router

SESSION = auth_constants.SESSION_COOKIE_NAME


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client


def _invite(email: str) -> None:
    with storage_db.connect() as conn:
        assert share_allow.invite(
            conn,
            email=email,
            display_name="",
            note="",
            now_iso="2026-09-01T10:00:00+09:00",
        )


def test_명단_회원_세션은_홈과_조사_경로를_통과한다(client: TestClient, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    _invite("friend@example.com")
    session = auth_logic.create_session("friend@example.com", is_admin=False)
    client.cookies.set(SESSION, session.token)

    home = client.get("/", follow_redirects=False)
    robots = client.get("/robots.txt", follow_redirects=False)

    assert home.status_code == 200
    assert robots.status_code == 200
    assert "Disallow" in robots.text


def test_명단_회원_세션도_admin_경로는_막힌다(client: TestClient, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    _invite("friend@example.com")
    session = auth_logic.create_session("friend@example.com", is_admin=False)
    client.cookies.set(SESSION, session.token)

    admin_home = client.get("/admin", follow_redirects=False)
    admin_dashboard = client.get("/admin/dashboard", follow_redirects=False)
    admin_access = client.get("/admin/access", follow_redirects=False)

    for response in (admin_home, admin_dashboard, admin_access):
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/not-admin"


def test_명단_밖_구글_세션은_not_admin으로_간다(client: TestClient, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    session = auth_logic.create_session("outsider@example.com", is_admin=False)
    client.cookies.set(SESSION, session.token)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"


def test_비로그인은_login으로_간다(client: TestClient, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


def test_명단회원_취소된_초대는_통과하지_못한다(client: TestClient, monkeypatch):
    """활성 여부까지 본다 — 예전에 초대했다가 철회한 사람은 다시 막혀야 한다."""
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    _invite("revoked@example.com")
    with storage_db.connect() as conn:
        assert share_allow.revoke(conn, "revoked@example.com")
    session = auth_logic.create_session("revoked@example.com", is_admin=False)
    client.cookies.set(SESSION, session.token)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"


# ══════════════════════════════════════════════════════════
# 로그인 콜백 직후 도착지 (routers/auth.py:448-449 부근)
# ══════════════════════════════════════════════════════════


def _state(char: str) -> str:
    assert len(char) == 1 and char.isascii() and char.isalnum()
    return char * auth_constants.STATE_TOKEN_CHARS


def _issue(raw: str) -> None:
    with storage_db.connect() as conn:
        state_store.issue_state(conn, raw, now=auth_router._wall_now())


def _test_app() -> FastAPI:
    application = FastAPI()
    application.include_router(auth_router.router)
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


def _login_result(email: str, *, is_admin: bool) -> auth_google.LoginResult:
    session = auth_logic.create_session(
        email, is_admin=is_admin, subject=f"google:{email}"
    )
    return auth_google.LoginResult(email=session.email, is_admin=is_admin, session=session)


def test_명단_회원은_로그인_뒤_홈으로_간다(monkeypatch) -> None:
    raw = _state("m")
    _issue(raw)
    _invite("member@example.com")

    def fake_callback(*_args, **_kwargs):
        return _login_result("member@example.com", is_admin=False)

    monkeypatch.setattr(auth_google, "handle_callback", fake_callback)

    async def scenario() -> httpx.Response:
        async with _client(_test_app()) as client:
            return await client.get(
                _callback_path(raw), headers=_cookie(raw), follow_redirects=False
            )

    response = asyncio.run(scenario())

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_명단_밖_로그인은_여전히_not_admin으로_간다(monkeypatch) -> None:
    raw = _state("n")
    _issue(raw)

    def fake_callback(*_args, **_kwargs):
        return _login_result("outsider@example.com", is_admin=False)

    monkeypatch.setattr(auth_google, "handle_callback", fake_callback)

    async def scenario() -> httpx.Response:
        async with _client(_test_app()) as client:
            return await client.get(
                _callback_path(raw), headers=_cookie(raw), follow_redirects=False
            )

    response = asyncio.run(scenario())

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"
