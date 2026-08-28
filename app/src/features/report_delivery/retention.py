"""30일 휴지통 정리의 DB·PDF blob 수명주기를 중단 뒤에도 완결한다.

정리 대상은 공개 ID에서 실제 Delivery, ContentSnapshot, 승인 PDF artifact로
이어지는 한 벌이다. DB transaction과 파일 삭제는 하나의 원자 작업이 될 수
없으므로, 파일보다 먼저 불변 retirement intent를 별도 commit하고 사건을
append-only로 쌓는다. DB 정리 뒤 프로세스가 꺼져도 다음 시작 또는 다음 정리가
``db_retired`` 사건에서 정확한 blob만 이어서 회수한다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Final

from src.features.report_delivery import artifact
from src.features.report_delivery import singleflight
from src.features.report_delivery import store as lifecycle_store
from src.features.report_delivery.canonical import (
    datetime_from_utc_text,
    require_aware,
    require_sha256_hex,
    utc_text,
)


TABLE_RETIREMENT_INTENTS: Final[str] = "report_delivery_retirement_intents"
TABLE_RETIREMENT_EVENTS: Final[str] = "report_delivery_retirement_events"
TABLE_RETIRED_PUBLIC_IDS: Final[str] = "report_delivery_retired_public_ids"

TRASH_RETENTION_POLICY_ID: Final[str] = "trash-30-days-after-trash-v1"

EVENT_PREPARED: Final[str] = "prepared"
EVENT_PREPARE_BLOCKED: Final[str] = "prepare_blocked"
EVENT_DB_RETIRED: Final[str] = "db_retired"
EVENT_BLOB_BLOCKED: Final[str] = "blob_blocked"
EVENT_COMPLETED_DELETED: Final[str] = "completed_deleted"
EVENT_COMPLETED_ABSENT: Final[str] = "completed_absent"
EVENT_COMPLETED_NO_BLOB: Final[str] = "completed_no_blob"
EVENT_COMPLETED_PRESERVED: Final[str] = "completed_preserved"
EVENT_CANCELLED: Final[str] = "cancelled"

_EVENTS: Final[frozenset[str]] = frozenset(
    {
        EVENT_PREPARED,
        EVENT_PREPARE_BLOCKED,
        EVENT_DB_RETIRED,
        EVENT_BLOB_BLOCKED,
        EVENT_COMPLETED_DELETED,
        EVENT_COMPLETED_ABSENT,
        EVENT_COMPLETED_NO_BLOB,
        EVENT_COMPLETED_PRESERVED,
        EVENT_CANCELLED,
    }
)
_TERMINAL_EVENTS: Final[frozenset[str]] = frozenset(
    {
        EVENT_COMPLETED_DELETED,
        EVENT_COMPLETED_ABSENT,
        EVENT_COMPLETED_NO_BLOB,
        EVENT_CANCELLED,
    }
)
_PRE_DB_EVENTS: Final[frozenset[str]] = frozenset(
    {EVENT_PREPARED, EVENT_PREPARE_BLOCKED}
)
_POST_DB_EVENTS: Final[frozenset[str]] = frozenset(
    {EVENT_DB_RETIRED, EVENT_BLOB_BLOCKED}
)

_SCHEMA: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_RETIREMENT_INTENTS} (
        retirement_id       TEXT PRIMARY KEY,
        public_id           TEXT NOT NULL,
        delivery_id         TEXT NOT NULL,
        content_snapshot_id TEXT NOT NULL,
        artifact_id         TEXT NOT NULL,
        storage_identity    TEXT NOT NULL,
        blob_key            TEXT NOT NULL,
        bytes_sha256        TEXT NOT NULL,
        byte_length         INTEGER NOT NULL CHECK(byte_length >= 0),
        eligible_at         TEXT NOT NULL,
        created_at          TEXT NOT NULL,
        UNIQUE(public_id, eligible_at),
        CHECK(
            (blob_key = '' AND bytes_sha256 = '' AND byte_length = 0)
            OR
            (blob_key <> '' AND bytes_sha256 <> '' AND byte_length > 0)
        )
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_RETIREMENT_EVENTS} (
        event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        retirement_id TEXT NOT NULL
                      REFERENCES {TABLE_RETIREMENT_INTENTS}(retirement_id),
        event_type    TEXT NOT NULL CHECK(event_type IN (
            '{EVENT_PREPARED}', '{EVENT_PREPARE_BLOCKED}',
            '{EVENT_DB_RETIRED}', '{EVENT_BLOB_BLOCKED}',
            '{EVENT_COMPLETED_DELETED}', '{EVENT_COMPLETED_ABSENT}',
            '{EVENT_COMPLETED_NO_BLOB}', '{EVENT_COMPLETED_PRESERVED}',
            '{EVENT_CANCELLED}'
        )),
        recorded_at   TEXT NOT NULL
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_report_retirement_events_latest
    ON {TABLE_RETIREMENT_EVENTS}(retirement_id, event_id DESC)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_RETIRED_PUBLIC_IDS} (
        public_id       TEXT PRIMARY KEY,
        retirement_id   TEXT NOT NULL
                        REFERENCES {TABLE_RETIREMENT_INTENTS}(retirement_id),
        retired_at      TEXT NOT NULL
    )
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_retirement_intents_no_update
    BEFORE UPDATE ON {TABLE_RETIREMENT_INTENTS}
    BEGIN SELECT RAISE(ABORT, 'report retirement intent is immutable'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_retirement_intents_no_delete
    BEFORE DELETE ON {TABLE_RETIREMENT_INTENTS}
    BEGIN SELECT RAISE(ABORT, 'report retirement intent is immutable'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_retirement_events_no_update
    BEFORE UPDATE ON {TABLE_RETIREMENT_EVENTS}
    BEGIN SELECT RAISE(ABORT, 'report retirement events are append-only'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_retirement_events_no_delete
    BEFORE DELETE ON {TABLE_RETIREMENT_EVENTS}
    BEGIN SELECT RAISE(ABORT, 'report retirement events are append-only'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_retired_public_ids_no_update
    BEFORE UPDATE ON {TABLE_RETIRED_PUBLIC_IDS}
    BEGIN SELECT RAISE(ABORT, 'retired public id is immutable'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_retired_public_ids_no_delete
    BEFORE DELETE ON {TABLE_RETIRED_PUBLIC_IDS}
    BEGIN SELECT RAISE(ABORT, 'retired public id is permanent'); END
    """,
)


