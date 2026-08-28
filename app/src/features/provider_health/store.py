"""provider별 가역 차단기(circuit breaker)의 SQLite 정본.

이 기능은 “지금 해당 provider를 호출해도 되는가”만 다룬다. 실제 비용,
보수 부채, 예약금은 budget 원장의 소유이며 이 표에 복사하지 않는다.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Final

from src.features.provider_health import constants


TABLE_STATES: Final[str] = "provider_health_states"
TABLE_EVENTS: Final[str] = "provider_health_events"


class ProviderHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    OPEN = "OPEN"
    PROBING = "PROBING"


class ProviderFailureKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    PROVIDER_RESPONSE = "provider_response"
    ACCOUNT_CONFIGURATION = "account_configuration"


class ProviderHealthWriteConflict(RuntimeError):
    """동일 provider 상태가 계속 바뀌어 안전하게 기록하지 못함."""


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: str
    state: ProviderHealthState
    consecutive_failures: int
    open_until: str
    probe_lease_until: str
    last_success_at: str
    last_failure_at: str
    last_failure_kind: str
    updated_at: str
    version: int


@dataclass(frozen=True)
class ProviderPermission:
    provider: str
    allowed: bool
    state: ProviderHealthState
    reason_code: str
    retry_at: str
    is_probe: bool


_PROVIDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EVENT_SUCCESS: Final[str] = "success"
_EVENT_FAILURE: Final[str] = "failure"
_EVENT_PROBE_ACQUIRED: Final[str] = "probe_acquired"
_EVENT_PROBE_RECLAIMED: Final[str] = "probe_reclaimed"
_EVENT_NEUTRAL: Final[str] = "neutral"

_CREATE_STATEMENTS: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_STATES} (
        provider              TEXT PRIMARY KEY,
        state                 TEXT NOT NULL CHECK(state IN (
            'HEALTHY', 'DEGRADED', 'OPEN', 'PROBING'
        )),
        consecutive_failures  INTEGER NOT NULL CHECK(consecutive_failures >= 0),
        open_until            TEXT NOT NULL DEFAULT '',
        probe_lease_until     TEXT NOT NULL DEFAULT '',
        last_success_at       TEXT NOT NULL DEFAULT '',
        last_failure_at       TEXT NOT NULL DEFAULT '',
        last_failure_kind     TEXT NOT NULL DEFAULT '',
        updated_at            TEXT NOT NULL,
        version               INTEGER NOT NULL CHECK(version >= 1)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_EVENTS} (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        provider              TEXT NOT NULL,
        event_kind            TEXT NOT NULL CHECK(event_kind IN (
            'success', 'failure', 'probe_acquired', 'probe_reclaimed', 'neutral'
        )),
        previous_state        TEXT NOT NULL CHECK(previous_state IN (
            'HEALTHY', 'DEGRADED', 'OPEN', 'PROBING'
        )),
        next_state            TEXT NOT NULL CHECK(next_state IN (
            'HEALTHY', 'DEGRADED', 'OPEN', 'PROBING'
        )),
        failure_kind          TEXT NOT NULL DEFAULT '',
        consecutive_failures  INTEGER NOT NULL CHECK(consecutive_failures >= 0),
        open_until            TEXT NOT NULL DEFAULT '',
        probe_lease_until     TEXT NOT NULL DEFAULT '',
        occurred_at           TEXT NOT NULL
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_provider_health_events_provider
    ON {TABLE_EVENTS}(provider, id)
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS provider_health_events_no_update
    BEFORE UPDATE ON {TABLE_EVENTS}
    BEGIN
        SELECT RAISE(ABORT, 'provider 건강 사건은 수정할 수 없습니다');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS provider_health_events_no_delete
    BEFORE DELETE ON {TABLE_EVENTS}
    BEGIN
        SELECT RAISE(ABORT, 'provider 건강 사건은 삭제할 수 없습니다');
    END
    """,
)


