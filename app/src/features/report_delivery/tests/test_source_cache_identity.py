from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from src.features.report_delivery.artifact import (
    ArtifactRetention,
    ArtifactVersion,
    FilesystemArtifactBlobBackend,
    create_blob_write_intent,
    store_approved_pdf,
)
from src.features.report_delivery.cache_identity import (
    CacheIdentityUnavailable,
    CacheLookupKey,
    CacheNamespace,
)
from src.features.report_delivery.models import ContentSnapshot, DeliveryPolicy
from src.features.report_delivery.source_identity import (
    SourceSnapshot,
    financial_payload_digest,
)
from src.features.report_delivery.store import (
    ImmutableRecordConflict,
    LifecycleStoreError,
    LifecycleStoreCorrupt,
    TABLE_CACHE_ENTRIES,
    TABLE_CACHE_INVALIDATIONS,
    TABLE_CACHE_NAMESPACES,
    TABLE_SOURCE_SNAPSHOTS,
    bind_cache_entry,
    ensure_schema,
    load_cache_namespace,
    load_cache_hit,
    load_source_snapshot,
    save_cache_namespace,
    save_content_snapshot,
    save_source_snapshot,
)
from src.shared.report_source_identity import ReportSourceIdentity


def _financial_payload(amount: str = "1000000") -> dict[str, object]:
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {"account_nm": "영업이익", "thstrm_amount": "200", "ord": "2"},
            {"account_nm": "매출액", "thstrm_amount": amount, "ord": "1"},
        ],
    }


def _preflight_digest(source: SourceSnapshot) -> str:
    return ReportSourceIdentity(
        dart_receipt_numbers=source.dart_receipt_nos,
        financial_payload_digest=source.financial_payload_sha256,
    ).cache_digest


def _store_artifact(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    *,
    now: dt.datetime,
    root: Path,
    marker: bytes = b"original",
) -> str:
    backend = FilesystemArtifactBlobBackend(root)
    pdf_bytes = b"%PDF-1.4\n" + marker + b"\n%%EOF\n"
    intent = create_blob_write_intent(
        conn,
        backend,
        pdf_bytes=pdf_bytes,
        created_at=now,
    )
    metadata = store_approved_pdf(
        conn,
        backend,
        blob_intent=intent,
        content_snapshot_id=content.content_id,
        pdf_bytes=pdf_bytes,
        version=ArtifactVersion(
            renderer_version="renderer-1",
            font_bundle_version="fonts-1",
            checker_version="checker-1",
        ),
        created_at=now,
        retention=ArtifactRetention(policy_id="test-no-delete", retain_until=None),
    )
    return metadata.artifact_id


def test_financial_digest_ignores_json_key_row_order_and_outer_spaces() -> None:
    first = _financial_payload()
    second = {
        "list": [
            {"ord": " 1 ", "thstrm_amount": "1000000", "account_nm": "매출액"},
            {"ord": "2", "account_nm": "영업이익", "thstrm_amount": "200"},
        ],
        "message": "서버 설명 문구가 바뀌어도 자료값은 같다",
        "status": "000",
    }

    assert financial_payload_digest(first) == financial_payload_digest(second)


def test_corrected_financial_amount_changes_source_identity(now: dt.datetime) -> None:
    before = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload=_financial_payload("1000000"),
        captured_at=now,
        source_as_of=now.date(),
    )
    corrected = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload=_financial_payload("1100000"),
        captured_at=now,
        source_as_of=now.date(),
    )

    assert before.financial_payload_sha256 != corrected.financial_payload_sha256
    assert before.identity_digest != corrected.identity_digest


def test_revalidation_event_is_distinct_but_same_source_identity(now: dt.datetime) -> None:
    first = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload=_financial_payload(),
        captured_at=now,
        source_as_of=now.date(),
    )
    next_day = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload=_financial_payload(),
        captured_at=now + dt.timedelta(days=1),
        source_as_of=(now + dt.timedelta(days=1)).date(),
    )

    assert first.snapshot_id != next_day.snapshot_id
    assert first.identity_digest == next_day.identity_digest


