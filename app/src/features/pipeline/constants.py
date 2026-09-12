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
#: 소요 시간을 진단(`shared.stage_elapsed_constants.STAGE_ELAPSED_STEP`)으로
#: 남긴다. 그 값은 파이프라인(생산자)·관리자 화면(소비자)이 함께 읽어야
#: 해서 `shared`에 두지만(`runtime_failure_constants.py`와 같은 이유), 이
#: 「시동」키는 이 feature 안에서만 쓰는 값이라 여기 그대로 둔다.
#: 첫 화면 단계(01 식별) «앞»의 구간 — `_engine()`의 1판 모듈 import가 여기
#: 안에 들어간다. 냉시동에서 수 초가 걸리는데 다른 단계 키로는 잡을 자리가
#: 없어서 따로 둔다. 화면 단계 목록(`core.constants.PROGRESS_STEPS`)에는
#: 넣지 않는다 — 진단 전용이지 사용자에게 보여줄 화면 문구가 아니다.
STAGE_BOOT: Final[str] = "시동"

#: 본조사에서 «경쟁사 비교»와 «뉴스 검색 스냅샷»을 동시에 돌릴 때 쓰는 worker 수.
#: 서로 결과를 읽지 않는 갈래가 정확히 둘이라 둘이다 — 늘려도 돌릴 일이 없다.
PARALLEL_COLLECT_BRANCH_WORKERS: Final[int] = 2
