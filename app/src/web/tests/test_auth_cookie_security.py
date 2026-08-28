from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.core.constants import PIPELINE_ENV
from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import sessions as session_store
from src.web import runtime
from src.web.main import app
from src.web.routers import auth as auth_router


SESSION = auth_constants.SESSION_COOKIE_NAME
STATE = auth_constants.STATE_COOKIE_NAME
LOCAL_DEMO_TOKEN = "ab" * 32
OAUTH_STATE = "s" * auth_constants.STATE_TOKEN_CHARS


REQUEST_CASES = (
    pytest.param(
        "http://127.0.0.1:8000",
        "127.0.0.1",
        {},
        False,
        id="local-http",
    ),
    pytest.param(
        "https://127.0.0.1:8443",
        "127.0.0.1",
        {},
        True,
        id="local-https",
    ),
    pytest.param(
        "http://public.example",
        "203.0.113.10",
        {},
        True,
        id="public-http",
    ),
    pytest.param(
        "https://public.example",
        "203.0.113.10",
        {},
        True,
        id="public-https",
    ),
    pytest.param(
        "http://public.example",
        "127.0.0.1",
        {"X-Forwarded-For": "203.0.113.10"},
        True,
        id="public-http-loopback-proxy",
    ),
    pytest.param(
        "http://public.example",
        "127.0.0.1",
        {"Host": "localhost:8000"},
        True,
        id="host-spoof-loopback-proxy",
    ),
)


def _cookie_header(response, name: str) -> str:
    matches = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.lower().startswith(f"{name.lower()}=")
    ]
    assert len(matches) == 1, response.headers.get_list("set-cookie")
    return matches[0]


def _assert_secure(header: str, expected: bool) -> None:
    assert ("; secure" in header.lower()) is expected


def _callback_result(previous_session_token: str | None) -> auth_google.LoginResult:
    session = auth_logic.rotate_session(
        "admin@example.com",
        is_admin=True,
        previous_token=previous_session_token,
    )
    return auth_google.LoginResult(
        email=session.email,
        is_admin=session.is_admin,
        session=session,
    )


@pytest.mark.parametrize(
    ("base_url", "client_host", "headers", "secure"), REQUEST_CASES
)
def test_oauth_state_cookie_secure_is_scoped_to_direct_loopback_http(
    monkeypatch, base_url, client_host, headers, secure
):
    monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "1")
    monkeypatch.setattr(
        auth_google,
        "start_login",
        lambda: auth_google.LoginStart(
            auth_url="https://accounts.example/authorize",
            state=OAUTH_STATE,
        ),
    )

    with TestClient(
        app, base_url=base_url, client=(client_host, 50000)
    ) as client:
        response = client.get(
            "/auth/login", headers=headers, follow_redirects=False
        )

    assert response.status_code == 303
    cookie = _cookie_header(response, STATE)
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    _assert_secure(cookie, secure)


def test_explicit_flag_is_required_even_on_direct_loopback_http(monkeypatch):
    monkeypatch.delenv(auth_constants.ENV_COOKIE_INSECURE, raising=False)
    monkeypatch.setattr(
        auth_google,
        "start_login",
        lambda: auth_google.LoginStart(
            auth_url="https://accounts.example/authorize",
            state=OAUTH_STATE,
        ),
    )

    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    ) as client:
        response = client.get("/auth/login", follow_redirects=False)

    _assert_secure(_cookie_header(response, STATE), True)