class RetirementError(RuntimeError):
    """보고서 정리 불변식을 증명하지 못했다."""


@dataclass(frozen=True)
class RetirementIntent:
    retirement_id: str
    public_id: str
    delivery_id: str
    content_snapshot_id: str
    artifact_id: str
    storage_identity: str
    blob_pointer: artifact.BlobPointer | None
    eligible_at: dt.datetime
    created_at: dt.datetime

    def __post_init__(self) -> None:
        if not self.retirement_id.strip() or not self.public_id.strip():
            raise ValueError("정리 intent에는 ID와 공개 ID가 필요합니다")
        require_aware(self.eligible_at, label="보고서 정리 가능")
        require_aware(self.created_at, label="보고서 정리 intent 생성")
        if bool(self.delivery_id) != bool(self.content_snapshot_id):
            raise ValueError("delivery와 content 정리 신원이 함께 있어야 합니다")
        if self.artifact_id and not self.content_snapshot_id:
            raise ValueError("artifact 정리 신원에는 content가 필요합니다")
        if self.blob_pointer is not None and (
            not self.storage_identity.strip() or not self.artifact_id.strip()
        ):
            raise ValueError("blob 정리 신원에는 저장소와 artifact가 필요합니다")


@dataclass(frozen=True)
class DatabaseRetirementResult:
    event_type: str
    delivery_deleted: bool = False
    artifact_deleted: bool = False
    content_deleted: bool = False

    @property
    def blocked(self) -> bool:
        return self.event_type == EVENT_PREPARE_BLOCKED


@dataclass(frozen=True)
class BlobRetirementResult:
    event_type: str
    reclaimed_bytes: int = 0


@dataclass(frozen=True)
class RetirementReconcileReport:
    examined: int = 0
    deleted: int = 0
    absent: int = 0
    preserved: int = 0
    blocked: int = 0
    reclaimed_bytes: int = 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _install_permanent_guards(conn: sqlite3.Connection) -> None:
    """옛 코드가 돌아와도 정리된 공개 ID를 다시 발급하지 못하게 한다."""

    targets = (
        (lifecycle_store.TABLE_DELIVERIES, "public_id", "delivery"),
        (lifecycle_store.TABLE_DELIVERY_INTENTS, "public_id", "intent"),
    )
    for table, column, suffix in targets:
        if not _table_exists(conn, table):
            continue
        conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS report_retired_public_id_blocks_{suffix}
            BEFORE INSERT ON {table}
            WHEN EXISTS (
                SELECT 1 FROM {TABLE_RETIRED_PUBLIC_IDS}
                WHERE public_id = NEW.{column}
            )
            BEGIN SELECT RAISE(ABORT, 'retired public id cannot be reused'); END
            """
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """정리 intent·사건·영구 tombstone만 멱등으로 준비한다."""

    for statement in _SCHEMA:
        conn.execute(statement)
    _install_permanent_guards(conn)


def ensure_retirement_schema(conn: sqlite3.Connection) -> None:
    lifecycle_store.ensure_schema(conn)
    artifact.ensure_schema(conn)
    ensure_schema(conn)


def _intent_from_row(row: sqlite3.Row | tuple[object, ...]) -> RetirementIntent:
    try:
        key = str(row[6])
        digest = str(row[7])
        length = int(row[8])
        pointer = (
            None
            if not key
            else artifact.BlobPointer(
                key=key,
                sha256=require_sha256_hex(digest, label="정리 PDF blob"),
                byte_length=length,
            )
        )
        return RetirementIntent(
            retirement_id=str(row[0]),
            public_id=str(row[1]),
            delivery_id=str(row[2]),
            content_snapshot_id=str(row[3]),
            artifact_id=str(row[4]),
            storage_identity=str(row[5]),
            blob_pointer=pointer,
            eligible_at=datetime_from_utc_text(row[9], label="보고서 정리 가능"),
            created_at=datetime_from_utc_text(row[10], label="정리 intent 생성"),
        )
    except (TypeError, ValueError) as exc:
        raise RetirementError("저장된 보고서 정리 intent가 손상됐습니다") from exc


def load_retirement_intent(
    conn: sqlite3.Connection, retirement_id: str
) -> RetirementIntent | None:
    ensure_retirement_schema(conn)
    row = conn.execute(
        f"""
        SELECT retirement_id, public_id, delivery_id, content_snapshot_id,
               artifact_id, storage_identity, blob_key, bytes_sha256,
               byte_length, eligible_at, created_at
        FROM {TABLE_RETIREMENT_INTENTS} WHERE retirement_id = ?
        """,
        (str(retirement_id).strip(),),
    ).fetchone()
    return None if row is None else _intent_from_row(row)


def latest_event(conn: sqlite3.Connection, retirement_id: str) -> str | None:
    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT event_type FROM {TABLE_RETIREMENT_EVENTS}
        WHERE retirement_id = ? ORDER BY event_id DESC LIMIT 1
        """,
        (str(retirement_id).strip(),),
    ).fetchone()
    return None if row is None else str(row[0])


