# -*- coding: utf-8 -*-
"""NAVER API HUB 뉴스 검색 클라이언트 — 키는 환경변수로만, 값은 로그·출력 금지.

정본: https://api.ncloud-docs.com/docs/naver-api-hub-search-news (2026-08-15 확인)
  GET https://naverapihub.apigw.ntruss.com/search/v1/news
  헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY (구 오픈API에서 이사 — 헤더 이름이 다르다)
무료 한도가 문서에 없어 로컬 계수기로 일 호출 수를 세고 보수 상한을 둔다 (429 = 한도 초과).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.runtime_paths import ENV_DATA_ROOT, LOCAL_LOG_DIR, runtime_log_dir
from core import usage_store

BASE_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
TIMEOUT_SEC = 15
JSON_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
DAILY_SOFT_LIMIT = 2_000          # 보수 상한 — 공식 무료 한도 미확인이라 낮게 잡는다
COUNTER_FILENAME = "naver_usage.json"
# 예전 코드가 가져다 쓸 수 있도록 남겨 둔 로컬 기본값이다.
COUNTER_PATH = LOCAL_LOG_DIR / COUNTER_FILENAME
_TAG_RE = re.compile(r"</?b>|&quot;|&amp;|&lt;|&gt;")


class NaverClientError(RuntimeError):
    """비밀값·원문을 노출하지 않는 Naver 어댑터 오류."""

    stop_further_requests = False


class NaverLimitReached(NaverClientError):
    """일 소프트 상한 도달 — 오늘은 더 부르지 않는다."""

    stop_further_requests = True


class NaverAuthenticationError(NaverClientError):
    """API 키·접근 권한이 거부되었다."""

    stop_further_requests = True


class NaverTransportError(NaverClientError):
    """타임아웃·네트워크 오류로 응답을 확정할 수 없다."""


class NaverResponseError(NaverClientError):
    """Naver가 예상한 계약과 다른 응답을 돌려줬다."""

    stop_further_requests = True


def _read_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
            data = response.read(JSON_RESPONSE_MAX_BYTES + 1)
            if len(data) > JSON_RESPONSE_MAX_BYTES:
                raise NaverResponseError(
                    "Naver 뉴스 JSON 응답이 허용 크기를 초과했습니다"
                )
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise NaverLimitReached("Naver 뉴스 HTTP 429 — 사용량 한도 도달") from None
        if error.code in {401, 403}:
            raise NaverAuthenticationError("Naver 뉴스 API 인증이 거부되었습니다") from None
        raise NaverResponseError(
            f"Naver 뉴스 API가 HTTP {error.code} 오류를 돌려줬습니다"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NaverTransportError("Naver 뉴스 API와 통신하지 못했습니다") from None
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NaverResponseError("Naver 뉴스 JSON 응답을 해석하지 못했습니다") from None
    if not isinstance(payload, dict):
        raise NaverResponseError("Naver 뉴스 JSON 응답 형식이 올바르지 않습니다")
    return payload


@dataclass(frozen=True)
class NewsItem:
    title: str
    link: str
    originallink: str
    description: str
    pub_date: Optional[dt.date]


def _keys() -> tuple[str, str]:
    cid = os.environ.get("NAVER_CLIENT_ID", "").strip()
    sec = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not sec:
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 없음 — analysis_engine/.env 확인")
    return cid, sec


def default_counter_path() -> Path:
    """네이버 사용량 기록 경로를 돌려준다.

    로컬 기본 위치는 바꾸지 않는다. 배포에서 ``APP_DATA_ROOT=/var/data``를
    설정하면 Render 영속 디스크 아래에 기록한다.
    """
    return runtime_log_dir() / COUNTER_FILENAME


def _tick(path: Path | None = None, today: str | None = None) -> None:
    counter_path = path if path is not None else default_counter_path()
    day_key = today or dt.date.today().isoformat()
    try:
        usage_store.tick(counter_path, day_key, DAILY_SOFT_LIMIT)
    except usage_store.UsageLimitReached as exc:
        raise NaverLimitReached(
            f"네이버 뉴스 일 소프트 상한({DAILY_SOFT_LIMIT}) 도달"
        ) from exc


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text)


def _parse_date(raw: object) -> Optional[dt.date]:
    # 예: "Fri, 15 Aug 2026 09:00:00 +0900"
    try:
        if not isinstance(raw, str):
            return None
        return dt.datetime.strptime(raw[:16].strip(), "%a, %d %b %Y").date()
    except ValueError:
        return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def search_news(query: str, display: int = 10, sort: str = "date") -> list[NewsItem]:
    """뉴스 검색 1회. 429·인증·통신 실패를 비밀값 없는 예외로 올린다."""
    cid, sec = _keys()
    params = urllib.parse.urlencode({"query": query, "display": display, "sort": sort})
    req = urllib.request.Request(f"{BASE_URL}?{params}",
                                 headers={"X-NCP-APIGW-API-KEY-ID": cid,
                                          "X-NCP-APIGW-API-KEY": sec})
    _tick()
    payload = _read_json(req)
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise NaverResponseError("Naver 뉴스 응답의 items 형식이 올바르지 않습니다")
    return [NewsItem(title=_clean(_text(i.get("title"))), link=_text(i.get("link")),
                     originallink=_text(i.get("originallink")),
                     description=_clean(_text(i.get("description"))),
                     pub_date=_parse_date(i.get("pubDate", "")))
            for i in items]