@pytest.mark.parametrize(
    ("base_url", "client_host", "headers", "secure"), REQUEST_CASES
)
def test_oauth_success_rotates_session_and_scopes_set_and_delete_cookies(
    monkeypatch, base_url, client_host, headers, secure
):
    monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "1")
    old = auth_logic.create_session("admin@example.com", is_admin=True)

    def fake_callback(
        code,
        state_received,
        state_expected,
        *,
        previous_session_token=None,
        provider_deadline_monotonic=None,
    ):
        assert provider_deadline_monotonic is not None
        assert (code, state_received, state_expected) == (
            "code",
            OAUTH_STATE,
            OAUTH_STATE,
        )
        assert previous_session_token == old.token
        return _callback_result(previous_session_token)

    monkeypatch.setattr(auth_google, "handle_callback", fake_callback)
    auth_router._issue_oauth_state(OAUTH_STATE)
    request_headers = {
        **headers,
        "Cookie": f"{STATE}={OAUTH_STATE}; {SESSION}={old.token}",
    }
    with TestClient(
        app, base_url=base_url, client=(client_host, 50000)
    ) as client:
        response = client.get(
            f"/auth/callback?code=code&state={OAUTH_STATE}",
            headers=request_headers,
            follow_redirects=False,
        )

    assert response.status_code == 303
    state_delete = _cookie_header(response, STATE)
    session_set = _cookie_header(response, SESSION)
    assert "Max-Age=0" in state_delete and "HttpOnly" in state_delete
    assert "HttpOnly" in session_set and "SameSite=lax" in session_set
    _assert_secure(state_delete, secure)
    _assert_secure(session_set, secure)

    new_token = response.cookies[SESSION]
    assert new_token != old.token
    assert auth_logic.get_session(old.token) is None
    assert auth_logic.get_session(new_token) is not None

    with TestClient(app) as old_client:
        old_client.cookies.set(SESSION, old.token)
        assert old_client.get("/admin", follow_redirects=False).status_code == 303
    with TestClient(app) as new_client:
        new_client.cookies.set(SESSION, new_token)
        assert new_client.get("/admin", follow_redirects=False).status_code == 200


@pytest.mark.parametrize(
    ("base_url", "client_host", "headers", "secure"), REQUEST_CASES
)
def test_logout_delete_cookie_uses_the_same_request_scoped_secure_policy(
    monkeypatch, base_url, client_host, headers, secure
):
    monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "1")
    session = auth_logic.create_session("admin@example.com", is_admin=True)
    request_headers = {**headers, "Cookie": f"{SESSION}={session.token}"}

    with TestClient(
        app, base_url=base_url, client=(client_host, 50000)
    ) as client:
        response = client.post(
            "/auth/logout",
            data={
                "csrf_token": auth_logic.csrf_token_for_session(session.token)
            },
            headers=request_headers,
            follow_redirects=False,
        )

    assert response.status_code == 303
    deletion = _cookie_header(response, SESSION)
    assert "Max-Age=0" in deletion and "HttpOnly" in deletion
    _assert_secure(deletion, secure)
    assert auth_logic.get_session(session.token) is None


def _enable_local_demo(monkeypatch) -> None:
    monkeypatch.setenv(auth_constants.ENV_LOCAL_DEMO_AUTH, "1")
    monkeypatch.setenv(auth_constants.ENV_LOCAL_DEMO_AUTH_TOKEN, LOCAL_DEMO_TOKEN)
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "1")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "demo-admin@localhost")
    monkeypatch.setenv(PIPELINE_ENV, "demo")
    for name in (
        auth_constants.ENV_CLIENT_ID,
        auth_constants.ENV_CLIENT_SECRET,
        auth_constants.ENV_REDIRECT_URI,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())


def _local_demo_state(body: str) -> str:
    matched = re.search(r'name="state" value="([^"]+)"', body)
    assert matched is not None
    return matched.group(1)


def test_local_demo_relogin_rotates_old_session(monkeypatch):
    _enable_local_demo(monkeypatch)
    old = auth_logic.create_session("demo-admin@localhost", is_admin=True)

    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    ) as client:
        client.cookies.set(SESSION, old.token)
        client.get(f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}")
        landing = client.get("/auth/local-demo")
        response = client.post(
            "/auth/local-demo",
            data={"state": _local_demo_state(landing.text)},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    new_token = response.cookies[SESSION]
    assert new_token != old.token
    assert auth_logic.get_session(old.token) is None
    assert auth_logic.get_session(new_token) is not None

    with TestClient(app) as old_client:
        old_client.cookies.set(SESSION, old.token)
        assert old_client.get("/admin", follow_redirects=False).status_code == 303
    with TestClient(app) as new_client:
        new_client.cookies.set(SESSION, new_token)
        assert new_client.get("/admin", follow_redirects=False).status_code == 200


def test_local_demo_login_navigation_logout_rejects_reused_raw_cookie(
    monkeypatch,
):
    _enable_local_demo(monkeypatch)

    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    ) as client:
        client.get(f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}")
        landing = client.get("/auth/local-demo")
        logged_in = client.post(
            "/auth/local-demo",
            data={"state": _local_demo_state(landing.text)},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )
        raw_token = logged_in.cookies[SESSION]
        assert client.get("/admin", follow_redirects=False).status_code == 200

        logged_out = client.post(
            "/auth/logout",
            data={"csrf_token": auth_logic.csrf_token_for_session(raw_token)},
            follow_redirects=False,
        )
        assert logged_out.status_code == 303

        client.cookies.set(SESSION, raw_token)
        reused = client.get("/admin", follow_redirects=False)

    assert auth_logic.get_session(raw_token) is None
    assert reused.status_code == 303
    assert reused.headers["location"] == "/auth/not-admin"