def _append_event(
    conn: sqlite3.Connection,
    *,
    intent: RetirementIntent,
    event_type: str,
    recorded_at: dt.datetime,
) -> None:
    if event_type not in _EVENTS:
        raise RetirementError("알 수 없는 보고서 정리 사건입니다")
    conn.execute(
        f"""
        INSERT INTO {TABLE_RETIREMENT_EVENTS}
            (retirement_id, event_type, recorded_at)
        VALUES (?, ?, ?)
        """,
        (
            intent.retirement_id,
            event_type,
            utc_text(require_aware(recorded_at, label="정리 사건"), label="정리 사건"),
        ),
    )


def _current_provenance(
    conn: sqlite3.Connection,
    *,
    public_id: str,
    backend: artifact.ArtifactBlobBackend,
) -> tuple[str, str, str, str, artifact.BlobPointer | None]:
    delivery = conn.execute(
        f"""
        SELECT delivery_id, content_snapshot_id
        FROM {lifecycle_store.TABLE_DELIVERIES} WHERE public_id = ?
        """,
        (public_id,),
    ).fetchone()
    if delivery is None:
        return "", "", "", "", None
    delivery_id, content_id = str(delivery[0]), str(delivery[1])
    if lifecycle_store.load_content_snapshot(conn, content_id) is None:
        raise RetirementError("delivery가 가리키는 ContentSnapshot 원본이 없습니다")
    binding = conn.execute(
        f"""
        SELECT artifact_id FROM {artifact.TABLE_DELIVERY_ARTIFACTS}
        WHERE delivery_id = ? AND channel = 'pdf'
        """,
        (delivery_id,),
    ).fetchone()
    if binding is None:
        raise RetirementError("현재 delivery에 최초 승인 PDF 결속이 없습니다")
    artifact_id = str(binding[0])
    metadata = conn.execute(
        f"""
        SELECT content_snapshot_id, original_state, blob_key,
               bytes_sha256, byte_length
        FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?
        """,
        (artifact_id,),
    ).fetchone()
    if metadata is None or str(metadata[0]) != content_id:
        raise RetirementError("공개 ID→delivery→content→artifact 계보가 맞지 않습니다")
    typed_metadata = artifact.load_artifact_metadata(conn, artifact_id)
    if (
        typed_metadata is None
        or typed_metadata.content_snapshot_id != content_id
        or typed_metadata.channel != "pdf"
    ):
        raise RetirementError("artifact ID·channel·content 불변 신원이 맞지 않습니다")
    if str(metadata[1]) == artifact.ArtifactOriginalState.STORED.value:
        pointer = artifact.BlobPointer(
            key=str(metadata[2]),
            sha256=str(metadata[3]),
            byte_length=int(metadata[4]),
        )
        expected = backend.expected_pointer(
            sha256=pointer.sha256,
            byte_length=pointer.byte_length,
        )
        if pointer != expected:
            raise RetirementError("artifact metadata의 blob 경로·hash·길이가 맞지 않습니다")
        roots = conn.execute(
            f"""
            SELECT DISTINCT intents.storage_identity, intents.blob_key,
                            intents.bytes_sha256, intents.byte_length
            FROM {artifact.TABLE_BLOB_INTENTS} AS intents
            JOIN {artifact.TABLE_BLOB_INTENT_EVENTS} AS events
              ON events.intent_id = intents.intent_id
             AND events.event_type = 'bound'
             AND events.artifact_id = ?
            """,
            (artifact_id,),
        ).fetchall()
        if not roots:
            raise RetirementError(
                "artifact를 처음 쓴 저장소 root를 증명할 bound intent가 없습니다"
            )
        identities: set[str] = set()
        for row in roots:
            if (str(row[1]), str(row[2]), int(row[3])) != (
                pointer.key,
                pointer.sha256,
                pointer.byte_length,
            ):
                raise RetirementError("artifact bound intent와 blob metadata가 다릅니다")
            identities.add(str(row[0]))
        if len(identities) != 1 or "" in identities:
            raise RetirementError("artifact 원본 저장소 root 신원이 하나로 확정되지 않습니다")
        return delivery_id, content_id, artifact_id, identities.pop(), pointer
    if str(metadata[1]) != artifact.ArtifactOriginalState.LEGACY_ORIGINAL_UNKNOWN.value:
        raise RetirementError("artifact 최초 원본 상태가 손상됐습니다")
    if str(metadata[2]) or str(metadata[3]) or int(metadata[4]) != 0:
        raise RetirementError("원본 미상 artifact에 blob 신원이 섞였습니다")
    return delivery_id, content_id, artifact_id, "", None


