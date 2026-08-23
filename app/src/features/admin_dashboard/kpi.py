"""보고서 첫 열람부터 구분점 설문 제출까지의 KPI 계측.

시간 안에 답했다는 사실만 기록한다. 답변이 실제 근거에 맞는지는 운영자가 설문
내용을 별도로 읽어 판정하므로, 이 수치를 이해 정확성으로 승격하지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from src.features.admin_dashboard import store


TABLE_ATTEMPTS: Final[str] = "dashboard_report_kpi_attempts"
TABLE_EVENTS: Final[str] = "dashboard_report_kpi_events"
TARGET_SEC: Final[int] = 3 * 60


@dataclass(frozen=True)
class KpiMeasurement:
    elapsed_sec: int
    within_target: bool


@dataclass(frozen=True)
class KpiSummary:
    measured_responses: int
    within_target: int


def ensure_schema(conn: sqlite3.Connection) -> None:
    """KPI projection과 append-only 사건 표를 멱등 준비한다."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_ATTEMPTS} (
            report_id          TEXT NOT NULL,
            report_version     INTEGER NOT NULL CHECK(report_version >= 1),
            actor_email        TEXT NOT NULL,
            first_viewed_at     TEXT NOT NULL,
            first_survey_at     TEXT NOT NULL DEFAULT '',
            elapsed_sec         INTEGER CHECK(elapsed_sec >= 0),
            within_target       INTEGER CHECK(within_target IN (0, 1)),
            PRIMARY KEY (report_id, report_version, actor_email)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_EVENTS} (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id           TEXT NOT NULL,
            report_version      INTEGER NOT NULL CHECK(report_version >= 1),
            actor_email         TEXT NOT NULL,
            event_type          TEXT NOT NULL CHECK(event_type IN ('first_view', 'survey_submitted')),
            elapsed_sec         INTEGER CHECK(elapsed_sec >= 0),
            within_target       INTEGER CHECK(within_target IN (0, 1)),
            created_at          TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""CREATE TRIGGER IF NOT EXISTS {TABLE_EVENTS}_no_update
        BEFORE UPDATE ON {TABLE_EVENTS}
        BEGIN SELECT RAISE(ABORT, '{TABLE_EVENTS} is append-only'); END"""
    )
    conn.execute(
        f"""CREATE TRIGGER IF NOT EXISTS {TABLE_EVENTS}_no_delete
        BEFORE DELETE ON {TABLE_EVENTS}
        BEGIN SELECT RAISE(ABORT, '{TABLE_EVENTS} is append-only'); END"""
    )


def record_first_view(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    report_version: int,
    actor_email: str,
    now_iso: str,
) -> bool:
    """MEMBER·보고서 snapshot별 첫 결과 열람만 원자적으로 기록한다."""
    ensure_schema(conn)
    clean_report_id = _required(report_id, maximum=128, label="보고서 ID")
    actor = _actor(actor_email)
    viewed_at = _aware_iso(now_iso)
    version = max(1, int(report_version))
    cursor = conn.execute(
        f"""INSERT OR IGNORE INTO {TABLE_ATTEMPTS}
        (report_id, report_version, actor_email, first_viewed_at)
        VALUES (?, ?, ?, ?)""",
        (clean_report_id, version, actor, viewed_at),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        f"""INSERT INTO {TABLE_EVENTS}
        (report_id, report_version, actor_email, event_type, created_at)
        VALUES (?, ?, ?, 'first_view', ?)""",
        (clean_report_id, version, actor, viewed_at),
    )
    return True


def record_first_survey(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    report_version: int,
    actor_email: str,
    now_iso: str,
) -> KpiMeasurement | None:
    """첫 설문만 첫 열람과 결속해 3분 응답시간을 기록한다."""
    ensure_schema(conn)
    clean_report_id = _required(report_id, maximum=128, label="보고서 ID")
    actor = _actor(actor_email)
    version = max(1, int(report_version))
    submitted_at = _aware_iso(now_iso)
    row = conn.execute(
        f"""SELECT first_viewed_at, first_survey_at FROM {TABLE_ATTEMPTS}
        WHERE report_id = ? AND report_version = ? AND actor_email = ?""",
        (clean_report_id, version, actor),
    ).fetchone()
    if row is None or str(row["first_survey_at"]):
        return None
    first_viewed_at = str(row["first_viewed_at"])
    elapsed = int(
        (
            datetime.fromisoformat(submitted_at)
            - datetime.fromisoformat(first_viewed_at)
        ).total_seconds()
    )
    if elapsed < 0:
        return None
    within_target = elapsed <= TARGET_SEC
    cursor = conn.execute(
        f"""UPDATE {TABLE_ATTEMPTS}
        SET first_survey_at = ?, elapsed_sec = ?, within_target = ?
        WHERE report_id = ? AND report_version = ? AND actor_email = ?
          AND first_survey_at = ''""",
        (
            submitted_at,
            elapsed,
            int(within_target),
            clean_report_id,
            version,
            actor,
        ),
    )
    if cursor.rowcount != 1:
        return None
    conn.execute(
        f"""INSERT INTO {TABLE_EVENTS}
        (report_id, report_version, actor_email, event_type, elapsed_sec,
         within_target, created_at)
        VALUES (?, ?, ?, 'survey_submitted', ?, ?, ?)""",
        (
            clean_report_id,
            version,
            actor,
            elapsed,
            int(within_target),
            submitted_at,
        ),
    )
    return KpiMeasurement(elapsed, within_target)


def summary(conn: sqlite3.Connection, *, start_day: str = "") -> KpiSummary:
    """휴지통이 아닌 보고서의 측정 가능한 첫 설문만 집계한다."""
    ensure_schema(conn)
    clean_start = str(start_day or "").strip()[:10]
    query = f"""SELECT COUNT(*) AS measured,
               COALESCE(SUM(CASE WHEN k.within_target = 1 THEN 1 ELSE 0 END), 0)
                 AS within_target
        FROM {TABLE_ATTEMPTS} AS k
        LEFT JOIN {store.TABLE_REPORT_TRASH} AS t ON t.report_id = k.report_id
        WHERE k.first_survey_at <> ''
          AND (t.status IS NULL OR t.status = ?)"""
    params: list[object] = [store.TRASH_ACTIVE]
    if clean_start:
        query += " AND substr(k.first_survey_at, 1, 10) >= ?"
        params.append(clean_start)
    row = conn.execute(query, tuple(params)).fetchone()
    return KpiSummary(int(row["measured"]), int(row["within_target"]))


def _required(value: str, *, maximum: int, label: str) -> str:
    clean = str(value or "").strip()[:maximum]
    if not clean:
        raise ValueError(f"{label}가 필요합니다")
    return clean


def _actor(email: str) -> str:
    actor = _required(email, maximum=320, label="인증 이메일").lower()
    if "@" not in actor:
        raise ValueError("인증된 이메일이 필요합니다")
    return actor


def _aware_iso(value: str) -> str:
    clean = _required(value, maximum=64, label="측정 시각")
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("측정 시각 형식이 올바르지 않습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("측정 시각에는 시간대가 필요합니다")
    return parsed.isoformat()
