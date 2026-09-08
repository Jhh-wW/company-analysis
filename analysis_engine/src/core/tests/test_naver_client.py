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

    def reject_network(*_args, **_kwargs):
        pytest.fail("단위시험에서 실제 NAVER 전송을 허용하지 않습니다")

    monkeypatch.setattr(naver_client, "_urlopen", reject_network)


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
        "transport_attempts",
        "retry_recovered",
        "attempt_reason_codes",
        "transport_observed",
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
    assert result.transport_attempts == 1
    assert result.retry_recovered is False
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_OK,)


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
    assert result.transport_attempts == 0
    assert result.retry_recovered is False
    assert result.attempt_reason_codes == ()


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
    assert result.transport_attempts == 1
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_AUTHENTICATION_FAILED,)


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
    assert result.transport_attempts == 1
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_RATE_LIMITED,)


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
    assert result.transport_attempts == 2
    assert result.retry_recovered is False
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE,) * 2


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
    assert result.transport_attempts == 2
    assert result.retry_recovered is False
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE,) * 2


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
    assert result.transport_attempts == 2
    assert result.retry_recovered is True
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE, c.NEWS_SEARCH_OK)


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
    assert result.transport_attempts == 1
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_INVALID_RESPONSE,)


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
    assert first.transport_attempts == 1
    assert capped.transport_attempts == 0
    assert capped.transport_observed is True
    assert capped.attempt_reason_codes == ()


def test_기존_네_위치인자와_단일인자_읽기대역을_유지한다(monkeypatch):
    observed = []

    def read(request):
        observed.append(request)
        return {"items": []}

    monkeypatch.setattr(naver_client, "_read_payload", read)
    result = naver_client.search_news("회사", 20, 3, "sim")
    assert result.ok and result.transport_attempts == 1
    assert result.transport_observed is True
    assert len(observed) == 1
    legacy = naver_client.NewsSearchResult("success", c.NEWS_SEARCH_OK, [], 0)
    assert legacy.ok
    assert legacy.transport_attempts == 0
    assert legacy.transport_observed is False
    assert legacy.retry_recovered is False and legacy.attempt_reason_codes == ()
    with pytest.raises(TypeError):
        naver_client.search_news("회사", 20, 3, "sim", 1)
    assert len(observed) == 1


def test_요청_구성_실패는_전송과_일일_사용량을_차감하지_않는다(monkeypatch):
    def fail_before_transport(*_args, **_kwargs):
        raise ValueError("요청 구성 비밀")

    monkeypatch.setattr(naver_client, "_request", fail_before_transport)
    result = naver_client.search_news("회사", remaining_transport_budget=2)
    assert result.reason_code == c.NEWS_SEARCH_INVALID_RESPONSE
    assert result.transport_attempts == 0 and result.attempt_reason_codes == ()
    assert result.transport_observed is True
    assert naver_client._daily_counter_count == 0
    assert "요청 구성 비밀" not in repr(result)


@pytest.mark.parametrize("budget", [-1, True, "1", 1.0])
def test_잘못된_전송예산은_요청_전에_거절한다(budget):
    result = naver_client.search_news("회사", remaining_transport_budget=budget)
    assert result.state == c.NAVER_NEWS_STATE_FAILED
    assert result.reason_code == c.NEWS_SEARCH_INVALID_REQUEST
    assert result.transport_attempts == 0 and result.attempt_reason_codes == ()
    assert naver_client._daily_counter_count == 0


def test_전송예산_0이면_일일_예약도_하지_않는다(monkeypatch):
    def unexpected_reservation():
        pytest.fail("전송예산이 없는데 일일 자리를 예약했습니다")

    monkeypatch.setattr(naver_client, "_reserve_daily_call", unexpected_reservation)
    result = naver_client.search_news("회사", remaining_transport_budget=0)
    assert result.state == c.NAVER_NEWS_STATE_SKIPPED
    assert result.reason_code == c.NEWS_SEARCH_TRANSPORT_BUDGET_EXHAUSTED
    assert result.transport_attempts == 0 and result.attempt_reason_codes == ()


@pytest.mark.parametrize("budget", [1, 2, 3])
def test_읽기_경계에서_남은예산만큼만_재시도한다(monkeypatch, budget):
    calls = []

    def read(request):
        calls.append(request)
        if len(calls) == 1:
            raise naver_client._TransientNewsError("전송 비밀")
        return {"items": []}

    monkeypatch.setattr(naver_client, "_read_payload", read)
    result = naver_client.search_news("회사", remaining_transport_budget=budget)
    assert result.transport_attempts == len(calls) == min(budget, 2)
    assert naver_client._daily_counter_count == len(calls)
    if budget == 1:
        assert result.reason_code == c.NEWS_SEARCH_TRANSPORT_BUDGET_EXHAUSTED
        assert result.state == c.NAVER_NEWS_STATE_SKIPPED
        assert result.attempt_reason_codes == (c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE,)
        assert result.retry_recovered is False
    else:
        assert result.ok and result.retry_recovered is True
        assert result.attempt_reason_codes == (c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE, c.NEWS_SEARCH_OK)
    assert "전송 비밀" not in repr(result)


def test_재시도_전에_일일상한이_차면_앞선_실패시도는_보존한다(monkeypatch):
    monkeypatch.setattr(c, "NAVER_NEWS_DAILY_CAP", 1)

    def fail(*_args, **_kwargs):
        raise TimeoutError("재시도 실패 비밀")

    monkeypatch.setattr(naver_client, "_urlopen", fail)
    result = naver_client.search_news("회사", remaining_transport_budget=2)
    assert result.reason_code == c.NEWS_SEARCH_DAILY_CAP
    assert result.transport_attempts == naver_client._daily_counter_count == 1
    assert result.attempt_reason_codes == (c.NEWS_SEARCH_TEMPORARILY_UNAVAILABLE,)
    assert result.retry_recovered is False


@pytest.mark.parametrize("use_budget", [False, True])
def test_열한_논리검색의_전송수를_드러내고_예산을_전달하면_초과하지_않는다(monkeypatch, use_budget):
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("실제 전송 대역")

    monkeypatch.setattr(naver_client, "_urlopen", fail)
    remaining = 11
    results = []
    for index in range(11):
        if use_budget:
            result = naver_client.search_news(f"검색 {index}", remaining_transport_budget=remaining)
        else:
            result = naver_client.search_news(f"검색 {index}")
        remaining -= result.transport_attempts
        results.append(result)
    expected_transports = 11 if use_budget else 22
    assert sum(result.transport_attempts for result in results) == calls == expected_transports
    assert naver_client._daily_counter_count == expected_transports
    assert all(len(result.attempt_reason_codes) == result.transport_attempts for result in results)
    if use_budget:
        assert remaining == 0
        assert results[-1].reason_code == c.NEWS_SEARCH_TRANSPORT_BUDGET_EXHAUSTED
        assert results[-1].transport_attempts == 0
    else:
        assert all(result.transport_attempts == 2 for result in results)
