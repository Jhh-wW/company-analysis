"""사용량 계수기 테스트 — 한도·경보·날짜 전환."""
from __future__ import annotations

import sys
import traceback
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import dart_client
from core.dart_client import DartLimitReached, UsageCounter


class _Response:
    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


def test_tick이_세고_한도에서_멈춘다(tmp_path):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=3)
    assert counter.tick("2026-08-14") == 1
    assert counter.tick("2026-08-14") == 2
    assert counter.tick("2026-08-14") == 3
    with pytest.raises(DartLimitReached):
        counter.tick("2026-08-14")


def test_날짜가_바뀌면_0부터(tmp_path):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=3)
    counter.tick("2026-08-14")
    counter.tick("2026-08-14")
    assert counter.tick("2026-08-15") == 1


def test_경보_문턱_경고_출력(tmp_path, capsys):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)
    for _ in range(8):
        counter.tick("2026-08-14")
    assert "경보" in capsys.readouterr().out


def test_로컬_기본_경로는_예전과_같다(monkeypatch):
    monkeypatch.delenv(dart_client.ENV_DATA_ROOT, raising=False)

    assert dart_client.default_counter_path() == dart_client.COUNTER_PATH


def test_배포에서는_영속_데이터_루트를_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv(dart_client.ENV_DATA_ROOT, str(tmp_path))

    counter = UsageCounter(limit=3)
    counter.tick("2026-08-17")

    expected = tmp_path / "logs" / dart_client.COUNTER_FILENAME
    assert counter.path == expected
    assert expected.exists()


def test_키가_없으면_사용량을_올리기_전에_멈춘다(monkeypatch):
    class Counter:
        calls = 0

        def tick(self):
            self.calls += 1

    counter = Counter()
    monkeypatch.delenv("DART_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="DART_API_KEY"):
        dart_client.get_json("company.json", {}, counter)

    assert counter.calls == 0


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, dart_client.DartAuthenticationError),
        (429, dart_client.DartLimitReached),
        (503, dart_client.DartResponseError),
    ],
)
def test_HTTP오류를_키없는_예외로_정규화한다(
    tmp_path, monkeypatch, status, error_type
):
    secret = "dart-secret-must-not-leak"
    monkeypatch.setenv("DART_API_KEY", secret)

    def fail(request_url, **_kwargs):
        raise urllib.error.HTTPError(request_url, status, "provider body", None, None)

    monkeypatch.setattr(dart_client.urllib.request, "urlopen", fail)
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)

    with pytest.raises(error_type) as caught:
        dart_client.get_json("company.json", {"corp_code": "001"}, counter)

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered
    assert counter.today_count() == 1


def test_timeout을_비밀값없는_통신오류로_정규화한다(
    tmp_path, monkeypatch
):
    secret = "timeout-secret"
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError(secret)),
    )

    with pytest.raises(dart_client.DartTransportError) as caught:
        dart_client.get_json(
            "company.json", {}, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert secret not in rendered


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"message":"missing status"}'])
def test_깨진_JSON과_누락_응답을_계약오류로_막는다(
    tmp_path, monkeypatch, body
):
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(body)
    )

    with pytest.raises(dart_client.DartResponseError):
        dart_client.get_json(
            "company.json", {}, UsageCounter(path=tmp_path / "usage.json", limit=10)
        )


def test_zip이_아닌_다운로드응답의_원문을_반사하지_않는다(
    tmp_path, monkeypatch
):
    secret = "reflected-secret"
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            f"<error><message>{secret}</message></error>".encode()
        ),
    )

    with pytest.raises(dart_client.DartResponseError) as caught:
        dart_client.download_corpcode(
            tmp_path / "corp",
            UsageCounter(path=tmp_path / "usage.json", limit=10),
        )

    assert secret not in str(caught.value)
