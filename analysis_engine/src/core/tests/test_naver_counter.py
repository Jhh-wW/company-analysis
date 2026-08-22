"""네이버 사용량 계수기의 영속 경로 시험."""

from __future__ import annotations

import json
import sys
import traceback
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import naver_client


class _Response:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


def test_로컬_기본_경로는_예전과_같다(monkeypatch):
    monkeypatch.delenv(naver_client.ENV_DATA_ROOT, raising=False)

    assert naver_client.default_counter_path() == naver_client.COUNTER_PATH


def test_배포에서는_영속_데이터_루트를_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv(naver_client.ENV_DATA_ROOT, str(tmp_path))

    naver_client._tick(today="2026-08-17")

    expected = tmp_path / "logs" / naver_client.COUNTER_FILENAME
    assert json.loads(expected.read_text(encoding="utf-8")) == {"2026-08-17": 1}


def test_키가_없으면_사용량을_올리기_전에_멈춘다(monkeypatch):
    calls = 0

    def tick():
        nonlocal calls
        calls += 1

    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(naver_client, "_tick", tick)

    with pytest.raises(RuntimeError, match="NAVER_CLIENT_ID"):
        naver_client.search_news("회사")

    assert calls == 0


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, naver_client.NaverAuthenticationError),
        (429, naver_client.NaverLimitReached),
        (503, naver_client.NaverResponseError),
    ],
)
def test_HTTP오류를_비밀값없는_예외로_정규화한다(
    tmp_path, monkeypatch, status, error_type
):
    secret = "naver-secret-must-not-leak"
    monkeypatch.setenv("NAVER_CLIENT_ID", "fake-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", secret)
    monkeypatch.setattr(
        naver_client,
        "default_counter_path",
        lambda: tmp_path / "usage.json",
    )

    def fail(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, status, secret, None, None)

    monkeypatch.setattr(naver_client.urllib.request, "urlopen", fail)

    with pytest.raises(error_type) as caught:
        naver_client.search_news("회사")

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered


def test_timeout을_비밀값없는_통신오류로_정규화한다(
    tmp_path, monkeypatch
):
    secret = "timeout-secret"
    monkeypatch.setenv("NAVER_CLIENT_ID", "fake-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(
        naver_client,
        "default_counter_path",
        lambda: tmp_path / "usage.json",
    )
    monkeypatch.setattr(
        naver_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError(secret)),
    )

    with pytest.raises(naver_client.NaverTransportError) as caught:
        naver_client.search_news("회사")

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered


@pytest.mark.parametrize("body", [b"not-json", b"[]", b"{}", b'{"items":null}'])
def test_깨진_JSON과_items_누락을_계약오류로_막는다(
    tmp_path, monkeypatch, body
):
    monkeypatch.setenv("NAVER_CLIENT_ID", "fake-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(
        naver_client,
        "default_counter_path",
        lambda: tmp_path / "usage.json",
    )
    monkeypatch.setattr(
        naver_client.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(body)
    )

    with pytest.raises(naver_client.NaverResponseError):
        naver_client.search_news("회사")


def test_필드_형식이_틀려도_전체_호출이_터지지_않는다(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NAVER_CLIENT_ID", "fake-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "fake-secret")
    monkeypatch.setattr(
        naver_client,
        "default_counter_path",
        lambda: tmp_path / "usage.json",
    )
    monkeypatch.setattr(
        naver_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            b'{"items":[{"title":null,"link":3,"pubDate":false}]}'
        ),
    )

    items = naver_client.search_news("회사")

    assert len(items) == 1
    assert items[0].title == ""
    assert items[0].link == ""
    assert items[0].pub_date is None
