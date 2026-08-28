"""같은 비용 통장 안의 동일 생성만 합치는 SQLite single-flight lease."""

from __future__ import annotations

import datetime as dt
import sqlite3
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Final

from src.features.report_delivery.canonical import (
    datetime_from_utc_text,
    require_aware,
    utc_text,
)


TABLE_SINGLEFLIGHT_LEASES: Final[str] = "report_delivery_singleflight_leases"


class LeaseError(RuntimeError):
    """single-flight lease 계약을 지킬 수 없다."""


@dataclass(frozen=True)
class LeaseKey:
    """비용 주체를 섞지 않는 생성 작업 신원.

    ``billing_bucket_id``에는 원문 계정·링크가 아니라 기존 비용 계층이 만든
    불투명 지문을 넣는다. 다른 통장은 같은 회사여도 서로의 비용을 대신 내지 않는다.
    """

    billing_bucket_id: str
    corp_id: str
    cache_namespace_id: str
    source_identity_digest: str

    def __post_init__(self) -> None:
        if any(
            not str(value).strip()
            for value in (
                self.billing_bucket_id,
                self.corp_id,
                self.cache_namespace_id,
                self.source_identity_digest,
            )
        ):
            raise ValueError("single-flight에는 통장·회사·생성기·출처 신원이 필요합니다")


