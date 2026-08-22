"""외부 백업 실행 결과의 영속 상태와 append-only 사건 기록."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final


TABLE_STATE: Final[str] = "backup_run_state"
TABLE_EVENTS: Final[str] = "backup_run_events"
OUTCOME_SUCCEEDED: Final[str] = "succeeded"
OUTCOME_FAILED: Final[str] = "failed"
OUTCOMES: Final[frozenset[str]] = frozenset({OUTCOME_SUCCEEDED, OUTCOME_FAILED})
FAILURE_CONFIGURATION: Final[str] = "백업 저장소 설정 확인 필요"
FAILURE_EXECUTION: Final[str] = "백업 생성·업로드·검증 실패"
OVERDUE_AFTER: Final[timedelta] = timedelta(hours=30)

_CREATE_SQL: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_STATE} (
        singleton             INTEGER PRIMARY KEY CHECK(singleton = 1),
        latest_outcome        TEXT NOT NULL CHECK(latest_outcome IN ('succeeded', 'failed')),
        last_attempt_at       TEXT NOT NULL,
        last_success_at       TEXT NOT NULL DEFAULT '',
        last_failure_at       TEXT NOT NULL DEFAULT '',
        last_failure_summary  TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_EVENTS} (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        outcome          TEXT NOT NULL CHECK(outcome IN ('succeeded', 'failed')),
        failure_summary  TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL
    )
    """,
    f"""CREATE TRIGGER IF NOT EXISTS {TABLE_EVENTS}_no_update
    BEFORE UPDATE ON {TABLE_EVENTS}
    BEGIN SELECT RAISE(ABORT, '백업 실행 사건은 추가만 허용됩니다'); END
    """,
    f"""CREATE TRIGGER IF NOT EXISTS {TABLE_EVENTS}_no_delete
    BEFORE DELETE ON {TABLE_EVENTS}
    BEGIN SELECT RAISE(ABORT, '백업 실행 사건은 추가만 허용됩니다'); END
    """,
)


@dataclass(frozen=True)
class BackupRunState:
    latest_outcome: str
    last_attempt_at: str
    last_success_at: str
    last_failure_at: str
    last_failure_summary: str


@dataclass(frozen=True)
class BackupStatusView:
    status: str
    last_attempt_at: str
    last_success_at: str
    last_failure_at: str
    last_failure_summary: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    for statement in _CREATE_SQL:
        conn.execute(statement)


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("백업 실행 시각은 ISO 8601 형식이어야 합니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("백업 실행 시각에는 시간대가 필요합니다")
    return parsed


def _record(
    conn: sqlite3.Connection,
    *,
    outcome: str,
    now_iso: str,
    failure_summary: str = "",
) -> BackupRunState:
    if outcome not in OUTCOMES:
        raise ValueError("알 수 없는 백업 실행 결과입니다")
    _aware_datetime(now_iso)
    clean_summary = failure_summary.strip()
    if outcome == OUTCOME_FAILED and not clean_summary:
        raise ValueError("백업 실패 요약이 필요합니다")
    if outcome == OUTCOME_SUCCEEDED and clean_summary:
        raise ValueError("백업 성공 기록에는 실패 요약을 넣을 수 없습니다")

    ensure_schema(conn)
    conn.execute(
        f"INSERT INTO {TABLE_EVENTS}(outcome, failure_summary, created_at) VALUES (?, ?, ?)",
        (outcome, clean_summary, now_iso),
    )
    success_at = now_iso if outcome == OUTCOME_SUCCEEDED else ""
    failure_at = now_iso if outcome == OUTCOME_FAILED else ""
    conn.execute(
        f"""INSERT INTO {TABLE_STATE}(
            singleton, latest_outcome, last_attempt_at, last_success_at,
            last_failure_at, last_failure_summary
        ) VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET
            latest_outcome = excluded.latest_outcome,
            last_attempt_at = excluded.last_attempt_at,
            last_success_at = CASE
                WHEN excluded.last_success_at <> '' THEN excluded.last_success_at
                ELSE {TABLE_STATE}.last_success_at END,
            last_failure_at = CASE
                WHEN excluded.last_failure_at <> '' THEN excluded.last_failure_at
                ELSE {TABLE_STATE}.last_failure_at END,
            last_failure_summary = CASE
                WHEN excluded.last_failure_at <> '' THEN excluded.last_failure_summary
                ELSE {TABLE_STATE}.last_failure_summary END
        """,
        (outcome, now_iso, success_at, failure_at, clean_summary),
    )
    state = load(conn)
    if state is None:
        raise RuntimeError("백업 실행 상태를 저장하지 못했습니다")
    return state


def record_success(conn: sqlite3.Connection, *, now_iso: str) -> BackupRunState:
    return _record(conn, outcome=OUTCOME_SUCCEEDED, now_iso=now_iso)


def record_failure(
    conn: sqlite3.Connection, *, now_iso: str, failure_summary: str
) -> BackupRunState:
    return _record(
        conn,
        outcome=OUTCOME_FAILED,
        now_iso=now_iso,
        failure_summary=failure_summary,
    )


def load(conn: sqlite3.Connection) -> BackupRunState | None:
    ensure_schema(conn)
    row = conn.execute(
        f"""SELECT latest_outcome, last_attempt_at, last_success_at,
        last_failure_at, last_failure_summary FROM {TABLE_STATE} WHERE singleton = 1"""
    ).fetchone()
    if row is None:
        return None
    return BackupRunState(
        latest_outcome=str(row["latest_outcome"]),
        last_attempt_at=str(row["last_attempt_at"]),
        last_success_at=str(row["last_success_at"]),
        last_failure_at=str(row["last_failure_at"]),
        last_failure_summary=str(row["last_failure_summary"]),
    )


def status_view(conn: sqlite3.Connection, *, now_iso: str) -> BackupStatusView:
    """최근 실행 결과와 일일 스케줄 중단을 구분해 관리자 표시값을 만든다."""
    now = _aware_datetime(now_iso)
    state = load(conn)
    if state is None:
        return BackupStatusView("not_run", "", "", "", "")
    last_attempt = _aware_datetime(state.last_attempt_at)
    if now < last_attempt:
        status = state.latest_outcome
    elif now - last_attempt > OVERDUE_AFTER:
        status = "overdue"
    else:
        status = state.latest_outcome
    return BackupStatusView(
        status=status,
        last_attempt_at=state.last_attempt_at,
        last_success_at=state.last_success_at,
        last_failure_at=state.last_failure_at,
        last_failure_summary=state.last_failure_summary,
    )
