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
from src.shared.engine_build_identity import epoch_digest_is_valid


TABLE_SINGLEFLIGHT_LEASES: Final[str] = "report_delivery_singleflight_leases"
INDEX_ONE_ACTIVE_GENERATION: Final[str] = (
    "uq_report_delivery_singleflight_one_active_generation"
)
EXPIRED_LEASE_FAILURE_CODE: Final[str] = "LEASE_EXPIRED"
MIGRATED_DUPLICATE_FAILURE_CODE: Final[str] = "DUPLICATE_ACTIVE_MIGRATED"


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
    engine_epoch_digest: str

    def __post_init__(self) -> None:
        if any(
            not str(value).strip()
            for value in (
                self.billing_bucket_id,
                self.corp_id,
                self.cache_namespace_id,
                self.source_identity_digest,
                self.engine_epoch_digest,
            )
        ):
            raise ValueError("single-flight에는 통장·회사·생성기·출처 신원이 필요합니다")
        if not epoch_digest_is_valid(self.engine_epoch_digest):
            raise ValueError("single-flight engine epoch 영수증이 손상됐습니다")


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
    """획득 결과.

    ``WAIT``가 같은 exact key의 owner를 가리킬 때만 ``handle``이 있다. 다른
    namespace/epoch의 provider는 중복 과금 방지용 장벽일 뿐 그 결과나 lease를
    제어할 권리가 없으므로 ``handle=None``을 돌려준다.
    """

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
    engine_epoch_digest     TEXT NOT NULL DEFAULT '',
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
        billing_bucket_id, corp_id, cache_namespace_id, source_identity_digest,
        engine_epoch_digest
    )
)
"""

_CREATE_ONE_ACTIVE_INDEX_SQL: Final[str] = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_ONE_ACTIVE_GENERATION}
ON {TABLE_SINGLEFLIGHT_LEASES} (
    billing_bucket_id, corp_id, source_identity_digest
)
WHERE state = 'active'
"""


def _primary_key(conn: sqlite3.Connection) -> tuple[str, ...]:
    """현재 표의 선언 순서 PRIMARY KEY 열을 돌려준다."""

    rows = conn.execute(
        f'PRAGMA table_info("{TABLE_SINGLEFLIGHT_LEASES}")'
    ).fetchall()
    return tuple(
        str(row[1])
        for row in sorted(
            (row for row in rows if int(row[5]) > 0),
            key=lambda row: int(row[5]),
        )
    )


def _rebuild_epoch_scoped_table(conn: sqlite3.Connection) -> None:
    """옛 4열 PK를 5열로 바꾸되 진행 중 lease는 빈 epoch 장벽으로 보존한다."""

    columns = tuple(
        str(row[1])
        for row in conn.execute(
            f'PRAGMA table_info("{TABLE_SINGLEFLIGHT_LEASES}")'
        ).fetchall()
    )
    rows = tuple(
        conn.execute(f"SELECT * FROM {TABLE_SINGLEFLIGHT_LEASES}").fetchall()
    )
    legacy_table = f"{TABLE_SINGLEFLIGHT_LEASES}_legacy_epoch"
    conn.execute(f"ALTER TABLE {TABLE_SINGLEFLIGHT_LEASES} RENAME TO {legacy_table}")
    conn.execute(_CREATE_SQL)
    required = {
        "billing_bucket_id",
        "corp_id",
        "cache_namespace_id",
        "source_identity_digest",
        "state",
        "owner_id",
        "lease_token",
        "fencing_token",
        "acquired_at",
        "heartbeat_at",
        "expires_at",
        "completed_content_id",
        "failure_code",
    }
    if required.issubset(columns):
        for raw in rows:
            values = {
                name: raw[position]
                for position, name in enumerate(columns)
            }
            epoch = str(values.get("engine_epoch_digest", "") or "").strip()
            if not epoch_digest_is_valid(epoch):
                epoch = ""
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {TABLE_SINGLEFLIGHT_LEASES} (
                    billing_bucket_id, corp_id, cache_namespace_id,
                    source_identity_digest, engine_epoch_digest,
                    state, owner_id, lease_token, fencing_token,
                    acquired_at, heartbeat_at, expires_at,
                    completed_content_id, completed_artifact_id, failure_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(values["billing_bucket_id"]),
                    str(values["corp_id"]),
                    str(values["cache_namespace_id"]),
                    str(values["source_identity_digest"]),
                    epoch,
                    str(values["state"]),
                    str(values["owner_id"]),
                    str(values["lease_token"]),
                    int(values["fencing_token"]),
                    str(values["acquired_at"]),
                    str(values["heartbeat_at"]),
                    str(values["expires_at"]),
                    str(values["completed_content_id"]),
                    str(values.get("completed_artifact_id", "") or ""),
                    str(values["failure_code"]),
                ),
            )
    conn.execute(f"DROP TABLE {legacy_table}")


