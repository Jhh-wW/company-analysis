"""내용·출처·전달·캐시 참조를 중복 없이 저장하는 SQLite 저장소."""

from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final

from src.features.report_delivery.cache_identity import CacheLookupKey, CacheNamespace
from src.features.report_delivery.canonical import (
    datetime_from_utc_text,
    require_aware,
    sha256_hex,
    utc_text,
)
from src.features.report_delivery.models import ContentSnapshot, Delivery, DeliveryPolicy
from src.features.report_delivery.policy import CacheMissReason
from src.features.report_delivery.source_identity import SourceSnapshot
from src.shared.engine_build_identity import epoch_digest_is_valid


TABLE_SOURCE_SNAPSHOTS: Final[str] = "report_delivery_source_snapshots"
TABLE_CACHE_NAMESPACES: Final[str] = "report_delivery_cache_namespaces"
TABLE_CONTENT_SNAPSHOTS: Final[str] = "report_delivery_content_snapshots"
TABLE_DELIVERIES: Final[str] = "report_delivery_deliveries"
TABLE_CACHE_ENTRIES: Final[str] = "report_delivery_cache_entries"
TABLE_CACHE_INVALIDATIONS: Final[str] = "report_delivery_cache_invalidations"
TABLE_DELIVERY_INTENTS: Final[str] = "report_delivery_intents"
_TABLE_ARTIFACTS: Final[str] = "report_delivery_artifacts"
_CACHE_PREFLIGHT_INDEX: Final[str] = "uq_report_delivery_cache_bucket_preflight"
_OBSOLETE_CACHE_PREFLIGHT_INDEX: Final[str] = "uq_report_delivery_cache_preflight"

DELIVERY_INTENT_REQUIRED: Final[str] = "required"
DELIVERY_INTENT_COMPLETE: Final[str] = "complete"
DELIVERY_INTENT_FAILED: Final[str] = "failed"
_DELIVERY_INTENT_STATES: Final[frozenset[str]] = frozenset(
    {
        DELIVERY_INTENT_REQUIRED,
        DELIVERY_INTENT_COMPLETE,
        DELIVERY_INTENT_FAILED,
    }
)
_FAILURE_CODE_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9_]{1,64}")
_CACHE_INVALIDATION_REASON_RE: Final[re.Pattern[str]] = re.compile(
    r"[a-z0-9_]{1,64}"
)

