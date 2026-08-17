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
DAILY_SOFT_LIMIT = 2_000          # 보수 상한 — 공식 무료 한도 미확인이라 낮게 잡는다
COUNTER_FILENAME = "naver_usage.json"
# 예전 코드가 가져다 쓸 수 있도록 남겨 둔 로컬 기본값이다.
COUNTER_PATH = LOCAL_LOG_DIR / COUNTER_FILENAME
_TAG_RE = re.compile(r"</?b>|&quot;|&amp;|&lt;|&gt;")


class NaverLimitReached(RuntimeError):
    """일 소프트 상한 도달 — 오늘은 더 부르지 않는다."""


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
        raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 없음 — prototype_v1/.env 확인")
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


def _parse_date(raw: str) -> Optional[dt.date]:
    # 예: "Fri, 15 Aug 2026 09:00:00 +0900"
    try:
        return dt.datetime.strptime(raw[:16].strip(), "%a, %d %b %Y").date()
    except ValueError:
        return None


def search_news(query: str, display: int = 10, sort: str = "date") -> list[NewsItem]:
    """뉴스 검색 1회. 429·401은 그대로 예외로 올린다 (호출자가 사유를 기록)."""
    _tick()
    cid, sec = _keys()
    params = urllib.parse.urlencode({"query": query, "display": display, "sort": sort})
    req = urllib.request.Request(f"{BASE_URL}?{params}",
                                 headers={"X-NCP-APIGW-API-KEY-ID": cid,
                                          "X-NCP-APIGW-API-KEY": sec})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        payload = json.loads(res.read().decode("utf-8"))
    return [NewsItem(title=_clean(i.get("title", "")), link=i.get("link", ""),
                     originallink=i.get("originallink", ""),
                     description=_clean(i.get("description", "")),
                     pub_date=_parse_date(i.get("pubDate", "")))
            for i in payload.get("items", [])]
