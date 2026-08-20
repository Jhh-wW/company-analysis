"""Persisted, crash-aware idempotency state for Notion exports.

The Notion create-page API is not intrinsically idempotent.  A browser retry,
two concurrent requests, or a process crash after the remote page was created
must therefore never silently start a second create operation.  This module
owns one SQLite row for each ``(job, target, report digest)`` capability and
uses compare-and-swap updates to decide which request may call Notion.

Only safe metadata is stored.  Tokens, report contents and remote error bodies
never enter this table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Final

from src.features.pipeline.port import Report
from src.features.storage.reports import report_to_dict


TABLE_NOTION_EXPORTS: Final[str] = "notion_export_operations"
TARGET_NOTION: Final[str] = "notion"

STATE_IN_PROGRESS: Final[str] = "in_progress"
STATE_SUCCEEDED: Final[str] = "succeeded"
STATE_PARTIAL: Final[str] = "partial"
STATE_FAILED: Final[str] = "failed"
STATE_UNKNOWN: Final[str] = "unknown"
TERMINAL_RETRY_STATES: Final[frozenset[str]] = frozenset(
    {STATE_PARTIAL, STATE_FAILED, STATE_UNKNOWN}
)

# A live urllib request is bounded to 15 seconds per chunk.  Five minutes is
# deliberately longer than a normal export, but finite so a process crash is
# eventually surfaced as "unknown" instead of looking busy forever.
STALE_IN_PROGRESS_AFTER_SEC: Final[float] = 300.0

CREATE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NOTION_EXPORTS} (
    job_id        TEXT NOT NULL,
    target        TEXT NOT NULL,
    report_digest TEXT NOT NULL,
    state         TEXT NOT NULL CHECK (
        state IN ('{STATE_IN_PROGRESS}', '{STATE_SUCCEEDED}',
                  '{STATE_PARTIAL}', '{STATE_FAILED}', '{STATE_UNKNOWN}')
    ),
    revision      INTEGER NOT NULL CHECK (revision >= 1),
    page_id       TEXT NOT NULL DEFAULT '',
    page_url      TEXT NOT NULL DEFAULT '',
    error_kind    TEXT NOT NULL DEFAULT '',
    started_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (job_id, target, report_digest)
)
"""


@dataclass(frozen=True)
class ExportRecord:
    job_id: str
    target: str
    report_digest: str
    state: str
    revision: int
    page_id: str
    page_url: str
    error_kind: str
    started_at: float
    updated_at: float


@dataclass(frozen=True)
class ClaimResult:
    record: ExportRecord
    claimed: bool


