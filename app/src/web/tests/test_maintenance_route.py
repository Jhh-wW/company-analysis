"""cron 전용 관리자 정기 작업 경로의 인증·오류 은닉 시험."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.features.admin_dashboard import maintenance
from src.features.auth import constants as auth_constants
from src.web import main
from src.web.routers import maintenance as maintenance_router


SECRET = "maintenance-trigger-secret-that-is-at-least-32-bytes"


def test_beta_login_wall_inside_bearer_missing_does_not_run(monkeypatch):
    called = False

    def should_not_run(_operation):
        nonlocal called
        called = True

    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(maintenance.ENV_TRIGGER_SECRET, SECRET)
    monkeypatch.setattr(maintenance_router, "_run", should_not_run)
    with TestClient(main.app) as client:
        missing = client.post(
            "/internal/maintenance/run",
            headers={maintenance_router.OPERATION_HEADER: "weekly"},
            follow_redirects=False,
        )
        wrong = client.post(
            "/internal/maintenance/run",
            headers={
                "Authorization": "Bearer wrong-secret",
                maintenance_router.OPERATION_HEADER: "weekly",
            },
            follow_redirects=False,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "location" not in missing.headers
    assert not called


def test_valid_secret_runs_only_requested_maintenance(monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(maintenance.ENV_TRIGGER_SECRET, SECRET)
    monkeypatch.setattr(
        maintenance_router,
        "_run",
        lambda operation: maintenance.MaintenanceResult(
            operation=operation,
            period_key="2026-08-17",
            status="ok",
            weekly_report_saved=True,
        ),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/internal/maintenance/run",
            headers={
                "Authorization": f"Bearer {SECRET}",
                maintenance_router.OPERATION_HEADER: "weekly",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "operation": "weekly",
        "period_key": "2026-08-17",
        "weekly_report_saved": True,
        "purged_reports": 0,
        "stopped_operations": 0,
    }
    assert response.headers["cache-control"] == "private, no-store"


def test_invalid_operation_is_rejected_before_work(monkeypatch):
    called = False

    def should_not_run(_operation):
        nonlocal called
        called = True

    monkeypatch.setenv(maintenance.ENV_TRIGGER_SECRET, SECRET)
    monkeypatch.setattr(maintenance_router, "_run", should_not_run)
    with TestClient(main.app) as client:
        response = client.post(
            "/internal/maintenance/run",
            headers={
                "Authorization": f"Bearer {SECRET}",
                maintenance_router.OPERATION_HEADER: "unknown",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"status": "invalid_operation"}
    assert not called


def test_internal_failure_detail_is_not_exposed(monkeypatch):
    monkeypatch.setenv(maintenance.ENV_TRIGGER_SECRET, SECRET)

    def fail(_operation):
        raise maintenance.MaintenanceRunError("private-database-detail")

    monkeypatch.setattr(maintenance_router, "_run", fail)
    with TestClient(main.app) as client:
        response = client.post(
            "/internal/maintenance/run",
            headers={
                "Authorization": f"Bearer {SECRET}",
                maintenance_router.OPERATION_HEADER: "cleanup",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"status": "failed", "operation": "cleanup"}
    assert "private-database-detail" not in response.text