def prepare_retirement(
    conn: sqlite3.Connection,
    backend: artifact.ArtifactBlobBackend,
    *,
    public_id: str,
    eligible_at: dt.datetime,
    created_at: dt.datetime,
) -> RetirementIntent:
    """DB·파일보다 먼저 별도 commit할 정리 신원을 정확한 계보로 고정한다."""

    ensure_retirement_schema(conn)
    clean_public_id = str(public_id).strip()
    eligible = require_aware(eligible_at, label="보고서 정리 가능")
    created = require_aware(created_at, label="보고서 정리 intent 생성")
    if not clean_public_id:
        raise ValueError("정리할 공개 ID가 필요합니다")
    values = _current_provenance(conn, public_id=clean_public_id, backend=backend)
    delivery_id, content_id, artifact_id, storage_identity, pointer = values
    eligible_text = utc_text(eligible, label="보고서 정리 가능")
    existing = conn.execute(
        f"""
        SELECT retirement_id, public_id, delivery_id, content_snapshot_id,
               artifact_id, storage_identity, blob_key, bytes_sha256,
               byte_length, eligible_at, created_at
        FROM {TABLE_RETIREMENT_INTENTS}
        WHERE public_id = ? AND eligible_at = ?
        """,
        (clean_public_id, eligible_text),
    ).fetchone()
    if existing is not None:
        intent = _intent_from_row(existing)
        if (
            intent.delivery_id,
            intent.content_snapshot_id,
            intent.artifact_id,
            intent.storage_identity,
            intent.blob_pointer,
        ) != (delivery_id, content_id, artifact_id, storage_identity, pointer):
            raise RetirementError("같은 휴지통 주기의 정리 계보가 달라졌습니다")
        return intent
    intent = RetirementIntent(
        retirement_id="retirement_" + uuid.uuid4().hex,
        public_id=clean_public_id,
        delivery_id=delivery_id,
        content_snapshot_id=content_id,
        artifact_id=artifact_id,
        storage_identity=storage_identity,
        blob_pointer=pointer,
        eligible_at=eligible,
        created_at=created,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_RETIREMENT_INTENTS} (
            retirement_id, public_id, delivery_id, content_snapshot_id,
            artifact_id, storage_identity, blob_key, bytes_sha256,
            byte_length, eligible_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent.retirement_id,
            intent.public_id,
            intent.delivery_id,
            intent.content_snapshot_id,
            intent.artifact_id,
            intent.storage_identity,
            pointer.key if pointer else "",
            pointer.sha256 if pointer else "",
            pointer.byte_length if pointer else 0,
            eligible_text,
            utc_text(created, label="정리 intent 생성"),
        ),
    )
    if cursor.rowcount != 1:
        concurrent = conn.execute(
            f"""
            SELECT retirement_id, public_id, delivery_id, content_snapshot_id,
                   artifact_id, storage_identity, blob_key, bytes_sha256,
                   byte_length, eligible_at, created_at
            FROM {TABLE_RETIREMENT_INTENTS}
            WHERE public_id = ? AND eligible_at = ?
            """,
            (clean_public_id, eligible_text),
        ).fetchone()
        if concurrent is None:
            raise RetirementError("경쟁 중인 정리 intent를 다시 읽지 못했습니다")
        winner = _intent_from_row(concurrent)
        if (
            winner.delivery_id,
            winner.content_snapshot_id,
            winner.artifact_id,
            winner.storage_identity,
            winner.blob_pointer,
        ) != (delivery_id, content_id, artifact_id, storage_identity, pointer):
            raise RetirementError("경쟁 중 저장된 정리 intent의 계보가 다릅니다")
        return winner
    _append_event(
        conn,
        intent=intent,
        event_type=EVENT_PREPARED,
        recorded_at=created,
    )
    return intent