def test_failed_oauth_callback_keeps_existing_session(monkeypatch):
    old = auth_logic.create_session("admin@example.com", is_admin=True)

    def reject_callback(*_args, **_kwargs):
        raise auth_google.GoogleAuthError("rejected")

    monkeypatch.setattr(auth_google, "handle_callback", reject_callback)
    with TestClient(app) as client:
        response = client.get(
            "/auth/callback?code=bad&state=bad",
            headers={"Cookie": f"{STATE}=expected; {SESSION}={old.token}"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert auth_logic.get_session(old.token) is not None


def test_failed_local_demo_relogin_keeps_existing_session(monkeypatch):
    _enable_local_demo(monkeypatch)
    old = auth_logic.create_session("demo-admin@localhost", is_admin=True)

    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50000),
    ) as client:
        client.cookies.set(SESSION, old.token)
        client.get(f"/auth/local-demo/start?token={LOCAL_DEMO_TOKEN}")
        client.get("/auth/local-demo")
        response = client.post(
            "/auth/local-demo",
            data={"state": "wrong-state"},
            headers={"Origin": "http://127.0.0.1:8000"},
            follow_redirects=False,
        )

    assert response.status_code == 404
    assert auth_logic.get_session(old.token) is not None


def test_real_oauth_handler_rotates_only_after_fake_provider_success(
    monkeypatch,
):
    monkeypatch.setenv(auth_constants.ENV_CLIENT_ID, "client-id")
    monkeypatch.setenv(auth_constants.ENV_CLIENT_SECRET, "client-secret")
    monkeypatch.setenv(
        auth_constants.ENV_REDIRECT_URI,
        "http://127.0.0.1:8000/auth/callback",
    )
    old = auth_logic.create_session("admin@example.com", is_admin=True)
    state = auth_logic.make_state()

    result = auth_google.handle_callback(
        "code",
        state,
        state,
        previous_session_token=old.token,
        exchange=lambda *_args: {"access_token": "fake-token"},
        fetch=lambda _token: {
            "email": "admin@example.com",
            "email_verified": True,
            "sub": "admin-subject-1",
        },
    )

    assert auth_logic.get_session(old.token) is None
    assert auth_logic.get_session(result.session.token) is not None


def test_real_oauth_handler_failure_does_not_rotate_existing_session(
    monkeypatch,
):
    monkeypatch.setenv(auth_constants.ENV_CLIENT_ID, "client-id")
    monkeypatch.setenv(auth_constants.ENV_CLIENT_SECRET, "client-secret")
    monkeypatch.setenv(
        auth_constants.ENV_REDIRECT_URI,
        "http://127.0.0.1:8000/auth/callback",
    )
    old = auth_logic.create_session("admin@example.com", is_admin=True)

    with pytest.raises(auth_logic.StateMismatchError):
        auth_google.handle_callback(
            "code",
            "wrong-state",
            "expected-state",
            previous_session_token=old.token,
            exchange=lambda *_args: pytest.fail("provider must not be called"),
            fetch=lambda _token: pytest.fail("provider must not be called"),
        )

    assert auth_logic.get_session(old.token) is not None


def test_session_rotation_rolls_back_old_token_if_new_token_cannot_be_saved(
    monkeypatch,
):
    old = auth_logic.create_session("admin@example.com", is_admin=True)

    def reject_save(*_args, **_kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(session_store, "save_session", reject_save)
    with pytest.raises(RuntimeError, match="simulated storage failure"):
        auth_logic.rotate_session(
            "admin@example.com",
            is_admin=True,
            previous_token=old.token,
        )

    assert auth_logic.get_session(old.token) is not None