def _event_table_supports_neutral(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE_EVENTS,),
    ).fetchone()
    return row is None or "'neutral'" in str(row[0] or "")


def _migrate_event_table_for_neutral(conn: sqlite3.Connection) -> None:
    """배포 전 구 schema의 CHECK를 자료 손실 없이 한 번 넓힌다."""

    legacy = f"{TABLE_EVENTS}_without_neutral"
    conn.execute("DROP TRIGGER IF EXISTS provider_health_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS provider_health_events_no_delete")
    conn.execute("DROP INDEX IF EXISTS idx_provider_health_events_provider")
    conn.execute(f"ALTER TABLE {TABLE_EVENTS} RENAME TO {legacy}")
    conn.execute(_CREATE_STATEMENTS[1])
    conn.execute(
        f"""INSERT INTO {TABLE_EVENTS} (
            id, provider, event_kind, previous_state, next_state, failure_kind,
            consecutive_failures, open_until, probe_lease_until, occurred_at
        ) SELECT
            id, provider, event_kind, previous_state, next_state, failure_kind,
            consecutive_failures, open_until, probe_lease_until, occurred_at
        FROM {legacy}"""
    )
    conn.execute(f"DROP TABLE {legacy}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """provider 건강 정본과 append-only 사건표를 멱등으로 만든다."""

    # states/events를 먼저 만든 뒤, 이전 개발 DB의 event CHECK만 forward-only로
    # 넓힌다. provider_health는 아직 운영 배포 전이지만 로컬 DB도 추측 없이
    # 그대로 보존한다.
    conn.execute(_CREATE_STATEMENTS[0])
    conn.execute(_CREATE_STATEMENTS[1])
    if not _event_table_supports_neutral(conn):
        _migrate_event_table_for_neutral(conn)
    for statement in _CREATE_STATEMENTS[2:]:
        conn.execute(statement)


def _provider(value: str) -> str:
    if type(value) is not str or _PROVIDER_PATTERN.fullmatch(value) is None:
        raise ValueError("provider 식별자 형식이 올바르지 않습니다")
    return value


def _moment(value: str) -> datetime:
    if type(value) is not str or not value:
        raise ValueError("시간은 시간대가 포함된 ISO 형식이어야 합니다")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("시간은 시간대가 포함된 ISO 형식이어야 합니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("시간은 시간대가 포함된 ISO 형식이어야 합니다")
    return parsed


def _canonical_time(value: str) -> tuple[datetime, str]:
    parsed = _moment(value)
    return parsed, parsed.isoformat()


def _default_snapshot(provider: str) -> ProviderHealthSnapshot:
    return ProviderHealthSnapshot(
        provider=provider,
        state=ProviderHealthState.HEALTHY,
        consecutive_failures=0,
        open_until="",
        probe_lease_until="",
        last_success_at="",
        last_failure_at="",
        last_failure_kind="",
        updated_at="",
        version=0,
    )


def _snapshot(row: sqlite3.Row | tuple[object, ...]) -> ProviderHealthSnapshot:
    return ProviderHealthSnapshot(
        provider=str(row[0]),
        state=ProviderHealthState(str(row[1])),
        consecutive_failures=int(row[2]),
        open_until=str(row[3]),
        probe_lease_until=str(row[4]),
        last_success_at=str(row[5]),
        last_failure_at=str(row[6]),
        last_failure_kind=str(row[7]),
        updated_at=str(row[8]),
        version=int(row[9]),
    )


def get_state(conn: sqlite3.Connection, provider: str) -> ProviderHealthSnapshot:
    """현재 상태를 읽기만 한다. 아직 기록이 없으면 기본 HEALTHY다."""
    clean_provider = _provider(provider)
    row = conn.execute(
        f"""SELECT provider, state, consecutive_failures, open_until,
        probe_lease_until, last_success_at, last_failure_at, last_failure_kind,
        updated_at, version FROM {TABLE_STATES} WHERE provider = ?""",
        (clean_provider,),
    ).fetchone()
    return _default_snapshot(clean_provider) if row is None else _snapshot(row)


def list_states(conn: sqlite3.Connection) -> tuple[ProviderHealthSnapshot, ...]:
    """저장된 provider 상태를 provider 순으로 읽기만 한다."""
    rows = conn.execute(
        f"""SELECT provider, state, consecutive_failures, open_until,
        probe_lease_until, last_success_at, last_failure_at, last_failure_kind,
        updated_at, version FROM {TABLE_STATES} ORDER BY provider"""
    ).fetchall()
    return tuple(_snapshot(row) for row in rows)


def _permission(
    snapshot: ProviderHealthSnapshot, *, now: datetime
) -> ProviderPermission:
    if snapshot.state is ProviderHealthState.HEALTHY:
        return ProviderPermission(
            snapshot.provider, True, snapshot.state,
            constants.REASON_AVAILABLE, "", False,
        )
    if snapshot.state is ProviderHealthState.DEGRADED:
        return ProviderPermission(
            snapshot.provider, True, snapshot.state,
            constants.REASON_DEGRADED, "", False,
        )
    if snapshot.state is ProviderHealthState.OPEN:
        deadline = _moment(snapshot.open_until)
        if now < deadline:
            return ProviderPermission(
                snapshot.provider, False, snapshot.state,
                constants.REASON_COOLDOWN, snapshot.open_until, False,
            )
        return ProviderPermission(
            snapshot.provider, True, snapshot.state,
            constants.REASON_PROBE_AVAILABLE, "", True,
        )
    deadline = _moment(snapshot.probe_lease_until)
    if now < deadline:
        return ProviderPermission(
            snapshot.provider, False, snapshot.state,
            constants.REASON_PROBE_IN_PROGRESS,
            snapshot.probe_lease_until, False,
        )
    return ProviderPermission(
        snapshot.provider, True, snapshot.state,
        constants.REASON_PROBE_AVAILABLE, "", True,
    )


def peek_permission(
    conn: sqlite3.Connection, provider: str, *, now_iso: str
) -> ProviderPermission:
    """호출 가능 여부를 읽기만 한다. cooldown 만료도 쓰기를 발생시키지 않는다."""
    now = _moment(now_iso)
    return _permission(get_state(conn, provider), now=now)


def list_blocked(
    conn: sqlite3.Connection, providers: tuple[str, ...], *, now_iso: str
) -> tuple[ProviderPermission, ...]:
    """readiness에서 쓸 수 있도록 현재 막힌 provider만 순수 조회한다."""
    return tuple(
        permission
        for provider in providers
        if not (
            permission := peek_permission(conn, provider, now_iso=now_iso)
        ).allowed
    )


def _begin_immediate_if_possible(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _insert_event(
    conn: sqlite3.Connection,
    *,
    provider: str,
    event_kind: str,
    previous_state: ProviderHealthState,
    snapshot: ProviderHealthSnapshot,
    occurred_at: str,
) -> None:
    conn.execute(
        f"""INSERT INTO {TABLE_EVENTS}
        (provider, event_kind, previous_state, next_state, failure_kind,
         consecutive_failures, open_until, probe_lease_until, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            provider,
            event_kind,
            previous_state.value,
            snapshot.state.value,
            snapshot.last_failure_kind if event_kind == _EVENT_FAILURE else "",
            snapshot.consecutive_failures,
            snapshot.open_until,
            snapshot.probe_lease_until,
            occurred_at,
        ),
    )