def report_digest(report: Report) -> str:
    """Return a deterministic digest of the exact report being exported."""
    payload = json.dumps(
        report_to_dict(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the feature-owned table when opening a legacy database."""
    conn.execute(CREATE_SQL)


def _record(row: sqlite3.Row) -> ExportRecord:
    return ExportRecord(
        job_id=str(row["job_id"]),
        target=str(row["target"]),
        report_digest=str(row["report_digest"]),
        state=str(row["state"]),
        revision=int(row["revision"]),
        page_id=str(row["page_id"]),
        page_url=str(row["page_url"]),
        error_kind=str(row["error_kind"]),
        started_at=float(row["started_at"]),
        updated_at=float(row["updated_at"]),
    )


def load(
    conn: sqlite3.Connection,
    job_id: str,
    digest: str,
    *,
    target: str = TARGET_NOTION,
) -> ExportRecord | None:
    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT job_id, target, report_digest, state, revision,
               page_id, page_url, error_kind, started_at, updated_at
          FROM {TABLE_NOTION_EXPORTS}
         WHERE job_id = ? AND target = ? AND report_digest = ?
        """,
        (job_id, target, digest),
    ).fetchone()
    return _record(row) if row is not None else None


def claim(
    conn: sqlite3.Connection,
    job_id: str,
    digest: str,
    *,
    explicit_retry: bool = False,
    expected_revision: int | None = None,
    target: str = TARGET_NOTION,
    now: float | None = None,
) -> ClaimResult:
    """Atomically claim one export attempt or return the persisted decision.

    A terminal attempt can only be replaced by a deliberate retry whose form
    carries the exact revision shown to the administrator.  The revision CAS
    prevents two confirmation tabs from both creating a page.
    """
    ensure_schema(conn)
    current_time = time.time() if now is None else now
    inserted = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_NOTION_EXPORTS} (
            job_id, target, report_digest, state, revision,
            page_id, page_url, error_kind, started_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, '', '', '', ?, ?)
        """,
        (
            job_id,
            target,
            digest,
            STATE_IN_PROGRESS,
            current_time,
            current_time,
        ),
    )
    if inserted.rowcount == 1:
        created = load(conn, job_id, digest, target=target)
        assert created is not None
        return ClaimResult(record=created, claimed=True)

    existing = load(conn, job_id, digest, target=target)
    assert existing is not None

    # A process may have died after the remote request was accepted.  We can no
    # longer prove whether a page exists, so never automatically send again.
    if (
        existing.state == STATE_IN_PROGRESS
        and current_time - existing.updated_at >= STALE_IN_PROGRESS_AFTER_SEC
    ):
        conn.execute(
            f"""
            UPDATE {TABLE_NOTION_EXPORTS}
               SET state = ?, error_kind = 'interrupted', updated_at = ?
             WHERE job_id = ? AND target = ? AND report_digest = ?
               AND state = ? AND revision = ? AND updated_at = ?
            """,
            (
                STATE_UNKNOWN,
                current_time,
                job_id,
                target,
                digest,
                STATE_IN_PROGRESS,
                existing.revision,
                existing.updated_at,
            ),
        )
        existing = load(conn, job_id, digest, target=target)
        assert existing is not None

    if (
        explicit_retry
        and expected_revision is not None
        and existing.state in TERMINAL_RETRY_STATES
    ):
        updated = conn.execute(
            f"""
            UPDATE {TABLE_NOTION_EXPORTS}
               SET state = ?, revision = revision + 1,
                   error_kind = '', started_at = ?, updated_at = ?
             WHERE job_id = ? AND target = ? AND report_digest = ?
               AND state = ? AND revision = ?
            """,
            (
                STATE_IN_PROGRESS,
                current_time,
                current_time,
                job_id,
                target,
                digest,
                existing.state,
                expected_revision,
            ),
        )
        latest = load(conn, job_id, digest, target=target)
        assert latest is not None
        return ClaimResult(record=latest, claimed=updated.rowcount == 1)

    return ClaimResult(record=existing, claimed=False)


def finish(
    conn: sqlite3.Connection,
    job_id: str,
    digest: str,
    revision: int,
    *,
    state: str,
    page_id: str = "",
    page_url: str = "",
    error_kind: str = "",
    target: str = TARGET_NOTION,
    now: float | None = None,
) -> bool:
    """Persist an adapter result iff this worker still owns the revision."""
    if state not in {
        STATE_SUCCEEDED,
        STATE_PARTIAL,
        STATE_FAILED,
        STATE_UNKNOWN,
    }:
        raise ValueError("완료 상태가 올바르지 않습니다")
    ensure_schema(conn)
    current_time = time.time() if now is None else now
    updated = conn.execute(
        f"""
        UPDATE {TABLE_NOTION_EXPORTS}
           SET state = ?,
               page_id = CASE WHEN ? <> '' THEN ? ELSE page_id END,
               page_url = CASE WHEN ? <> '' THEN ? ELSE page_url END,
               error_kind = ?, updated_at = ?
         WHERE job_id = ? AND target = ? AND report_digest = ?
           AND state IN (?, ?) AND revision = ?
        """,
        (
            state,
            page_id,
            page_id,
            page_url,
            page_url,
            error_kind,
            current_time,
            job_id,
            target,
            digest,
            STATE_IN_PROGRESS,
            STATE_UNKNOWN,
            revision,
        ),
    )
    return updated.rowcount == 1


__all__ = [
    "CREATE_SQL",
    "ClaimResult",
    "ExportRecord",
    "STALE_IN_PROGRESS_AFTER_SEC",
    "STATE_FAILED",
    "STATE_IN_PROGRESS",
    "STATE_PARTIAL",
    "STATE_SUCCEEDED",
    "STATE_UNKNOWN",
    "claim",
    "ensure_schema",
    "finish",
    "load",
    "report_digest",
]