class LeaseState(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class AcquireDisposition(str, Enum):
    ACQUIRED = "acquired"
    TAKEOVER = "takeover"
    WAIT = "wait"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class LeaseHandle:
    key: LeaseKey
    owner_id: str
    lease_token: str
    fencing_token: int
    acquired_at: dt.datetime
    heartbeat_at: dt.datetime
    expires_at: dt.datetime


@dataclass(frozen=True)
class AcquireResult:
    disposition: AcquireDisposition
    handle: LeaseHandle | None = None
    completed_content_id: str = ""
    completed_artifact_id: str = ""
    failure_code: str = ""


_CREATE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SINGLEFLIGHT_LEASES} (
    billing_bucket_id       TEXT NOT NULL,
    corp_id                 TEXT NOT NULL,
    cache_namespace_id      TEXT NOT NULL,
    source_identity_digest  TEXT NOT NULL,
    state                   TEXT NOT NULL CHECK(state IN ('active', 'completed', 'failed')),
    owner_id                TEXT NOT NULL,
    lease_token             TEXT NOT NULL,
    fencing_token           INTEGER NOT NULL,
    acquired_at             TEXT NOT NULL,
    heartbeat_at            TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    completed_content_id    TEXT NOT NULL,
    completed_artifact_id   TEXT NOT NULL,
    failure_code            TEXT NOT NULL,
    PRIMARY KEY (
        billing_bucket_id, corp_id, cache_namespace_id, source_identity_digest
    )
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """영속 schema registry용 표준 bootstrap."""

    conn.execute(_CREATE_SQL)
    columns = {
        str(row[1])
        for row in conn.execute(
            f'PRAGMA table_info("{TABLE_SINGLEFLIGHT_LEASES}")'
        ).fetchall()
    }
    if "completed_artifact_id" not in columns:
        # 이 표가 아직 배포되기 전 만들어진 개발 DB도 폐기하지 않고
        # content-only 완료행을 «재사용 불가»인 빈 artifact로 올린다.
        conn.execute(
            f"ALTER TABLE {TABLE_SINGLEFLIGHT_LEASES} "
            "ADD COLUMN completed_artifact_id TEXT NOT NULL DEFAULT ''"
        )


def ensure_lease_schema(conn: sqlite3.Connection) -> None:
    """기존 호출부에 의미가 보이는 별칭."""

    ensure_schema(conn)


def _key_values(key: LeaseKey) -> tuple[str, str, str, str]:
    return (
        key.billing_bucket_id.strip(),
        key.corp_id.strip(),
        key.cache_namespace_id.strip(),
        key.source_identity_digest.strip(),
    )


def _duration(value: dt.timedelta, *, label: str) -> dt.timedelta:
    if not isinstance(value, dt.timedelta) or value <= dt.timedelta(0):
        raise ValueError(f"{label}은 0보다 길어야 합니다")
    return value


def _select_row(conn: sqlite3.Connection, key: LeaseKey) -> tuple[object, ...] | None:
    row = conn.execute(
        f"""
        SELECT state, owner_id, lease_token, fencing_token,
               acquired_at, heartbeat_at, expires_at,
               completed_content_id, completed_artifact_id, failure_code
        FROM {TABLE_SINGLEFLIGHT_LEASES}
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
        """,
        _key_values(key),
    ).fetchone()
    return None if row is None else tuple(row)


def _handle_from_row(key: LeaseKey, row: tuple[object, ...]) -> LeaseHandle:
    return LeaseHandle(
        key=key,
        owner_id=str(row[1]),
        lease_token=str(row[2]),
        fencing_token=int(row[3]),
        acquired_at=datetime_from_utc_text(row[4], label="lease 획득"),
        heartbeat_at=datetime_from_utc_text(row[5], label="lease heartbeat"),
        expires_at=datetime_from_utc_text(row[6], label="lease 만료"),
    )


def _result_from_live_row(
    key: LeaseKey, row: tuple[object, ...]
) -> AcquireResult:
    try:
        state = LeaseState(str(row[0]))
    except ValueError as exc:
        raise LeaseError("single-flight 상태가 손상됐습니다") from exc
    if state is LeaseState.ACTIVE:
        return AcquireResult(
            disposition=AcquireDisposition.WAIT,
            handle=_handle_from_row(key, row),
        )
    if state is LeaseState.COMPLETED:
        return AcquireResult(
            disposition=AcquireDisposition.COMPLETED,
            completed_content_id=str(row[7]),
            completed_artifact_id=str(row[8]),
        )
    return AcquireResult(
        disposition=AcquireDisposition.FAILED,
        failure_code=str(row[9]),
    )


def acquire(
    conn: sqlite3.Connection,
    *,
    key: LeaseKey,
    owner_id: str,
    now: dt.datetime,
    lease_ttl: dt.timedelta,
) -> AcquireResult:
    """작업을 잡거나, 같은 통장 작업·완료·실패를 정직하게 돌려준다.

    만료된 active/completed/failed 행은 fencing token을 올려 takeover한다.
    완료·실패 행도 짧은 fan-out 기간 뒤에는 재사용되지 않아, 오래된 cache가
    만료됐는데 single-flight 결과가 영원히 새 생성을 막는 일을 피한다.
    """

    ensure_lease_schema(conn)
    owner = str(owner_id).strip()
    if not owner:
        raise ValueError("single-flight owner ID가 필요합니다")
    current = require_aware(now, label="lease 획득")
    ttl = _duration(lease_ttl, label="lease TTL")
    current_text = utc_text(current, label="lease 획득")
    expires_text = utc_text(current + ttl, label="lease 만료")
    token = uuid.uuid4().hex
    insert_payload = (
        *_key_values(key),
        LeaseState.ACTIVE.value,
        owner,
        token,
        1,
        current_text,
        current_text,
        expires_text,
        "",
        "",
        "",
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_SINGLEFLIGHT_LEASES} (
            billing_bucket_id, corp_id, cache_namespace_id,
            source_identity_digest, state, owner_id, lease_token,
            fencing_token, acquired_at, heartbeat_at, expires_at,
            completed_content_id, completed_artifact_id, failure_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        insert_payload,
    )
    if cursor.rowcount == 1:
        row = _select_row(conn, key)
        if row is None:  # pragma: no cover - 같은 statement 직후 방어선
            raise LeaseError("획득한 single-flight lease를 다시 읽지 못했습니다")
        return AcquireResult(
            disposition=AcquireDisposition.ACQUIRED,
            handle=_handle_from_row(key, row),
        )

    row = _select_row(conn, key)
    if row is None:
        raise LeaseError("경쟁 중인 single-flight lease를 읽지 못했습니다")
    expires_at = datetime_from_utc_text(row[6], label="lease 만료")
    if expires_at > current:
        return _result_from_live_row(key, row)

    previous_fence = int(row[3])
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET state = ?, owner_id = ?, lease_token = ?,
            fencing_token = fencing_token + 1,
            acquired_at = ?, heartbeat_at = ?, expires_at = ?,
            completed_content_id = '', completed_artifact_id = '',
            failure_code = ''
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
          AND fencing_token = ? AND expires_at <= ?
        """,
        (
            LeaseState.ACTIVE.value,
            owner,
            token,
            current_text,
            current_text,
            expires_text,
            *_key_values(key),
            previous_fence,
            current_text,
        ),
    )
    updated = _select_row(conn, key)
    if updated is None:
        raise LeaseError("takeover 뒤 single-flight lease를 읽지 못했습니다")
    if cursor.rowcount == 1:
        return AcquireResult(
            disposition=AcquireDisposition.TAKEOVER,
            handle=_handle_from_row(key, updated),
        )
    # 다른 process가 먼저 takeover했다. 조건부 UPDATE가 막았으므로 그 결과를
    # 기다리면 되고, 옛 owner의 token으로 상태를 건드릴 수 없다.
    return _result_from_live_row(key, updated)


def heartbeat(
    conn: sqlite3.Connection,
    *,
    handle: LeaseHandle,
    now: dt.datetime,
    lease_ttl: dt.timedelta,
) -> LeaseHandle | None:
    """아직 만료되지 않은 현재 fencing owner만 lease를 연장한다."""

    ensure_lease_schema(conn)
    current = require_aware(now, label="lease heartbeat")
    ttl = _duration(lease_ttl, label="lease TTL")
    current_text = utc_text(current, label="lease heartbeat")
    expires_text = utc_text(current + ttl, label="lease 만료")
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET heartbeat_at = ?, expires_at = ?
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
          AND state = ? AND owner_id = ? AND lease_token = ?
          AND fencing_token = ? AND expires_at > ?
        """,
        (
            current_text,
            expires_text,
            *_key_values(handle.key),
            LeaseState.ACTIVE.value,
            handle.owner_id,
            handle.lease_token,
            handle.fencing_token,
            current_text,
        ),
    )
    if cursor.rowcount != 1:
        return None
    row = _select_row(conn, handle.key)
    return None if row is None else _handle_from_row(handle.key, row)


