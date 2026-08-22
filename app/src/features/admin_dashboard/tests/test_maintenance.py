from __future__ import annotations

from datetime import date, datetime

import pytest

from src.features.admin_dashboard import maintenance, store
from src.features.storage import db


def _connect_to(path):
    return lambda: db.connect(path)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 8, 24), "2026-08-17"),
        (date(2026, 8, 30), "2026-08-17"),
        (date(2026, 8, 31), "2026-08-24"),
    ],
)
def test_last_completed_week_period_is_shared_for_the_whole_kst_week(today, expected):
    assert maintenance.last_completed_week_start(today) == expected


def test_current_operation_captures_one_kst_snapshot(monkeypatch):
    current = datetime.fromisoformat("2026-08-24T04:10:11+09:00")
    captured = {}
    monkeypatch.setattr(maintenance.clock, "now_kst", lambda: current)

    def capture(connect, *, operation, today, now_iso, actor_email):
        captured.update(
            connect=connect,
            operation=operation,
            today=today,
            now_iso=now_iso,
            actor_email=actor_email,
        )
        return maintenance.MaintenanceResult(operation, "2026-08-17", "already_done")

    monkeypatch.setattr(maintenance, "run_operation", capture)
    connect = object()
    result = maintenance.run_current_operation(
        connect,
        operation=maintenance.OPERATION_WEEKLY,
        actor_email="admin@example.com",
    )

    assert result.status == "already_done"
    assert captured == {
        "connect": connect,
        "operation": maintenance.OPERATION_WEEKLY,
        "today": date(2026, 8, 24),
        "now_iso": "2026-08-24T04:10:11+09:00",
        "actor_email": "admin@example.com",
    }


def test_weekly_scheduler_creates_previous_week_once_without_ai(tmp_path):
    target = tmp_path / "maintenance.db"
    connect = _connect_to(target)

    first = maintenance.run_weekly_xlsx(
        connect,
        today=date(2026, 8, 24),
        now_iso="2026-08-24T04:10:00+09:00",
    )
    second = maintenance.run_weekly_xlsx(
        connect,
        today=date(2026, 8, 24),
        now_iso="2026-08-24T04:11:00+09:00",
    )

    assert first.status == "ok"
    assert first.period_key == "2026-08-17"
    assert first.weekly_report_saved
    assert second.status == "already_done"
    with connect() as conn:
        assert store.load_weekly_report_blob(conn, week_start="2026-08-17")
        claims = store.list_operation_claims(conn)
    assert len(claims) == 1
    assert claims[0]["status"] == "succeeded"


def test_cleanup_closes_stalled_claim_and_runs_once_per_kst_day(tmp_path):
    target = tmp_path / "maintenance.db"
    connect = _connect_to(target)
    with connect() as conn:
        assert store.claim_operation(
            conn,
            operation=store.OPERATION_WEEKLY_XLSX,
            period_key="2026-08-10",
            actor_email="admin@example.com",
            now_iso="2026-08-17T04:10:00+09:00",
        )

    first = maintenance.run_daily_cleanup(
        connect,
        today=date(2026, 8, 18),
        now_iso="2026-08-18T04:20:00+09:00",
    )
    second = maintenance.run_daily_cleanup(
        connect,
        today=date(2026, 8, 18),
        now_iso="2026-08-18T04:21:00+09:00",
    )

    assert first.status == "ok"
    assert first.stopped_operations == 1
    assert second.status == "already_done"
    with connect() as conn:
        claims = store.list_operation_claims(conn)
        issues = store.list_failed_operation_issues(conn)
    assert {row["status"] for row in claims} == {"succeeded", "failed"}
    assert issues[0]["detail"] == "previous_kst_day_not_finished"


def test_weekly_failure_is_persisted_then_retry_succeeds_without_duplicate(
    tmp_path, monkeypatch
):
    target = tmp_path / "maintenance.db"
    connect = _connect_to(target)
    real_builder = maintenance.weekly.build_weekly_workbook
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("시험용 내부 오류")
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(maintenance.weekly, "build_weekly_workbook", fail_once)

    with pytest.raises(maintenance.MaintenanceRunError):
        maintenance.run_weekly_xlsx(
            connect,
            today=date(2026, 8, 24),
            now_iso="2026-08-24T04:10:00+09:00",
        )
    retried = maintenance.run_weekly_xlsx(
        connect,
        today=date(2026, 8, 24),
        now_iso="2026-08-24T04:11:00+09:00",
    )
    completed = maintenance.run_weekly_xlsx(
        connect,
        today=date(2026, 8, 24),
        now_iso="2026-08-24T04:12:00+09:00",
    )

    assert retried.status == "ok"
    assert completed.status == "already_done"
    with connect() as conn:
        claims = store.list_operation_claims(conn)
        events = conn.execute(
            f"SELECT status, detail FROM {store.TABLE_OPERATION_EVENTS} ORDER BY id"
        ).fetchall()
        reports = store.list_weekly_reports(conn)
    assert claims[0]["status"] == "succeeded"
    assert [(row["status"], row["detail"]) for row in events] == [
        ("running", ""),
        ("failed", "정기 주간 XLSX 생성에 실패했습니다."),
        ("running", "failed_retry"),
        ("succeeded", "정기 관리자 전용 XLSX 생성 완료"),
    ]
    assert len(reports) == 1


def test_fresh_running_is_not_done_and_stale_running_is_reclaimed(tmp_path):
    target = tmp_path / "maintenance.db"
    connect = _connect_to(target)
    with connect() as conn:
        assert store.claim_operation(
            conn,
            operation=store.OPERATION_WEEKLY_XLSX,
            period_key="2026-08-17",
            actor_email="old-worker@example.com",
            now_iso="2026-08-24T04:00:00+09:00",
        )

    fresh = maintenance.run_operation(
        connect,
        operation=maintenance.OPERATION_WEEKLY,
        today=date(2026, 8, 24),
        now_iso="2026-08-24T04:29:59+09:00",
    )
    reclaimed = maintenance.run_operation(
        connect,
        operation=maintenance.OPERATION_WEEKLY,
        today=date(2026, 8, 24),
        now_iso="2026-08-24T04:30:01+09:00",
    )

    assert fresh.status == "already_running"
    assert reclaimed.status == "ok"
    with connect() as conn:
        events = conn.execute(
            f"SELECT status, detail FROM {store.TABLE_OPERATION_EVENTS} ORDER BY id"
        ).fetchall()
    assert [(row["status"], row["detail"]) for row in events] == [
        ("running", ""),
        ("failed", "stale_running_reclaimed"),
        ("running", "stale_retry"),
        ("succeeded", "정기 관리자 전용 XLSX 생성 완료"),
    ]


def test_trigger_secret_rejects_missing_and_short_values(monkeypatch):
    monkeypatch.delenv(maintenance.ENV_TRIGGER_SECRET, raising=False)
    with pytest.raises(maintenance.MaintenanceConfigurationError):
        maintenance.trigger_secret_from_env()

    monkeypatch.setenv(maintenance.ENV_TRIGGER_SECRET, "short")
    with pytest.raises(maintenance.MaintenanceConfigurationError):
        maintenance.trigger_secret_from_env()