def test_new_dart_receipt_changes_source_identity(now: dt.datetime) -> None:
    first = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload=_financial_payload(),
        captured_at=now,
        source_as_of=now.date(),
    )
    correction = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000999",),
        financial_payload=_financial_payload(),
        captured_at=now,
        source_as_of=now.date(),
    )

    assert first.identity_digest != correction.identity_digest


def test_namespace_changes_for_revision_model_and_output_setting() -> None:
    base = dict(
        product="company-analysis-v2",
        schema_version="v2",
        deployment_revision="commit-a",
        requested_models={"writer": "model-a"},
        output_settings={"temperature": 0},
    )
    original = CacheNamespace.create(**base)
    revision = CacheNamespace.create(**{**base, "deployment_revision": "commit-b"})
    model = CacheNamespace.create(
        **{**base, "requested_models": {"writer": "model-b"}}
    )
    setting = CacheNamespace.create(
        **{**base, "output_settings": {"temperature": 0, "max_tokens": 9000}}
    )

    assert len(
        {
            original.namespace_id,
            revision.namespace_id,
            model.namespace_id,
            setting.namespace_id,
        }
    ) == 4


def test_cache_namespace_provenance_is_persisted(
    conn: sqlite3.Connection, namespace: CacheNamespace
) -> None:
    save_cache_namespace(conn, namespace)

    assert load_cache_namespace(conn, namespace.namespace_id) == namespace


def test_source_and_namespace_metadata_fingerprints_are_checked_on_read(
    conn: sqlite3.Connection,
    source: SourceSnapshot,
    namespace: CacheNamespace,
) -> None:
    save_source_snapshot(conn, source)
    save_cache_namespace(conn, namespace)
    conn.execute(
        f"UPDATE {TABLE_SOURCE_SNAPSHOTS} SET identity_digest = ? WHERE snapshot_id = ?",
        ("0" * 64, source.snapshot_id),
    )
    conn.execute(
        f"UPDATE {TABLE_CACHE_NAMESPACES} SET settings_sha256 = ? WHERE namespace_id = ?",
        ("0" * 64, namespace.namespace_id),
    )

    with pytest.raises(LifecycleStoreCorrupt, match="source snapshot metadata"):
        load_source_snapshot(conn, source.snapshot_id)
    with pytest.raises(LifecycleStoreCorrupt, match="cache namespace metadata"):
        load_cache_namespace(conn, namespace.namespace_id)


def test_unknown_deployment_and_image_identity_fail_closed() -> None:
    with pytest.raises(CacheIdentityUnavailable, match="캐시를 끕니다"):
        CacheNamespace.create(
            product="company-analysis-v2",
            schema_version="v2",
            deployment_revision="unknown",
            image_digest="",
            requested_models={"writer": "model-a"},
        )


def test_incomplete_source_cannot_make_cache_key(
    now: dt.datetime, namespace: CacheNamespace
) -> None:
    incomplete = SourceSnapshot.capture(
        dart_receipt_nos=(),
        financial_payload=None,
        captured_at=now,
        source_as_of=now.date(),
    )

    with pytest.raises(CacheIdentityUnavailable, match="출처 신원"):
        CacheLookupKey.from_preflight(
            billing_bucket_id="bucket-a",
            corp_id="00126380",
            namespace=namespace,
            preflight_identity_digest="",
            preflight_cache_usable=incomplete.cache_usable,
        )