def list_nonterminal_intents(conn: sqlite3.Connection) -> tuple[RetirementIntent, ...]:
    ensure_retirement_schema(conn)
    rows = conn.execute(
        f"""
        SELECT retirement_id, public_id, delivery_id, content_snapshot_id,
               artifact_id, storage_identity, blob_key, bytes_sha256,
               byte_length, eligible_at, created_at
        FROM {TABLE_RETIREMENT_INTENTS} ORDER BY created_at, retirement_id
        """
    ).fetchall()
    return tuple(
        intent
        for row in rows
        if (intent := _intent_from_row(row))
        and latest_event(conn, intent.retirement_id) not in _TERMINAL_EVENTS
    )


def cancel_retirement(
    conn: sqlite3.Connection,
    *,
    intent: RetirementIntent,
    cancelled_at: dt.datetime,
) -> None:
    persisted = load_retirement_intent(conn, intent.retirement_id)
    if persisted != intent:
        raise RetirementError("취소할 정리 intent 신원이 다릅니다")
    state = latest_event(conn, intent.retirement_id)
    if state == EVENT_CANCELLED:
        return
    if state not in _PRE_DB_EVENTS:
        raise RetirementError("DB 정리가 시작된 intent는 취소할 수 없습니다")
    _append_event(
        conn,
        intent=intent,
        event_type=EVENT_CANCELLED,
        recorded_at=cancelled_at,
    )


def _same_pointer_rows(
    conn: sqlite3.Connection, pointer: artifact.BlobPointer
) -> tuple[tuple[str, str, int], ...]:
    rows = conn.execute(
        f"""
        SELECT artifact_id, bytes_sha256, byte_length
        FROM {artifact.TABLE_ARTIFACTS} WHERE blob_key = ?
        ORDER BY artifact_id
        """,
        (pointer.key,),
    ).fetchall()
    return tuple((str(row[0]), str(row[1]), int(row[2])) for row in rows)


def _active_write_intent_for_pointer(
    conn: sqlite3.Connection,
    *,
    storage_identity: str,
    pointer: artifact.BlobPointer,
) -> tuple[bool, bool]:
    """(정확한 활성 intent, 같은 key지만 불일치하는 손상 intent)."""

    rows = conn.execute(
        f"""
        SELECT intents.bytes_sha256, intents.byte_length,
               (
                   SELECT events.event_type
                   FROM {artifact.TABLE_BLOB_INTENT_EVENTS} AS events
                   WHERE events.intent_id = intents.intent_id
                   ORDER BY events.event_id DESC LIMIT 1
               ) AS latest_event
        FROM {artifact.TABLE_BLOB_INTENTS} AS intents
        WHERE intents.storage_identity = ? AND intents.blob_key = ?
        """,
        (storage_identity, pointer.key),
    ).fetchall()
    exact = mismatch = False
    terminal = {"bound", "reclaimed_deleted", "reclaimed_absent"}
    for row in rows:
        latest = "" if row[2] is None else str(row[2])
        if latest in terminal:
            continue
        if (str(row[0]), int(row[1])) == (pointer.sha256, pointer.byte_length):
            exact = True
        else:
            mismatch = True
    return exact, mismatch


def _live_singleflight_reference(
    conn: sqlite3.Connection,
    *,
    content_id: str,
    artifact_id: str,
    now: dt.datetime,
) -> bool:
    if not _table_exists(conn, singleflight.TABLE_SINGLEFLIGHT_LEASES):
        return False
    row = conn.execute(
        f"""
        SELECT 1 FROM {singleflight.TABLE_SINGLEFLIGHT_LEASES}
        WHERE state = 'completed' AND expires_at > ?
          AND (completed_content_id = ? OR completed_artifact_id = ?)
        LIMIT 1
        """,
        (
            utc_text(now, label="정리 single-flight 판정"),
            content_id,
            artifact_id,
        ),
    ).fetchone()
    return row is not None


def _artifact_must_remain(
    conn: sqlite3.Connection,
    *,
    intent: RetirementIntent,
    now: dt.datetime,
) -> bool:
    metadata = conn.execute(
        f"SELECT legal_hold FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?",
        (intent.artifact_id,),
    ).fetchone()
    if metadata is None:
        raise RetirementError("정리 대상 artifact metadata가 사라졌습니다")
    if bool(metadata[0]):
        return True
    if conn.execute(
        f"""
        SELECT 1 FROM {lifecycle_store.TABLE_CACHE_ENTRIES}
        WHERE content_snapshot_id = ? OR artifact_id = ? LIMIT 1
        """,
        (intent.content_snapshot_id, intent.artifact_id),
    ).fetchone() is not None:
        return True
    if conn.execute(
        f"""
        SELECT 1 FROM {artifact.TABLE_DELIVERY_ARTIFACTS}
        WHERE artifact_id = ? LIMIT 1
        """,
        (intent.artifact_id,),
    ).fetchone() is not None:
        return True
    return _live_singleflight_reference(
        conn,
        content_id=intent.content_snapshot_id,
        artifact_id=intent.artifact_id,
        now=now,
    )