def _replace_state(
    conn: sqlite3.Connection,
    *,
    previous: ProviderHealthSnapshot,
    state: ProviderHealthState,
    consecutive_failures: int,
    open_until: str,
    probe_lease_until: str,
    last_success_at: str,
    last_failure_at: str,
    last_failure_kind: str,
    updated_at: str,
) -> ProviderHealthSnapshot | None:
    if previous.version == 0:
        try:
            conn.execute(
                f"""INSERT INTO {TABLE_STATES}
                (provider, state, consecutive_failures, open_until,
                 probe_lease_until, last_success_at, last_failure_at,
                 last_failure_kind, updated_at, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    previous.provider, state.value, consecutive_failures,
                    open_until, probe_lease_until, last_success_at,
                    last_failure_at, last_failure_kind, updated_at,
                ),
            )
        except sqlite3.IntegrityError:
            return None
    else:
        cursor = conn.execute(
            f"""UPDATE {TABLE_STATES} SET
            state = ?, consecutive_failures = ?, open_until = ?,
            probe_lease_until = ?, last_success_at = ?, last_failure_at = ?,
            last_failure_kind = ?, updated_at = ?, version = version + 1
            WHERE provider = ? AND version = ?""",
            (
                state.value, consecutive_failures, open_until,
                probe_lease_until, last_success_at, last_failure_at,
                last_failure_kind, updated_at, previous.provider,
                previous.version,
            ),
        )
        if cursor.rowcount != 1:
            return None
    return get_state(conn, previous.provider)


def acquire_probe(
    conn: sqlite3.Connection, provider: str, *, now_iso: str
) -> ProviderPermission:
    """일반 호출은 통과시키고, 만료된 차단기는 탐색 하나만 원자적으로 허용한다."""
    clean_provider = _provider(provider)
    now, clean_now = _canonical_time(now_iso)
    initial = get_state(conn, clean_provider)
    initial_permission = _permission(initial, now=now)
    if not initial_permission.is_probe:
        return initial_permission

    _begin_immediate_if_possible(conn)
    for _attempt in range(3):
        current = get_state(conn, clean_provider)
        permission = _permission(current, now=now)
        if not permission.allowed or not permission.is_probe:
            return permission
        event_kind = (
            _EVENT_PROBE_RECLAIMED
            if current.state is ProviderHealthState.PROBING
            else _EVENT_PROBE_ACQUIRED
        )
        probe_until = (
            now + timedelta(seconds=constants.PROBE_LEASE_SEC)
        ).isoformat()
        updated = _replace_state(
            conn,
            previous=current,
            state=ProviderHealthState.PROBING,
            consecutive_failures=current.consecutive_failures,
            open_until="",
            probe_lease_until=probe_until,
            last_success_at=current.last_success_at,
            last_failure_at=current.last_failure_at,
            last_failure_kind=current.last_failure_kind,
            updated_at=clean_now,
        )
        if updated is None:
            continue
        _insert_event(
            conn,
            provider=clean_provider,
            event_kind=event_kind,
            previous_state=current.state,
            snapshot=updated,
            occurred_at=clean_now,
        )
        return ProviderPermission(
            clean_provider, True, ProviderHealthState.PROBING,
            constants.REASON_PROBE_AVAILABLE, "", True,
        )
    raise ProviderHealthWriteConflict("provider 탐색 권한을 안전하게 획득하지 못했습니다")


def record_success(
    conn: sqlite3.Connection, provider: str, *, now_iso: str
) -> ProviderHealthSnapshot:
    """정상 응답을 기록하고 해당 provider의 차단기를 즉시 닫는다."""
    clean_provider = _provider(provider)
    _now, clean_now = _canonical_time(now_iso)
    _begin_immediate_if_possible(conn)
    for _attempt in range(3):
        current = get_state(conn, clean_provider)
        updated = _replace_state(
            conn,
            previous=current,
            state=ProviderHealthState.HEALTHY,
            consecutive_failures=0,
            open_until="",
            probe_lease_until="",
            last_success_at=clean_now,
            last_failure_at=current.last_failure_at,
            last_failure_kind="",
            updated_at=clean_now,
        )
        if updated is None:
            continue
        _insert_event(
            conn,
            provider=clean_provider,
            event_kind=_EVENT_SUCCESS,
            previous_state=current.state,
            snapshot=updated,
            occurred_at=clean_now,
        )
        return updated
    raise ProviderHealthWriteConflict("provider 성공을 안전하게 기록하지 못했습니다")


def release_probe_without_health_signal(
    conn: sqlite3.Connection, provider: str, *, now_iso: str
) -> ProviderHealthSnapshot:
    """요청별 4xx·로컬 오류가 probe를 점유한 경우 즉시 원래 OPEN으로 돌린다.

    이런 관측은 provider가 건강하다는 증거도, 일시 장애라는 증거도 아니다.
    HEALTHY/DEGRADED 상태에서는 아무것도 쓰지 않으며, 이전 OPEN에서 얻은
    PROBING lease만 해제해 다음 독립 probe가 시도될 수 있게 한다.
    """

    clean_provider = _provider(provider)
    _now, clean_now = _canonical_time(now_iso)
    _begin_immediate_if_possible(conn)
    for _attempt in range(3):
        current = get_state(conn, clean_provider)
        if current.state is not ProviderHealthState.PROBING:
            return current
        updated = _replace_state(
            conn,
            previous=current,
            state=ProviderHealthState.OPEN,
            consecutive_failures=current.consecutive_failures,
            # 현재 시각부터 바로 다음 probe가 가능하다. 중립 관측 때문에 새
            # cooldown을 지어내지 않는다.
            open_until=clean_now,
            probe_lease_until="",
            last_success_at=current.last_success_at,
            last_failure_at=current.last_failure_at,
            last_failure_kind=current.last_failure_kind,
            updated_at=clean_now,
        )
        if updated is None:
            continue
        _insert_event(
            conn,
            provider=clean_provider,
            event_kind=_EVENT_NEUTRAL,
            previous_state=current.state,
            snapshot=updated,
            occurred_at=clean_now,
        )
        return updated
    raise ProviderHealthWriteConflict("provider 중립 관측을 안전하게 기록하지 못했습니다")


def record_failure(
    conn: sqlite3.Connection,
    provider: str,
    *,
    failure_kind: ProviderFailureKind,
    now_iso: str,
) -> ProviderHealthSnapshot:
    """실패를 해당 provider에만 누적하고 유한 cooldown을 설정한다."""
    clean_provider = _provider(provider)
    if not isinstance(failure_kind, ProviderFailureKind):
        raise ValueError("provider 실패 종류가 올바르지 않습니다")
    now, clean_now = _canonical_time(now_iso)
    _begin_immediate_if_possible(conn)
    for _attempt in range(3):
        current = get_state(conn, clean_provider)
        failure_count = current.consecutive_failures + 1
        should_open = (
            current.state in {ProviderHealthState.OPEN, ProviderHealthState.PROBING}
            or failure_count >= constants.FAILURES_TO_OPEN
        )
        if should_open:
            next_state = ProviderHealthState.OPEN
            candidate_deadline = (
                now + timedelta(seconds=constants.OPEN_COOLDOWN_SEC)
            )
            if current.state is ProviderHealthState.OPEN and current.open_until:
                candidate_deadline = max(
                    candidate_deadline, _moment(current.open_until)
                )
            open_until = candidate_deadline.isoformat()
        else:
            next_state = ProviderHealthState.DEGRADED
            open_until = ""
        updated = _replace_state(
            conn,
            previous=current,
            state=next_state,
            consecutive_failures=failure_count,
            open_until=open_until,
            probe_lease_until="",
            last_success_at=current.last_success_at,
            last_failure_at=clean_now,
            last_failure_kind=failure_kind.value,
            updated_at=clean_now,
        )
        if updated is None:
            continue
        _insert_event(
            conn,
            provider=clean_provider,
            event_kind=_EVENT_FAILURE,
            previous_state=current.state,
            snapshot=updated,
            occurred_at=clean_now,
        )
        return updated
    raise ProviderHealthWriteConflict("provider 실패를 안전하게 기록하지 못했습니다")