def test_cache_hit_requires_company_namespace_and_current_source_identity(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=_preflight_digest(source),
        preflight_cache_usable=source.cache_usable,
    )
    # 생성 뒤 provenance에는 공식문서/adapter도 들어가므로 사전 지문과
    # 달라지는 것이 정상이다. 둘을 같다고 강제하면 실제 제품은 늘 miss한다.
    assert key.preflight_identity_digest != content.source_identity_digest
    artifact_id = _store_artifact(
        conn,
        content,
        now=now,
        root=tmp_path / "artifact-blobs",
    )
    bind_cache_entry(
        conn,
        key=key,
        content=content,
        artifact_id=artifact_id,
        cached_at=now,
    )
    policy = DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60))

    hit = load_cache_hit(
        conn,
        key=key,
        policy=policy,
        delivered_at=now + dt.timedelta(days=59),
    )
    assert hit is not None
    assert hit.content == content
    assert hit.artifact_id == artifact_id

    corrected = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000999",),
        financial_payload=_financial_payload("1100000"),
        captured_at=now + dt.timedelta(days=1),
        source_as_of=(now + dt.timedelta(days=1)).date(),
    )
    corrected_key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=_preflight_digest(corrected),
        preflight_cache_usable=corrected.cache_usable,
    )
    assert (
        load_cache_hit(
            conn, key=corrected_key, policy=policy, delivered_at=now + dt.timedelta(days=1)
        )
        is None
    )


def test_cache_entry_is_first_writer_wins_not_silent_overwrite(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=_preflight_digest(source),
        preflight_cache_usable=True,
    )
    original_artifact_id = _store_artifact(
        conn,
        content,
        now=now,
        root=tmp_path / "artifact-blobs",
        marker=b"first",
    )
    bind_cache_entry(
        conn,
        key=key,
        content=content,
        artifact_id=original_artifact_id,
        cached_at=now,
    )
    competitor = ContentSnapshot.create(
        payload=b'{"company":"sample","sections":["different"]}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now + dt.timedelta(seconds=1),
    )
    save_source_snapshot(conn, source)
    save_content_snapshot(conn, competitor)
    competitor_artifact_id = _store_artifact(
        conn,
        competitor,
        now=now + dt.timedelta(seconds=1),
        root=tmp_path / "artifact-blobs",
        marker=b"competitor",
    )

    with pytest.raises(ImmutableRecordConflict, match="single-flight"):
        bind_cache_entry(
            conn,
            key=key,
            content=competitor,
            artifact_id=competitor_artifact_id,
            cached_at=now + dt.timedelta(seconds=1),
        )


def test_cache_entry_rejects_content_without_approved_artifact(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
) -> None:
    key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=_preflight_digest(source),
        preflight_cache_usable=True,
    )

    with pytest.raises(LifecycleStoreError, match="PDF artifact"):
        bind_cache_entry(
            conn,
            key=key,
            content=content,
            artifact_id="artifact-does-not-exist",
            cached_at=now,
        )


def test_cache_hit_fails_closed_when_artifact_binding_is_missing(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=_preflight_digest(source),
        preflight_cache_usable=True,
    )
    artifact_id = _store_artifact(
        conn,
        content,
        now=now,
        root=tmp_path / "artifact-blobs",
    )
    bind_cache_entry(
        conn,
        key=key,
        content=content,
        artifact_id=artifact_id,
        cached_at=now,
    )
    conn.execute(
        f"UPDATE {TABLE_CACHE_ENTRIES} SET artifact_id = '' "
        "WHERE preflight_identity_digest = ?",
        (key.preflight_identity_digest,),
    )

    with pytest.raises(LifecycleStoreCorrupt, match="PDF artifact"):
        load_cache_hit(
            conn,
            key=key,
            policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
            delivered_at=now + dt.timedelta(days=1),
        )


