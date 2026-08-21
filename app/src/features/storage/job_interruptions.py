"""종료 제한시간을 넘긴 작업의 재시작 안내용 최소 영속 상태."""

from __future__ import annotations

import sqlite3


TABLE_NAME = "job_interruptions"
CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    job_id         TEXT PRIMARY KEY,
    interrupted_at TEXT NOT NULL,
    reason         TEXT NOT NULL
)
"""


def mark(
    conn: sqlite3.Connection, *, job_id: str, interrupted_at: str, reason: str
) -> None:
    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME} (job_id, interrupted_at, reason)
        VALUES (?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
            interrupted_at=excluded.interrupted_at,
            reason=excluded.reason
        """,
        (job_id, interrupted_at, reason),
    )


def exists(conn: sqlite3.Connection, job_id: str) -> bool:
    return conn.execute(
        f"SELECT 1 FROM {TABLE_NAME} WHERE job_id = ?", (job_id,)
    ).fetchone() is not None


def delete(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(f"DELETE FROM {TABLE_NAME} WHERE job_id = ?", (job_id,))
