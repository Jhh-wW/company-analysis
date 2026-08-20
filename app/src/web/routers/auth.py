"""Google 로그인, 로그인 결과, 로그아웃 경로."""

import logging
import secrets
import threading
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.web import request_helpers, runtime
from src.web.security import CSRF_TOKEN_MAX_CHARS


router = APIRouter()
logger = logging.getLogger(__name__)
_LOCAL_DEMO_COOKIE_PATH = "/auth/local-demo"
_LOCAL_DEMO_FLOW_LOCK = threading.Lock()
_LOCAL_DEMO_GRANTS: dict[str, float] = {}
_LOCAL_DEMO_STATES: dict[str, tuple[str, float]] = {}


def _local_demo_login_available(request: Request) -> bool:
    """OAuth 없는 로컬 데모에서만 관리자 입구를 연다.

    Host 헤더만 로컬로 꾸미는 요청도 통과하지 못하게 실제 TCP 클라이언트 주소까지
    loopback이어야 한다. 프록시·공개 배포에서 애매하면 항상 닫힌다.
    """
    client_host = request.client.host if request.client is not None else ""
    return bool(
        auth_logic.local_demo_auth_enabled_from_env()
        and isinstance(runtime._PIPELINE, DemoPipeline)
        and not auth_google.credentials_configured()
        and request.url.scheme.lower() == "http"
        and request_helpers._request_targets_loopback(request)
        and request_helpers._is_loopback_hostname(client_host)
    )


def _sweep_local_demo_flow_locked(now: float) -> None:
    """짧은 교환권과 state를 메모리에서 즉시 만료시킨다. lock 안에서만 부른다."""
    for grant, expires_at in tuple(_LOCAL_DEMO_GRANTS.items()):
        if expires_at <= now:
            _LOCAL_DEMO_GRANTS.pop(grant, None)
    for state, (_grant, expires_at) in tuple(_LOCAL_DEMO_STATES.items()):
        if expires_at <= now:
            _LOCAL_DEMO_STATES.pop(state, None)


def _issue_local_demo_grant() -> str:
    """검증된 root capability를 브라우저 전용 단기 교환권으로 바꾼다."""
    now = time.monotonic()
    grant = secrets.token_urlsafe(auth_constants.STATE_TOKEN_BYTES)
    with _LOCAL_DEMO_FLOW_LOCK:
        _sweep_local_demo_flow_locked(now)
        _LOCAL_DEMO_GRANTS[grant] = (
            now + auth_constants.LOCAL_DEMO_GRANT_MAX_AGE_SEC
        )
    return grant


def _issue_local_demo_state(grant: str) -> str:
    """살아 있는 교환권에만 서버 저장 CSRF state를 발급한다."""
    now = time.monotonic()
    with _LOCAL_DEMO_FLOW_LOCK:
        _sweep_local_demo_flow_locked(now)
        expires_at = _LOCAL_DEMO_GRANTS.get(grant, 0.0)
        if expires_at <= now:
            return ""
        state = auth_logic.make_state()
        _LOCAL_DEMO_STATES[state] = (grant, expires_at)
        return state


def _consume_local_demo_state(grant: str, expected: str, received: str) -> bool:
    """grant·쿠키 state·폼 state를 함께 확인하고 성공 시 한 번만 소비한다."""
    state_matches = auth_logic.state_matches(expected, received)
    now = time.monotonic()
    with _LOCAL_DEMO_FLOW_LOCK:
        _sweep_local_demo_flow_locked(now)
        grant_expires = _LOCAL_DEMO_GRANTS.get(grant, 0.0)
        state_record = _LOCAL_DEMO_STATES.get(expected)
        bound_grant = state_record[0] if state_record is not None else ""
        bound_expires = state_record[1] if state_record is not None else 0.0
        grant_matches = bool(
            grant
            and bound_grant
            and secrets.compare_digest(grant, bound_grant)
        )
        valid = bool(
            state_matches
            and grant_matches
            and grant_expires > now
            and bound_expires > now
        )
        if not valid:
            return False

        _LOCAL_DEMO_GRANTS.pop(grant, None)
        for state, (owner, _expires_at) in tuple(_LOCAL_DEMO_STATES.items()):
            if secrets.compare_digest(owner, grant):
                _LOCAL_DEMO_STATES.pop(state, None)
        return True