def _delete_unreferenced_content(
    conn: sqlite3.Connection,
    *,
    content_id: str,
) -> bool:
    if not content_id:
        return False
    content = conn.execute(
        f"""
        SELECT source_snapshot_id, cache_namespace_id
        FROM {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} WHERE content_id = ?
        """,
        (content_id,),
    ).fetchone()
    if content is None:
        return False
    referenced = any(
        conn.execute(query, (content_id,)).fetchone() is not None
        for query in (
            f"SELECT 1 FROM {lifecycle_store.TABLE_DELIVERIES} WHERE content_snapshot_id = ? LIMIT 1",
            f"SELECT 1 FROM {artifact.TABLE_ARTIFACTS} WHERE content_snapshot_id = ? LIMIT 1",
            f"SELECT 1 FROM {lifecycle_store.TABLE_CACHE_ENTRIES} WHERE content_snapshot_id = ? LIMIT 1",
        )
    )
    if referenced:
        return False
    source_id, namespace_id = str(content[0]), str(content[1])
    conn.execute(
        f"DELETE FROM {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} WHERE content_id = ?",
        (content_id,),
    )
    if conn.execute(
        f"SELECT 1 FROM {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} WHERE source_snapshot_id = ? LIMIT 1",
        (source_id,),
    ).fetchone() is None:
        conn.execute(
            f"DELETE FROM {lifecycle_store.TABLE_SOURCE_SNAPSHOTS} WHERE snapshot_id = ?",
            (source_id,),
        )
    namespace_referenced = conn.execute(
        f"SELECT 1 FROM {lifecycle_store.TABLE_CONTENT_SNAPSHOTS} WHERE cache_namespace_id = ? LIMIT 1",
        (namespace_id,),
    ).fetchone()
    cache_referenced = conn.execute(
        f"SELECT 1 FROM {lifecycle_store.TABLE_CACHE_ENTRIES} WHERE cache_namespace_id = ? LIMIT 1",
        (namespace_id,),
    ).fetchone()
    if namespace_referenced is None and cache_referenced is None:
        conn.execute(
            f"DELETE FROM {lifecycle_store.TABLE_CACHE_NAMESPACES} WHERE namespace_id = ?",
            (namespace_id,),
        )
    return True


def _provenance_still_exact(
    conn: sqlite3.Connection, intent: RetirementIntent
) -> bool:
    if not intent.delivery_id:
        return conn.execute(
            f"SELECT 1 FROM {lifecycle_store.TABLE_DELIVERIES} WHERE public_id = ?",
            (intent.public_id,),
        ).fetchone() is None
    delivery = conn.execute(
        f"""
        SELECT delivery_id, content_snapshot_id
        FROM {lifecycle_store.TABLE_DELIVERIES} WHERE public_id = ?
        """,
        (intent.public_id,),
    ).fetchone()
    if delivery is None or (str(delivery[0]), str(delivery[1])) != (
        intent.delivery_id,
        intent.content_snapshot_id,
    ):
        return False
    if lifecycle_store.load_content_snapshot(conn, intent.content_snapshot_id) is None:
        return False
    binding = conn.execute(
        f"""
        SELECT artifact_id FROM {artifact.TABLE_DELIVERY_ARTIFACTS}
        WHERE delivery_id = ? AND channel = 'pdf'
        """,
        (intent.delivery_id,),
    ).fetchone()
    if binding is None or str(binding[0]) != intent.artifact_id:
        return False
    metadata = conn.execute(
        f"""
        SELECT content_snapshot_id, blob_key, bytes_sha256, byte_length
        FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?
        """,
        (intent.artifact_id,),
    ).fetchone()
    if metadata is None or str(metadata[0]) != intent.content_snapshot_id:
        return False
    typed_metadata = artifact.load_artifact_metadata(conn, intent.artifact_id)
    if (
        typed_metadata is None
        or typed_metadata.content_snapshot_id != intent.content_snapshot_id
        or typed_metadata.channel != "pdf"
    ):
        return False
    pointer = intent.blob_pointer
    actual_pointer = (str(metadata[1]), str(metadata[2]), int(metadata[3]))
    expected_pointer = (
        ("", "", 0)
        if pointer is None
        else (pointer.key, pointer.sha256, pointer.byte_length)
    )
    return actual_pointer == expected_pointer


