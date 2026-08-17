"""구글 로그인 기능에서 쓰는 값들을 한곳에 모은다.

★ 규칙 — 스코프·엔드포인트·쿠키 이름·유효시간·관리자 이메일을 코드 여기저기에
  숫자·문자열로 흩어 쓰지 않는다. 여기만 고치면 전체가 맞춰진다.

정본: 기획서.ver2/확정/90_운영기록/03_결정기록_03_구현중.md (D15)
"""

from __future__ import annotations

from typing import Final

# ── 구글 OAuth 엔드포인트 ────────────────────────────────
GOOGLE_AUTHORIZE_ENDPOINT: Final[str] = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT: Final[str] = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT: Final[str] = "https://openidconnect.googleapis.com/v1/userinfo"

#: 구글에 요청하는 권한 범위. 로그인(신원 확인)에 필요한 최소한만 받는다.
GOOGLE_SCOPES: Final[tuple[str, ...]] = ("openid", "email")

# ── 환경변수 이름 ─────────────────────────────────────────
# ★ 실제 값(비밀키 등)은 절대 코드에 넣지 않는다. os.environ에서만 읽는다.
ENV_CLIENT_ID: Final[str] = "GOOGLE_CLIENT_ID"
ENV_CLIENT_SECRET: Final[str] = "GOOGLE_CLIENT_SECRET"
ENV_REDIRECT_URI: Final[str] = "GOOGLE_REDIRECT_URI"
#: 콤마로 구분한 관리자 이메일 목록. 코드에는 실제 이메일 기본값을 두지 않는다.
ENV_ADMIN_EMAILS: Final[str] = "ADMIN_EMAILS"
#: 시험 배포에서 사이트 전체를 관리자 로그인 뒤에 둘지. 정확히 ``1``일 때만 켠다.
ENV_BETA_ADMIN_ONLY: Final[str] = "BETA_ADMIN_ONLY"

#: 관리자 전용 시험 중에도 로그인 왕복·로그아웃·배포 상태 확인은 열려 있어야 한다.
BETA_PUBLIC_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/auth/login",
        "/auth/callback",
        "/auth/logout",
        "/auth/not-admin",
        "/healthz",
    }
)
BETA_PUBLIC_PATH_PREFIXES: Final[tuple[str, ...]] = ("/static/",)

# ── 권한 판단 (D15 — 구글 로그인은 「누구인가」, 이 목록은 「관리자인가」) ─
#: 환경변수가 없으면 관리자는 0명이다. 배포에서 ENV_ADMIN_EMAILS를 반드시 넣는다.
DEFAULT_ADMIN_EMAILS: Final[tuple[str, ...]] = ()

# ── 쿠키 ──────────────────────────────────────────────────
#: CSRF 방지용 state를 담아두는 쿠키. 로그인 흐름이 끝나면 바로 지운다.
STATE_COOKIE_NAME: Final[str] = "auth_state"
#: 로그인한 사람의 세션 토큰을 담는 쿠키.
SESSION_COOKIE_NAME: Final[str] = "auth_session"

#: ★ 쿠키를 HTTPS에서만 보낼지. **기본은 켜짐(안전)**이다.
#:   로컬(`http://localhost`)에서 시험할 때만 이 환경변수를 `1`로 두어 잠시 끈다.
#:   끄는 쪽을 «명시»하게 만든 이유 — 기본을 꺼 두면 배포할 때 켜는 걸 잊는다.
ENV_COOKIE_INSECURE: Final[str] = "AUTH_COOKIE_INSECURE"

# ── 토큰 길이·유효시간 ────────────────────────────────────
#: state·세션 토큰을 만들 때 쓰는 난수 바이트 수. 32바이트 = 추측이 사실상 불가능한 수준.
STATE_TOKEN_BYTES: Final[int] = 32
SESSION_TOKEN_BYTES: Final[int] = 32
#: state는 로그인 흐름(리다이렉트 왕복) 동안만 살아있으면 된다.
STATE_MAX_AGE_SEC: Final[int] = 600  # 10분
#: 세션 유효시간. 너무 길면 탈취됐을 때 위험이 커지고, 너무 짧으면 자꾸 다시 로그인해야 한다.
SESSION_MAX_AGE_SEC: Final[int] = 8 * 60 * 60  # 8시간

# ── 네트워크 ──────────────────────────────────────────────
#: 구글 서버 호출 타임아웃(초). 응답이 없을 때 화면이 무한히 멈추지 않게 한다.
HTTP_TIMEOUT_SEC: Final[int] = 10