def _delete_local_demo_flow_cookies(
    response: HTMLResponse | RedirectResponse, request: Request
) -> None:
    response.delete_cookie(
        auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME,
        path=_LOCAL_DEMO_COOKIE_PATH,
        secure=request_helpers._cookie_secure(request),
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        auth_constants.LOCAL_DEMO_STATE_COOKIE_NAME,
        path=_LOCAL_DEMO_COOKIE_PATH,
        secure=request_helpers._cookie_secure(request),
        httponly=True,
        samesite="strict",
    )


def _delete_oauth_state_cookie(response: RedirectResponse, request: Request) -> None:
    """OAuth state 쿠키도 발급 때와 같은 보안 속성으로 지운다."""
    response.delete_cookie(
        auth_constants.STATE_COOKIE_NAME,
        secure=request_helpers._cookie_secure(request),
        httponly=True,
        samesite="lax",
    )


def _set_session_cookie(
    response: RedirectResponse, request: Request, token: str
) -> None:
    """Google·로컬 데모 로그인이 같은 세션 쿠키 정책을 쓰게 한다."""
    response.set_cookie(
        auth_constants.SESSION_COOKIE_NAME,
        token,
        max_age=auth_constants.SESSION_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=request_helpers._cookie_secure(request),
    )


@router.get("/auth/login")
async def auth_login(request: Request):
    """구글 로그인 화면으로 보낸다. 비밀번호는 구글이 받고 우리는 보지 않는다."""
    try:
        started = auth_google.start_login()
    except auth_google.MissingCredentialError as exc:
        # 로그에는 빠진 설정 이름을 남기되 화면에는 이름이나 값을 노출하지 않는다.
        logger.error("구글 로그인 설정 오류: %s", exc)
        return request_helpers.templates.TemplateResponse(
            request=request,
            name="login_unavailable.html",
            context=request_helpers._ctx(request),
            status_code=503,
        )
    response = RedirectResponse(started.auth_url, status_code=303)
    response.set_cookie(
        auth_constants.STATE_COOKIE_NAME,
        started.state,
        max_age=auth_constants.STATE_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=request_helpers._cookie_secure(request),
    )
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    """구글에서 돌아왔을 때. state를 반드시 대조한다 (CSRF 방어)."""
    expected = request.cookies.get(auth_constants.STATE_COOKIE_NAME)
    try:
        result = auth_google.handle_callback(
            code,
            state,
            expected,
            previous_session_token=request.cookies.get(
                auth_constants.SESSION_COOKIE_NAME
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 실패 이유를 화면에 흘리지 않는다
        logger.warning("로그인 실패: %s — %s", type(exc).__name__, exc)
        response = RedirectResponse("/", status_code=303)
        _delete_oauth_state_cookie(response, request)
        return response

    response = RedirectResponse(
        "/" if result.is_admin else "/auth/not-admin", status_code=303
    )
    _delete_oauth_state_cookie(response, request)
    _set_session_cookie(response, request, result.session.token)
    return response


@router.get("/auth/local-demo/start")
async def auth_local_demo_start(request: Request):
    """실행기가 안내한 root capability를 짧은 브라우저 교환권으로 바꾼다."""
    received = request.query_params.get("token", "")
    if (
        not _local_demo_login_available(request)
        or not auth_logic.local_demo_auth_token_matches(received)
    ):
        return HTMLResponse("찾을 수 없습니다.", status_code=404)

    grant = _issue_local_demo_grant()
    response = RedirectResponse(_LOCAL_DEMO_COOKIE_PATH, status_code=303)
    response.set_cookie(
        auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME,
        grant,
        max_age=auth_constants.LOCAL_DEMO_GRANT_MAX_AGE_SEC,
        httponly=True,
        samesite="strict",
        secure=request_helpers._cookie_secure(request),
        path=_LOCAL_DEMO_COOKIE_PATH,
    )
    return response


@router.get("/auth/local-demo", response_class=HTMLResponse)
async def auth_local_demo_landing(request: Request):
    """capability가 URL에서 사라진 뒤 1회용 관리자 로그인 폼을 보여준다."""
    grant = request.cookies.get(auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME) or ""
    state = _issue_local_demo_state(grant) if _local_demo_login_available(request) else ""
    if not state:
        response = HTMLResponse("찾을 수 없습니다.", status_code=404)
        _delete_local_demo_flow_cookies(response, request)
        return response

    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="local_demo_login.html",
        context=request_helpers._ctx(request, local_demo_state=state),
    )
    response.set_cookie(
        auth_constants.LOCAL_DEMO_STATE_COOKIE_NAME,
        state,
        max_age=auth_constants.LOCAL_DEMO_GRANT_MAX_AGE_SEC,
        httponly=True,
        samesite="strict",
        secure=request_helpers._cookie_secure(request),
        path=_LOCAL_DEMO_COOKIE_PATH,
    )
    return response


@router.post("/auth/local-demo")
async def auth_local_demo(
    request: Request,
    state: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """명시적으로 켠 로컬 무료 데모에서만 임시 관리자 세션을 만든다."""
    if not _local_demo_login_available(request):
        # 공개 주소에서 이 기능의 존재나 어느 조건이 어긋났는지 알려주지 않는다.
        return HTMLResponse("찾을 수 없습니다.", status_code=404)

    grant = request.cookies.get(auth_constants.LOCAL_DEMO_GRANT_COOKIE_NAME) or ""
    expected = request.cookies.get(auth_constants.LOCAL_DEMO_STATE_COOKIE_NAME) or ""
    if (
        not request_helpers._csrf_origin_matches(request)
        or not _consume_local_demo_state(grant, expected, state)
    ):
        response = HTMLResponse("찾을 수 없습니다.", status_code=404)
        _delete_local_demo_flow_cookies(response, request)
        return response

    admin_email = auth_logic.local_demo_admin_email_from_env()
    if not admin_email:
        # 앞의 설정 판정 뒤 환경이 바뀐 경우에도 권한을 만들지 않는다.
        return HTMLResponse("찾을 수 없습니다.", status_code=404)

    session = auth_logic.rotate_session(
        admin_email,
        is_admin=True,
        subject=auth_constants.LOCAL_DEMO_IDENTITY_SUBJECT,
        previous_token=request.cookies.get(auth_constants.SESSION_COOKIE_NAME),
    )
    response = RedirectResponse("/admin", status_code=303)
    _delete_local_demo_flow_cookies(response, request)
    _set_session_cookie(response, request, session.token)
    return response


@router.get("/auth/not-admin", response_class=HTMLResponse)
async def auth_not_admin(request: Request):
    """로그인은 됐지만 관리자가 아닐 때."""
    return request_helpers.templates.TemplateResponse(
        request=request,
        name="not_admin.html",
        context=request_helpers._ctx(request),
    )


@router.post("/auth/logout")
async def auth_logout(
    request: Request,
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """우리 쪽 세션만 지운다. 구글 계정 로그인 상태는 건드리지 않는다."""
    blocked = request_helpers.require_csrf(request, csrf_token)
    if blocked is not None:
        return blocked
    auth_logic.delete_session(request.cookies.get(auth_constants.SESSION_COOKIE_NAME))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(
        auth_constants.SESSION_COOKIE_NAME,
        secure=request_helpers._cookie_secure(request),
        httponly=True,
        samesite="lax",
    )
    response.headers["Clear-Site-Data"] = '"cache", "storage"'
    return response