def retire_database_records(
    conn: sqlite3.Connection,
    backend: artifact.ArtifactBlobBackend,
    *,
    intent: RetirementIntent,
    retired_at: dt.datetime,
) -> DatabaseRetirementResult:
    """대상 delivery와 마지막 참조인 metadata·content를 한 transaction에서 지운다."""

    ensure_retirement_schema(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    current = require_aware(retired_at, label="보고서 DB 정리")
    persisted = load_retirement_intent(conn, intent.retirement_id)
    if persisted != intent:
        raise RetirementError("DB 정리 intent 신원이 달라졌습니다")
    state = latest_event(conn, intent.retirement_id)
    if state in _TERMINAL_EVENTS or state in _POST_DB_EVENTS:
        return DatabaseRetirementResult(event_type=state or "")
    if state == EVENT_COMPLETED_PRESERVED:
        # 최초 DB 정리 때 cache·legal hold·공유 artifact·활성 쓰기 intent가
        # 보호했을 수 있다. 보호가 나중에 사라지면 같은 immutable intent가
        # 남은 metadata/content/blob을 다시 마지막 참조 판정으로 회수한다.
        blob_may_be_reclaimed = intent.blob_pointer is not None
        metadata = conn.execute(
            f"""
            SELECT content_snapshot_id, blob_key, bytes_sha256, byte_length
            FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?
            """,
            (intent.artifact_id,),
        ).fetchone()
        artifact_deleted = False
        if metadata is not None:
            pointer = intent.blob_pointer
            expected = (
                str(metadata[0]),
                "" if pointer is None else pointer.key,
                "" if pointer is None else pointer.sha256,
                0 if pointer is None else pointer.byte_length,
            )
            if tuple(metadata) != expected:
                raise RetirementError("보존 뒤 artifact 계보가 달라졌습니다")
            if _artifact_must_remain(conn, intent=intent, now=current):
                return DatabaseRetirementResult(event_type=EVENT_COMPLETED_PRESERVED)
            conn.execute(
                f"DELETE FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?",
                (intent.artifact_id,),
            )
            artifact_deleted = True
        content_deleted = _delete_unreferenced_content(
            conn,
            content_id=intent.content_snapshot_id,
        )
        if blob_may_be_reclaimed and intent.blob_pointer is not None:
            references = _same_pointer_rows(conn, intent.blob_pointer)
            exact_active, mismatch_active = _active_write_intent_for_pointer(
                conn,
                storage_identity=intent.storage_identity,
                pointer=intent.blob_pointer,
            )
            if references or exact_active or mismatch_active:
                return DatabaseRetirementResult(
                    event_type=EVENT_COMPLETED_PRESERVED,
                    artifact_deleted=artifact_deleted,
                    content_deleted=content_deleted,
                )
            _append_event(
                conn,
                intent=intent,
                event_type=EVENT_DB_RETIRED,
                recorded_at=current,
            )
            return DatabaseRetirementResult(
                event_type=EVENT_DB_RETIRED,
                artifact_deleted=artifact_deleted,
                content_deleted=content_deleted,
            )
        event = (
            EVENT_COMPLETED_NO_BLOB
            if intent.blob_pointer is None and (metadata is None or artifact_deleted)
            else EVENT_COMPLETED_PRESERVED
        )
        if event != state:
            _append_event(
                conn,
                intent=intent,
                event_type=event,
                recorded_at=current,
            )
        return DatabaseRetirementResult(
            event_type=event,
            artifact_deleted=artifact_deleted,
            content_deleted=content_deleted,
        )
    if state not in _PRE_DB_EVENTS:
        raise RetirementError("DB 정리 intent 사건 순서가 손상됐습니다")
    if intent.blob_pointer is not None and (
        intent.storage_identity != backend.storage_identity
    ):
        if state != EVENT_PREPARE_BLOCKED:
            _append_event(
                conn,
                intent=intent,
                event_type=EVENT_PREPARE_BLOCKED,
                recorded_at=current,
            )
        return DatabaseRetirementResult(event_type=EVENT_PREPARE_BLOCKED)
    if not _provenance_still_exact(conn, intent):
        raise RetirementError("삭제 직전 공개 ID→delivery→content→artifact 계보가 달라졌습니다")

    conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_RETIRED_PUBLIC_IDS}
            (public_id, retirement_id, retired_at)
        VALUES (?, ?, ?)
        """,
        (intent.public_id, intent.retirement_id, utc_text(current, label="공개 ID 정리")),
    )
    tombstone = conn.execute(
        f"SELECT retirement_id FROM {TABLE_RETIRED_PUBLIC_IDS} WHERE public_id = ?",
        (intent.public_id,),
    ).fetchone()
    if tombstone is None or str(tombstone[0]) != intent.retirement_id:
        raise RetirementError("공개 ID 영구 정리 표식이 다른 intent를 가리킵니다")

    delivery_deleted = artifact_deleted = content_deleted = False
    if intent.delivery_id:
        conn.execute(
            f"DELETE FROM {artifact.TABLE_DELIVERY_ARTIFACTS} WHERE delivery_id = ?",
            (intent.delivery_id,),
        )
        cursor = conn.execute(
            f"DELETE FROM {lifecycle_store.TABLE_DELIVERIES} WHERE delivery_id = ? AND public_id = ?",
            (intent.delivery_id, intent.public_id),
        )
        if cursor.rowcount != 1:
            raise RetirementError("정리 대상 delivery를 정확히 한 건 지우지 못했습니다")
        delivery_deleted = True
    conn.execute(
        f"DELETE FROM {lifecycle_store.TABLE_DELIVERY_INTENTS} WHERE public_id = ?",
        (intent.public_id,),
    )

    blob_may_be_reclaimed = intent.blob_pointer is not None
    artifact_preserved = False
    if intent.artifact_id:
        if _artifact_must_remain(conn, intent=intent, now=current):
            blob_may_be_reclaimed = False
            artifact_preserved = True
        else:
            cursor = conn.execute(
                f"DELETE FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?",
                (intent.artifact_id,),
            )
            if cursor.rowcount != 1:
                raise RetirementError("정리 대상 artifact metadata를 지우지 못했습니다")
            artifact_deleted = True
    content_deleted = _delete_unreferenced_content(
        conn,
        content_id=intent.content_snapshot_id,
    )

    if blob_may_be_reclaimed and intent.blob_pointer is not None:
        same_pointer = _same_pointer_rows(conn, intent.blob_pointer)
        exact_active, mismatch_active = _active_write_intent_for_pointer(
            conn,
            storage_identity=intent.storage_identity,
            pointer=intent.blob_pointer,
        )
        if same_pointer or exact_active or mismatch_active:
            blob_may_be_reclaimed = False

    if blob_may_be_reclaimed:
        event = EVENT_DB_RETIRED
    elif intent.blob_pointer is None and not artifact_preserved:
        event = EVENT_COMPLETED_NO_BLOB
    else:
        event = EVENT_COMPLETED_PRESERVED
    _append_event(conn, intent=intent, event_type=event, recorded_at=current)
    return DatabaseRetirementResult(
        event_type=event,
        delivery_deleted=delivery_deleted,
        artifact_deleted=artifact_deleted,
        content_deleted=content_deleted,
    )


def reconcile_retired_blob(
    conn: sqlite3.Connection,
    backend: artifact.ArtifactBlobBackend,
    *,
    intent: RetirementIntent,
    reconciled_at: dt.datetime,
) -> BlobRetirementResult:
    """DB 정리 뒤 참조를 다시 읽고 root lock 안에서 정확한 파일만 회수한다."""

    ensure_retirement_schema(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    current = require_aware(reconciled_at, label="정리 PDF 복구")
    persisted = load_retirement_intent(conn, intent.retirement_id)
    if persisted != intent:
        raise RetirementError("PDF 복구 intent 신원이 달라졌습니다")
    state = latest_event(conn, intent.retirement_id)
    if state in _TERMINAL_EVENTS:
        return BlobRetirementResult(event_type=state or "")
    if state not in _POST_DB_EVENTS or intent.blob_pointer is None:
        raise RetirementError("DB 정리가 끝나지 않은 intent로 PDF를 지울 수 없습니다")
    if intent.storage_identity != backend.storage_identity:
        if state != EVENT_BLOB_BLOCKED:
            _append_event(
                conn,
                intent=intent,
                event_type=EVENT_BLOB_BLOCKED,
                recorded_at=current,
            )
        return BlobRetirementResult(event_type=EVENT_BLOB_BLOCKED)

    # BEGIN IMMEDIATE를 가진 caller 안에서 삭제 직전 DB를 다시 읽는다.
    references = _same_pointer_rows(conn, intent.blob_pointer)
    exact_active, mismatch_active = _active_write_intent_for_pointer(
        conn,
        storage_identity=intent.storage_identity,
        pointer=intent.blob_pointer,
    )
    if references or exact_active:
        _append_event(
            conn,
            intent=intent,
            event_type=EVENT_COMPLETED_PRESERVED,
            recorded_at=current,
        )
        return BlobRetirementResult(event_type=EVENT_COMPLETED_PRESERVED)
    if mismatch_active:
        if state != EVENT_BLOB_BLOCKED:
            _append_event(
                conn,
                intent=intent,
                event_type=EVENT_BLOB_BLOCKED,
                recorded_at=current,
            )
        return BlobRetirementResult(event_type=EVENT_BLOB_BLOCKED)

    try:
        outcome = backend.delete_retired_if_exact(intent.blob_pointer)
    except artifact.ArtifactError:
        if state != EVENT_BLOB_BLOCKED:
            _append_event(
                conn,
                intent=intent,
                event_type=EVENT_BLOB_BLOCKED,
                recorded_at=current,
            )
        return BlobRetirementResult(event_type=EVENT_BLOB_BLOCKED)
    if outcome is artifact.OrphanDeleteResult.MISMATCH:
        if state != EVENT_BLOB_BLOCKED:
            _append_event(
                conn,
                intent=intent,
                event_type=EVENT_BLOB_BLOCKED,
                recorded_at=current,
            )
        return BlobRetirementResult(event_type=EVENT_BLOB_BLOCKED)
    event = (
        EVENT_COMPLETED_DELETED
        if outcome is artifact.OrphanDeleteResult.DELETED
        else EVENT_COMPLETED_ABSENT
    )
    _append_event(conn, intent=intent, event_type=event, recorded_at=current)
    return BlobRetirementResult(
        event_type=event,
        reclaimed_bytes=(
            intent.blob_pointer.byte_length
            if outcome is artifact.OrphanDeleteResult.DELETED
            else 0
        ),
    )


def retired_public_id(conn: sqlite3.Connection, public_id: str) -> bool:
    ensure_schema(conn)
    return conn.execute(
        f"SELECT 1 FROM {TABLE_RETIRED_PUBLIC_IDS} WHERE public_id = ?",
        (str(public_id).strip(),),
    ).fetchone() is not None