def _one_active_generation_constraint_is_exact(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        f'PRAGMA index_list("{TABLE_SINGLEFLIGHT_LEASES}")'
    ).fetchall()
    matching = tuple(
        row for row in rows if str(row[1]) == INDEX_ONE_ACTIVE_GENERATION
    )
    if (
        len(matching) != 1
        or int(matching[0][2]) != 1
        or int(matching[0][4]) != 1
    ):
        return False
    columns = tuple(
        str(row[2])
        for row in conn.execute(
            f'PRAGMA index_info("{INDEX_ONE_ACTIVE_GENERATION}")'
        ).fetchall()
    )
    if columns != ("billing_bucket_id", "corp_id", "source_identity_digest"):
        return False
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (INDEX_ONE_ACTIVE_GENERATION,),
    ).fetchone()
    raw_sql = "" if sql_row is None or sql_row[0] is None else str(sql_row[0])
    normalized_sql = "".join(raw_sql.split()).lower()
    return normalized_sql.endswith("wherestate='active'")


def _install_one_active_generation_constraint(conn: sqlite3.Connection) -> None:
    """옛 신·구 코드가 함께 써도 비용 범위마다 ACTIVE 하나만 허용한다.

    Python 조회는 예전 바이너리의 INSERT를 통제할 수 없다. SQLite partial
    unique index가 namespace·epoch를 열쇠에서 일부러 빼, 구 바이너리가 빈
    epoch 기본값으로 직접 INSERT해도 살아 있는 provider와 겹치지 못하게 한다.

    이미 결함 버전이 ACTIVE 중복을 남긴 DB에는 가장 늦게 만료되는 한 행만
    장벽으로 보존하고 나머지는 실패 이력으로 닫은 뒤 제약을 건다. 실제 만료
    판정은 caller의 ``now``를 써야 하므로 여기서 벽시계를 추측하지 않는다.
    """

    if _one_active_generation_constraint_is_exact(conn):
        return
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"DROP INDEX IF EXISTS {INDEX_ONE_ACTIVE_GENERATION}")
    conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES} AS duplicate
        SET state = ?, completed_content_id = '', completed_artifact_id = '',
            failure_code = ?
        WHERE duplicate.state = ?
          AND EXISTS (
              SELECT 1
              FROM {TABLE_SINGLEFLIGHT_LEASES} AS keeper
              WHERE keeper.billing_bucket_id = duplicate.billing_bucket_id
                AND keeper.corp_id = duplicate.corp_id
                AND keeper.source_identity_digest = duplicate.source_identity_digest
                AND keeper.state = ?
                AND (
                    keeper.expires_at > duplicate.expires_at
                    OR (
                        keeper.expires_at = duplicate.expires_at
                        AND keeper.rowid < duplicate.rowid
                    )
                )
          )
        """,
        (
            LeaseState.FAILED.value,
            MIGRATED_DUPLICATE_FAILURE_CODE,
            LeaseState.ACTIVE.value,
            LeaseState.ACTIVE.value,
        ),
    )
    conn.execute(_CREATE_ONE_ACTIVE_INDEX_SQL)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """영속 schema registry용 표준 bootstrap."""

    conn.execute(_CREATE_SQL)
    expected_primary_key = (
        "billing_bucket_id",
        "corp_id",
        "cache_namespace_id",
        "source_identity_digest",
        "engine_epoch_digest",
    )
    if _primary_key(conn) != expected_primary_key:
        # 옛 4열 PK에 epoch 열만 보태면 A 행 때문에 B INSERT가 무시된다.
        # 그렇다고 활성 A 행을 버리면 B가 같은 AI를 동시에 호출한다. 5열로
        # 재작성하면서 옛 행은 빈 epoch 장벽으로만 보존해 결과 재사용은 막고
        # 살아 있는 provider가 끝날 시간은 지킨다.
        _rebuild_epoch_scoped_table(conn)
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
    if "engine_epoch_digest" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_SINGLEFLIGHT_LEASES} "
            "ADD COLUMN engine_epoch_digest TEXT NOT NULL DEFAULT ''"
        )
    # 새 5열 API는 빈 epoch를 만들지 않는다. 빈 값은 구 process의 진행 중
    # provider가 끝날 때까지만 쓰는 이관 장벽이며 결과로 재사용하지 않는다.
    _install_one_active_generation_constraint(conn)


def ensure_lease_schema(conn: sqlite3.Connection) -> None:
    """기존 호출부에 의미가 보이는 별칭."""

    ensure_schema(conn)


def _key_values(key: LeaseKey) -> tuple[str, str, str, str, str]:
    return (
        key.billing_bucket_id.strip(),
        key.corp_id.strip(),
        key.cache_namespace_id.strip(),
        key.source_identity_digest.strip(),
        key.engine_epoch_digest,
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
          AND engine_epoch_digest = ?
        """,
        _key_values(key),
    ).fetchone()
    return None if row is None else tuple(row)


