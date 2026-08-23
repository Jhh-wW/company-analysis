"""로그인·권한 «연결»이 실제로 도는지 화면 층에서 확인한다.

정본: 확정/07_출력/3_기준/01_성공기준.md **P4** —
「권한 없는 계정에 노션 또는 대시보드 경로가 열림 = **0건 고정**, 미달 시 확산 중단」

★ 이 시험이 지키는 것 — **버튼을 숨기는 것은 권한이 아니다.**
  화면에 안 보여도 주소를 직접 치면 열리면 P4 위반이다. 그래서 여기서 «직접 호출»한다.
  근거 → 확정/07_출력/4_근거/01_출력근거.md §4

★ 진짜 구글에 접속하지 않는다. 열쇠(환경변수)도 넣지 않는다.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.core.constants import PIPELINE_ENV
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as web_share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as web_storage_db
from src.web import deployment_mode, main as web_main
from src.web import paid_runtime, request_helpers, runtime
from src.features.provenance import sources as provenance_sources
from src.web.main import app, require_admin
from src.web.routers import auth as auth_router

SESSION = auth_constants.SESSION_COOKIE_NAME
ANALYSIS_FORM = {
    "company": "우리엔",
    "job": "영업",
    "region": "서울",
    "posting_text": "공고",
}
LOCAL_DEMO_TOKEN = "ab" * 32


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


def _enable_local_demo_login(monkeypatch) -> None:
    """OAuth 없이도 로컬 관리자 흐름을 시험할 명시적 안전 조건 전부."""
    monkeypatch.setenv(auth_constants.ENV_LOCAL_DEMO_AUTH, "1")
    monkeypatch.setenv(auth_constants.ENV_LOCAL_DEMO_AUTH_TOKEN, LOCAL_DEMO_TOKEN)
    monkeypatch.setenv(PIPELINE_ENV, "demo")
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "1")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "demo-admin@localhost")
    for name in (
        auth_constants.ENV_CLIENT_ID,
        auth_constants.ENV_CLIENT_SECRET,
        auth_constants.ENV_REDIRECT_URI,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())


def _local_auth_client(*, host: str = "127.0.0.1", client_host: str = "127.0.0.1"):
    return TestClient(
        app,
        base_url=f"http://{host}:8000",
        client=(client_host, 50000),
    )


def _local_demo_state(body: str) -> str:
    matched = re.search(r'name="state" value="([^"]+)"', body)
    assert matched is not None
    return matched.group(1)


def test_로컬_무료데모는_OAuth없이_관리자화면까지_들어간다(monkeypatch):
    _enable_local_demo_login(monkeypatch)

    with _local_auth_client() as local_client:
        login = local_client.get("/auth/login")
        assert login.status_code == 503
        assert 'data-local-demo-login="true"' not in login.text
        assert LOCAL_DEMO_TOKEN not in login.text

        start = local_client.get(
            f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}",
            follow_redirects=False,
        )
        assert start.status_code == 303
        assert start.headers["location"] == "/auth/local-demo"
        assert LOCAL_DEMO_TOKEN not in start.headers["location"]
        landing = local_client.get(start.headers["location"])
        assert landing.status_code == 200
        assert 'data-local-demo-login="true"' in landing.text

        response = local_client.post(
            "/auth/local-demo",
            data={"state": _local_demo_state(landing.text)},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/admin"
        cookie = response.headers.get("set-cookie", "")
        assert "auth_session=" in cookie
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        session_token = local_client.cookies.get(auth_constants.SESSION_COOKIE_NAME)
        assert auth_logic.current_subject(session_token) == (
            auth_constants.LOCAL_DEMO_IDENTITY_SUBJECT
        )
        assert local_client.get("/admin").status_code == 200
        assert "demo-admin@localhost" in local_client.get("/admin").text


@pytest.mark.parametrize(
    "blocked_case",
    [
        "pipeline_env",
        "runtime_pipeline",
        "beta_gate",
        "explicit_switch",
        "public_host",
        "remote_client",
        "oauth_partial",
        "oauth_configured",
        "no_admin",
        "secure_cookie_mode",
        "missing_capability",
    ],
)
def test_로컬_데모로그인은_안전조건_하나라도_다르면_404이고_세션을_안만든다(
    monkeypatch, blocked_case
):
    _enable_local_demo_login(monkeypatch)
    host = "127.0.0.1"
    client_host = "127.0.0.1"
    if blocked_case == "pipeline_env":
        monkeypatch.setenv(PIPELINE_ENV, "real")
    elif blocked_case == "runtime_pipeline":
        monkeypatch.setattr(runtime, "_PIPELINE", object())
    elif blocked_case == "beta_gate":
        monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    elif blocked_case == "explicit_switch":
        monkeypatch.setenv(auth_constants.ENV_LOCAL_DEMO_AUTH, "0")
    elif blocked_case == "public_host":
        host = "demo.example"
    elif blocked_case == "remote_client":
        client_host = "203.0.113.10"
    elif blocked_case == "oauth_partial":
        monkeypatch.setenv(auth_constants.ENV_CLIENT_ID, "partly-configured")
    elif blocked_case == "oauth_configured":
        monkeypatch.setenv(auth_constants.ENV_CLIENT_ID, "configured")
        monkeypatch.setenv(auth_constants.ENV_CLIENT_SECRET, "configured")
        monkeypatch.setenv(
            auth_constants.ENV_REDIRECT_URI,
            "https://demo.example/auth/callback",
        )
    elif blocked_case == "no_admin":
        monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "")
    elif blocked_case == "secure_cookie_mode":
        monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "0")
    elif blocked_case == "missing_capability":
        monkeypatch.delenv(auth_constants.ENV_LOCAL_DEMO_AUTH_TOKEN, raising=False)

    monkeypatch.setattr(
        auth_logic,
        "create_session",
        lambda *_args, **_kwargs: pytest.fail("거부 요청이 세션을 만들었습니다"),
    )
    with _local_auth_client(host=host, client_host=client_host) as local_client:
        response = local_client.get(
            f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}",
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert auth_constants.SESSION_COOKIE_NAME not in response.headers.get(
        "set-cookie", ""
    )


@pytest.mark.parametrize(
    ("state_kind", "origin"),
    [
        ("wrong", "http://127.0.0.1:8000"),
        ("valid", "https://attacker.example"),
        ("valid", "null"),
    ],
)
def test_로컬_데모로그인도_state와_same_origin을_모두_검사한다(
    monkeypatch, state_kind, origin
):
    _enable_local_demo_login(monkeypatch)

    with _local_auth_client() as local_client:
        local_client.get(f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}")
        landing = local_client.get("/auth/local-demo")
        valid_state = _local_demo_state(landing.text)
        response = local_client.post(
            "/auth/local-demo",
            data={"state": valid_state if state_kind == "valid" else "wrong"},
            headers={"Origin": origin},
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert auth_constants.SESSION_COOKIE_NAME not in response.headers.get(
        "set-cookie", ""
    )


@pytest.mark.parametrize("token", ["", "cd" * 32, "너무짧음"])
def test_로컬_capability가_없거나_틀리면_흔적없이_404다(monkeypatch, token):
    _enable_local_demo_login(monkeypatch)
    suffix = f"?token={token}" if token else ""
    with _local_auth_client() as local_client:
        response = local_client.get(
            f"/auth/local-demo/start{suffix}", follow_redirects=False
        )

    assert response.status_code == 404
    assert "local_demo" not in response.headers.get("set-cookie", "")
    assert LOCAL_DEMO_TOKEN not in response.text


def test_공개_https프록시가_Host와_client를_loopback으로_꾸며도_거부한다(
    monkeypatch,
):
    _enable_local_demo_login(monkeypatch)
    with TestClient(
        app,
        base_url="https://public.example",
        client=("127.0.0.1", 50000),
    ) as public_proxy:
        response = public_proxy.get(
            f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}",
            headers={"Host": "localhost:8000"},
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert auth_constants.SESSION_COOKIE_NAME not in response.headers.get(
        "set-cookie", ""
    )


def test_root_capability는_로그아웃뒤_재진입용으로_새_grant를_다시_발급한다(
    monkeypatch,
):
    _enable_local_demo_login(monkeypatch)
    with _local_auth_client() as local_client:
        first = local_client.get(
            f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}",
            follow_redirects=False,
        )
        first_grant = local_client.cookies.get(
            auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME
        )
        second = local_client.get(
            f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}",
            follow_redirects=False,
        )
        second_grant = local_client.cookies.get(
            auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME
        )

    assert first.status_code == second.status_code == 303
    assert first_grant and second_grant and first_grant != second_grant


def test_grant와_state는_성공뒤_재사용할수없다(monkeypatch):
    _enable_local_demo_login(monkeypatch)
    with _local_auth_client() as local_client:
        local_client.get(f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}")
        landing = local_client.get("/auth/local-demo")
        state = _local_demo_state(landing.text)
        grant = local_client.cookies.get(auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME)
        state_cookie = local_client.cookies.get(
            auth_constants.LOCAL_DEMO_STATE_COOKIE_NAME
        )
        succeeded = local_client.post(
            "/auth/local-demo",
            data={"state": state},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )
        local_client.cookies.clear()
        local_client.cookies.set(auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME, grant)
        local_client.cookies.set(
            auth_constants.LOCAL_DEMO_STATE_COOKIE_NAME, state_cookie
        )
        replayed = local_client.post(
            "/auth/local-demo",
            data={"state": state},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )

    assert succeeded.status_code == 303
    assert replayed.status_code == 404


def test_2분이_지난_grant는_깨끗한_화면도_열지못한다(monkeypatch):
    _enable_local_demo_login(monkeypatch)
    issued_at = auth_router.time.monotonic()
    with _local_auth_client() as local_client:
        start = local_client.get(
            f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}",
            follow_redirects=False,
        )
        assert start.status_code == 303
        monkeypatch.setattr(
            auth_router.time,
            "monotonic",
            lambda: issued_at + auth_constants.LOCAL_DEMO_GRANT_MAX_AGE_SEC + 1,
        )
        expired = local_client.get("/auth/local-demo", follow_redirects=False)

    assert expired.status_code == 404


def test_공격자가_grant와_state쿠키를_직접_지어내도_POST가_안열린다(
    monkeypatch,
):
    _enable_local_demo_login(monkeypatch)
    forged = "attacker-chosen-state"
    with _local_auth_client() as local_client:
        local_client.cookies.set(
            auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME, "attacker-grant"
        )
        local_client.cookies.set(
            auth_constants.LOCAL_DEMO_STATE_COOKIE_NAME, forged
        )
        response = local_client.post(
            "/auth/local-demo",
            data={"state": forged},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert auth_constants.SESSION_COOKIE_NAME not in response.headers.get(
        "set-cookie", ""
    )


def test_잘못된_state로_돌아오면_로그인이_안_된다(client):
    """★ CSRF 방어 — 남이 만든 로그인 흐름을 가로챌 수 없어야 한다."""
    response = client.get(
        "/auth/callback?code=aaa&state=공격자가_만든_state", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION not in response.cookies


def test_로그아웃은_세션_쿠키를_지운다(client):
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)
    client.cookies.set(SESSION, session.token)
    response = client.post(
        "/auth/logout",
        data={"csrf_token": auth_logic.csrf_token_for_session(session.token)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["clear-site-data"] == '"cache", "storage"'
    assert 'auth_session=""' in response.headers.get("set-cookie", "") or (
        "auth_session=;" in response.headers.get("set-cookie", "")
    )


def test_임의로_심은_세션쿠키는_자체_csrf를_계산해도_로그아웃이_안된다(client):
    forged = "attacker-chosen-session"
    client.cookies.set(SESSION, forged)

    response = client.post(
        "/auth/logout",
        data={"csrf_token": auth_logic.csrf_token_for_session(forged)},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert "clear-site-data" not in response.headers


def test_로그아웃은_GET이나_CSRF없는_POST로_시킬수없다(client):
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)
    client.cookies.set(SESSION, session.token)

    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout", data={}).status_code == 403
    assert auth_logic.get_session(session.token) is not None


# ── 비용 입력 폼 CSRF ───────────────────────────────────

@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/confirm", ANALYSIS_FORM),
        ("/reject", ANALYSIS_FORM),
        ("/run", {**ANALYSIS_FORM, "ref": "재수집-p003"}),
    ],
)
def test_권한쿠키가_있는_비용입력_POST는_CSRF없으면_막힌다(
    client, monkeypatch, path, data
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(SESSION, session.token)

    response = client.post(path, data=data, follow_redirects=False)

    assert response.status_code == 403


def test_비ASCII_CSRF도_500없이_거부한다(client, monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(SESSION, session.token)

    response = client.post(
        "/confirm", data={**ANALYSIS_FORM, "csrf_token": "한글"}
    )

    assert response.status_code == 403


def test_유효세션_CSRF와_같은_HTTP출처는_비용입력에_통과한다(
    client, monkeypatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(SESSION, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)

    response = client.post(
        "/confirm",
        data={**ANALYSIS_FORM, "csrf_token": csrf},
        headers={"Origin": "http://testserver:80"},
    )

    assert response.status_code == 200
    assert response.headers["referrer-policy"] == "same-origin"


@pytest.mark.parametrize(
    "origin",
    [
        "https://testserver",
        "custom://testserver",
        "http://testserver:81",
        "http://testserver/path",
        "http://attacker.example",
    ],
)
def test_CSRF_Origin은_scheme_host_effective_port가_모두_같아야_한다(
    client, monkeypatch, origin
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(SESSION, session.token)

    response = client.post(
        "/confirm",
        data={
            **ANALYSIS_FORM,
            "csrf_token": auth_logic.csrf_token_for_session(session.token),
        },
        headers={"Origin": origin},
    )

    assert response.status_code == 403


def _enable_narrow_render_admin_demo(monkeypatch) -> None:
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")


def test_좁은Render계약은_내부HTTP가_아닌_PUBLIC_ORIGIN으로_POST를_검사한다(
    client, monkeypatch
):
    _enable_narrow_render_admin_demo(monkeypatch)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(SESSION, session.token)

    response = client.post(
        "/confirm",
        data={
            **ANALYSIS_FORM,
            "csrf_token": auth_logic.csrf_token_for_session(session.token),
        },
        headers={
            "Host": "demo.example",
            "Origin": "https://demo.example",
            "X-Forwarded-For": "203.0.113.99",
            "X-Forwarded-Host": "attacker.example",
            "X-Forwarded-Proto": "http",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    (
        None,
        "null",
        "http://demo.example",
        "https://demo.example:444",
        "https://demo.example/path",
        "https://attacker.example",
    ),
)
def test_좁은Render계약은_Origin누락과_불일치를_거부한다(
    client, monkeypatch, origin
):
    _enable_narrow_render_admin_demo(monkeypatch)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(SESSION, session.token)
    headers = {
        "Host": "demo.example",
        "X-Forwarded-Host": "demo.example",
        "X-Forwarded-Proto": "https",
    }
    if origin is not None:
        headers["Origin"] = origin

    response = client.post(
        "/confirm",
        data={
            **ANALYSIS_FORM,
            "csrf_token": auth_logic.csrf_token_for_session(session.token),
        },
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_좁은Render계약은_중복Origin을_거부한다(client, monkeypatch):
    _enable_narrow_render_admin_demo(monkeypatch)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(SESSION, session.token)

    response = client.post(
        "/confirm",
        data={
            **ANALYSIS_FORM,
            "csrf_token": auth_logic.csrf_token_for_session(session.token),
        },
        headers=[
            ("Host", "demo.example"),
            ("Origin", "https://demo.example"),
            ("Origin", "https://attacker.example"),
        ],
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_세션과_링크가_함께_있으면_세션_CSRF가_우선한다(
    client, monkeypatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("member@example.com", False)
    link_key = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
    with web_storage_db.connect() as conn:
        web_share_store.insert_new(
            conn,
            key=link_key,
            company="우리엔",
            job="영업",
            now_iso="2026-08-17T10:00:00",
        )
    client.cookies.set(SESSION, session.token)
    client.cookies.set(KEY_COOKIE_NAME, link_key)

    link_token = auth_logic.csrf_token_for_session(link_key)
    session_token = auth_logic.csrf_token_for_session(session.token)
    assert client.post(
        "/confirm", data={**ANALYSIS_FORM, "csrf_token": link_token}
    ).status_code == 403
    assert client.post(
        "/confirm", data={**ANALYSIS_FORM, "csrf_token": session_token}
    ).status_code == 200


@pytest.mark.parametrize("session_kind", ["invalid", "expired"])
def test_무효세션과_활성링크가_함께있으면_링크_CSRF를_쓴다(
    client, monkeypatch, session_kind
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    link_key = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
    with web_storage_db.connect() as conn:
        web_share_store.insert_new(
            conn,
            key=link_key,
            company="우리엔",
            job="영업",
            now_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
        )

    if session_kind == "expired":
        session_secret = auth_logic.create_session(
            "expired@example.com", False, now=0
        ).token
    else:
        session_secret = "attacker-chosen-invalid-session"

    cookie_header = (
        f"{SESSION}={session_secret}; {KEY_COOKIE_NAME}={link_key}"
    ).encode("ascii")
    request = Request({"type": "http", "headers": [(b"cookie", cookie_header)]})
    assert request_helpers._request_csrf_secret(request) == link_key

    client.cookies.set(SESSION, session_secret)
    client.cookies.set(KEY_COOKIE_NAME, link_key)
    link_csrf = auth_logic.csrf_token_for_session(link_key)
    invalid_session_csrf = auth_logic.csrf_token_for_session(session_secret)

    accepted = client.post(
        "/confirm", data={**ANALYSIS_FORM, "csrf_token": link_csrf}
    )
    rejected = client.post(
        "/confirm", data={**ANALYSIS_FORM, "csrf_token": invalid_session_csrf}
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 403


@pytest.mark.parametrize(
    ("cookie_name", "cookie_value"),
    [
        (SESSION, "forged-session"),
        (KEY_COOKIE_NAME, "a1b2c3d4e5f60718a1b2c3d4e5f60718"),
    ],
)
def test_무효_권한쿠키와_그값의_CSRF로_real_confirm을_열수없다(
    client, monkeypatch, cookie_name, cookie_value
):
    class NeverCalledPaidPipeline:
        def find_company_metered(self, *_args, **_kwargs):
            raise AssertionError("CSRF 검증 전에 유료 회사 식별을 부르면 안 됩니다")

    monkeypatch.setattr(runtime, "_PIPELINE", NeverCalledPaidPipeline())
    client.cookies.set(cookie_name, cookie_value)

    response = client.post(
        "/confirm",
        data={
            **ANALYSIS_FORM,
            "csrf_token": auth_logic.csrf_token_for_session(cookie_value),
        },
    )

    assert response.status_code == 403


def test_권한쿠키없는_공개손님은_데모만_CSRF없이_쓸수있다(
    client, monkeypatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    assert client.post("/confirm", data=ANALYSIS_FORM).status_code == 200

    monkeypatch.setattr(runtime, "_PIPELINE", object())
    assert client.post("/confirm", data=ANALYSIS_FORM).status_code == 403


@pytest.mark.parametrize(
    "origin", ["http://localhost:8000", "http://localhost:8000/", "null"]
)
def test_로컬_익명_데모는_브라우저_Origin차이에도_확인화면이_열린다(
    monkeypatch, origin
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(app, base_url="http://127.0.0.1:8000") as local_client:
        response = local_client.post(
            "/confirm", data=ANALYSIS_FORM, headers={"Origin": origin}
        )

    assert response.status_code == 200
    assert 'action="/run"' in response.text


def test_로컬_익명_데모도_외부사이트_Origin은_허용하지않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(app, base_url="http://127.0.0.1:8000") as local_client:
        response = local_client.post(
            "/confirm",
            data=ANALYSIS_FORM,
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403


def test_로컬이어도_실조사는_다른_Origin을_허용하지않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", object())

    with TestClient(app, base_url="http://127.0.0.1:8000") as local_client:
        response = local_client.post(
            "/confirm",
            data=ANALYSIS_FORM,
            headers={"Origin": "http://localhost:8000"},
        )

    assert response.status_code == 403


def test_배포주소의_익명_데모는_다른_Origin을_허용하지않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(app, base_url="https://demo.example") as public_client:
        response = public_client.post(
            "/confirm",
            data=ANALYSIS_FORM,
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403


@pytest.mark.parametrize("credential_kind", ["session", "share"])
@pytest.mark.parametrize("origin", ["http://localhost:8000", "null"])
def test_로컬_데모여도_유효권한쿠키는_다른_Origin이나_null에서_쓸수없다(
    monkeypatch, credential_kind, origin
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    if credential_kind == "session":
        secret = auth_logic.create_session("member@example.com", False).token
        cookie_name = SESSION
    else:
        secret = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
        cookie_name = KEY_COOKIE_NAME
        with web_storage_db.connect() as conn:
            web_share_store.insert_new(
                conn,
                key=secret,
                company="우리엔",
                job="영업",
                now_iso=dt.datetime.now(dt.timezone.utc).isoformat(),
            )

    with TestClient(app, base_url="http://127.0.0.1:8000") as local_client:
        local_client.cookies.set(cookie_name, secret)
        response = local_client.post(
            "/confirm",
            data={
                **ANALYSIS_FORM,
                "csrf_token": auth_logic.csrf_token_for_session(secret),
            },
            headers={"Origin": origin},
        )

    assert response.status_code == 403


def test_비용입력_모든_화면이_검증된_쿠키의_CSRF를_폼에_싣는다(
    client, monkeypatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    session = auth_logic.create_session("member@example.com", False)
    client.cookies.set(SESSION, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)

    landing = client.get("/").text
    confirm = client.post(
        "/confirm", data={**ANALYSIS_FORM, "csrf_token": csrf}
    ).text
    retry = client.post(
        "/confirm",
        data={
            **ANALYSIS_FORM,
            "company": "존재하지않는회사이름",
            "csrf_token": csrf,
        },
    ).text

    hidden = f'name="csrf_token" value="{csrf}"'
    assert hidden in landing
    assert confirm.count(hidden) >= 2  # /run과 /reject
    assert hidden in retry


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


def test_로그인_가능한_익명_화면에는_로그인만_보이고_관리자_직접링크는_없다(
    client, monkeypatch
):
    monkeypatch.setenv(auth_constants.ENV_CLIENT_ID, "configured-client")
    monkeypatch.setenv(auth_constants.ENV_CLIENT_SECRET, "configured-secret")
    monkeypatch.setenv(
        auth_constants.ENV_REDIRECT_URI,
        "https://service.example/auth/callback",
    )

    body = client.get("/").text

    assert 'data-login-available="true"' in body
    assert 'href="/auth/login"' in body
    assert 'href="/admin"' not in body


def test_로그인_링크는_흰_상단바에서도_보이는_색을_쓴다(client):
    css = client.get("/static/style.css").text
    start = css.index(".auth-status .auth-email")
    end = css.index("/* 「관리자」 배지", start)
    auth_rules = css[start:end]

    assert "var(--ink-2)" in auth_rules
    assert "rgba(255, 255, 255" not in auth_rules
    assert "color: #fff" not in auth_rules


def test_로그인_설정이_없으면_첫화면에서_빈_인증영역과_링크를_숨긴다(
    client, monkeypatch
):
    for env in (
        auth_constants.ENV_CLIENT_ID,
        auth_constants.ENV_CLIENT_SECRET,
        auth_constants.ENV_REDIRECT_URI,
    ):
        monkeypatch.delenv(env, raising=False)

    body = client.get("/").text

    assert 'class="auth-status"' not in body
    assert 'data-login-available="false"' not in body
    assert 'href="/auth/login"' not in body


def test_관리자_배지는_실제_링크다(client):
    session = auth_logic.create_session(email="관리자@example.com", is_admin=True)

    body = client.get("/", cookies={SESSION: session.token}).text

    assert "관리자@example.com" in body
    assert 'class="tag admin" href="/admin"' in body
    assert 'action="/auth/logout"' in body
    assert 'href="/auth/login"' not in body


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


def test_시험공개_관리자_추가와_삭제는_기존세션에도_즉시_반영된다(
    client, monkeypatch
):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "other@example.com")
    session = auth_logic.create_session("live-admin@example.com", is_admin=True)
    client.cookies.set(SESSION, session.token)

    blocked = client.get("/", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/auth/not-admin"

    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "live-admin@example.com")
    assert client.get("/").status_code == 200

    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "other@example.com")
    revoked = client.get("/", follow_redirects=False)
    assert revoked.status_code == 303
    assert revoked.headers["location"] == "/auth/not-admin"


def test_시험공개여도_로그인과_상태확인은_열려있다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    monkeypatch.setenv(auth_constants.ENV_CLIENT_ID, "client")
    monkeypatch.setenv(auth_constants.ENV_CLIENT_SECRET, "secret")
    monkeypatch.setenv(
        auth_constants.ENV_REDIRECT_URI, "https://demo.example/auth/callback"
    )
    read_checks: list[bool] = []
    monkeypatch.setattr(
        runtime, "_check_storage_read_ready", lambda: read_checks.append(True)
    )

    assert client.get("/auth/not-admin").status_code == 200
    health = client.get("/healthz")
    ready = client.get("/readyz")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert read_checks == [True]


def test_상태확인은_SQLite를_읽을수없으면_실패한다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    def reject_read():
        raise sqlite3.OperationalError("시험용 읽기 오류")

    monkeypatch.setattr(runtime, "_check_storage_read_ready", reject_read)

    health = client.get("/healthz")
    ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "unready", "failed_checks": ["storage"]}


def test_시험공개_readyz는_빠진_로그인설정의_이름만_알린다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    monkeypatch.delenv(auth_constants.ENV_CLIENT_ID, raising=False)
    monkeypatch.setenv(auth_constants.ENV_CLIENT_SECRET, "절대-응답하면-안되는-비밀")
    monkeypatch.setenv(
        auth_constants.ENV_REDIRECT_URI, "https://demo.example/auth/callback"
    )
    monkeypatch.setattr(runtime, "_check_storage_read_ready", lambda: None)

    ready = client.get("/readyz")

    assert ready.status_code == 503
    assert ready.json() == {
        "status": "unready",
        "failed_checks": [auth_constants.ENV_CLIENT_ID],
    }
    assert "절대-응답하면-안되는-비밀" not in ready.text


def test_real_비용원장잠김은_유료조사만_degraded로_드러낸다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    monkeypatch.setenv(PIPELINE_ENV, "real")
    monkeypatch.setattr(runtime, "_check_storage_read_ready", lambda: None)
    monkeypatch.setattr(paid_runtime, "_BUDGET_STORE_HEALTHY", False)

    ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json() == {
        "status": "degraded",
        "blocked_capabilities": ["paid_research:budget_store"],
    }


def test_demo는_비용원장상태를_readiness에_쓰지않는다(client, monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    monkeypatch.setenv(PIPELINE_ENV, "demo")
    monkeypatch.setattr(runtime, "_check_storage_read_ready", lambda: None)
    monkeypatch.setattr(paid_runtime, "_BUDGET_STORE_HEALTHY", False)

    ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_쓰기_가능성은_공개_health가_아니라_시작할때_한번_확인한다(monkeypatch):
    path = web_storage_db.default_db_path()
    runtime._check_storage_write_ready()

    assert path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()


def test_저장소상태확인용_SQLite연결은_항상_닫는다(tmp_path, monkeypatch):
    connections = []

    class FakeConnection:
        closed = False
        in_transaction = False

        def execute(self, sql):
            if sql == "BEGIN IMMEDIATE":
                self.in_transaction = True
            return self

        def fetchone(self):
            return (1,)

        def rollback(self):
            self.in_transaction = False

        def close(self):
            self.closed = True

    def connect(*_args, **_kwargs):
        conn = FakeConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(web_storage_db, "default_db_path", lambda: tmp_path / "db")
    monkeypatch.setattr(runtime.sqlite3, "connect", connect)

    runtime._check_storage_write_ready()
    runtime._check_storage_read_ready()

    assert len(connections) == 2
    assert all(conn.closed for conn in connections)


def test_real_pipeline은_영속_provenance_key가_없으면_시작전에_실패한다(
    monkeypatch,
):
    monkeypatch.setenv(PIPELINE_ENV, "real")
    monkeypatch.setattr(provenance_sources, "seal_key_is_persistent", lambda: False)

    with pytest.raises(RuntimeError, match="PROVENANCE_SEAL_SECRET"):
        with TestClient(app):
            pass


def test_demo_pipeline은_영속_provenance_key가_없어도_로컬실행을_허용한다(
    monkeypatch,
):
    monkeypatch.setenv(PIPELINE_ENV, "demo")
    monkeypatch.setattr(provenance_sources, "seal_key_is_persistent", lambda: False)

    with TestClient(app) as demo_client:
        assert demo_client.get("/healthz").status_code == 200


# ── 도우미 ──────────────────────────────────────────────

def _fake_request(cookies: dict[str, str]):
    """권한·감사 결합을 시험할 최소 요청 객체."""

    class _Req:
        def __init__(self, c: dict[str, str]) -> None:
            self.cookies = c
            self.headers = {}
            self.state = SimpleNamespace()
            self.url = SimpleNamespace(path="/admin")

    return _Req(cookies)
