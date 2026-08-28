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
#: 시험 배포에서 사이트 전체를 관리자 로그인 뒤에 둘지. 정확히 ``0``일 때만 끈다.
#: 누락·오타는 잠금 상태로 남겨 초기 배포가 실수로 공개되지 않게 한다.
ENV_BETA_ADMIN_ONLY: Final[str] = "BETA_ADMIN_ONLY"
#: OAuth 비밀값 없이 관리자 화면을 둘러보는 로컬 데모 입구.
#: 정확히 ``1``이어야 하며, 이 값 하나만으로는 절대 입구가 열리지 않는다.
ENV_LOCAL_DEMO_AUTH: Final[str] = "LOCAL_DEMO_AUTH"
#: 로컬 실행기 수명 동안 재진입에 쓰는 root capability. 이를 확인한 브라우저에
#: 발급하는 grant/state만 2분·1회용이다. 서버가 끝나면 root도 함께 폐기된다.
#: 플래그·Host 헤더·프록시 주소만으로는 로컬임을 증명할 수 없으므로 반드시 함께 본다.
ENV_LOCAL_DEMO_AUTH_TOKEN: Final[str] = "LOCAL_DEMO_AUTH_TOKEN"
#: PDF 출고 참여자 역할과 OAuth subject를 담은 JSON 객체. 이메일은 허용하지
#: 않는다. 필수 키: author, producer, fact, editorial, visual.
ENV_PDF_RELEASE_PARTICIPANTS: Final[str] = "PDF_RELEASE_PARTICIPANTS"

#: 로컬 데모 로그인은 실제 OAuth 공급자가 없으므로 고정된 불변 subject를 쓴다.
LOCAL_DEMO_IDENTITY_SUBJECT: Final[str] = "local-demo:operator"

#: 관리자 전용 시험 중에도 로그인 왕복·로그아웃·배포 상태 확인은 열려 있어야 한다.
BETA_PUBLIC_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/auth/login",
        "/auth/callback",
        "/auth/logout",
        "/auth/local-demo",
        "/auth/local-demo/start",
        "/auth/not-admin",
        "/healthz",
        "/readyz",
        "/internal/backup/run",
        "/internal/maintenance/run",
    }
)
# 공유 capability 진입점은 좁은 Render 관리자 데모에서 공개 예외가 아니다.
BETA_SHARE_ENTRY_PATH_PREFIXES: Final[tuple[str, ...]] = ("/k/",)
BETA_PUBLIC_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "/static/",
    *BETA_SHARE_ENTRY_PATH_PREFIXES,
)

# 살아 있는 공유 capability 쿠키로 열 수 있는 사용자 흐름만 명시한다. 관리자·검수·
# 외부 내보내기 경로는 각 라우터의 자체 권한 검사와 별개로 beta gate에서도 닫는다.
# /feedback은 로그인·LINK 없이도 검색없음·기업선택·생성중 화면에서 신고를 접수해야
# 하므로 함께 연다 — 안 열면 시험공개 중인 실제 손님은 신고 화면에 아예 못 들어간다.
BETA_SHARE_PATHS: Final[frozenset[str]] = frozenset(
    {"/", "/confirm", "/reject", "/run", "/robots.txt", "/feedback"}
)
BETA_SHARE_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "/progress/",
    "/api/progress/",
    "/result/",
    "/download/pdf/",
)

# ── 권한 판단 (D15 — 구글 로그인은 「누구인가」, 이 목록은 「관리자인가」) ─
#: 환경변수가 없으면 관리자는 0명이다. 배포에서 ENV_ADMIN_EMAILS를 반드시 넣는다.
DEFAULT_ADMIN_EMAILS: Final[tuple[str, ...]] = ()

# ── 쿠키 ──────────────────────────────────────────────────
#: CSRF 방지용 state를 담아두는 쿠키. 로그인 흐름이 끝나면 바로 지운다.
STATE_COOKIE_NAME: Final[str] = "auth_state"
#: 1회용 URL을 검증한 브라우저에만 잠깐 주는 로컬 데모 교환권.
LOCAL_DEMO_GRANT_COOKIE_NAME: Final[str] = "local_demo_grant"
#: 로컬 데모 POST를 서버 저장 state와 묶는 전용 쿠키.
LOCAL_DEMO_STATE_COOKIE_NAME: Final[str] = "local_demo_state"
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
#: 실행기 capability는 32바이트 난수의 소문자 16진수 표현만 받는다.
LOCAL_DEMO_AUTH_TOKEN_HEX_CHARS: Final[int] = 64
#: state는 로그인 흐름(리다이렉트 왕복) 동안만 살아있으면 된다.
STATE_MAX_AGE_SEC: Final[int] = 600  # 10분
#: ``token_urlsafe(32)``가 만드는 padding 없는 URL-safe 문자열의 정확한 길이.
#: 콜백의 공개 입력은 이 모양이 아니면 DB와 구글에 닿기 전에 거부한다.
STATE_TOKEN_CHARS: Final[int] = 43
#: 아직 만료되지 않은 OAuth 로그인 왕복을 서버가 동시에 기억할 최대 개수.
#: 로그인 시작을 무한 호출해도 SQLite가 끝없이 자라지 않게 한다.
OAUTH_STATE_MAX_RECORDS: Final[int] = 256
#: capability URL을 깨끗한 주소로 바꾼 뒤 로그인 폼을 끝낼 짧은 시간.
LOCAL_DEMO_GRANT_MAX_AGE_SEC: Final[int] = 120
#: 세션 유효시간. 너무 길면 탈취됐을 때 위험이 커지고, 너무 짧으면 자꾸 다시 로그인해야 한다.
SESSION_MAX_AGE_SEC: Final[int] = 8 * 60 * 60  # 8시간

# ── 네트워크 ──────────────────────────────────────────────
#: 구글 서버 호출 타임아웃(초). 응답이 없을 때 화면이 무한히 멈추지 않게 한다.
HTTP_TIMEOUT_SEC: Final[int] = 10
#: 토큰 교환과 사용자 정보 조회를 합친 OAuth 제공자 통신의 전체 제한시간.
OAUTH_PROVIDER_TOTAL_DEADLINE_SEC: Final[float] = 15.0
#: worker가 deadline 예외를 event loop에 돌려줄 아주 짧은 정리 여유.
OAUTH_DEADLINE_RETURN_GRACE_SEC: Final[float] = 0.1
#: worker=1 웹 프로세스에서 동시에 구글과 통신할 수 있는 콜백 수.
OAUTH_PROVIDER_MAX_CONCURRENCY: Final[int] = 2
#: 고정 슬롯이 찼을 때 브라우저에 안내할 최소 재시도 간격.
OAUTH_OVERLOAD_RETRY_AFTER_SEC: Final[int] = 2
#: 공개 콜백의 인가 코드에 허용할 최대 길이. 구글의 정상 코드는 이보다 훨씬 짧다.
OAUTH_CODE_MAX_CHARS: Final[int] = 4096
#: Google token/userinfo JSON 한 건의 실제 바이트 상한. 로그인 응답은 매우 작다.
OAUTH_RESPONSE_MAX_BYTES: Final[int] = 64 * 1024
#: 내부 진단용 Google 오류 본문도 전부 읽지 않고 이 크기까지만 본다.
OAUTH_ERROR_RESPONSE_MAX_BYTES: Final[int] = 4 * 1024
