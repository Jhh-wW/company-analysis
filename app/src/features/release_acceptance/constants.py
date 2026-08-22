"""로컬 릴리스 수락시험의 고정 계약."""

from __future__ import annotations

from typing import Final


SCHEMA_VERSION: Final[str] = "release-acceptance-v1"
DEMO_COMPANY: Final[str] = "(주)진영"
DEMO_REGION: Final[str] = "서울"
LOOPBACK_HOST: Final[str] = "127.0.0.1"

CHECKS: Final[tuple[tuple[str, str], ...]] = (
    ("runtime_dependencies", "로컬 실행 의존성"),
    ("provider_isolation", "무료·외부 provider 격리"),
    ("server_lifecycle", "서버 자동 기동·종료"),
    ("health", "liveness /healthz"),
    ("readiness", "readiness /readyz"),
    ("authentication", "로컬 관리자 인증"),
    ("authorization", "관리자 권한·CSRF 차단"),
    ("free_local_demo", "무료 로컬 데모 전체 흐름"),
    ("output_identity", "화면·PDF·공유 LINK report_id 일치"),
    ("restart_persistence", "재시작 뒤 저장본 복구"),
    ("duplicate_cost_invariant", "중복 실행·비용 불변식"),
    ("sensitive_nonexposure", "민감정보 비노출"),
    ("port_cleanup", "잔여 포트 정리"),
)
CHECK_TITLES: Final[dict[str, str]] = dict(CHECKS)

SAFE_PARENT_ENV_NAMES: Final[tuple[str, ...]] = (
    "SystemRoot",
    "WINDIR",
    "SystemDrive",
    "ComSpec",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "ALLUSERSPROFILE",
    "ProgramData",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "CommonProgramFiles",
    "CommonProgramFiles(x86)",
    "CommonProgramW6432",
    "LANG",
    "LC_ALL",
)

# 값은 읽지 않는다. 명시적 자식 환경에 이 이름이 없는지만 검사한다.
EXTERNAL_PROVIDER_ENV_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "DART_API_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_PLACES_API_KEY",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
        "NOTION_PARENT_PAGE_ID",
        "NOTION_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    }
)

REQUIRED_LEDGER_TABLES: Final[frozenset[str]] = frozenset(
    {
        "reports",
        "budget_spend_events",
        "budget_spend_inflight",
        "ai_variable_cost_events",
        "report_cost_summaries",
    }
)

DEPENDENCY_IMPORTS: Final[tuple[str, ...]] = (
    "fastapi",
    "uvicorn",
    "pypdf",
    "pypdfium2",
    "reportlab",
    "PIL",
    "lxml",
)

DEFAULT_STARTUP_TIMEOUT_SEC: Final[float] = 30.0
DEFAULT_WORKFLOW_TIMEOUT_SEC: Final[float] = 150.0
HTTP_TIMEOUT_SEC: Final[float] = 45.0
PORT_CLOSE_TIMEOUT_SEC: Final[float] = 8.0
POLL_INTERVAL_SEC: Final[float] = 0.2
MAX_REDIRECT_HOPS: Final[int] = 5
REDIRECT_STATUS_CODES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})
EGRESS_AUDIT_ENV_NAME: Final[str] = "RELEASE_ACCEPTANCE_EGRESS_AUDIT_PATH"
EGRESS_AUDIT_SCHEMA_VERSION: Final[str] = "release-acceptance-egress-v1"
MAX_EGRESS_AUDIT_BYTES: Final[int] = 4096
EGRESS_AUDIT_COUNTER_KEYS: Final[tuple[str, ...]] = (
    "self_test_dns_denied",
    "self_test_ip_denied",
    "self_test_socket_denied",
    "runtime_dns_denied",
    "runtime_ip_denied",
    "runtime_socket_denied",
)
