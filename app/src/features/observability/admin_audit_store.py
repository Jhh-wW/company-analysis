"""관리자 상태 변경의 append-only SQLite 감사 원장."""

from __future__ import annotations

import datetime as dt
import sqlite3
import string
from typing import Callable, Final


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
    event_time  TEXT NOT NULL CHECK(
        length(event_time) BETWEEN 1 AND 40
        AND event_time NOT GLOB '*[^0-9T:+.Z-]*'
    ),
    request_id  TEXT NOT NULL CHECK(
        length(request_id) BETWEEN 1 AND 64
        AND request_id NOT GLOB '*[^A-Za-z0-9_.:-]*'
    ),
    actor_id    TEXT NOT NULL CHECK(
        length(actor_id) BETWEEN 1 AND 80
        AND actor_id NOT GLOB '*[^A-Za-z0-9_.:-]*'
    ),
    action      TEXT NOT NULL CHECK(
        length(action) BETWEEN 1 AND 64
        AND action NOT GLOB '*[^A-Za-z0-9_.:-]*'
    ),
    target_id   TEXT NOT NULL CHECK(
        length(target_id) BETWEEN 1 AND 80
        AND target_id NOT GLOB '*[^A-Za-z0-9_.:-]*'
    ),
    outcome     TEXT NOT NULL CHECK(outcome = 'success'),
    reason_code TEXT NOT NULL CHECK(
        length(reason_code) BETWEEN 1 AND 48
        AND reason_code NOT GLOB '*[^A-Za-z0-9_.:-]*'
    )
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


def _require_event_time(value: str) -> str:
    clean = _require_safe(value, maximum=40, time_value=True)
    try:
        parsed = dt.datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("관리자 감사 시각 형식이 올바르지 않습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("관리자 감사 시각에는 시간대가 필요합니다")
    return clean


def validate_persisted_events(
    conn: sqlite3.Connection,
    *,
    deadline_check: Callable[[], None] | None = None,
) -> None:
    """기존 관리자 감사 행을 현재 실제 소비 계약으로 전수 검사한다.

    CHECK 제약을 우회해 만든 DB나 제약 도입 전 DB도 복구 시 fail-closed하도록
    저장형·허용문자·길이·성공 결과·시간대 시각을 모두 다시 확인한다.
    """

    cursor = conn.execute(
        f"""
        SELECT id,
               typeof(event_time), event_time,
               typeof(request_id), request_id,
               typeof(actor_id), actor_id,
               typeof(action), action,
               typeof(target_id), target_id,
               typeof(outcome), outcome,
               typeof(reason_code), reason_code
        FROM {TABLE_ADMIN_AUDIT_EVENTS}
        ORDER BY id
        """
    )
    while rows := cursor.fetchmany(128):
        for row in rows:
            if deadline_check is not None:
                deadline_check()
            if type(row[0]) is not int or row[0] <= 0:
                raise ValueError("관리자 감사 행 식별자가 올바르지 않습니다")
            if any(row[index] != "text" for index in range(1, 15, 2)):
                raise ValueError("관리자 감사 DB 저장형이 올바르지 않습니다")
            values = tuple(row[index] for index in range(2, 16, 2))
            if any(type(value) is not str for value in values):
                raise ValueError("관리자 감사 DB 필드 형식이 올바르지 않습니다")
            (
                event_time,
                request_id,
                actor_id,
                action,
                target_id,
                outcome,
                reason_code,
            ) = values
            _require_event_time(event_time)
            _require_safe(request_id, maximum=64)
            _require_safe(actor_id, maximum=80)
            _require_safe(action, maximum=64)
            _require_safe(target_id, maximum=80)
            if outcome != "success":
                raise ValueError("관리자 감사 결과는 성공 기록이어야 합니다")
            _require_safe(reason_code, maximum=48)


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
        _require_event_time(event_time),
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
