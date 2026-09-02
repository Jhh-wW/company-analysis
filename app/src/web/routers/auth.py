"""Google 로그인, 로그인 결과, 로그아웃 경로."""

import asyncio
import concurrent.futures
import logging
import secrets
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.auth import state_store as oauth_state_store
from src.features.pipeline.demo import DemoPipeline
from src.features.report_access import store as report_access_store
from src.features.sharelink import allowlist as share_allow
from src.features.storage import db as storage_db
from src.web import request_helpers, runtime
from src.web.security import CSRF_TOKEN_MAX_CHARS


router = APIRouter()
logger = logging.getLogger(__name__)
_LOCAL_DEMO_COOKIE_PATH = "/auth/local-demo"
_LOCAL_DEMO_FLOW_LOCK = threading.Lock()
_LOCAL_DEMO_GRANTS: dict[str, float] = {}
_LOCAL_DEMO_STATES: dict[str, tuple[str, float]] = {}
_OAuthResult = TypeVar("_OAuthResult")

# urllib·SQLite는 동기 API다. worker=1의 asyncio event loop에서 직접 부르면 로그인
# 한 번이 모든 health/화면 요청을 멈춘다. 전용 executor와 같은 크기의 비차단
# semaphore를 붙여 실제 실행 수와 내부 대기열을 모두 고정한다.
_OAUTH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=auth_constants.OAUTH_PROVIDER_MAX_CONCURRENCY,
    thread_name_prefix="oauth-provider",
)
_OAUTH_SLOTS = threading.BoundedSemaphore(
    auth_constants.OAUTH_PROVIDER_MAX_CONCURRENCY
)


class _OAuthFlowBusy(Exception):
    """전용 OAuth 실행 슬롯이 모두 사용 중이다."""


class _OAuthStateRejected(Exception):
    """쿠키와 query는 같아도 서버가 발급한 미사용 state가 아니다."""


class _OAuthStateStoreUnavailable(Exception):
    """provider 호출 전에 OAuth state 원장을 확인하지 못했다."""


class _OAuthLegacyMigrationUnavailable(Exception):
    """확인된 로그인 신원을 기존 MEMBER 보고서에 원자 결속하지 못했다."""


def _monotonic_now() -> float:
    """프로세스 안의 짧은 수명과 deadline에 쓰는 단조 시계."""

    return time.monotonic()


def _wall_now() -> float:
    """재시작 뒤에도 비교할 영속 원장용 Unix 시각."""

    return time.time()


async def _run_oauth_work(
    work: Callable[[], _OAuthResult], *, timeout_sec: float
) -> _OAuthResult:
    """동기 OAuth 일을 event loop 밖의 고정 슬롯에서만 실행한다."""

    if not _OAUTH_SLOTS.acquire(blocking=False):
        raise _OAuthFlowBusy("OAuth 처리 슬롯이 모두 사용 중입니다")
    loop = asyncio.get_running_loop()

    def run_and_release() -> _OAuthResult:
        # asyncio loop가 요청 취소·서버 종료로 먼저 사라져도 worker thread 자신의
        # finally가 슬롯을 돌려준다. loop callback에 맡기면 영구 누수가 될 수 있다.
        try:
            return work()
        finally:
            _OAUTH_SLOTS.release()

    try:
        future = loop.run_in_executor(_OAUTH_EXECUTOR, run_and_release)
    except BaseException:
        _OAUTH_SLOTS.release()
        raise

    # asyncio timeout은 실행 중인 스레드를 강제 종료할 수 없다. 응답이 먼저 끝나도
    # 실제 worker가 끝날 때까지 슬롯을 쥐게 해야 동시 provider 수가 상한을 넘지 않는다.
    try:
        return await asyncio.wait_for(
            asyncio.shield(future),
            timeout=float(timeout_sec),
        )
    except TimeoutError as exc:
        raise auth_google.GoogleAuthDeadlineError(
            "구글 로그인 전체 제한시간을 넘겼습니다"
        ) from exc


def _issue_oauth_state(state: str) -> None:
    """새 연결 하나에서 state 발급을 완결해 재시작 뒤에도 남긴다."""

    with storage_db.connect() as conn:
        oauth_state_store.issue_state(conn, state, now=_wall_now())