def test_legacy_content_only_cache_row_is_migrated_as_explicit_miss(
    namespace: CacheNamespace,
    source: SourceSnapshot,
    now: dt.datetime,
) -> None:
    legacy = sqlite3.connect(":memory:")
    try:
        legacy.execute(
            f"""
            CREATE TABLE {TABLE_CACHE_ENTRIES} (
                corp_id TEXT NOT NULL,
                cache_namespace_id TEXT NOT NULL,
                source_identity_digest TEXT NOT NULL,
                content_snapshot_id TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (
                    corp_id, cache_namespace_id, source_identity_digest
                )
            )
            """
        )
        legacy.execute(
            f"""
            INSERT INTO {TABLE_CACHE_ENTRIES} (
                corp_id, cache_namespace_id, source_identity_digest,
                content_snapshot_id, cached_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "00126380",
                namespace.namespace_id,
                source.identity_digest,
                "legacy-content-only",
                now.isoformat(),
            ),
        )

        ensure_schema(legacy)
        migrated = legacy.execute(
            f"""
            SELECT preflight_identity_digest, artifact_id
            FROM {TABLE_CACHE_ENTRIES}
            """
        ).fetchone()
        assert migrated is None
        audit = legacy.execute(
            f"SELECT reason_code FROM {TABLE_CACHE_INVALIDATIONS}"
        ).fetchone()
        assert audit == ("legacy_unscoped_cache_quarantined",)

        key = CacheLookupKey.from_preflight(
            billing_bucket_id="bucket-a",
            corp_id="00126380",
            namespace=namespace,
            preflight_identity_digest=_preflight_digest(source),
            preflight_cache_usable=True,
        )
        assert (
            load_cache_hit(
                legacy,
                key=key,
                policy=DeliveryPolicy(
                    dt.timedelta(days=60),
                    dt.timedelta(days=60),
                ),
                delivered_at=now,
            )
            is None
        )
    finally:
        legacy.close()


def test_old_primary_key_is_rebuilt_so_two_buckets_can_bind_independently(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    conn.execute(f"DROP TABLE {TABLE_CACHE_ENTRIES}")
    conn.execute(
        f"""
        CREATE TABLE {TABLE_CACHE_ENTRIES} (
            billing_bucket_id TEXT NOT NULL,
            corp_id TEXT NOT NULL,
            cache_namespace_id TEXT NOT NULL,
            preflight_identity_digest TEXT NOT NULL,
            source_identity_digest TEXT NOT NULL,
            content_snapshot_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            cached_at TEXT NOT NULL,
            PRIMARY KEY (
                corp_id, cache_namespace_id, preflight_identity_digest
            )
        )
        """
    )
    ensure_schema(conn)
    pk = tuple(
        str(row[1])
        for row in sorted(
            conn.execute(f"PRAGMA table_info({TABLE_CACHE_ENTRIES})").fetchall(),
            key=lambda row: int(row[5]),
        )
        if int(row[5]) > 0
    )
    assert pk == (
        "billing_bucket_id",
        "corp_id",
        "cache_namespace_id",
        "preflight_identity_digest",
    )

    competitor = ContentSnapshot.create(
        payload=b'{"company":"sample","sections":["bucket-b"]}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
    )
    save_content_snapshot(conn, competitor)
    first_artifact = _store_artifact(
        conn,
        content,
        now=now,
        root=tmp_path / "bucket-artifacts",
        marker=b"bucket-a",
    )
    second_artifact = _store_artifact(
        conn,
        competitor,
        now=now,
        root=tmp_path / "bucket-artifacts",
        marker=b"bucket-b",
    )
    keys = tuple(
        CacheLookupKey.from_preflight(
            billing_bucket_id=bucket,
            corp_id="00126380",
            namespace=namespace,
            preflight_identity_digest=_preflight_digest(source),
            preflight_cache_usable=True,
        )
        for bucket in ("bucket-a", "bucket-b")
    )
    bind_cache_entry(
        conn,
        key=keys[0],
        content=content,
        artifact_id=first_artifact,
        cached_at=now,
    )
    bind_cache_entry(
        conn,
        key=keys[1],
        content=competitor,
        artifact_id=second_artifact,
        cached_at=now,
    )
    policy = DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60))
    first = load_cache_hit(conn, key=keys[0], policy=policy, delivered_at=now)
    second = load_cache_hit(conn, key=keys[1], policy=policy, delivered_at=now)
    assert first is not None and first.content == content
    assert second is not None and second.content == competitor