_CACHE_ENTRIES_SCHEMA: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_CACHE_ENTRIES} (
    billing_bucket_id         TEXT NOT NULL,
    corp_id                   TEXT NOT NULL,
    cache_namespace_id        TEXT NOT NULL,
    preflight_identity_digest TEXT NOT NULL,
    engine_epoch_digest       TEXT NOT NULL,
    source_identity_digest    TEXT NOT NULL,
    content_snapshot_id       TEXT NOT NULL
                              REFERENCES {TABLE_CONTENT_SNAPSHOTS}(content_id),
    artifact_id               TEXT NOT NULL,
    cached_at                 TEXT NOT NULL,
    PRIMARY KEY (
        billing_bucket_id, corp_id, cache_namespace_id,
        preflight_identity_digest, engine_epoch_digest
    )
)
"""


class LifecycleStoreError(RuntimeError):
    """보고서 수명주기 저장소가 불변식을 지키지 못했다."""


class ImmutableRecordConflict(LifecycleStoreError):
    """같은 ID에 다른 불변 자료를 덮어쓰려 했다."""


class LifecycleStoreCorrupt(LifecycleStoreError):
    """저장된 hash·참조·payload가 서로 맞지 않는다."""


@dataclass(frozen=True)
class CachedRelease:
    """한 cache hit이 재사용해야 하는 불변 본문과 최초 승인 PDF."""

    content: ContentSnapshot
    artifact_id: str
    preflight_identity_digest: str

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ValueError("캐시 결과에는 최초 승인 PDF artifact가 필요합니다")
        digest = self.preflight_identity_digest
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError("캐시 결과의 사전 출처 신원이 손상됐습니다")


@dataclass(frozen=True)
class CacheLookup:
    """캐시 조회 한 번의 «결과 또는 이유».

    적중이면 `hit`만 채워지고, 미적중이면 `miss_reason`이 왜인지 말한다.
    나이를 지나 못 쓴 경우에는 그 열쇠가 가리키던 두 원본 ID도 함께 준다 —
    부른 쪽이 같은 열쇠를 지우려면 정확한 대상을 알아야 하기 때문이다.
    """

    hit: CachedRelease | None = None
    miss_reason: CacheMissReason | None = None
    expired_content_snapshot_id: str = ""
    expired_artifact_id: str = ""


@dataclass(frozen=True)
class DeliveryIntent:
    """새 보고서는 불변 delivery 없이는 공개하지 않는다는 영속 표식."""

    public_id: str
    state: str
    required_at: dt.datetime
    updated_at: dt.datetime
    failure_code: str = ""

    def __post_init__(self) -> None:
        if not self.public_id.strip() or self.state not in _DELIVERY_INTENT_STATES:
            raise ValueError("delivery 의무 표식의 공개 ID·상태가 올바르지 않습니다")
        required = require_aware(self.required_at, label="delivery 의무 시작")
        updated = require_aware(self.updated_at, label="delivery 의무 변경")
        if updated < required:
            raise ValueError("delivery 의무 변경 시각이 시작보다 빠릅니다")
        if self.state == DELIVERY_INTENT_FAILED:
            if _FAILURE_CODE_RE.fullmatch(self.failure_code) is None:
                raise ValueError("delivery 실패 코드는 닫힌 기계 코드여야 합니다")
        elif self.failure_code:
            raise ValueError("실패가 아닌 delivery 의무에 실패 코드를 넣을 수 없습니다")


_SCHEMA: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SOURCE_SNAPSHOTS} (
        snapshot_id                 TEXT PRIMARY KEY,
        identity_digest             TEXT NOT NULL,
        captured_at                 TEXT NOT NULL,
        source_as_of                TEXT NOT NULL,
        dart_receipt_nos_json       TEXT NOT NULL,
        financial_payload_sha256    TEXT NOT NULL,
        official_document_ids_json  TEXT NOT NULL,
        adapter_versions_json       TEXT NOT NULL,
        preflight_identity_digest   TEXT NOT NULL DEFAULT '',
        cache_usable                INTEGER NOT NULL CHECK(cache_usable IN (0, 1))
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_report_delivery_source_identity
    ON {TABLE_SOURCE_SNAPSHOTS}(identity_digest)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_CACHE_NAMESPACES} (
        namespace_id             TEXT PRIMARY KEY,
        product                  TEXT NOT NULL,
        schema_version           TEXT NOT NULL,
        deployment_revision      TEXT NOT NULL,
        image_digest             TEXT NOT NULL,
        model_identity_sha256    TEXT NOT NULL,
        settings_sha256          TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_CONTENT_SNAPSHOTS} (
        content_id               TEXT PRIMARY KEY,
        payload                  BLOB NOT NULL,
        payload_sha256           TEXT NOT NULL,
        source_snapshot_id       TEXT NOT NULL
                                 REFERENCES {TABLE_SOURCE_SNAPSHOTS}(snapshot_id),
        source_identity_digest   TEXT NOT NULL,
        cache_namespace_id       TEXT NOT NULL
                                 REFERENCES {TABLE_CACHE_NAMESPACES}(namespace_id),
        content_generated_at     TEXT NOT NULL,
        actual_models_json       TEXT NOT NULL,
        engine_epoch_digest      TEXT NOT NULL DEFAULT '',
        -- ReleaseAuthority.save_release_authority가 발급 직후 정확히 한 번
        -- 0->1로만 뒤집는다(authority.py 소유). 이후 이 표 자체의 트리거가
        -- 모든 UPDATE를 막는다 -- 다른 표를 참조하지 않는 자기완결 결속이라
        -- authority.py·artifact.py가 아직 부팅되지 않은 단독 시험·부분
        -- 마이그레이션에서도 안전하다.
        release_locked            INTEGER NOT NULL DEFAULT 0
                                 CHECK(release_locked IN (0, 1))
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_report_delivery_content_source
    ON {TABLE_CONTENT_SNAPSHOTS}(source_identity_digest, cache_namespace_id)
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_DELIVERIES} (
        delivery_id              TEXT PRIMARY KEY,
        public_id                TEXT NOT NULL UNIQUE,
        content_snapshot_id      TEXT NOT NULL
                                 REFERENCES {TABLE_CONTENT_SNAPSHOTS}(content_id),
        billing_bucket_id        TEXT NOT NULL,
        delivered_at             TEXT NOT NULL,
        expires_at               TEXT NOT NULL,
        cache_origin_content_id  TEXT NOT NULL,
        release_locked            INTEGER NOT NULL DEFAULT 0
                                 CHECK(release_locked IN (0, 1))
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_report_delivery_by_content
    ON {TABLE_DELIVERIES}(content_snapshot_id, delivered_at)
    """,
    _CACHE_ENTRIES_SCHEMA,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_DELIVERY_INTENTS} (
        public_id     TEXT PRIMARY KEY,
        state         TEXT NOT NULL CHECK(state IN (
                          '{DELIVERY_INTENT_REQUIRED}',
                          '{DELIVERY_INTENT_COMPLETE}',
                          '{DELIVERY_INTENT_FAILED}'
                      )),
        required_at   TEXT NOT NULL,
        updated_at    TEXT NOT NULL,
        failure_code  TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_CACHE_INVALIDATIONS} (
        invalidation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
        billing_bucket_id         TEXT NOT NULL,
        corp_id                   TEXT NOT NULL,
        cache_namespace_id        TEXT NOT NULL,
        preflight_identity_digest TEXT NOT NULL,
        content_snapshot_id       TEXT NOT NULL,
        artifact_id               TEXT NOT NULL,
        reason_code               TEXT NOT NULL,
        invalidated_at            TEXT NOT NULL
    )
    """,
    # 두 표 모두 완전히 content-addressed다(각 PK는 나머지 모든 컬럼의
    # canonical hash). 정상 코드는 이 표들을 UPDATE하지 않으며(같은 표의
    # 손상 재현 시험만 raw SQL로 직접 UPDATE한다), ReleaseAuthority가
    # 발급된 뒤에는 그 raw SQL 우회조차 막는다. authority가 아직 없는 행은
    # 기존 손상 재현 시험(test_delivery_store.py)이 그대로 UPDATE할 수
    # 있게 둔다 — 이 트리거는 발급 "뒤"에만 적용된다.
    #
    # ★ 다른 표를 참조하지 않는다(자기 컬럼 release_locked만 본다). 처음에는
    #   report_delivery_release_authorities를 EXISTS로 조회하는 트리거를
    #   시도했는데, SQLite는 ALTER TABLE RENAME 때 스키마 전체 트리거
    #   본문이 가리키는 표 이름을 다시 검증한다 — authority.py가 아직
    #   부팅되지 않은 단독 호출(예: 이 표만 쓰는 시험, 부분 마이그레이션)에서
    #   이 표들과 무관한 RENAME(_rebuild_bucket_scoped_cache_table)까지
    #   "no such table"로 죽는 걸 실측으로 확인했다. 자기완결 컬럼이면 이
    #   문제 자체가 생기지 않는다.
    f"""
    CREATE TRIGGER IF NOT EXISTS report_delivery_content_snapshots_no_mutation_after_release
    BEFORE UPDATE ON {TABLE_CONTENT_SNAPSHOTS}
    WHEN OLD.release_locked = 1
    BEGIN
        SELECT RAISE(ABORT, 'content snapshot is bound to an issued release authority');
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS report_delivery_deliveries_no_mutation_after_release
    BEFORE UPDATE ON {TABLE_DELIVERIES}
    WHEN OLD.release_locked = 1
    BEGIN
        SELECT RAISE(ABORT, 'delivery is bound to an issued release authority');
    END
    """,
)


def _cache_primary_key(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA table_info({TABLE_CACHE_ENTRIES})").fetchall()
    return tuple(
        str(row[1])
        for row in sorted(rows, key=lambda item: int(item[5]))
        if int(row[5]) > 0
    )


def _rebuild_bucket_scoped_cache_table(conn: sqlite3.Connection) -> None:
    """SQLite에서 바꿀 수 없는 옛 PRIMARY KEY를 실제 5열 key로 교체한다."""

    old_columns = tuple(
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({TABLE_CACHE_ENTRIES})").fetchall()
    )
    old_rows = tuple(conn.execute(f"SELECT * FROM {TABLE_CACHE_ENTRIES}").fetchall())
    legacy_table = f"{TABLE_CACHE_ENTRIES}_unscoped_legacy"
    conn.execute(f"DROP INDEX IF EXISTS {_OBSOLETE_CACHE_PREFLIGHT_INDEX}")
    conn.execute(f"DROP INDEX IF EXISTS {_CACHE_PREFLIGHT_INDEX}")
    conn.execute(f"ALTER TABLE {TABLE_CACHE_ENTRIES} RENAME TO {legacy_table}")
    conn.execute(_CACHE_ENTRIES_SCHEMA)
    stamp = utc_text(dt.datetime.now(dt.timezone.utc), label="캐시 격리")
    for raw_row in old_rows:
        values = {
            name: raw_row[position]
            for position, name in enumerate(old_columns)
        }
        bucket = str(values.get("billing_bucket_id", "") or "").strip()
        preflight = str(
            values.get("preflight_identity_digest", "") or ""
        ).strip()
        artifact_id = str(values.get("artifact_id", "") or "").strip()
        if bucket and preflight and artifact_id:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {TABLE_CACHE_ENTRIES} (
                    billing_bucket_id, corp_id, cache_namespace_id,
                    preflight_identity_digest, engine_epoch_digest,
                    source_identity_digest,
                    content_snapshot_id, artifact_id, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bucket,
                    str(values.get("corp_id", "")),
                    str(values.get("cache_namespace_id", "")),
                    preflight,
                    str(values.get("engine_epoch_digest", "")),
                    str(values.get("source_identity_digest", "")),
                    str(values.get("content_snapshot_id", "")),
                    artifact_id,
                    str(values.get("cached_at", "")),
                ),
            )
            continue
        # 통장/사전지문/PDF가 없던 행은 어느 새 통장에도 귀속시키지 않는다.
        conn.execute(
            f"""
            INSERT INTO {TABLE_CACHE_INVALIDATIONS} (
                billing_bucket_id, corp_id, cache_namespace_id,
                preflight_identity_digest, content_snapshot_id, artifact_id,
                reason_code, invalidated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket,
                str(values.get("corp_id", "")),
                str(values.get("cache_namespace_id", "")),
                preflight,
                str(values.get("content_snapshot_id", "")),
                artifact_id,
                "legacy_unscoped_cache_quarantined",
                stamp,
            ),
        )
    conn.execute(f"DROP TABLE {legacy_table}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """이 feature 소유 표만 멱등으로 만들고 옛 캐시는 fail-closed한다.

    예전 표는 post-generation 출처 지문과 Content만 가리켰다. 생성 전에는
    알 수 없는 값이어서 실제 제품 조회에 연결할 수 없고 PDF 원본도 없었다.
    bucket 없는 옛 행은 감사 원장에 격리하고 캐시 hit으로 인정하지 않는다.
    """

    for statement in _SCHEMA:
        conn.execute(statement)
    content_columns = {
        str(row[1])
        for row in conn.execute(
            f'PRAGMA table_info("{TABLE_CONTENT_SNAPSHOTS}")'
        ).fetchall()
    }
    if "engine_epoch_digest" not in content_columns:
        conn.execute(
            f"ALTER TABLE {TABLE_CONTENT_SNAPSHOTS} "
            "ADD COLUMN engine_epoch_digest TEXT NOT NULL DEFAULT ''"
        )
    if "release_locked" not in content_columns:
        conn.execute(
            f"ALTER TABLE {TABLE_CONTENT_SNAPSHOTS} "
            "ADD COLUMN release_locked INTEGER NOT NULL DEFAULT 0"
        )
    source_columns = {
        str(row[1])
        for row in conn.execute(
            f'PRAGMA table_info("{TABLE_SOURCE_SNAPSHOTS}")'
        ).fetchall()
    }
    if "preflight_identity_digest" not in source_columns:
        conn.execute(
            f"ALTER TABLE {TABLE_SOURCE_SNAPSHOTS} "
            "ADD COLUMN preflight_identity_digest TEXT NOT NULL DEFAULT ''"
        )
    delivery_columns = {
        str(row[1])
        for row in conn.execute(
            f'PRAGMA table_info("{TABLE_DELIVERIES}")'
        ).fetchall()
    }
    if "release_locked" not in delivery_columns:
        conn.execute(
            f"ALTER TABLE {TABLE_DELIVERIES} "
            "ADD COLUMN release_locked INTEGER NOT NULL DEFAULT 0"
        )
    expected_pk = (
        "billing_bucket_id",
        "corp_id",
        "cache_namespace_id",
        "preflight_identity_digest",
        "engine_epoch_digest",
    )
    if _cache_primary_key(conn) != expected_pk:
        _rebuild_bucket_scoped_cache_table(conn)
    conn.execute(f"DROP INDEX IF EXISTS {_OBSOLETE_CACHE_PREFLIGHT_INDEX}")
    conn.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_CACHE_PREFLIGHT_INDEX}
        ON {TABLE_CACHE_ENTRIES}(
            billing_bucket_id, corp_id, cache_namespace_id,
            preflight_identity_digest, engine_epoch_digest
        )
        WHERE billing_bucket_id <> '' AND preflight_identity_digest <> ''
        """
    )


