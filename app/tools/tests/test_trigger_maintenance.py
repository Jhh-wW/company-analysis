"""Render cron 정기 작업 호출 도구 시험."""

from __future__ import annotations

import json

import pytest

from tools import trigger_maintenance


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


def test_post_bearer_and_operation_receive_verified_response():
    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured["method"] = request.get_method()
            captured["authorization"] = request.get_header("Authorization")
            # urllib은 사용자 지정 헤더 이름을 title-case로 정규화한다.
            captured["operation"] = request.get_header("X-maintenance-operation")
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "status": "ok",
                    "operation": "weekly",
                    "period_key": "2026-08-17",
                }
            )

    payload = trigger_maintenance.trigger_once(
        "https://service.example/internal/maintenance/run",
        "x" * 32,
        "weekly",
        opener_factory=lambda *_handlers: FakeOpener(),
    )

    assert payload["period_key"] == "2026-08-17"
    assert captured == {
        "method": "POST",
        "authorization": "Bearer " + "x" * 32,
        "operation": "weekly",
        "timeout": trigger_maintenance.internal_trigger.TIMEOUT_SEC,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://service.example/internal/maintenance/run",
        "https://user:pass@service.example/internal/maintenance/run",
        "https://service.example/internal/maintenance/run?token=value",
        "https://service.example/internal/maintenance/run/",
        "https://service.example/another-path",
    ],
)
def test_only_exact_https_maintenance_path_is_accepted(monkeypatch, url):
    monkeypatch.setenv(trigger_maintenance.ENV_TRIGGER_URL, url)
    monkeypatch.setenv(trigger_maintenance.ENV_TRIGGER_SECRET, "s" * 32)

    with pytest.raises(trigger_maintenance.TriggerError, match="HTTPS 주소"):
        trigger_maintenance._config_from_env()


def test_mismatched_operation_response_is_failure():
    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == trigger_maintenance.internal_trigger.TIMEOUT_SEC
            return FakeResponse(
                {
                    "status": "ok",
                    "operation": "cleanup",
                    "period_key": "2026-08-17",
                }
            )

    with pytest.raises(trigger_maintenance.TriggerError, match="완료 상태"):
        trigger_maintenance.trigger_once(
            "https://service.example/internal/maintenance/run",
            "x" * 32,
            "weekly",
            opener_factory=lambda *_handlers: FakeOpener(),
        )


def test_failure_output_does_not_include_secret(monkeypatch, capsys):
    secret = "do-not-print-this-maintenance-secret-123456789"
    monkeypatch.setenv(trigger_maintenance.ENV_TRIGGER_URL, "not-a-url")
    monkeypatch.setenv(trigger_maintenance.ENV_TRIGGER_SECRET, secret)

    assert trigger_maintenance.main(["weekly"]) == 1

    captured = capsys.readouterr()
    assert secret not in captured.err
    assert "정기 작업 실패" in captured.err
