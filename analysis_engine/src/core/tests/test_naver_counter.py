"""NAVER API HUB 프로세스 내부 일일 계수기 회귀 시험."""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import constants as c
from core import naver_client


class _Response:
    def __init__(self, data: bytes, url: str = "") -> None:
        self.data = data
        self.url = url
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1):
        self.read_sizes.append(size)
        return self.data if size < 0 else self.data[:size]

    def geturl(self) -> str:
        return self.url


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setenv(c.NAVER_API_KEY_ID_ENV, "fake-id")
    monkeypatch.setenv(c.NAVER_API_KEY_ENV, "fake-secret")
    monkeypatch.setattr(naver_client, "_daily_counter_day", None)
    monkeypatch.setattr(naver_client, "_daily_counter_count", 0)


def _response(request, body: bytes = b'{"items":[]}') -> _Response:
    return _Response(body, request.full_url)


def test_계수기는_프로세스_안에서_시작한다():
    # 파일 경로 계약은 프로세스 내부 계수 계약으로 대체돼 더는 성립하지 않는다.
    assert naver_client._daily_counter_day is None
    assert naver_client._daily_counter_count == 0


def test_같은_날짜의_호출은_프로세스_안에서_누적된다():
    today = dt.date(2026, 8, 17)

    assert naver_client._reserve_daily_call(today=today)
    assert naver_client._reserve_daily_call(today=today)
    assert naver_client._daily_counter_day == today
    assert naver_client._daily_counter_count == 2


def test_날짜가_바뀌면_프로세스_계수기를_초기화한다():
    assert naver_client._reserve_daily_call(today=dt.date(2026, 8, 17))
    assert naver_client._reserve_daily_call(today=dt.date(2026, 8, 18))
    assert naver_client._daily_counter_day == dt.date(2026, 8, 18)
    assert naver_client._daily_counter_count == 1


def test_키가_없으면_사용량을_올리기_전에_건너뛴다(monkeypatch):
    monkeypatch.delenv(c.NAVER_API_KEY_ID_ENV, raising=False)
    monkeypatch.delenv(c.NAVER_API_KEY_ENV, raising=False)

    result = naver_client.search_news("회사")

    assert result.reason_code == c.NEWS_SEARCH_NOT_CONFIGURED
    assert result.items == []
    assert naver_client._daily_counter_count == 0


@pytest.mark.parametrize(
    ("status", "reason_code", "expected_calls"),
    [
        (401, c.NEWS_SEARCH_AUTHENTICATION_FAILED, 1),
        (429, c.NEWS_SEARCH_RATE_LIMITED, 1),
        (503, c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE, 2),
    ],
)
def test_HTTP오류를_비밀값없는_결과로_정규화한다(
    monkeypatch, status, reason_code, expected_calls
):
    secret = "naver-secret-must-not-leak"
    calls = 0

    def fail(request, **_kwargs):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, status, secret, None, None)

    monkeypatch.setattr(naver_client, "_urlopen", fail)

    result = naver_client.search_news("회사")

    assert result.reason_code == reason_code
    assert result.items == []
    assert calls == expected_calls
    assert secret not in repr(result)


def test_timeout을_비밀값없는_일시장애_결과로_정규화한다(monkeypatch):
    secret = "timeout-secret"
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError(secret)

    monkeypatch.setattr(naver_client, "_urlopen", fail)

    result = naver_client.search_news("회사")

    assert result.reason_code == c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE
    assert result.items == []
    assert calls == 2
    assert secret not in repr(result)


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}", b'{"items":null}'])
def test_깨진_JSON과_items_누락을_계약실패_결과로_막는다(monkeypatch, body):
    monkeypatch.setattr(
        naver_client,
        "_urlopen",
        lambda request, **_kwargs: _response(request, body),
    )

    result = naver_client.search_news("회사")

    assert result.reason_code == c.NEWS_SEARCH_INVALID_RESPONSE
    assert result.items == []


def test_필드_형식이_틀려도_전체_호출이_터지지_않는다(monkeypatch):
    body = json.dumps(
        {"items": [{"title": None, "link": 3, "pubDate": False}]}
    ).encode("utf-8")
    monkeypatch.setattr(
        naver_client,
        "_urlopen",
        lambda request, **_kwargs: _response(request, body),
    )

    result = naver_client.search_news("회사")

    assert result.ok
    assert len(result.items) == 1
    assert result.items[0].title == ""
    assert result.items[0].link == ""
    assert result.items[0].pubDate == ""


def test_뉴스_JSON은_상한보다_한_바이트만_더_읽고_원문없이_거부한다(
    monkeypatch,
):
    secret = b"oversized-news-secret"
    response = _Response(secret)
    monkeypatch.setattr(c, "NAVER_NEWS_MAX_RESPONSE_BYTES", 8)

    def open_response(request, **_kwargs):
        response.url = request.full_url
        return response

    monkeypatch.setattr(naver_client, "_urlopen", open_response)

    result = naver_client.search_news("회사")

    assert result.reason_code == c.NEWS_SEARCH_INVALID_RESPONSE
    assert response.read_sizes == [9]
    assert secret.decode() not in repr(result)


def test_일일_상한은_다음_HTTP_호출을_막는다(monkeypatch):
    calls = 0

    def succeed(request, **_kwargs):
        nonlocal calls
        calls += 1
        return _response(request)

    monkeypatch.setattr(c, "NAVER_NEWS_DAILY_CAP", 1)
    monkeypatch.setattr(naver_client, "_urlopen", succeed)

    assert naver_client.search_news("첫 호출").ok
    capped = naver_client.search_news("둘째 호출")

    assert capped.reason_code == c.NEWS_SEARCH_DAILY_CAP
    assert capped.items == []
    assert calls == 1
