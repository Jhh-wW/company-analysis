"""로그인·권한 «연결»이 실제로 도는지 화면 층에서 확인한다.

정본: 확정/07_출력/3_기준/01_성공기준.md **P4** —
「권한 없는 계정에 노션 또는 대시보드 경로가 열림 = **0건 고정**, 미달 시 확산 중단」

★ 이 시험이 지키는 것 — **버튼을 숨기는 것은 권한이 아니다.**
  화면에 안 보여도 주소를 직접 치면 열리면 P4 위반이다. 그래서 여기서 «직접 호출»한다.
  근거 → 확정/07_출력/4_근거/01_출력근거.md §4

★ 진짜 구글에 접속하지 않는다. 열쇠(환경변수)도 넣지 않는다.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.web import main as web_main
from src.web.main import app, require_admin

SESSION = auth_constants.SESSION_COOKIE_NAME


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── ★ P4 — 권한 없는 접근은 «서버»에서 막힌다 ───────────

@pytest.mark.parametrize(
    "cookies",
    [
        {},                                  # 로그인 안 함
        {SESSION: ""},                       # 빈 토큰
        {SESSION: "아무거나-지어낸-토큰"},      # 위조 토큰
        {SESSION: "0" * 64},                 # 길이만 그럴듯한 토큰
    ],
    ids=["없음", "빈값", "위조", "길이만맞음"],
)
def test_관리자가_아니면_막힌다(cookies):
    """★ 세션이 없거나 가짜면 무조건 막혀야 한다."""
    request = _fake_request(cookies)
    blocked = require_admin(request)
    assert blocked is not None, "권한 없는 요청이 통과했습니다 (P4 위반)"
    assert blocked.status_code == 303


def test_관리자_세션이면_통과한다():
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)
    assert require_admin(_fake_request({SESSION: session.token})) is None


def test_로그인은_했지만_관리자가_아니면_막힌다():
    """★ 「로그인 됨」과 「관리자임」은 다른 것이다."""
    session = auth_logic.create_session(email="남@example.com", is_admin=False)
    assert require_admin(_fake_request({SESSION: session.token})) is not None


def test_로그아웃하면_그_세션은_더_이상_안_통한다():
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)
    auth_logic.delete_session(session.token)
    assert require_admin(_fake_request({SESSION: session.token})) is not None


# ── 라우트가 실제로 붙어 있나 ───────────────────────────

def test_열쇠가_없으면_로그인이_친절하게_거절한다(client, monkeypatch):
    """설정이 없을 때 «내부 오류»를 그대로 보여주면 안 된다."""
    for env in (
        auth_constants.ENV_CLIENT_ID,
        auth_constants.ENV_CLIENT_SECRET,
        auth_constants.ENV_REDIRECT_URI,
    ):
        monkeypatch.delenv(env, raising=False)
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    body = response.text
    assert 'data-login-unavailable="true"' in body
    # ★ 내부 사정(변수 이름·경로·스택)을 화면에 흘리지 않는다
    assert "GOOGLE_CLIENT_SECRET" not in body
    assert "Traceback" not in body


def test_잘못된_state로_돌아오면_로그인이_안_된다(client):
    """★ CSRF 방어 — 남이 만든 로그인 흐름을 가로챌 수 없어야 한다."""
    response = client.get(
        "/auth/callback?code=aaa&state=공격자가_만든_state", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION not in response.cookies


def test_로그아웃은_세션_쿠키를_지운다(client):
    response = client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 303
    assert 'auth_session=""' in response.headers.get("set-cookie", "") or (
        "auth_session=;" in response.headers.get("set-cookie", "")
    )


def test_관리자_아님_화면이_뜬다(client):
    assert client.get("/auth/not-admin").status_code == 200


# ── ★ 실제 주소를 «직접 쳐서» 들어와도 막히는가 (P4의 핵심) ──

@pytest.mark.parametrize(
    "cookies",
    # ★ 쿠키 값은 영숫자만 실린다 — 실제 공격자도 이 모양으로만 보낼 수 있다
    [{}, {SESSION: "forged-token-0123456789abcdef"}],
    ids=["로그인안함", "위조토큰"],
)
def test_관리자_화면_주소를_직접_쳐도_막힌다(client, cookies):
    """★ 화면에 링크가 안 보여도 «주소를 알면» 들어올 수 있다. 서버가 막아야 한다."""
    response = client.get("/admin", cookies=cookies, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"


def test_관리자_세션이면_관리자_화면이_열린다(client):
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)
    response = client.get("/admin", cookies={SESSION: session.token})
    assert response.status_code == 200
    assert "관리자로 로그인하셨습니다" in response.text


def test_로그인_안_한_화면에는_로그인과_관리자_링크가_보인다(client):
    body = client.get("/").text
    assert 'href="/auth/login"' in body
    assert 'href="/admin"' in body


def test_로그인_링크는_흰_상단바에서도_보이는_색을_쓴다(client):
    css = client.get("/static/style.css").text
    start = css.index(".auth-status .auth-email")
    end = css.index("/* 「관리자」 배지", start)
    auth_rules = css[start:end]

    assert "var(--ink-2)" in auth_rules
    assert "rgba(255, 255, 255" not in auth_rules
    assert "color: #fff" not in auth_rules


def test_로그인_설정이_없으면_첫화면에서_상태를_알린다(client, monkeypatch):
    for env in (
        auth_constants.ENV_CLIENT_ID,
        auth_constants.ENV_CLIENT_SECRET,
        auth_constants.ENV_REDIRECT_URI,
    ):
        monkeypatch.delenv(env, raising=False)

    body = client.get("/").text

    assert 'data-login-available="false"' in body
    assert 'href="/auth/login"' in body


def test_관리자_배지는_실제_링크다(client):
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)

    body = client.get("/", cookies={SESSION: session.token}).text

    assert 'class="tag admin" href="/admin"' in body


def test_로그인하지_않고_관리자를_누르면_로그인_버튼이_나온다(client):
    response = client.get("/admin", follow_redirects=True)

    assert response.status_code == 200
    assert 'href="/auth/login"' in response.text


# ── 관리자 전용 시험 공개 ────────────────────────────────

def test_시험공개에서는_로그인전_첫화면도_로그인으로_보낸다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


def test_시험공개에서는_일반로그인도_본문에_들어오지_못한다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    session = auth_logic.create_session(email="일반@example.com", is_admin=False)

    response = client.get(
        "/", cookies={SESSION: session.token}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/not-admin"


def test_시험공개에서는_관리자만_첫화면을_연다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)

    response = client.get("/", cookies={SESSION: session.token})

    assert response.status_code == 200


def test_시험공개여도_로그인과_상태확인은_열려있다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    original_connect = web_main.storage_db.connect
    statements: list[str] = []
    rollback_calls: list[bool] = []
    transaction_states: list[bool] = []

    class TrackedConnection:
        def __init__(self, conn):
            self._conn = conn

        @property
        def in_transaction(self):
            return self._conn.in_transaction

        def execute(self, statement, parameters=()):
            statements.append(statement)
            return self._conn.execute(statement, parameters)

        def rollback(self):
            rollback_calls.append(True)
            return self._conn.rollback()

    @contextmanager
    def tracked_connect():
        with original_connect() as conn:
            try:
                yield TrackedConnection(conn)
            finally:
                transaction_states.append(conn.in_transaction)

    monkeypatch.setattr(web_main.storage_db, "connect", tracked_connect)

    assert client.get("/auth/not-admin").status_code == 200
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert statements == ["SELECT 1", "BEGIN IMMEDIATE"]
    assert rollback_calls == [True]
    assert transaction_states == [False]


def test_상태확인은_SQLite를_쓸수없으면_실패한다(client, monkeypatch):
    statements: list[str] = []

    class WriteRejectedConnection:
        in_transaction = False

        def execute(self, statement, _parameters=()):
            statements.append(statement)
            if statement == "BEGIN IMMEDIATE":
                raise sqlite3.OperationalError("시험용 쓰기 트랜잭션 오류")
            return self

        def fetchone(self):
            return (1,)

        def rollback(self):
            self.in_transaction = False

    @contextmanager
    def write_rejected_connect():
        yield WriteRejectedConnection()

    monkeypatch.setattr(web_main.storage_db, "connect", write_rejected_connect)

    health = client.get("/healthz")

    assert health.status_code == 503
    assert health.json() == {"status": "unhealthy"}
    assert statements == ["SELECT 1", "BEGIN IMMEDIATE"]


# ── 도우미 ──────────────────────────────────────────────

def _fake_request(cookies: dict[str, str]):
    """`require_admin`은 쿠키만 본다. 진짜 요청을 만들 필요가 없다."""

    class _Req:
        def __init__(self, c: dict[str, str]) -> None:
            self.cookies = c

    return _Req(cookies)