def _json_tuple(value: tuple[object, ...]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_tuple(value: str, *, label: str) -> tuple[object, ...]:
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise LifecycleStoreCorrupt(f"{label} JSON이 손상됐습니다") from exc
    if not isinstance(loaded, list):
        raise LifecycleStoreCorrupt(f"{label} JSON은 목록이어야 합니다")
    return tuple(loaded)


def save_source_snapshot(conn: sqlite3.Connection, snapshot: SourceSnapshot) -> None:
    """출처 snapshot을 ID당 한 번만 저장한다."""

    ensure_schema(conn)
    payload = (
        snapshot.snapshot_id,
        snapshot.identity_digest,
        utc_text(snapshot.captured_at, label="출처 확인"),
        snapshot.source_as_of.isoformat(),
        _json_tuple(tuple(snapshot.dart_receipt_nos)),
        snapshot.financial_payload_sha256,
        _json_tuple(tuple(snapshot.official_document_ids)),
        _json_tuple(tuple(snapshot.adapter_versions)),
        snapshot.preflight_identity_digest,
        1 if snapshot.cache_usable else 0,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_SOURCE_SNAPSHOTS} (
            snapshot_id, identity_digest, captured_at, source_as_of,
            dart_receipt_nos_json, financial_payload_sha256,
            official_document_ids_json, adapter_versions_json,
            preflight_identity_digest, cache_usable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"""
        SELECT snapshot_id, identity_digest, captured_at, source_as_of,
               dart_receipt_nos_json, financial_payload_sha256,
               official_document_ids_json, adapter_versions_json,
               preflight_identity_digest, cache_usable
        FROM {TABLE_SOURCE_SNAPSHOTS} WHERE snapshot_id = ?
        """,
        (snapshot.snapshot_id,),
    ).fetchone()
    if existing is None or tuple(existing) != payload:
        raise ImmutableRecordConflict("같은 source snapshot ID를 다른 값으로 덮어쓸 수 없습니다")


def save_cache_namespace(conn: sqlite3.Connection, namespace: CacheNamespace) -> None:
    """배포·모델·설정 digest를 내용 원본과 별개의 provenance로 저장한다."""

    ensure_schema(conn)
    payload = (
        namespace.namespace_id,
        namespace.product,
        namespace.schema_version,
        namespace.deployment_revision,
        namespace.image_digest,
        namespace.model_identity_sha256,
        namespace.settings_sha256,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_CACHE_NAMESPACES} (
            namespace_id, product, schema_version, deployment_revision,
            image_digest, model_identity_sha256, settings_sha256
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"SELECT * FROM {TABLE_CACHE_NAMESPACES} WHERE namespace_id = ?",
        (namespace.namespace_id,),
    ).fetchone()
    if existing is None or tuple(existing) != payload:
        raise ImmutableRecordConflict("같은 cache namespace ID를 다른 값으로 덮어쓸 수 없습니다")


def load_cache_namespace(
    conn: sqlite3.Connection, namespace_id: str
) -> CacheNamespace | None:
    """내용 원본을 만든 배포·모델·설정 신원을 읽는다."""

    ensure_schema(conn)
    row = conn.execute(
        f"SELECT * FROM {TABLE_CACHE_NAMESPACES} WHERE namespace_id = ?",
        (str(namespace_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    try:
        return CacheNamespace(
            namespace_id=str(row[0]),
            product=str(row[1]),
            schema_version=str(row[2]),
            deployment_revision=str(row[3]),
            image_digest=str(row[4]),
            model_identity_sha256=str(row[5]),
            settings_sha256=str(row[6]),
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleStoreCorrupt("cache namespace metadata가 손상됐습니다") from exc


def load_source_snapshot(
    conn: sqlite3.Connection, snapshot_id: str
) -> SourceSnapshot | None:
    """출처 snapshot을 원문 없는 typed DTO로 읽는다."""

    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT snapshot_id, identity_digest, captured_at, source_as_of,
               dart_receipt_nos_json, financial_payload_sha256,
               official_document_ids_json, adapter_versions_json,
               preflight_identity_digest, cache_usable
        FROM {TABLE_SOURCE_SNAPSHOTS} WHERE snapshot_id = ?
        """,
        (str(snapshot_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    try:
        source_as_of = dt.date.fromisoformat(str(row[3]))
    except ValueError as exc:
        raise LifecycleStoreCorrupt("자료 기준일이 손상됐습니다") from exc
    receipts = tuple(str(item) for item in _load_tuple(row[4], label="DART 접수번호"))
    documents = tuple(str(item) for item in _load_tuple(row[6], label="공식 문서"))
    raw_versions = _load_tuple(row[7], label="수집 adapter")
    try:
        versions = tuple((str(item[0]), str(item[1])) for item in raw_versions)
    except (IndexError, TypeError) as exc:
        raise LifecycleStoreCorrupt("수집 adapter 버전이 손상됐습니다") from exc
    if row[9] not in (0, 1):
        raise LifecycleStoreCorrupt("캐시 사용 표시가 손상됐습니다")
    try:
        return SourceSnapshot(
            snapshot_id=str(row[0]),
            identity_digest=str(row[1]),
            captured_at=datetime_from_utc_text(row[2], label="출처 확인"),
            source_as_of=source_as_of,
            dart_receipt_nos=receipts,
            financial_payload_sha256=str(row[5]),
            official_document_ids=documents,
            adapter_versions=versions,
            preflight_identity_digest=str(row[8]),
            cache_usable=bool(row[9]),
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleStoreCorrupt("source snapshot metadata가 손상됐습니다") from exc


def save_content_snapshot(conn: sqlite3.Connection, content: ContentSnapshot) -> None:
    """본문 bytes를 한 번만 저장하고 여러 delivery가 참조하게 한다."""

    ensure_schema(conn)
    if not epoch_digest_is_valid(content.engine_epoch_digest):
        raise LifecycleStoreError(
            "새 내용 원본에는 정상 engine epoch 영수증이 필요합니다"
        )
    source = load_source_snapshot(conn, content.source_snapshot_id)
    if source is None:
        raise LifecycleStoreError("내용보다 source snapshot을 먼저 저장해야 합니다")
    if source.identity_digest != content.source_identity_digest:
        raise LifecycleStoreCorrupt("내용과 source snapshot의 신원이 서로 다릅니다")
    if load_cache_namespace(conn, content.cache_namespace_id) is None:
        raise LifecycleStoreError("내용보다 cache namespace를 먼저 저장해야 합니다")
    payload = (
        content.content_id,
        sqlite3.Binary(content.payload),
        content.payload_sha256,
        content.source_snapshot_id,
        content.source_identity_digest,
        content.cache_namespace_id,
        utc_text(content.content_generated_at, label="내용 생성"),
        _json_tuple(tuple(content.actual_models)),
        content.engine_epoch_digest,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_CONTENT_SNAPSHOTS} (
            content_id, payload, payload_sha256, source_snapshot_id,
            source_identity_digest, cache_namespace_id,
            content_generated_at, actual_models_json, engine_epoch_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"""
        SELECT content_id, payload, payload_sha256, source_snapshot_id,
               source_identity_digest, cache_namespace_id,
               content_generated_at, actual_models_json, engine_epoch_digest
        FROM {TABLE_CONTENT_SNAPSHOTS} WHERE content_id = ?
        """,
        (content.content_id,),
    ).fetchone()
    expected = (
        content.content_id,
        content.payload,
        content.payload_sha256,
        content.source_snapshot_id,
        content.source_identity_digest,
        content.cache_namespace_id,
        utc_text(content.content_generated_at, label="내용 생성"),
        _json_tuple(tuple(content.actual_models)),
        content.engine_epoch_digest,
    )
    if existing is None or tuple(existing) != expected:
        raise ImmutableRecordConflict("같은 content ID를 다른 본문으로 덮어쓸 수 없습니다")


def load_content_snapshot(
    conn: sqlite3.Connection, content_id: str
) -> ContentSnapshot | None:
    """본문 hash를 다시 대조한 뒤 내용 원본을 읽는다."""

    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT content_id, payload, payload_sha256, source_snapshot_id,
               source_identity_digest, cache_namespace_id,
               content_generated_at, actual_models_json, engine_epoch_digest
        FROM {TABLE_CONTENT_SNAPSHOTS} WHERE content_id = ?
        """,
        (str(content_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    payload = bytes(row[1])
    if sha256_hex(payload) != str(row[2]):
        raise LifecycleStoreCorrupt("저장된 보고서 본문 checksum이 맞지 않습니다")
    models = tuple(str(item) for item in _load_tuple(row[7], label="실제 모델"))
    try:
        content = ContentSnapshot(
            content_id=str(row[0]),
            payload=payload,
            payload_sha256=str(row[2]),
            source_snapshot_id=str(row[3]),
            source_identity_digest=str(row[4]),
            cache_namespace_id=str(row[5]),
            content_generated_at=datetime_from_utc_text(row[6], label="내용 생성"),
            actual_models=models,
            engine_epoch_digest=str(row[8]),
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleStoreCorrupt("content snapshot metadata가 손상됐습니다") from exc
    source = load_source_snapshot(conn, content.source_snapshot_id)
    if source is None or source.identity_digest != content.source_identity_digest:
        raise LifecycleStoreCorrupt("내용 원본과 출처 snapshot 신원이 맞지 않습니다")
    if load_cache_namespace(conn, content.cache_namespace_id) is None:
        raise LifecycleStoreCorrupt("내용 원본이 없는 cache namespace를 가리킵니다")
    return content


def bind_cache_entry(
    conn: sqlite3.Connection,
    *,
    key: CacheLookupKey,
    content: ContentSnapshot,
    artifact_id: str,
    cached_at: dt.datetime,
) -> None:
    """같은 사전 신원에 다른 본문·PDF를 나중 승자로 덮어쓰지 않는다."""

    ensure_schema(conn)
    if content.cache_namespace_id != key.namespace_id:
        raise LifecycleStoreError("캐시 열쇠와 내용의 생성기 신원이 다릅니다")
    if content.engine_epoch_digest != key.engine_epoch_digest:
        raise LifecycleStoreError("캐시 열쇠와 내용의 engine epoch가 다릅니다")
    if load_content_snapshot(conn, content.content_id) is None:
        raise LifecycleStoreError("내용 원본을 먼저 저장해야 캐시에 연결할 수 있습니다")
    source = load_source_snapshot(conn, content.source_snapshot_id)
    if (
        source is None
        or source.preflight_identity_digest != key.preflight_identity_digest
    ):
        raise LifecycleStoreError("캐시 열쇠와 생성 전 출처 신원이 다릅니다")
    clean_artifact_id = str(artifact_id).strip()
    artifact_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_TABLE_ARTIFACTS,),
    ).fetchone()
    if artifact_table is None or not clean_artifact_id:
        raise LifecycleStoreError(
            "최초 승인 PDF artifact를 먼저 저장해야 캐시에 연결할 수 있습니다"
        )
    artifact = conn.execute(
        f"""
        SELECT content_snapshot_id, original_state
        FROM {_TABLE_ARTIFACTS} WHERE artifact_id = ?
        """,
        (clean_artifact_id,),
    ).fetchone()
    if artifact is None or str(artifact[0]) != content.content_id:
        raise LifecycleStoreError("캐시 PDF artifact와 내용 원본이 다릅니다")
    if str(artifact[1]) != "stored":
        raise LifecycleStoreError("최초 bytes를 보관하지 않은 PDF는 캐시할 수 없습니다")
    payload = (
        key.billing_bucket_id,
        key.corp_id,
        key.namespace_id,
        key.preflight_identity_digest,
        key.engine_epoch_digest,
        content.source_identity_digest,
        content.content_id,
        clean_artifact_id,
        utc_text(require_aware(cached_at, label="캐시 저장"), label="캐시 저장"),
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_CACHE_ENTRIES} (
            billing_bucket_id, corp_id, cache_namespace_id,
            preflight_identity_digest, engine_epoch_digest,
            source_identity_digest, content_snapshot_id, artifact_id, cached_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"""
        SELECT billing_bucket_id, corp_id, cache_namespace_id,
               preflight_identity_digest, engine_epoch_digest,
               source_identity_digest, content_snapshot_id, artifact_id, cached_at
        FROM {TABLE_CACHE_ENTRIES}
        WHERE billing_bucket_id = ? AND corp_id = ? AND cache_namespace_id = ?
          AND preflight_identity_digest = ?
          AND engine_epoch_digest = ?
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            key.engine_epoch_digest,
        ),
    ).fetchone()
    # cached_at은 첫 저장 시각을 보존한다. 같은 content를 장애 재시도로 다시
    # 연결할 때 현재 시각이 달라졌다는 이유로 불변식 오류를 만들지 않는다.
    if existing is None or tuple(existing[:8]) != payload[:8]:
        raise ImmutableRecordConflict(
            "같은 캐시 신원에 다른 내용을 덮어쓸 수 없습니다; single-flight 상태를 확인하세요"
        )


def load_cache_hit(
    conn: sqlite3.Connection,
    *,
    key: CacheLookupKey,
    policy: DeliveryPolicy,
    delivered_at: dt.datetime,
) -> CachedRelease | None:
    """사전 신원·나이·본문·최초 승인 PDF가 모두 맞을 때만 돌려준다.

    미적중 이유가 필요하면 `load_cache_lookup`을 쓴다.
    """

    return load_cache_lookup(
        conn,
        key=key,
        policy=policy,
        delivered_at=delivered_at,
    ).hit


def load_cache_lookup(
    conn: sqlite3.Connection,
    *,
    key: CacheLookupKey,
    policy: DeliveryPolicy,
    delivered_at: dt.datetime,
) -> CacheLookup:
    """캐시를 읽고, 못 쓰면 «왜 못 쓰는지»까지 돌려준다.

    ★ 나이를 지난 열쇠를 조용히 `None`으로 닫으면 그 행이 옛 본문을 계속
      가리킨 채 남는다. 그 뒤 새로 만든 보고서를 같은 열쇠에 결속할 때
      `bind_cache_entry`가 「다른 내용을 덮어쓸 수 없다」로 막아 재생성이
      반복해서 실패한다. 그래서 이유를 감추지 않고 위로 올린다.
    """

    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT source_identity_digest, content_snapshot_id, artifact_id,
               engine_epoch_digest
        FROM {TABLE_CACHE_ENTRIES}
        WHERE billing_bucket_id = ? AND corp_id = ? AND cache_namespace_id = ?
          AND preflight_identity_digest = ?
          AND engine_epoch_digest = ?
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            key.engine_epoch_digest,
        ),
    ).fetchone()
    if row is None:
        return CacheLookup(miss_reason=CacheMissReason.NOT_FOUND)
    content = load_content_snapshot(conn, str(row[1]))
    if content is None:
        raise LifecycleStoreCorrupt("캐시가 존재하지 않는 내용 원본을 가리킵니다")
    if (
        content.cache_namespace_id != key.namespace_id
        or content.source_identity_digest != str(row[0])
        or content.engine_epoch_digest != str(row[3])
    ):
        raise LifecycleStoreCorrupt("캐시와 내용 원본의 생성기·전체 출처 신원이 다릅니다")
    source = load_source_snapshot(conn, content.source_snapshot_id)
    if (
        source is None
        or source.preflight_identity_digest != key.preflight_identity_digest
    ):
        raise LifecycleStoreCorrupt("캐시와 생성 전 출처 신원이 다릅니다")
    artifact_id = str(row[2]).strip()
    if not policy.content_is_reusable(content, delivered_at=delivered_at):
        # 손상 판정이 아니라 나이 판정이다. 여기서 행을 지우지 않는다 —
        # 지우는 일은 원장에 사유를 남길 수 있는 `invalidate_cache_entry`의
        # 몫이고, 이 함수는 읽기만 한다.
        return CacheLookup(
            miss_reason=CacheMissReason.CONTENT_EXPIRED,
            expired_content_snapshot_id=content.content_id,
            expired_artifact_id=artifact_id,
        )
    if not artifact_id:
        raise LifecycleStoreCorrupt("캐시에 최초 승인 PDF artifact가 없습니다")
    return CacheLookup(
        hit=CachedRelease(
            content=content,
            artifact_id=artifact_id,
            preflight_identity_digest=key.preflight_identity_digest,
        )
    )


def cache_entry_matches_exactly(
    conn: sqlite3.Connection,
    *,
    key: CacheLookupKey,
    content_snapshot_id: str,
    artifact_id: str,
) -> bool:
    """재시도 문맥이 최초 확정한 정식 cache handle과 정확히 같은지 본다.

    나이 정책을 다시 적용하지 않는다. 이 함수는 새 cache hit 판정이 아니라,
    이미 COMPLETE인 공개 ID가 같은 입력으로 재시도됐는지를 확인하는 용도다.
    """

    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT 1 FROM {TABLE_CACHE_ENTRIES}
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND preflight_identity_digest = ?
          AND engine_epoch_digest = ?
          AND content_snapshot_id = ? AND artifact_id = ?
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            key.engine_epoch_digest,
            str(content_snapshot_id).strip(),
            str(artifact_id).strip(),
        ),
    ).fetchone()
    return row is not None


def invalidate_cache_entry(
    conn: sqlite3.Connection,
    *,
    key: CacheLookupKey,
    expected_content_snapshot_id: str,
    expected_artifact_id: str,
    reason_code: str,
    invalidated_at: dt.datetime,
) -> bool:
    """손상된 cache handle만 지우고 원인·정확한 대상을 감사 원장에 남긴다.

    ContentSnapshot·Artifact·과거 Delivery는 삭제하지 않는다. 과거 링크는
    자기 불변 원본을 계속 fail-closed로 검사하고, 새 생성 조회만 독성 cache
    열쇠를 버린 뒤 새 owner를 얻을 수 있다.
    """

    ensure_schema(conn)
    content_id = str(expected_content_snapshot_id).strip()
    artifact_id = str(expected_artifact_id).strip()
    reason = str(reason_code).strip()
    if not content_id or not artifact_id:
        raise LifecycleStoreError("무효화할 캐시 content·artifact 신원이 필요합니다")
    if _CACHE_INVALIDATION_REASON_RE.fullmatch(reason) is None:
        raise LifecycleStoreError("캐시 무효화 사유는 닫힌 기계 코드여야 합니다")
    cursor = conn.execute(
        f"""
        DELETE FROM {TABLE_CACHE_ENTRIES}
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND preflight_identity_digest = ?
          AND engine_epoch_digest = ?
          AND content_snapshot_id = ? AND artifact_id = ?
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            key.engine_epoch_digest,
            content_id,
            artifact_id,
        ),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        f"""
        INSERT INTO {TABLE_CACHE_INVALIDATIONS} (
            billing_bucket_id, corp_id, cache_namespace_id,
            preflight_identity_digest, content_snapshot_id, artifact_id,
            reason_code, invalidated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            content_id,
            artifact_id,
            reason,
            utc_text(
                require_aware(invalidated_at, label="캐시 무효화"),
                label="캐시 무효화",
            ),
        ),
    )
    return True


def quarantine_cache_key_after_receipt_mismatch(
    conn: sqlite3.Connection,
    *,
    key: CacheLookupKey,
    reason_code: str,
    invalidated_at: dt.datetime,
) -> bool:
    """commit 재대조가 어긋난 정확한 cache key를 대상 drift와 함께 지운다.

    일반 무효화는 기대한 content·artifact가 정확히 같을 때만 지운다. 반면
    commit 뒤 행 자체가 바뀐 경우 그 조건은 독성 행을 남기므로, 이 복구 전용
    함수는 5열 key를 권위로 삼고 현재 관측한 대상을 감사 원장에 기록한다.
    """

    ensure_schema(conn)
    reason = str(reason_code).strip()
    if _CACHE_INVALIDATION_REASON_RE.fullmatch(reason) is None:
        raise LifecycleStoreError("캐시 격리 사유는 닫힌 기계 코드여야 합니다")
    observed = conn.execute(
        f"""
        SELECT content_snapshot_id, artifact_id
        FROM {TABLE_CACHE_ENTRIES}
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND preflight_identity_digest = ?
          AND engine_epoch_digest = ?
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            key.engine_epoch_digest,
        ),
    ).fetchone()
    if observed is None:
        return False
    cursor = conn.execute(
        f"""
        DELETE FROM {TABLE_CACHE_ENTRIES}
        WHERE billing_bucket_id = ? AND corp_id = ?
          AND cache_namespace_id = ? AND preflight_identity_digest = ?
          AND engine_epoch_digest = ?
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            key.engine_epoch_digest,
        ),
    )
    if cursor.rowcount != 1:  # pragma: no cover - BEGIN IMMEDIATE 안쪽 방어선
        raise LifecycleStoreError("격리 중 캐시 행이 동시에 바뀌었습니다")
    conn.execute(
        f"""
        INSERT INTO {TABLE_CACHE_INVALIDATIONS} (
            billing_bucket_id, corp_id, cache_namespace_id,
            preflight_identity_digest, content_snapshot_id, artifact_id,
            reason_code, invalidated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key.billing_bucket_id,
            key.corp_id,
            key.namespace_id,
            key.preflight_identity_digest,
            str(observed[0]),
            str(observed[1]),
            reason,
            utc_text(
                require_aware(invalidated_at, label="캐시 격리"),
                label="캐시 격리",
            ),
        ),
    )
    return True


def save_delivery(conn: sqlite3.Connection, delivery: Delivery) -> None:
    """한 공개 ID의 전달 영수증을 불변으로 저장한다."""

    ensure_schema(conn)
    if load_content_snapshot(conn, delivery.content_snapshot_id) is None:
        raise LifecycleStoreError("내용 원본을 먼저 저장해야 전달할 수 있습니다")
    payload = (
        delivery.delivery_id,
        delivery.public_id,
        delivery.content_snapshot_id,
        delivery.billing_bucket_id,
        utc_text(delivery.delivered_at, label="보고서 전달"),
        utc_text(delivery.expires_at, label="보고서 만료"),
        delivery.cache_origin_content_id,
    )
    try:
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE_DELIVERIES} (
                delivery_id, public_id, content_snapshot_id, billing_bucket_id,
                delivered_at, expires_at, cache_origin_content_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
    except sqlite3.IntegrityError as exc:
        raise ImmutableRecordConflict("공개 ID가 이미 다른 전달에 사용됐습니다") from exc
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"""
        SELECT delivery_id, public_id, content_snapshot_id, billing_bucket_id,
               delivered_at, expires_at, cache_origin_content_id
        FROM {TABLE_DELIVERIES} WHERE delivery_id = ?
        """,
        (delivery.delivery_id,),
    ).fetchone()
    if existing is None or tuple(existing) != payload:
        raise ImmutableRecordConflict("같은 delivery ID를 다른 값으로 덮어쓸 수 없습니다")


def load_delivery(conn: sqlite3.Connection, delivery_id: str) -> Delivery | None:
    """전달 영수증을 내용 원본과 별개로 읽는다."""

    ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT delivery_id, public_id, content_snapshot_id, billing_bucket_id,
               delivered_at, expires_at, cache_origin_content_id
        FROM {TABLE_DELIVERIES} WHERE delivery_id = ?
        """,
        (str(delivery_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    try:
        delivery = Delivery(
            delivery_id=str(row[0]),
            public_id=str(row[1]),
            content_snapshot_id=str(row[2]),
            billing_bucket_id=str(row[3]),
            delivered_at=datetime_from_utc_text(row[4], label="보고서 전달"),
            expires_at=datetime_from_utc_text(row[5], label="보고서 만료"),
            cache_origin_content_id=str(row[6]),
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleStoreCorrupt("delivery metadata가 손상됐습니다") from exc
    if load_content_snapshot(conn, delivery.content_snapshot_id) is None:
        raise LifecycleStoreCorrupt("delivery가 없는 내용 원본을 가리킵니다")
    return delivery


def load_delivery_by_public_id(
    conn: sqlite3.Connection, public_id: str
) -> Delivery | None:
    """웹 경로의 job/public ID로 불변 delivery를 찾는다.

    조회된 행을 바로 DTO로 만들지 않고 ``load_delivery``를 통과시켜
    delivery 지문과 내용 snapshot 참조까지 같은 무결성 검사를 적용한다.
    """

    ensure_schema(conn)
    clean_public_id = str(public_id).strip()
    row = conn.execute(
        f"SELECT delivery_id FROM {TABLE_DELIVERIES} WHERE public_id = ?",
        (clean_public_id,),
    ).fetchone()
    if row is None:
        return None
    delivery = load_delivery(conn, str(row[0]))
    if delivery is None:  # pragma: no cover - 같은 연결·행의 방어선
        raise LifecycleStoreCorrupt("공개 ID가 없는 delivery를 가리킵니다")
    if delivery.public_id != clean_public_id:
        raise LifecycleStoreCorrupt("공개 ID와 delivery 기록이 맞지 않습니다")
    return delivery


def _delivery_intent_from_row(row: sqlite3.Row | tuple) -> DeliveryIntent:
    """delivery 의무 한 행을 검증된 DTO로 바꾼다(조회 함수 여러 곳이 공유)."""

    try:
        return DeliveryIntent(
            public_id=str(row[0]),
            state=str(row[1]),
            required_at=datetime_from_utc_text(row[2], label="delivery 의무 시작"),
            updated_at=datetime_from_utc_text(row[3], label="delivery 의무 변경"),
            failure_code=str(row[4]),
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleStoreCorrupt("delivery 의무 표식이 손상됐습니다") from exc


def load_delivery_intent(
    conn: sqlite3.Connection,
    public_id: str,
) -> DeliveryIntent | None:
    """새 보고서 출고 의무를 읽고 complete면 실제 delivery까지 확인한다."""

    ensure_schema(conn)
    clean_public_id = str(public_id).strip()
    row = conn.execute(
        f"""
        SELECT public_id, state, required_at, updated_at, failure_code
        FROM {TABLE_DELIVERY_INTENTS} WHERE public_id = ?
        """,
        (clean_public_id,),
    ).fetchone()
    if row is None:
        return None
    intent = _delivery_intent_from_row(row)
    if intent.public_id != clean_public_id:
        raise LifecycleStoreCorrupt("공개 ID와 delivery 의무 표식이 맞지 않습니다")
    if (
        intent.state == DELIVERY_INTENT_COMPLETE
        and load_delivery_by_public_id(conn, clean_public_id) is None
    ):
        raise LifecycleStoreCorrupt("완료 delivery 의무에 실제 delivery가 없습니다")
    return intent


def list_stale_required_delivery_intents(
    conn: sqlite3.Connection,
    *,
    older_than: dt.datetime,
) -> list[DeliveryIntent]:
    """N분 넘게 ``required``로 정체된 delivery 의무만 재시작 스윕 대상으로 돌려준다.

    ``_save_report`` 성공 직후 ~ 최종 출고 확정 사이에 프로세스가 죽으면 이
    의무는 영원히 required로 남는다(§F1). 정상 진행 중인 요청까지 스윕이
    건드리지 않도록 ``older_than``보다 새 의무는 제외한다. 같은 공개 ID에
    실제 delivery가 이미 있으면(정상 경로라면 의무도 이미 complete여야
    하지만 방어적으로 재확인) 절대 포함하지 않는다 — 실제 출고가 끝난
    보고서를 스윕이 실패로 뒤집는 사고를 막기 위함이다.
    """

    ensure_schema(conn)
    cutoff = require_aware(older_than, label="delivery 의무 정체 기준")
    rows = conn.execute(
        f"""
        SELECT public_id, state, required_at, updated_at, failure_code
        FROM {TABLE_DELIVERY_INTENTS}
        WHERE state = ?
        ORDER BY updated_at ASC, public_id ASC
        """,
        (DELIVERY_INTENT_REQUIRED,),
    ).fetchall()
    stale: list[DeliveryIntent] = []
    for row in rows:
        intent = _delivery_intent_from_row(row)
        if intent.updated_at > cutoff:
            continue
        if load_delivery_by_public_id(conn, intent.public_id) is not None:
            continue
        stale.append(intent)
    return stale


def mark_delivery_required(
    conn: sqlite3.Connection,
    *,
    public_id: str,
    required_at: dt.datetime,
) -> DeliveryIntent:
    """새 보고서임을 먼저 영속화해 실패 뒤 legacy로 오인하지 않게 한다."""

    ensure_schema(conn)
    clean_public_id = str(public_id).strip()
    required = require_aware(required_at, label="delivery 의무 시작")
    if not clean_public_id:
        raise ValueError("delivery 의무에 공개 ID가 필요합니다")
    existing = load_delivery_intent(conn, clean_public_id)
    if existing is None:
        stamp = utc_text(required, label="delivery 의무 시작")
        conn.execute(
            f"""
            INSERT INTO {TABLE_DELIVERY_INTENTS} (
                public_id, state, required_at, updated_at, failure_code
            ) VALUES (?, ?, ?, ?, '')
            """,
            (clean_public_id, DELIVERY_INTENT_REQUIRED, stamp, stamp),
        )
    elif existing.state == DELIVERY_INTENT_FAILED:
        stamp = utc_text(required, label="delivery 의무 재시도")
        conn.execute(
            f"""
            UPDATE {TABLE_DELIVERY_INTENTS}
            SET state = ?, required_at = ?, updated_at = ?, failure_code = ''
            WHERE public_id = ? AND state = ?
            """,
            (
                DELIVERY_INTENT_REQUIRED,
                stamp,
                stamp,
                clean_public_id,
                DELIVERY_INTENT_FAILED,
            ),
        )
    intent = load_delivery_intent(conn, clean_public_id)
    if intent is None:  # pragma: no cover - 같은 연결의 방어선
        raise LifecycleStoreCorrupt("delivery 의무 표식을 저장하지 못했습니다")
    return intent


def mark_delivery_complete(
    conn: sqlite3.Connection,
    *,
    public_id: str,
    completed_at: dt.datetime,
) -> DeliveryIntent:
    """본문·artifact 결속과 같은 거래 안에서 출고 의무를 완료한다."""

    clean_public_id = str(public_id).strip()
    completed = require_aware(completed_at, label="delivery 완료")
    intent = load_delivery_intent(conn, clean_public_id)
    if intent is None:
        raise LifecycleStoreError("delivery 의무를 먼저 저장해야 완료할 수 있습니다")
    if load_delivery_by_public_id(conn, clean_public_id) is None:
        raise LifecycleStoreError("실제 delivery 없이 의무만 완료할 수 없습니다")
    if intent.state == DELIVERY_INTENT_COMPLETE:
        return intent
    if intent.state != DELIVERY_INTENT_REQUIRED:
        raise LifecycleStoreError("실패한 delivery 의무는 재시도 표식 뒤 완료해야 합니다")
    conn.execute(
        f"""
        UPDATE {TABLE_DELIVERY_INTENTS}
        SET state = ?, updated_at = ?, failure_code = ''
        WHERE public_id = ? AND state = ?
        """,
        (
            DELIVERY_INTENT_COMPLETE,
            utc_text(completed, label="delivery 완료"),
            clean_public_id,
            DELIVERY_INTENT_REQUIRED,
        ),
    )
    completed_intent = load_delivery_intent(conn, clean_public_id)
    if completed_intent is None:  # pragma: no cover - 같은 연결의 방어선
        raise LifecycleStoreCorrupt("delivery 완료 표식을 읽지 못했습니다")
    return completed_intent


def mark_delivery_failed(
    conn: sqlite3.Connection,
    *,
    public_id: str,
    failure_code: str,
    failed_at: dt.datetime,
) -> DeliveryIntent:
    """예외문 대신 닫힌 코드만 남기고 새 보고서를 계속 fail-closed한다."""

    clean_public_id = str(public_id).strip()
    clean_code = str(failure_code).strip()
    failed = require_aware(failed_at, label="delivery 실패")
    if _FAILURE_CODE_RE.fullmatch(clean_code) is None:
        raise ValueError("delivery 실패 코드는 닫힌 기계 코드여야 합니다")
    intent = load_delivery_intent(conn, clean_public_id)
    if intent is None:
        raise LifecycleStoreError("delivery 의무를 먼저 저장해야 실패를 기록할 수 있습니다")
    if intent.state == DELIVERY_INTENT_COMPLETE:
        return intent
    conn.execute(
        f"""
        UPDATE {TABLE_DELIVERY_INTENTS}
        SET state = ?, updated_at = ?, failure_code = ?
        WHERE public_id = ? AND state = ?
        """,
        (
            DELIVERY_INTENT_FAILED,
            utc_text(failed, label="delivery 실패"),
            clean_code,
            clean_public_id,
            DELIVERY_INTENT_REQUIRED,
        ),
    )
    failed_intent = load_delivery_intent(conn, clean_public_id)
    if failed_intent is None:  # pragma: no cover - 같은 연결의 방어선
        raise LifecycleStoreCorrupt("delivery 실패 표식을 읽지 못했습니다")
    return failed_intent


def delivery_count_for_content(conn: sqlite3.Connection, content_id: str) -> int:
    """본문 복제 없이 한 내용에 연결된 전달 수를 센다."""

    ensure_schema(conn)
    row = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_DELIVERIES} WHERE content_snapshot_id = ?",
        (str(content_id).strip(),),
    ).fetchone()
    return 0 if row is None else int(row[0])
