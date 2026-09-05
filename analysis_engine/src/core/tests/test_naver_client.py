"""NAVER API HUB 뉴스 검색 계약 시험."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from dataclasses import fields
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import constants as c
from core import naver_client


class _Response:
    def __init__(self, body: bytes, url: str = "") -> None:
        self.body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int = -1) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


@pytest.fixture(autouse=True)
def _configured_client(monkeypatch):
    monkeypatch.setenv(c.NAVER_API_KEY_ID_ENV, "test-key-id")
    monkeypatch.setenv(c.NAVER_API_KEY_ENV, "test-api-key")
    monkeypatch.setattr(naver_client, "_daily_counter_day", None)
    monkeypatch.setattr(naver_client, "_daily_counter_count", 0)


def _json_response(request, payload: object) -> _Response:
    return _Response(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        request.full_url,
    )


def test_정상_응답을_정규화한다(monkeypatch):
    payload = {
        "items": [
            {
                "title": "<b>네이버</b> &amp; 클라우드",
                "originallink": "https://news.example/original",
                "link": "https://news.example/naver",
                "description": "<b>본문</b> &quot;확인&quot;",
                "pubDate": "Fri, 15 Aug 2025 09:00:00 +0900",
            },
            {
                "title": "날짜 오류",
                "originallink": "",
                "link": "https://news.example/invalid-date",
                "description": "설명",
                "pubDate": "날짜 아님",
            },
        ]
    }
    monkeypatch.setattr(
        naver_client,
        "_urlopen",
        lambda request, **_kwargs: _json_response(request, payload),
    )

    result = naver_client.search_news("네이버")

    assert result.state == c.NAVER_NEWS_STATE_SUCCESS
    assert result.reason_code == c.NEWS_SEARCH_OK
    assert [field.name for field in fields(naver_client.NewsSearchResult)] == [
        "state",
        "reason_code",
        "items",
        "elapsed_ms",
    ]
    assert [field.name for field in fields(naver_client.NewsItem)] == [
        "title",
        "originallink",
        "link",
        "description",
        "pubDate",
    ]
    assert result.items[0] == naver_client.NewsItem(
        title="네이버 & 클라우드",
        originallink="https://news.example/original",
        link="https://news.example/naver",
        description='본문 "확인"',
        pubDate="2025-08-15",
    )
    assert result.items[0].pub_date.isoformat() == "2025-08-15"
    assert result.items[1].pubDate == ""
    assert result.items[1].pub_date is None
    assert isinstance(result.elapsed_ms, int) and result.elapsed_ms >= 0


def test_요청_URL과_머리글이_API_HUB_규격과_일치한다(monkeypatch):
    captured = []

    def succeed(request, *, timeout):
        captured.append((request, timeout))
        return _json_response(request, {"items": []})

    monkeypatch.setattr(naver_client, "_urlopen", succeed)

    result = naver_client.search_news("네이버 클라우드", display=20, start=3, sort="sim")

    assert result.ok
    request, timeout = captured[0]
    parsed = urllib.parse.urlparse(request.full_url)
    assert f"{parsed.scheme}://{parsed.netloc}" == c.NAVER_API_HUB_BASE_URL
    assert parsed.path == c.NAVER_NEWS_SEARCH_PATH
    assert urllib.parse.parse_qs(parsed.query) == {
        "query": ["네이버 클라우드"],
        "display": ["20"],
        "start": ["3"],
        "sort": ["sim"],
    }
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers[c.NAVER_API_KEY_ID_HEADER.lower()] == "test-key-id"
    assert headers[c.NAVER_API_KEY_HEADER.lower()] == "test-api-key"
    assert request.get_method() == "GET"
    assert timeout == c.NAVER_NEWS_REQUEST_TIMEOUT_SECONDS


def test_키가_없으면_호출하지_않고_건너뛴다(monkeypatch):
    calls = 0

    def unexpected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("호출되면 안 됩니다")

    monkeypatch.delenv(c.NAVER_API_KEY_ID_ENV, raising=False)
    monkeypatch.delenv(c.NAVER_API_KEY_ENV, raising=False)
    monkeypatch.setattr(naver_client, "_urlopen", unexpected)

    result = naver_client.search_news("네이버")

    assert result.state == c.NAVER_NEWS_STATE_SKIPPED
    assert result.reason_code == c.NEWS_SEARCH_NOT_CONFIGURED
    assert result.items == []
    assert calls == 0
    assert naver_client._daily_counter_count == 0


@pytest.mark.parametrize("status", [401, 403])
def test_인증_실패는_재시도하지_않는다(monkeypatch, status):
    calls = 0

    def fail(request, **_kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, status, "비밀", None, None)

    monkeypatch.setattr(naver_client, "_urlopen", fail)

    result = naver_client.search_news("네이버")

    assert result.state == c.NAVER_NEWS_STATE_FAILED
    assert result.reason_code == c.NEWS_SEARCH_AUTHENTICATION_FAILED
    assert result.items == []
    assert calls == 1


def test_429는_재시도하지_않는다(monkeypatch):
    calls = 0

    def fail(request, **_kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, 429, "한도", None, None)

    monkeypatch.setattr(naver_client, "_urlopen", fail)

    result = naver_client.search_news("네이버")

    assert result.reason_code == c.NEWS_SEARCH_RATE_LIMITED
    assert result.items == []
    assert calls == 1


def test_5xx는_한_번_재시도한_뒤_실패한다(monkeypatch):
    calls = 0

    def fail(request, **_kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, 503, "장애", None, None)

    monkeypatch.setattr(naver_client, "_urlopen", fail)

    result = naver_client.search_news("네이버")

    assert result.reason_code == c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE
    assert result.items == []
    assert calls == 2


def test_타임아웃은_한_번_재시도한_뒤_실패한다(monkeypatch):
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("연결 시간 초과")

    monkeypatch.setattr(naver_client, "_urlopen", fail)

    result = naver_client.search_news("네이버")

    assert result.reason_code == c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE
    assert result.items == []
    assert calls == 2


def test_5xx_뒤_두_번째_응답이_정상이면_성공한다(monkeypatch):
    calls = 0

    def recover(request, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(request.full_url, 502, "장애", None, None)
        return _json_response(request, {"items": []})

    monkeypatch.setattr(naver_client, "_urlopen", recover)

    result = naver_client.search_news("네이버")

    assert result.ok
    assert calls == 2


@pytest.mark.parametrize("payload", [[], {}, {"items": None}, {"items": [1]}])
def test_응답_형식이_다르면_사유_코드로_실패한다(monkeypatch, payload):
    monkeypatch.setattr(
        naver_client,
        "_urlopen",
        lambda request, **_kwargs: _json_response(request, payload),
    )

    result = naver_client.search_news("네이버")

    assert result.reason_code == c.NEWS_SEARCH_INVALID_RESPONSE
    assert result.items == []


def test_일일_상한을_넘으면_HTTP를_호출하지_않는다(monkeypatch):
    calls = 0

    def succeed(request, **_kwargs):
        nonlocal calls
        calls += 1
        return _json_response(request, {"items": []})

    monkeypatch.setattr(c, "NAVER_NEWS_DAILY_CAP", 1)
    monkeypatch.setattr(naver_client, "_urlopen", succeed)

    first = naver_client.search_news("첫 호출")
    capped = naver_client.search_news("둘째 호출")

    assert first.ok
    assert capped.state == c.NAVER_NEWS_STATE_SKIPPED
    assert capped.reason_code == c.NEWS_SEARCH_DAILY_CAP
    assert capped.items == []
    assert calls == 1