def _consume_state_and_handle_callback(
    *,
    code: str,
    state: str,
    expected: str,
    previous_session_token: str | None,
    provider_deadline_monotonic: float,
) -> auth_google.LoginResult:
    """서버 원장을 먼저 1회 소비한 뒤에만 Google 코드를 보낸다."""

    try:
        with storage_db.connect() as conn:
            consumed = oauth_state_store.consume_state(
                conn, expected, now=_wall_now()
            )
    except Exception as exc:  # noqa: BLE001 — provider 호출 전 fail-closed 경계
        raise _OAuthStateStoreUnavailable(
            "OAuth state 원장을 확인하지 못했습니다"
        ) from exc
    if not consumed:
        raise _OAuthStateRejected("발급되지 않았거나 이미 끝난 OAuth state입니다")
    result = auth_google.handle_callback(
        code,
        state,
        expected,
        previous_session_token=previous_session_token,
        provider_deadline_monotonic=provider_deadline_monotonic,
    )
    if result.is_admin:
        return result
    try:
        if not auth_logic.is_approval_identity_subject(result.session.subject):
            raise RuntimeError("OAuth 불변 subject를 확인할 수 없습니다")
        with storage_db.connect() as conn:
            report_access_store.migrate_legacy_member_bindings(
                conn,
                member_email=result.email,
                identity_subject=result.session.subject,
                now=_wall_now(),
            )
    except Exception as exc:  # noqa: BLE001 - migration 실패면 새 세션도 전달하지 않는다
        try:
            auth_logic.delete_session(result.session.token)
        except Exception:  # noqa: BLE001 - 브라우저에 토큰을 주지 않는 것이 최종 fence다
            logger.error("legacy MEMBER 이관 실패 뒤 미전달 세션 정리에 실패했습니다")
        raise _OAuthLegacyMigrationUnavailable(
            "기존 MEMBER 접근 결속을 안전하게 이관하지 못했습니다"
        ) from exc
    return result


def _oauth_retry_response(
    *, status_code: int, delete_state: bool, request: Request
) -> HTMLResponse:
    """과부하·저장소 장애를 비밀 없이, 명시적인 재시도 시간과 함께 답한다."""

    response = HTMLResponse(
        "로그인을 잠시 완료할 수 없습니다. 잠시 뒤 로그인부터 다시 시도해 주세요.",
        status_code=status_code,
        headers={
            "Retry-After": str(auth_constants.OAUTH_OVERLOAD_RETRY_AFTER_SEC)
        },
    )
    if delete_state:
        _delete_oauth_state_cookie(response, request)
    return response


def _oauth_rejected_redirect(request: Request) -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    _delete_oauth_state_cookie(response, request)
    return response


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
    now = _monotonic_now()
    grant = secrets.token_urlsafe(auth_constants.STATE_TOKEN_BYTES)
    with _LOCAL_DEMO_FLOW_LOCK:
        _sweep_local_demo_flow_locked(now)
        _LOCAL_DEMO_GRANTS[grant] = (
            now + auth_constants.LOCAL_DEMO_GRANT_MAX_AGE_SEC
        )
    return grant


def _issue_local_demo_state(grant: str) -> str:
    """살아 있는 교환권에만 서버 저장 CSRF state를 발급한다."""
    now = _monotonic_now()
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
    now = _monotonic_now()
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