def complete(
    conn: sqlite3.Connection,
    *,
    handle: LeaseHandle,
    content_snapshot_id: str,
    artifact_id: str,
    now: dt.datetime,
    result_fanout_ttl: dt.timedelta,
) -> bool:
    """현재 owner만 완료 결과를 짧은 waiter fan-out 상태로 바꾼다."""

    ensure_lease_schema(conn)
    content_id = str(content_snapshot_id).strip()
    completed_artifact = str(artifact_id).strip()
    if not content_id or not completed_artifact:
        raise ValueError("완료한 content snapshot과 PDF artifact ID가 필요합니다")
    current = require_aware(now, label="lease 완료")
    fanout = _duration(result_fanout_ttl, label="완료 fan-out TTL")
    current_text = utc_text(current, label="lease 완료")
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET state = ?, heartbeat_at = ?, expires_at = ?,
            completed_content_id = ?, completed_artifact_id = ?, failure_code = ''
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
          AND state = ? AND owner_id = ? AND lease_token = ?
          AND fencing_token = ? AND expires_at > ?
        """,
        (
            LeaseState.COMPLETED.value,
            current_text,
            utc_text(current + fanout, label="완료 fan-out 만료"),
            content_id,
            completed_artifact,
            *_key_values(handle.key),
            LeaseState.ACTIVE.value,
            handle.owner_id,
            handle.lease_token,
            handle.fencing_token,
            current_text,
        ),
    )
    return cursor.rowcount == 1


def expire_completed_result(
    conn: sqlite3.Connection,
    *,
    key: LeaseKey,
    content_snapshot_id: str,
    artifact_id: str,
    now: dt.datetime,
) -> bool:
    """손상 cache와 같은 완료 fan-out만 즉시 takeover 가능하게 만료한다.

    key·두 원본 ID·상태를 모두 조건으로 삼아 새 owner나 다른 결과를 건드리지
    않는다. 행을 삭제하지 않아 다음 acquire의 fencing token은 계속 증가한다.
    """

    ensure_lease_schema(conn)
    current_text = utc_text(
        require_aware(now, label="완료 fan-out 무효화"),
        label="완료 fan-out 무효화",
    )
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET expires_at = ?, heartbeat_at = ?
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
          AND state = ? AND completed_content_id = ?
          AND completed_artifact_id = ? AND expires_at > ?
        """,
        (
            current_text,
            current_text,
            *_key_values(key),
            LeaseState.COMPLETED.value,
            str(content_snapshot_id).strip(),
            str(artifact_id).strip(),
            current_text,
        ),
    )
    return cursor.rowcount == 1


def fail(
    conn: sqlite3.Connection,
    *,
    handle: LeaseHandle,
    failure_code: str,
    now: dt.datetime,
    failure_fanout_ttl: dt.timedelta,
) -> bool:
    """현재 owner의 실패 코드를 waiter에게 잠깐 공유하고 즉시 폭주를 막는다."""

    ensure_lease_schema(conn)
    code = str(failure_code).strip()
    if not code:
        raise ValueError("single-flight 실패 코드가 필요합니다")
    current = require_aware(now, label="lease 실패")
    fanout = _duration(failure_fanout_ttl, label="실패 fan-out TTL")
    current_text = utc_text(current, label="lease 실패")
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET state = ?, heartbeat_at = ?, expires_at = ?,
            completed_content_id = '', completed_artifact_id = '', failure_code = ?
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
          AND state = ? AND owner_id = ? AND lease_token = ?
          AND fencing_token = ? AND expires_at > ?
        """,
        (
            LeaseState.FAILED.value,
            current_text,
            utc_text(current + fanout, label="실패 fan-out 만료"),
            code,
            *_key_values(handle.key),
            LeaseState.ACTIVE.value,
            handle.owner_id,
            handle.lease_token,
            handle.fencing_token,
            current_text,
        ),
    )
    return cursor.rowcount == 1
