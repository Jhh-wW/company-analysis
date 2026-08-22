"""외부 백업의 성공·실패·실행 지연 영속 상태 시험."""

from __future__ import annotations

import sqlite3

import pytest

from src.features.backup import status


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_실행전은_정상으로_꾸미지_않고_기록없음으로_표시한다() -> None:
    with _connection() as conn:
        view = status.status_view(conn, now_iso="2026-08-22T12:00:00+09:00")

    assert view.status == "not_run"
    assert view.last_attempt_at == ""


def test_실패뒤_성공해도_최근실패와_append_only_사건을_보존한다() -> None:
    with _connection() as conn:
        status.record_failure(
            conn,
            now_iso="2026-08-22T04:00:00+09:00",
            failure_summary=status.FAILURE_EXECUTION,
        )
        state = status.record_success(
            conn,
            now_iso="2026-08-22T05:00:00+09:00",
        )
        events = conn.execute(
            f"SELECT outcome FROM {status.TABLE_EVENTS} ORDER BY id"
        ).fetchall()

        with pytest.raises(sqlite3.IntegrityError, match="추가만"):
            conn.execute(f"DELETE FROM {status.TABLE_EVENTS}")

    assert state.latest_outcome == status.OUTCOME_SUCCEEDED
    assert state.last_success_at == "2026-08-22T05:00:00+09:00"
    assert state.last_failure_at == "2026-08-22T04:00:00+09:00"
    assert state.last_failure_summary == status.FAILURE_EXECUTION
    assert [row["outcome"] for row in events] == ["failed", "succeeded"]


def test_마지막시도가_30시간을_넘으면_과거성공대신_실행지연이다() -> None:
    with _connection() as conn:
        status.record_success(conn, now_iso="2026-08-20T04:00:00+09:00")
        view = status.status_view(conn, now_iso="2026-08-22T12:00:00+09:00")

    assert view.status == "overdue"
    assert view.last_success_at == "2026-08-20T04:00:00+09:00"


def test_시간대없는_시각은_영속기록하지_않는다() -> None:
    with _connection() as conn:
        with pytest.raises(ValueError, match="시간대"):
            status.record_success(conn, now_iso="2026-08-22T04:00:00")
