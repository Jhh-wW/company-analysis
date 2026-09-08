# -*- coding: utf-8 -*-
"""분석 엔진 공통 외부 연동 상수."""

from typing import Final


NAVER_API_HUB_BASE_URL: Final[str] = "https://naverapihub.apigw.ntruss.com"
NAVER_NEWS_SEARCH_PATH: Final[str] = "/search/v1/news"
NAVER_API_KEY_ID_HEADER: Final[str] = "X-NCP-APIGW-API-KEY-ID"
NAVER_API_KEY_HEADER: Final[str] = "X-NCP-APIGW-API-KEY"
NAVER_API_KEY_ID_ENV: Final[str] = "NCP_APIGW_API_KEY_ID"
NAVER_API_KEY_ENV: Final[str] = "NCP_APIGW_API_KEY"

NAVER_NEWS_REQUEST_TIMEOUT_SECONDS: Final[float] = 15.0
NAVER_NEWS_MAX_RETRIES: Final[int] = 1
NAVER_NEWS_MAX_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024
# HUB 무료 한도 미확인 — 실측 뒤 조정.
NAVER_NEWS_DAILY_CAP: Final[int] = 1_000
NAVER_NEWS_DEFAULT_DISPLAY: Final[int] = 10
NAVER_NEWS_DEFAULT_START: Final[int] = 1
NAVER_NEWS_DEFAULT_SORT: Final[str] = "date"

NAVER_NEWS_QUERY_PARAM: Final[str] = "query"
NAVER_NEWS_DISPLAY_PARAM: Final[str] = "display"
NAVER_NEWS_START_PARAM: Final[str] = "start"
NAVER_NEWS_SORT_PARAM: Final[str] = "sort"
NAVER_NEWS_ITEMS_FIELD: Final[str] = "items"
NAVER_NEWS_TITLE_FIELD: Final[str] = "title"
NAVER_NEWS_ORIGINAL_LINK_FIELD: Final[str] = "originallink"
NAVER_NEWS_LINK_FIELD: Final[str] = "link"
NAVER_NEWS_DESCRIPTION_FIELD: Final[str] = "description"
NAVER_NEWS_PUBLISHED_DATE_FIELD: Final[str] = "pubDate"

NAVER_AUTHENTICATION_FAILURE_STATUS_CODES: Final[frozenset[int]] = frozenset(
    {401, 403}
)
NAVER_RATE_LIMIT_STATUS_CODE: Final[int] = 429
HTTP_SERVER_ERROR_MIN: Final[int] = 500
HTTP_SERVER_ERROR_MAX: Final[int] = 599

NAVER_NEWS_STATE_SUCCESS: Final[str] = "success"
NAVER_NEWS_STATE_SKIPPED: Final[str] = "skipped"
NAVER_NEWS_STATE_FAILED: Final[str] = "failed"

NEWS_SEARCH_OK: Final[str] = "news_search_ok"
NEWS_SEARCH_NOT_CONFIGURED: Final[str] = "news_search_not_configured"
NEWS_SEARCH_AUTHENTICATION_FAILED: Final[str] = "news_search_authentication_failed"
NEWS_SEARCH_RATE_LIMITED: Final[str] = "news_search_rate_limited"
NEWS_SEARCH_TEMPORARILY_UNAVAILABLE: Final[str] = "news_search_temporarily_unavailable"
NEWS_SEARCH_INVALID_RESPONSE: Final[str] = "news_search_invalid_response"
NEWS_SEARCH_DAILY_CAP: Final[str] = "news_search_daily_cap"
NEWS_SEARCH_TRANSPORT_BUDGET_EXHAUSTED: Final[str] = "news_search_transport_budget_exhausted"
NEWS_SEARCH_INVALID_REQUEST: Final[str] = "news_search_invalid_request"

# 단계 로그와 이름이 겹치지 않는지 확인하는 기존 검수 계약에서 쓴다.
NAVER_USAGE_COUNTER_FILENAME: Final[str] = "naver_usage.json"
