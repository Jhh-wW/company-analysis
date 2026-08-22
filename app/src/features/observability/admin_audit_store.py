"""관리자 상태 변경의 append-only SQLite 감사 원장."""

from __future__ import annotations

import sqlite3
import string
from typing import Final


TABLE_ADMIN_AUDIT_EVENTS: Final[str] = "admin_audit_events"
_SAFE_FIELD_CHARS: Final[frozenset[str]] = frozenset(
    string.ascii_letters + string.digits + "_.:-"
)
_SAFE_TIME_CHARS: Final[frozenset[str]] = frozenset(
    string.digits + "T:+-.Z"
)
CREATE_TABLE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_ADMIN_AUDIT_EVENTS} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time  TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    reason_code TEXT NOT NULL
)
"""
CREATE_NO_UPDATE_TRIGGER_SQL: Final[str] = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_ADMIN_AUDIT_EVENTS}_no_update
BEFORE UPDATE ON {TABLE_ADMIN_AUDIT_EVENTS}
BEGIN SELECT RAISE(ABORT, 'admin audit events are append-only'); END
"""
CREATE_NO_DELETE_TRIGGER_SQL: Final[str] = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_ADMIN_AUDIT_EVENTS}_no_delete
BEFORE DELETE ON {TABLE_ADMIN_AUDIT_EVENTS}
BEGIN SELECT RAISE(ABORT, 'admin audit events are append-only'); END
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """감사 표와 update/delete 차단 trigger를 멱등 생성한다."""

    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


def _require_safe(value: str, *, maximum: int, time_value: bool = False) -> str:
    allowed = _SAFE_TIME_CHARS if time_value else _SAFE_FIELD_CHARS
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or any(character not in allowed for character in value)
    ):
        raise ValueError("관리자 감사 필드 형식이 올바르지 않습니다")
    return value


def append_success(
    conn: sqlite3.Connection,
    *,
    event_time: str,
    request_id: str,
    actor_id: str,
    action: str,
    target_id: str,
    reason_code: str,
) -> None:
    """호출자의 상태 변경 transaction에 성공 감사 사건을 함께 넣는다."""

    values = (
        _require_safe(event_time, maximum=40, time_value=True),
        _require_safe(request_id, maximum=64),
        _require_safe(actor_id, maximum=80),
        _require_safe(action, maximum=64),
        _require_safe(target_id, maximum=80),
        _require_safe(reason_code, maximum=48),
    )
    ensure_schema(conn)
    conn.execute(
        f"""
        INSERT INTO {TABLE_ADMIN_AUDIT_EVENTS}
            (event_time, request_id, actor_id, action, target_id, outcome, reason_code)
        VALUES (?, ?, ?, ?, ?, 'success', ?)
        """,
        values,
    )
