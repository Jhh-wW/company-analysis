"""진짜 조사 흐름에서만 쓰는 고정값."""

from typing import Final

#: DART OpenAPI가 요청을 정상 처리했음을 뜻하는 상태 코드.
#: 목록이 비어 있는 정상 응답과 한도·인증·서버 오류를 가르려면 반드시 확인한다.
DART_SUCCESS_STATUS: Final[str] = "000"

#: Anthropic 호출 한 번의 최대 대기 시간. SDK 기본 retry는 별도로
#: 끄므로, 이 값은 단일 호출이 서버 worker를 무한정 점유하지 못하게 한다.
ANTHROPIC_TIMEOUT_SEC: Final[float] = 180.0

# 뉴스에서 동명이의어·계열사·제품을 구분할 공식 문맥의 상한이다.
NEWS_IDENTITY_CONTEXT_CHARS: Final[int] = 4_000
NEWS_IDENTITY_FRAGMENT_CHARS: Final[int] = 600
NEWS_IDENTITY_CONTEXT_SECTIONS: Final[tuple[str, ...]] = (
    "identity", "business_model", "portfolio", "operations_partners"
)
NEWS_RESEARCH_CACHE_CONTRACT: Final[str] = "news-research-grounded-v1"
NEWS_RESEARCH_BUDGET_REASON_CODES: Final[frozenset[str]] = frozenset({
    "search_budget_exhausted", "news_search_transport_budget_exhausted",
    "news_search_daily_cap",
})


PROVIDER_API_ERROR_TYPES: Final[frozenset[str]] = frozenset({
    "invalid_request_error", "authentication_error", "billing_error", "permission_error",
    "not_found_error", "rate_limit_error", "timeout_error", "api_error", "overloaded_error",
    "request_too_large", "conflict_error",
})
PROVIDER_ERROR_MESSAGE_MAX_CHARS: Final[int] = 512
PROVIDER_SCHEMA_COMPLEXITY_MESSAGE: Final[str] = "Schema is too complex for compilation."
PROVIDER_ERROR_CATEGORY_UNKNOWN: Final[str] = "unknown"
PROVIDER_ERROR_CATEGORY_SCHEMA_COMPLEXITY: Final[str] = "schema_too_complex"
PROVIDER_INVALID_REQUEST_STATUS: Final[int] = 400
PROVIDER_RATE_LIMIT_STATUS: Final[int] = 429
# 공식 rate-limits 문서가 정의한 고정 접두부만 비교하며 뒤의 날짜는 버린다.
PROVIDER_SPEND_LIMIT_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    ("You have reached your specified workspace API usage limits", "workspace_spend_limit"),
    ("You have reached your specified API usage limits", "organization_spend_limit"),
)
PROVIDER_ERROR_PREFIX_BOUNDARIES: Final[tuple[str, ...]] = (".", ":", " ")
PROVIDER_ENFORCED_SPEND_LIMIT_CODE: Final[str] = "enforced_spend_limit_reached"
PROVIDER_MONTHLY_SPEND_CATEGORY: Final[str] = "organization_monthly_spend_cap"

# ── 실행 진단 — 단계별 소요 시간 ─────────────────────────
#: 화면 단계(`core.constants.PROGRESS_STEPS`)가 바뀔 때마다 직전 단계의
#: 소요 시간을 담는 진단 항목의 "step" 값. 400~600초 걸리는 본조사 하나가
#: 어느 단계에서 느린지 실행마다 실측으로 남기기 위해 둔다.
#: ★ 관리자 화면(`web/routers/admin.py`)도 이 값으로 항목을 가려 읽는다.
#:   feature 간 직접 import는 금지라 같은 문자열을
#:   `features/observability/constants.py`에도 따로 두고, 시험이 둘이
#:   같은지 못 박는다(`CACHE_HIT_LAYER1`과 같은 방식).
STAGE_ELAPSED_STEP: Final[str] = "단계소요"
#: 위 진단 항목에서 소요 시간을 담는 필드 이름. 값은 밀리초, 0 이상 정수다.
STAGE_ELAPSED_MS_KEY: Final[str] = "소요ms"
