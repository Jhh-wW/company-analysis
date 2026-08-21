"""Render cron 백업 호출 도구 시험."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


TOOL_PATH = Path(__file__).resolve().parents[1] / "trigger_backup.py"
SPEC = importlib.util.spec_from_file_location("trigger_backup", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
trigger_backup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = trigger_backup
SPEC.loader.exec_module(trigger_backup)


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


def test_POST_Bearer로_검증완료_응답을_받는다(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
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

    monkeypatch.setattr(trigger_backup, "urlopen", fake_urlopen)

    payload = trigger_backup.trigger_once(
        "https://service.example/internal/backup/run", "x" * 32
    )

    assert payload["sha256"] == "b" * 64
    assert captured == {
        "method": "POST",
        "authorization": "Bearer " + "x" * 32,
        "timeout": trigger_backup.TIMEOUT_SEC,
    }


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