def _select_foreign_active_row(
    conn: sqlite3.Connection,
    key: LeaseKey,
    *,
    current_text: str,
) -> tuple[object, ...] | None:
    """다른 namespace/epoch의 살아 있는 provider를 재사용 없이 기다린다."""

    row = conn.execute(
        f"""
        SELECT state, owner_id, lease_token, fencing_token,
               acquired_at, heartbeat_at, expires_at,
               completed_content_id, completed_artifact_id, failure_code
        FROM {TABLE_SINGLEFLIGHT_LEASES}
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND source_identity_digest = ?
          AND (cache_namespace_id <> ? OR engine_epoch_digest <> ?)
          AND state = ? AND expires_at > ?
        ORDER BY expires_at DESC LIMIT 1
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.source_identity_digest,
            key.cache_namespace_id,
            key.engine_epoch_digest,
            LeaseState.ACTIVE.value,
            current_text,
        ),
    ).fetchone()
    return None if row is None else tuple(row)


def _close_expired_active_rows(
    conn: sqlite3.Connection,
    key: LeaseKey,
    *,
    current_text: str,
) -> None:
    """같은 비용·회사·출처의 만료 장벽을 현재 write transaction에서 닫는다."""

    conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET state = ?, completed_content_id = '', completed_artifact_id = '',
            failure_code = ?
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND source_identity_digest = ? AND state = ? AND expires_at <= ?
        """,
        (
            LeaseState.FAILED.value,
            EXPIRED_LEASE_FAILURE_CODE,
            key.billing_bucket_id,
            key.corp_id,
            key.source_identity_digest,
            LeaseState.ACTIVE.value,
            current_text,
        ),
    )


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
    # foreign-epoch 확인과 새 owner INSERT를 같은 write lock 아래 직렬화한다.
    # 그렇지 않으면 A/B가 서로 없다고 읽은 직후 둘 다 provider를 열 수 있다.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    owner = str(owner_id).strip()
    if not owner:
        raise ValueError("single-flight owner ID가 필요합니다")
    current = require_aware(now, label="lease 획득")
    ttl = _duration(lease_ttl, label="lease TTL")
    current_text = utc_text(current, label="lease 획득")
    expires_text = utc_text(current + ttl, label="lease 만료")
    # partial unique index가 구 바이너리의 raw INSERT까지 막는다. 그 제약을
    # 풀 수 있는 만료 전이와 새 owner 획득은 반드시 같은 write transaction이다.
    _close_expired_active_rows(conn, key, current_text=current_text)
    foreign_active = _select_foreign_active_row(
        conn,
        key,
        current_text=current_text,
    )
    if foreign_active is not None:
        return AcquireResult(
            disposition=AcquireDisposition.WAIT,
            handle=None,
        )
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
            source_identity_digest, engine_epoch_digest,
            state, owner_id, lease_token,
            fencing_token, acquired_at, heartbeat_at, expires_at,
            completed_content_id, completed_artifact_id, failure_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
          AND engine_epoch_digest = ?
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
          AND engine_epoch_digest = ?
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
          AND engine_epoch_digest = ?
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
          AND engine_epoch_digest = ?
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


def quarantine_completed_key_after_receipt_mismatch(
    conn: sqlite3.Connection,
    *,
    key: LeaseKey,
    now: dt.datetime,
) -> bool:
    """commit 재대조가 어긋난 정확한 epoch 완료행을 즉시 만료한다.

    일반 무효화는 기대한 content·artifact가 일치해야 하지만, 행 자체가
    drift한 경우에는 그 조건이 독성 waiter 결과를 남긴다. 이 복구 전용
    경로는 5열 epoch key와 COMPLETED 상태를 조건으로 삼고 행은 보존해 다음
    owner의 fencing token이 계속 증가하게 한다.
    """

    ensure_lease_schema(conn)
    current_text = utc_text(
        require_aware(now, label="완료 fan-out 격리"),
        label="완료 fan-out 격리",
    )
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SINGLEFLIGHT_LEASES}
        SET expires_at = ?, heartbeat_at = ?
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND source_identity_digest = ?
          AND engine_epoch_digest = ? AND state = ?
        """,
        (
            current_text,
            current_text,
            *_key_values(key),
            LeaseState.COMPLETED.value,
        ),
    )
    return cursor.rowcount == 1


def completed_result_matches(
    conn: sqlite3.Connection,
    *,
    key: LeaseKey,
    content_snapshot_id: str,
    artifact_id: str,
    now: dt.datetime,
) -> bool:
    """아직 유효한 완료행이 정확한 content·PDF를 가리키는지 읽기만 한다.

    정식 장기 캐시 결속이 없는 waiter fan-out은 이 증거가 있을 때만 재사용한다.
    owner Delivery가 같은 통장에 있다는 사실만으로는 «이번 요청이 그 완료행을
    기다렸다»는 증명이 되지 않으므로 대체할 수 없다.
    """

    ensure_lease_schema(conn)
    row = _select_row(conn, key)
    if row is None:
        return False
    try:
        state = LeaseState(str(row[0]))
        expires_at = datetime_from_utc_text(row[6], label="완료 fan-out 만료")
    except ValueError as exc:
        raise LeaseError("single-flight 완료 증거가 손상됐습니다") from exc
    current = require_aware(now, label="완료 fan-out 확인")
    return bool(
        state is LeaseState.COMPLETED
        and expires_at > current
        and str(row[7]) == str(content_snapshot_id).strip()
        and str(row[8]) == str(artifact_id).strip()
    )


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
          AND engine_epoch_digest = ?
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
