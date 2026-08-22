"""Render cron 백업 호출 도구 시험."""

from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from urllib.request import BaseHandler

import pytest

from tools import trigger_backup


class FakeResponse:
    status = 200

    def __init__(self, payload: dict) -> None:
        self.content = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        return self.content


class RedirectResponse(BytesIO):
    def __init__(self, *, status: int, url: str, location: str) -> None:
        super().__init__(b"")
        self.status = status
        self.code = status
        self.url = url
        self.msg = "redirect"
        self.headers = Message()
        self.headers["Location"] = location

    def info(self):
        return self.headers

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


class RedirectingHttpsAdapter(BaseHandler):
    handler_order = 100

    def __init__(self, *, status: int, location: str) -> None:
        self.status = status
        self.location = location
        self.requests: list[tuple[str, str | None]] = []

    def https_open(self, request):
        self.requests.append(
            (request.full_url, request.get_header("Authorization"))
        )
        if len(self.requests) == 1:
            return RedirectResponse(
                status=self.status,
                url=request.full_url,
                location=self.location,
            )
        return FakeResponse(
            {
                "status": "ok",
                "sha256": "c" * 64,
            }
        )


def test_POST_Bearer로_검증완료_응답을_받는다() -> None:
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["method"] = request.get_method()
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "status": "ok",
                    "object_key": "company-analysis/a.sqlite3",
                    "checksum_key": "company-analysis/a.sqlite3.sha256",
                    "sha256": "b" * 64,
                    "deleted_objects": 0,
                }
            )

    def fake_build_opener(*handlers):
        assert len(handlers) == 1
        assert isinstance(
            handlers[0], trigger_backup.internal_trigger.FailClosedRedirectHandler
        )
        return FakeOpener()

    payload = trigger_backup.trigger_once(
        "https://service.example/internal/backup/run",
        "x" * 32,
        opener_factory=fake_build_opener,
    )

    assert payload["sha256"] == "b" * 64
    assert captured == {
        "method": "POST",
        "authorization": "Bearer " + "x" * 32,
        "timeout": trigger_backup.internal_trigger.TIMEOUT_SEC,
    }


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("redirect_host", ["service.example", "other.example"])
def test_리디렉션은_Bearer를_재전송하지_않고_실패한다(
    status: int, redirect_host: str
) -> None:
    secret = "do-not-forward-this-backup-secret"
    source_url = "https://service.example/internal/backup/run"
    redirect_query = "redirect-secret-must-not-leak"
    redirect_url = (
        f"https://{redirect_host}/internal/backup/run?token=" + redirect_query
    )
    adapter = RedirectingHttpsAdapter(status=status, location=redirect_url)
    real_build_opener = trigger_backup.internal_trigger.build_opener

    def build_opener_with_adapter(*handlers):
        return real_build_opener(*handlers, adapter)

    with pytest.raises(trigger_backup.TriggerError) as raised:
        trigger_backup.trigger_once(
            source_url,
            secret,
            opener_factory=build_opener_with_adapter,
        )

    assert str(raised.value) == f"백업 서버가 HTTP {status}을 반환했습니다"
    assert secret not in str(raised.value)
    assert redirect_query not in str(raised.value)
    assert adapter.requests == [(source_url, "Bearer " + secret)]


@pytest.mark.parametrize(
    "url",
    [
        "http://service.example/internal/backup/run",
        "https://user:pass@service.example/internal/backup/run",
        "https://service.example/internal/backup/run?token=value",
        "https://service.example/internal/backup/run/",
        "https://service.example/another-path",
    ],
)
def test_정확한_HTTPS_백업경로만_받는다(monkeypatch, url: str) -> None:
    monkeypatch.setenv(trigger_backup.ENV_TRIGGER_URL, url)
    monkeypatch.setenv(trigger_backup.ENV_TRIGGER_SECRET, "s" * 32)

    with pytest.raises(trigger_backup.TriggerError, match="HTTPS 주소"):
        trigger_backup._config_from_env()


def test_실패출력에_호출비밀을_싣지_않는다(monkeypatch, capsys) -> None:
    secret = "do-not-print-this-backup-secret-123456789"
    monkeypatch.setenv(trigger_backup.ENV_TRIGGER_URL, "not-a-url")
    monkeypatch.setenv(trigger_backup.ENV_TRIGGER_SECRET, secret)

    assert trigger_backup.main([]) == 1

    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "외부 백업 실패" in captured.err
