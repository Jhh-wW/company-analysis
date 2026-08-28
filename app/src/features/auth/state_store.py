"""Google OAuth state의 서버 발급·1회 소비 원장.

브라우저 쿠키에는 Google로 왕복해야 하는 원문을 두지만, 영속 DB에는 SHA-256
해시와 수명·소비 상태만 둔다. 쿠키를 공격자가 마음대로 만들어도 서버가 발급한
행이 없으면 외부 제공자 호출로 넘어갈 수 없다.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Final

from src.features.auth import constants


TABLE_OAUTH_LOGIN_STATES: Final[str] = "oauth_login_states"
STATE_ISSUED: Final[str] = "issued"
STATE_CONSUMED: Final[str] = "consumed"
_CAPACITY_TRIGGER: Final[str] = "oauth_login_states_capacity_guard"


class OAuthStateError(Exception):
    """OAuth state 원장이 요청을 안전하게 완료하지 못했다."""


class OAuthStateFormatError(OAuthStateError):
    """공개 state가 서버 발급 토큰의 정확한 wire 형식이 아니다."""


class OAuthStateCapacityError(OAuthStateError):
    """살아 있는 로그인 왕복의 고정 상한이 찼다."""


_SCHEMA: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_OAUTH_LOGIN_STATES} (
        state_hash  TEXT PRIMARY KEY
                    CHECK(length(state_hash) = 64)
                    CHECK(state_hash NOT GLOB '*[^0-9a-f]*'),
        issued_at   REAL NOT NULL,
        expires_at  REAL NOT NULL CHECK(expires_at > issued_at),
        status      TEXT NOT NULL CHECK(status IN ('{STATE_ISSUED}', '{STATE_CONSUMED}')),
        consumed_at REAL,
        CHECK(
            (status = '{STATE_ISSUED}' AND consumed_at IS NULL)
            OR
            (status = '{STATE_CONSUMED}' AND consumed_at IS NOT NULL
             AND consumed_at >= issued_at)
        )
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_oauth_login_states_expiry
        ON {TABLE_OAUTH_LOGIN_STATES}(expires_at)
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS {_CAPACITY_TRIGGER}
    BEFORE INSERT ON {TABLE_OAUTH_LOGIN_STATES}
    WHEN (SELECT COUNT(*) FROM {TABLE_OAUTH_LOGIN_STATES})
         >= {constants.OAUTH_STATE_MAX_RECORDS}
    BEGIN
        SELECT RAISE(ABORT, 'oauth state capacity exceeded');
    END
    """,
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """영속 schema registry가 호출하는 멱등 bootstrap."""

    for statement in _SCHEMA:
        conn.execute(statement)


def state_has_expected_shape(state: object) -> bool:
    """서버가 만든 32바이트 URL-safe state의 정확한 공개 형식인가."""

    return bool(
        isinstance(state, str)
        and len(state) == constants.STATE_TOKEN_CHARS
        and all(char.isascii() and (char.isalnum() or char in "-_") for char in state)
    )


def hash_state(state: str) -> str:
    """형식이 확인된 원문을 영속용 단방향 식별자로 바꾼다."""

    if not state_has_expected_shape(state):
        raise OAuthStateFormatError("OAuth state 형식이 올바르지 않습니다")
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def _delete_inactive(conn: sqlite3.Connection, *, now: float) -> int:
    """만료됐거나 이미 소비된 state를 다음 원장 작업 전에 치운다.

    소비된 state는 없어도 재사용이 거부된다. 이를 만료 때까지 256칸 상한에
    남겨 두면 공격자가 실패 콜백 256개만 끝내도 새 로그인을 10분 동안 모두
    막을 수 있으므로, replay 방지와 용량 점유를 분리한다.
    """

    cursor = conn.execute(
        f"""
        DELETE FROM {TABLE_OAUTH_LOGIN_STATES}
        WHERE expires_at <= ? OR status = ?
        """,
        (float(now), STATE_CONSUMED),
    )
    return max(int(cursor.rowcount), 0)


def issue_state(conn: sqlite3.Connection, state: str, *, now: float) -> str:
    """state 해시를 짧은 수명으로 기록한다.

    만료 정리의 DELETE가 먼저 SQLite write lock을 잡으므로, 이어지는 개수 확인과
    INSERT는 다른 발급자와 한 거래 안에서 직렬화된다. DB trigger도 같은 상한을
    재확인해 미래 호출부가 개수 확인을 빠뜨려도 무한 성장을 허용하지 않는다.
    """

    state_hash = hash_state(state)
    issued_at = float(now)
    _delete_inactive(conn, now=issued_at)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_OAUTH_LOGIN_STATES} WHERE status = ?",
        (STATE_ISSUED,),
    ).fetchone()
    active_count = 0 if row is None else int(row[0])
    if active_count >= constants.OAUTH_STATE_MAX_RECORDS:
        raise OAuthStateCapacityError("동시에 진행 중인 로그인이 너무 많습니다")
    try:
        conn.execute(
            f"""
            INSERT INTO {TABLE_OAUTH_LOGIN_STATES} (
                state_hash, issued_at, expires_at, status, consumed_at
            ) VALUES (?, ?, ?, ?, NULL)
            """,
            (
                state_hash,
                issued_at,
                issued_at + constants.STATE_MAX_AGE_SEC,
                STATE_ISSUED,
            ),
        )
    except sqlite3.IntegrityError as exc:
        if "capacity" in str(exc).lower():
            raise OAuthStateCapacityError(
                "동시에 진행 중인 로그인이 너무 많습니다"
            ) from exc
        raise OAuthStateError("OAuth state를 발급하지 못했습니다") from exc
    return state_hash


def consume_state(conn: sqlite3.Connection, state: str, *, now: float) -> bool:
    """살아 있는 미사용 state 한 건만 원자적으로 소비한다."""

    state_hash = hash_state(state)
    consumed_at = float(now)
    # 정리와 UPDATE가 같은 write transaction에 들어간다. 만료 경계(== now)는
    # 먼저 사라지며, 동시 callback 두 개 중 UPDATE rowcount=1은 정확히 하나뿐이다.
    _delete_inactive(conn, now=consumed_at)
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_OAUTH_LOGIN_STATES}
        SET status = ?, consumed_at = ?
        WHERE state_hash = ?
          AND status = ?
          AND expires_at > ?
        """,
        (
            STATE_CONSUMED,
            consumed_at,
            state_hash,
            STATE_ISSUED,
            consumed_at,
        ),
    )
    return int(cursor.rowcount) == 1


def count_records(conn: sqlite3.Connection) -> int:
    """운영 진단·시험용 현재 원장 행 수."""

    row = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_OAUTH_LOGIN_STATES}"
    ).fetchone()
    return 0 if row is None else int(row[0])
