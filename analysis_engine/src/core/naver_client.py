# -*- coding: utf-8 -*-
"""NAVER API HUB 뉴스 검색 클라이언트."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

from core import constants as c
from core import credentialed_http


COUNTER_FILENAME = c.NAVER_USAGE_COUNTER_FILENAME
_BOLD_TAG_RE = re.compile(r"</?b\s*>", re.IGNORECASE)
_NO_REDIRECT_OPENER = credentialed_http.build_no_redirect_opener()
_DAILY_COUNTER_LOCK = threading.Lock()
_daily_counter_day: dt.date | None = None
_daily_counter_count = 0


@dataclass(frozen=True)
class NewsItem:
    """보고서 단계가 쓰는 정규화된 뉴스 한 건."""

    title: str
    originallink: str
    link: str
    description: str
    pubDate: str

    @property
    def pub_date(self) -> dt.date | None:
        """기존 파일럿의 날짜 판정에는 ``date`` 형식으로 제공한다."""

        try:
            return dt.date.fromisoformat(self.pubDate) if self.pubDate else None
        except ValueError:
            return None


@dataclass(frozen=True)
class NewsSearchResult:
    """뉴스 실패가 전체 보고서 생성을 중단하지 않게 하는 반환 계약."""

    state: str
    reason_code: str
    items: list[NewsItem]
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return self.state == c.NAVER_NEWS_STATE_SUCCESS


class _TransientNewsError(RuntimeError):
    """재시도할 수 있는 5xx·타임아웃·통신 장애."""


class _AuthenticationNewsError(RuntimeError):
    """재시도해도 바뀌지 않는 인증 실패."""


class _RateLimitedNewsError(RuntimeError):
    """즉시 중단해야 하는 API 한도 초과."""


class _InvalidNewsResponse(RuntimeError):
    """응답 위치·크기·JSON 구조가 계약과 다름."""


def _urlopen(request: urllib.request.Request, *, timeout: float):
    """인증 머리글이 다른 주소로 전달되지 않게 redirect 없이 연다."""

    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _elapsed_ms(started_ns: int) -> int:
    return max(0, (time.perf_counter_ns() - started_ns) // 1_000_000)


def _result(
    started_ns: int,
    *,
    state: str,
    reason_code: str,
    items: list[NewsItem] | None = None,
) -> NewsSearchResult:
    return NewsSearchResult(
        state=state,
        reason_code=reason_code,
        items=[] if items is None else items,
        elapsed_ms=_elapsed_ms(started_ns),
    )


def _credentials() -> tuple[str, str] | None:
    key_id = os.environ.get(c.NAVER_API_KEY_ID_ENV, "").strip()
    api_key = os.environ.get(c.NAVER_API_KEY_ENV, "").strip()
    if not key_id or not api_key:
        return None
    return key_id, api_key


def _reserve_daily_call(*, today: dt.date | None = None) -> bool:
    """프로세스 안에서 오늘의 실제 HTTP 호출 자리를 원자적으로 예약한다."""

    global _daily_counter_count, _daily_counter_day

    current_day = today or dt.date.today()
    with _DAILY_COUNTER_LOCK:
        if _daily_counter_day != current_day:
            _daily_counter_day = current_day
            _daily_counter_count = 0
        if _daily_counter_count >= c.NAVER_NEWS_DAILY_CAP:
            return False
        _daily_counter_count += 1
        return True


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return html.unescape(_BOLD_TAG_RE.sub("", value))


def _normalize_pub_date(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _normalize_items(payload: object) -> list[NewsItem]:
    if not isinstance(payload, dict):
        raise _InvalidNewsResponse
    raw_items = payload.get(c.NAVER_NEWS_ITEMS_FIELD)
    if not isinstance(raw_items, list) or not all(
        isinstance(item, dict) for item in raw_items
    ):
        raise _InvalidNewsResponse
    return [
        NewsItem(
            title=_clean_text(item.get(c.NAVER_NEWS_TITLE_FIELD)),
            originallink=_text(item.get(c.NAVER_NEWS_ORIGINAL_LINK_FIELD)),
            link=_text(item.get(c.NAVER_NEWS_LINK_FIELD)),
            description=_clean_text(item.get(c.NAVER_NEWS_DESCRIPTION_FIELD)),
            pubDate=_normalize_pub_date(item.get(c.NAVER_NEWS_PUBLISHED_DATE_FIELD)),
        )
        for item in raw_items
    ]


def _read_payload(request: urllib.request.Request) -> object:
    try:
        with _urlopen(request, timeout=c.NAVER_NEWS_REQUEST_TIMEOUT_SECONDS) as response:
            credentialed_http.require_exact_response_url(
                response,
                expected_url=request.full_url,
            )
            data = response.read(c.NAVER_NEWS_MAX_RESPONSE_BYTES + 1)
            if not isinstance(data, bytes) or len(data) > c.NAVER_NEWS_MAX_RESPONSE_BYTES:
                raise _InvalidNewsResponse
    except urllib.error.HTTPError as error:
        if error.code in c.NAVER_AUTHENTICATION_FAILURE_STATUS_CODES:
            raise _AuthenticationNewsError from None
        if error.code == c.NAVER_RATE_LIMIT_STATUS_CODE:
            raise _RateLimitedNewsError from None
        if c.HTTP_SERVER_ERROR_MIN <= error.code <= c.HTTP_SERVER_ERROR_MAX:
            raise _TransientNewsError from None
        raise _InvalidNewsResponse from None
    except credentialed_http.CredentialedHTTPContractError:
        raise _InvalidNewsResponse from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise _TransientNewsError from None

    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _InvalidNewsResponse from None


def _request(
    query: str,
    *,
    display: int,
    start: int,
    sort: str,
    key_id: str,
    api_key: str,
) -> urllib.request.Request:
    params = urllib.parse.urlencode(
        {
            c.NAVER_NEWS_QUERY_PARAM: query,
            c.NAVER_NEWS_DISPLAY_PARAM: display,
            c.NAVER_NEWS_START_PARAM: start,
            c.NAVER_NEWS_SORT_PARAM: sort,
        }
    )
    return urllib.request.Request(
        f"{c.NAVER_API_HUB_BASE_URL}{c.NAVER_NEWS_SEARCH_PATH}?{params}",
        headers={
            c.NAVER_API_KEY_ID_HEADER: key_id,
            c.NAVER_API_KEY_HEADER: api_key,
        },
        method="GET",
    )


def search_news(
    query: str,
    display: int = c.NAVER_NEWS_DEFAULT_DISPLAY,
    start: int = c.NAVER_NEWS_DEFAULT_START,
    sort: str = c.NAVER_NEWS_DEFAULT_SORT,
) -> NewsSearchResult:
    """NAVER API HUB 뉴스를 검색하고 모든 실패를 결과 객체로 돌려준다."""

    started_ns = time.perf_counter_ns()
    credentials = _credentials()
    if credentials is None:
        return _result(
            started_ns,
            state=c.NAVER_NEWS_STATE_SKIPPED,
            reason_code=c.NEWS_SEARCH_NOT_CONFIGURED,
        )

    key_id, api_key = credentials
    try:
        request = _request(
            query,
            display=display,
            start=start,
            sort=sort,
            key_id=key_id,
            api_key=api_key,
        )
    except Exception:
        return _result(
            started_ns,
            state=c.NAVER_NEWS_STATE_FAILED,
            reason_code=c.NEWS_SEARCH_INVALID_RESPONSE,
        )
    attempts = c.NAVER_NEWS_MAX_RETRIES + 1

    for attempt in range(attempts):
        if not _reserve_daily_call():
            return _result(
                started_ns,
                state=c.NAVER_NEWS_STATE_SKIPPED,
                reason_code=c.NEWS_SEARCH_DAILY_CAP,
            )
        try:
            items = _normalize_items(_read_payload(request))
        except _AuthenticationNewsError:
            return _result(
                started_ns,
                state=c.NAVER_NEWS_STATE_FAILED,
                reason_code=c.NEWS_SEARCH_AUTHENTICATION_FAILED,
            )
        except _RateLimitedNewsError:
            return _result(
                started_ns,
                state=c.NAVER_NEWS_STATE_FAILED,
                reason_code=c.NEWS_SEARCH_RATE_LIMITED,
            )
        except _InvalidNewsResponse:
            return _result(
                started_ns,
                state=c.NAVER_NEWS_STATE_FAILED,
                reason_code=c.NEWS_SEARCH_INVALID_RESPONSE,
            )
        except _TransientNewsError:
            if attempt < c.NAVER_NEWS_MAX_RETRIES:
                continue
            return _result(
                started_ns,
                state=c.NAVER_NEWS_STATE_FAILED,
                reason_code=c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE,
            )
        except Exception:
            # 뉴스는 보조 근거라 예상 밖 어댑터 실패도 전체 보고서를 막지 않는다.
            return _result(
                started_ns,
                state=c.NAVER_NEWS_STATE_FAILED,
                reason_code=c.NEWS_SEARCH_INVALID_RESPONSE,
            )
        return _result(
            started_ns,
            state=c.NAVER_NEWS_STATE_SUCCESS,
            reason_code=c.NEWS_SEARCH_OK,
            items=items,
        )

    return _result(
        started_ns,
        state=c.NAVER_NEWS_STATE_FAILED,
        reason_code=c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE,
    )
