"""구글과 실제로 이야기하는 부분 — 로그인 URL 만들기 / 인가 코드를 토큰으로 바꾸기 /
사용자 정보(이메일) 가져오기.

★ 실제 네트워크 호출은 이 파일의 기본 구현(`_default_exchange_code`,
  `_default_fetch_userinfo`)에서만 일어난다. `handle_callback`을 부를 때
  `exchange`·`fetch` 자리에 가짜 함수를 넣으면 시험에서 진짜 구글 서버에
  접속하지 않고도 전체 흐름을 검증할 수 있다.

판단 로직(관리자 여부·state 대조·이메일 꺼내기·세션)은 여기 두지 않는다 — 전부 `logic.py`.
이 파일은 순수 함수 위에 얹힌 「바깥 세상과 이야기하는 껍데기」다.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from src.features.auth import constants, logic
from src.shared import credentialed_http

logger = logging.getLogger(__name__)

#: 인가 코드를 토큰으로 바꾸는 함수의 모양.
#: (code, redirect_uri, client_id, client_secret) -> 토큰 응답(dict)
ExchangeFn = Callable[[str, str, str, str], dict]
#: access_token으로 사용자 정보를 가져오는 함수의 모양. access_token -> 사용자 정보(dict)
UserInfoFn = Callable[[str], dict]

_OAUTH_OPENER = credentialed_http.build_no_redirect_opener()


def _urlopen(request: urllib.request.Request, *, timeout: float):
    """Google 고정 endpoint를 인증정보 redirect 없이 한 번만 연다."""

    return _OAUTH_OPENER.open(request, timeout=timeout)


class MissingCredentialError(Exception):
    """구글 로그인에 필요한 환경변수가 없다."""


class GoogleAuthError(Exception):
    """구글과의 통신이나 응답이 이상하다. 사용자에게 내부 내용을 그대로 보여주지 않는다."""


class GoogleAuthDeadlineError(GoogleAuthError):
    """토큰 교환과 사용자 조회를 합친 전체 제한시간을 넘겼다."""


@dataclass(frozen=True)
class GoogleCredentials:
    """구글 클라우드 콘솔에서 발급받은 값.

    ★ 절대 코드에 하드코딩하지 않는다 — `load_credentials()`로 환경변수에서만 읽는다.
    """

    client_id: str
    client_secret: str
    redirect_uri: str


def credentials_configured() -> bool:
    """구글 로그인에 필요한 설정이 모두 있는지만 답한다.

    화면에는 설정값이나 환경변수 이름을 절대 내보내지 않고, 로그인 기능이 지금
    준비됐는지를 설명하는 데에만 쓴다. ``.env`` 파일을 읽지 않고 현재 프로세스의
    환경변수만 확인한다.
    """
    return all(
        os.environ.get(name, "").strip()
        for name in (
            constants.ENV_CLIENT_ID,
            constants.ENV_CLIENT_SECRET,
            constants.ENV_REDIRECT_URI,
        )
    )


def load_credentials() -> GoogleCredentials:
    """환경변수에서 구글 인증 정보를 읽는다.

    ★ `.env` 파일을 직접 읽지 않는다 — `os.environ`만 본다 (배포 환경 표준과 맞춘다).

    Raises:
        MissingCredentialError: 하나라도 없으면, 무엇이 없는지 한국어로 알린다.
    """
    client_id = os.environ.get(constants.ENV_CLIENT_ID, "").strip()
    client_secret = os.environ.get(constants.ENV_CLIENT_SECRET, "").strip()
    redirect_uri = os.environ.get(constants.ENV_REDIRECT_URI, "").strip()

    missing = [
        name
        for name, value in (
            (constants.ENV_CLIENT_ID, client_id),
            (constants.ENV_CLIENT_SECRET, client_secret),
            (constants.ENV_REDIRECT_URI, redirect_uri),
        )
        if not value
    ]
    if missing:
        raise MissingCredentialError(
            "구글 로그인에 필요한 환경변수가 없습니다: " + ", ".join(missing)
        )
    return GoogleCredentials(
        client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
    )


def build_auth_url(credentials: GoogleCredentials, state: str) -> str:
    """사용자를 구글 로그인 화면으로 보낼 URL을 만든다."""
    query = urllib.parse.urlencode(
        {
            "client_id": credentials.client_id,
            "redirect_uri": credentials.redirect_uri,
            "response_type": "code",
            "scope": " ".join(constants.GOOGLE_SCOPES),
            "state": state,
            # 매번 계정 선택 화면을 보여준다 — 여러 구글 계정을 쓰는 사람이
            # 의도치 않은 계정으로 로그인해버리는 것을 막는다.
            "prompt": "select_account",
        }
    )
    return f"{constants.GOOGLE_AUTHORIZE_ENDPOINT}?{query}"


@dataclass(frozen=True)
class LoginStart:
    """로그인을 시작할 때 필요한 값.

    로그인 라우터는 `auth_url`로 리다이렉트하고, `state`를 (짧은 만료시간의) 쿠키에 심어
    콜백 때 돌아온 state와 대조해야 한다.
    """

    auth_url: str
    state: str


def start_login() -> LoginStart:
    """구글 로그인 URL과 CSRF state 값을 만든다.

    Raises:
        MissingCredentialError: 필요한 환경변수가 없을 때.
    """
    credentials = load_credentials()
    state = logic.make_state()
    return LoginStart(auth_url=build_auth_url(credentials, state), state=state)


# ══════════════════════════════════════════════════════════
# 실제 네트워크 호출 (기본 구현) — 시험에서는 주입한 가짜로 대체된다
# ══════════════════════════════════════════════════════════

def _default_exchange_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    *,
    deadline_monotonic: float | None = None,
) -> dict:
    """인가 코드를 토큰으로 바꾼다. (실제로 구글 서버에 접속한다)"""
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = urllib.request.Request(constants.GOOGLE_TOKEN_ENDPOINT, data=data, method="POST")
    return _call_and_parse(
        request,
        what="토큰 교환",
        deadline_monotonic=deadline_monotonic,
    )


def _default_fetch_userinfo(
    access_token: str, *, deadline_monotonic: float | None = None
) -> dict:
    """access_token으로 사용자 정보(이메일 등)를 가져온다. (실제로 구글 서버에 접속한다)"""
    request = urllib.request.Request(
        constants.GOOGLE_USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return _call_and_parse(
        request,
        what="사용자 정보 조회",
        deadline_monotonic=deadline_monotonic,
    )


#: 로그에 남길 구글 오류 설명의 최대 글자 수.
#: ★ 자르는 이유 — 구글이 언젠가 긴 본문을 보내도 로그가 통째로 뒤덮이지 않게.
_ERROR_DETAIL_MAX_CHARS = 200
_SAFE_OAUTH_ERROR_CODES = frozenset(
    {
        "access_denied",
        "admin_policy_enforced",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "redirect_uri_mismatch",
        "server_error",
        "temporarily_unavailable",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """구글이 «왜» 거절했는지를 응답 본문에서 꺼낸다 (로그 전용).

    Args:
        exc: `urlopen`이 던진 HTTP 오류.

    Returns:
        ``invalid_client`` 같은 닫힌 오류 코드. 못 읽거나 알 수 없는 값이면
        고정 문구를 돌려준다.

    ★ 이 값은 **로그에만** 쓴다. 화면에는 안 내보낸다 (사용자에게 내부 사정을 흘리지 않는다).
    오류 설명과 비정형 본문은 외부 입력이다. 공급자가 요청값을 되비추더라도
    인가 코드·비밀키가 로그에 남지 않도록 절대 반환하지 않는다.
    """
    try:
        raw = exc.read(constants.OAUTH_ERROR_RESPONSE_MAX_BYTES + 1)
    except OSError:
        return "(본문 없음)"
    if len(raw) > constants.OAUTH_ERROR_RESPONSE_MAX_BYTES:
        return "(오류 본문이 너무 큼)"
    body = raw.decode("utf-8", errors="replace")
    if not body.strip():
        return "(본문 없음)"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return "(오류 코드 해석 불가)"
    if not isinstance(parsed, dict):
        return "(오류 코드 해석 불가)"
    code = str(parsed.get("error", "")).strip()
    if code in _SAFE_OAUTH_ERROR_CODES:
        return code
    return "(알 수 없는 오류 코드)"


def _remaining_network_timeout(deadline_monotonic: float | None) -> float:
    """개별 10초 상한과 OAuth 전체 deadline 중 더 짧은 시간을 고른다."""

    if deadline_monotonic is None:
        return constants.HTTP_TIMEOUT_SEC
    remaining = float(deadline_monotonic) - time.monotonic()
    if remaining <= 0:
        raise GoogleAuthDeadlineError("구글 로그인 전체 제한시간을 넘겼습니다")
    return min(float(constants.HTTP_TIMEOUT_SEC), remaining)


def _ensure_before_deadline(deadline_monotonic: float | None) -> None:
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise GoogleAuthDeadlineError("구글 로그인 전체 제한시간을 넘겼습니다")


def _call_and_parse(
    request: urllib.request.Request,
    *,
    what: str,
    deadline_monotonic: float | None = None,
) -> dict:
    """구글에 요청을 보내고 JSON 응답을 dict로 돌려준다. 실패하면 한국어 예외로 감싼다.

    ★ 예외 메시지에 요청 내용(코드·비밀키·토큰)을 담지 않는다 — 로그에 민감정보가 남지 않게.
    """
    try:
        timeout = _remaining_network_timeout(deadline_monotonic)
        with _urlopen(request, timeout=timeout) as response:
            credentialed_http.require_exact_response_url(
                response,
                expected_url=request.full_url,
            )
            raw = credentialed_http.read_limited_bytes(
                response,
                max_bytes=constants.OAUTH_RESPONSE_MAX_BYTES,
            )
            result = json.loads(raw.decode("utf-8"))
        _ensure_before_deadline(deadline_monotonic)
        return result
    except urllib.error.HTTPError as exc:
        # ★ 구글은 «왜» 거절했는지를 «본문»에 담아 보낸다 — 그걸 안 읽으면
        #   로그에 「HTTP Error 401」만 남아 원인을 영영 못 찾는다. 실제로 그랬다.
        #   본문 예: {"error":"invalid_client","error_description":"Unauthorized"}
        #   → 열쇠가 틀렸는지(invalid_client) 주소가 안 맞는지(redirect_uri_mismatch)가 갈린다.
        # 오류 본문은 외부 입력이다. 진단에는 닫힌 오류 코드만 쓰고 설명·원문은
        # 버려, 공급자가 요청값을 되비춰도 코드·비밀키가 로그에 남지 않게 한다.
        logger.warning(
            "구글 %s 실패: HTTP %s — %s", what, exc.code, _error_detail(exc)
        )
        raise GoogleAuthError(
            f"구글이 요청을 거절했습니다 ({what} · HTTP {exc.code})"
        ) from exc
    except urllib.error.URLError as exc:
        logger.warning(
            "구글 %s 통신 실패 type=%s",
            what,
            type(getattr(exc, "reason", exc)).__name__,
        )
        raise GoogleAuthError(f"구글 서버와 통신하지 못했습니다 ({what})") from exc
    except credentialed_http.CredentialedHTTPContractError as exc:
        logger.warning("구글 %s 응답 안전 계약 위반", what)
        raise GoogleAuthError(f"구글 응답을 안전하게 읽지 못했습니다 ({what})") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("구글 %s 응답 해석 실패 type=%s", what, type(exc).__name__)
        raise GoogleAuthError(f"구글 응답을 해석하지 못했습니다 ({what})") from exc


# ══════════════════════════════════════════════════════════
# 콜백 처리 — state 대조 + 교환 + 조회 + 세션 생성
# ══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LoginResult:
    """로그인 한 번이 끝난 결과. 로그인 라우터가 세션 쿠키와 다음 화면을 정한다."""

    email: str
    is_admin: bool
    session: logic.Session


def handle_callback(
    code: str,
    state_received: str,
    state_expected: Optional[str],
    *,
    previous_session_token: Optional[str] = None,
    exchange: ExchangeFn = _default_exchange_code,
    fetch: UserInfoFn = _default_fetch_userinfo,
    provider_deadline_monotonic: float | None = None,
) -> LoginResult:
    """콜백에서 받은 인가 코드로 로그인을 끝까지 마친다.

    순서: state 대조(CSRF) → 코드를 토큰으로 교환 → 사용자 정보 조회
          → 이메일 확인 여부 검사 → 관리자 여부 판정 → 세션 생성.

    Args:
        code: 구글이 돌려준 인가 코드 (쿼리 파라미터 `code`).
        state_received: 구글이 돌려준 state (쿼리 파라미터 `state`).
        state_expected: 로그인을 시작할 때 쿠키에 심어둔 state. 쿠키가 없었으면 None.
        previous_session_token: 성공 시 새 세션으로 교체할 현재 브라우저 세션.
        exchange: 코드↔토큰 교환 함수. 시험에서 가짜로 바꿔 끼운다.
        fetch: 사용자 정보 조회 함수. 시험에서 가짜로 바꿔 끼운다.

    Returns:
        로그인 결과 (이메일 · 관리자 여부 · 세션).

    Raises:
        logic.StateMismatchError: state가 안 맞을 때 (CSRF 의심) — 로그인을 거부해야 한다.
        MissingCredentialError: 환경변수가 없을 때.
        GoogleAuthError: 구글과의 통신·응답이 이상할 때 (예: access_token 없음).
        logic.UnverifiedEmailError: 이메일이 확인되지 않았을 때.
    """
    if not logic.state_matches(state_expected or "", state_received):
        raise logic.StateMismatchError("state 값이 일치하지 않습니다 (CSRF 의심)")

    _ensure_before_deadline(provider_deadline_monotonic)
    credentials = load_credentials()
    if exchange is _default_exchange_code:
        token_response = _default_exchange_code(
            code,
            credentials.redirect_uri,
            credentials.client_id,
            credentials.client_secret,
            deadline_monotonic=provider_deadline_monotonic,
        )
    else:
        token_response = exchange(
            code,
            credentials.redirect_uri,
            credentials.client_id,
            credentials.client_secret,
        )
    _ensure_before_deadline(provider_deadline_monotonic)

    access_token = token_response.get("access_token")
    if not access_token:
        # ★ 토큰 응답 전체를 로그에 남기지 않는다 — access_token 등 민감정보를 담고 있을 수 있다.
        logger.warning("구글 토큰 응답에 access_token이 없음")
        raise GoogleAuthError("구글이 access_token을 돌려주지 않았습니다")

    if fetch is _default_fetch_userinfo:
        userinfo = _default_fetch_userinfo(
            access_token,
            deadline_monotonic=provider_deadline_monotonic,
        )
    else:
        userinfo = fetch(access_token)
    _ensure_before_deadline(provider_deadline_monotonic)
    identity = logic.extract_verified_identity(userinfo)
    email = identity.email
    is_admin = logic.check_admin(email)
    _ensure_before_deadline(provider_deadline_monotonic)
    session = logic.rotate_session(
        email,
        is_admin,
        subject=identity.subject,
        previous_token=previous_session_token,
    )
    return LoginResult(email=email, is_admin=is_admin, session=session)