def _delete_oauth_state_cookie(
    response: HTMLResponse | RedirectResponse, request: Request
) -> None:
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
    try:
        await _run_oauth_work(
            lambda: _issue_oauth_state(started.state),
            timeout_sec=auth_constants.OAUTH_PROVIDER_TOTAL_DEADLINE_SEC,
        )
    except _OAuthFlowBusy:
        return _oauth_retry_response(
            status_code=503,
            delete_state=False,
            request=request,
        )
    except oauth_state_store.OAuthStateCapacityError:
        logger.warning("OAuth state 발급 상한이 찼습니다")
        return _oauth_retry_response(
            status_code=503,
            delete_state=False,
            request=request,
        )
    except Exception as exc:  # noqa: BLE001 — DB 내부 정보는 화면·로그에 내보내지 않는다
        logger.error(
            "OAuth state를 안전하게 기록하지 못했습니다: %s",
            type(exc).__name__,
        )
        return _oauth_retry_response(
            status_code=503,
            delete_state=False,
            request=request,
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
async def auth_callback(
    request: Request,
    code: str = Query("", max_length=auth_constants.OAUTH_CODE_MAX_CHARS),
    state: str = Query("", max_length=auth_constants.STATE_TOKEN_CHARS),
):
    """서버 발급 state를 1회 소비한 요청만 event loop 밖에서 Google로 보낸다."""
    expected = request.cookies.get(auth_constants.STATE_COOKIE_NAME) or ""
    if (
        not code
        or not oauth_state_store.state_has_expected_shape(expected)
        or not oauth_state_store.state_has_expected_shape(state)
        or not auth_logic.state_matches(expected, state)
    ):
        return _oauth_rejected_redirect(request)

    provider_deadline = (
        _monotonic_now() + auth_constants.OAUTH_PROVIDER_TOTAL_DEADLINE_SEC
    )
    try:
        result = await _run_oauth_work(
            lambda: _consume_state_and_handle_callback(
                code=code,
                state=state,
                expected=expected,
                previous_session_token=request.cookies.get(
                    auth_constants.SESSION_COOKIE_NAME
                ),
                provider_deadline_monotonic=provider_deadline,
            ),
            # 네트워크 함수 자체가 같은 deadline을 쓰며, 이 작은 여유는 worker가
            # deadline 예외를 정리해 event loop로 돌려줄 시간이다.
            timeout_sec=(
                auth_constants.OAUTH_PROVIDER_TOTAL_DEADLINE_SEC
                + auth_constants.OAUTH_DEADLINE_RETURN_GRACE_SEC
            ),
        )
    except _OAuthFlowBusy:
        # 아직 원장을 소비하지 않았으므로 같은 callback URL을 잠시 뒤 재시도할 수 있다.
        return _oauth_retry_response(
            status_code=429,
            delete_state=False,
            request=request,
        )
    except _OAuthStateRejected:
        return _oauth_rejected_redirect(request)
    except _OAuthStateStoreUnavailable:
        logger.error("OAuth state 원장을 확인하지 못했습니다")
        return _oauth_retry_response(
            status_code=503,
            delete_state=False,
            request=request,
        )
    except _OAuthLegacyMigrationUnavailable:
        logger.error("기존 MEMBER 접근 결속을 안전하게 이관하지 못했습니다")
        return _oauth_retry_response(
            status_code=503,
            delete_state=True,
            request=request,
        )
    except auth_google.GoogleAuthDeadlineError:
        logger.warning("구글 로그인 전체 제한시간을 넘겼습니다")
        return _oauth_retry_response(
            status_code=503,
            delete_state=True,
            request=request,
        )
    except Exception as exc:  # noqa: BLE001 — 실패 이유를 화면에 흘리지 않는다
        # 예외 객체에는 제3자 라이브러리가 code/token을 섞을 수도 있다. 종류만
        # 기록하고 공개 입력·cookie·인가 코드는 로그에 넣지 않는다.
        logger.warning("로그인 실패: %s", type(exc).__name__)
        # state 원장 접근 실패도 여기서 provider 0회로 닫힌다. Google 응답 실패와
        # 화면 계약은 기존과 같이 홈으로 돌려보내되, 원장은 이미 1회 소비됐다.
        return _oauth_rejected_redirect(request)

    # 관리자가 아니어도 초대 명단(allowlist) 회원이면 홈으로 보낸다 — beta gate가
    # 회원을 통과시키게 된 뒤로, 여기서 계속 「관리자만」으로 판단하면 실제로는
    # 들어갈 수 있는 회원이 로그인 직후 "관리자 전용" 화면부터 보게 된다.
    # ★ 명단을 못 읽으면 「초대 안 된 사람」쪽으로 틀린다 — beta gate와 같은 원칙.
    destination = "/auth/not-admin"
    if result.is_admin:
        destination = "/"
    else:
        try:
            with storage_db.connect() as conn:
                if share_allow.is_allowed(conn, result.email):
                    destination = "/"
        except Exception:  # noqa: BLE001 — 못 읽으면 관리자 전용 화면으로 보낸다
            logger.exception(
                "로그인 직후 초대 명단을 못 읽어 관리자 전용 화면으로 보냈습니다"
            )
    response = RedirectResponse(destination, status_code=303)
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
