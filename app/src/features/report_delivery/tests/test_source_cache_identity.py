from __future__ import annotations

import datetime as dt
import json
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
from src.features.report_delivery.canonical import canonical_digest, sha256_hex, utc_text
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
    TABLE_CONTENT_SNAPSHOTS,
    TABLE_SOURCE_SNAPSHOTS,
    bind_cache_entry,
    ensure_schema,
    load_cache_namespace,
    load_cache_hit,
    load_content_snapshot,
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
    return source.preflight_identity_digest


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


def test_옛_source행은_빈_preflight로_읽되_새cache권위로_승격하지_않는다(
    conn: sqlite3.Connection,
    namespace: CacheNamespace,
    now: dt.datetime,
) -> None:
    """열 추가 전 발급본은 읽을 수 있지만 새 조사의 cache 원본은 아니다."""

    receipts = ("20260828000123",)
    finance_digest = financial_payload_digest(_financial_payload())
    documents = ("dart.fss.or.kr:20260828000123",)
    versions = (("dart", "2"),)
    legacy_identity = canonical_digest(
        {
            "dart_receipt_nos": receipts,
            "financial_payload_sha256": finance_digest,
            "official_document_ids": documents,
            "adapter_versions": versions,
        }
    )
    snapshot_id = "source_" + canonical_digest(
        {
            "identity_digest": legacy_identity,
            "captured_at": utc_text(now, label="출처 확인"),
            "source_as_of": now.date().isoformat(),
        }
    )
    # 배포 전 실제 표 모양: source 행에는 preflight 열 자체가 없었다.
    conn.execute(
        f"""
        CREATE TABLE {TABLE_SOURCE_SNAPSHOTS} (
            snapshot_id TEXT PRIMARY KEY,
            identity_digest TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            source_as_of TEXT NOT NULL,
            dart_receipt_nos_json TEXT NOT NULL,
            financial_payload_sha256 TEXT NOT NULL,
            official_document_ids_json TEXT NOT NULL,
            adapter_versions_json TEXT NOT NULL,
            cache_usable INTEGER NOT NULL CHECK(cache_usable IN (0, 1))
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO {TABLE_SOURCE_SNAPSHOTS} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            legacy_identity,
            utc_text(now, label="출처 확인"),
            now.date().isoformat(),
            json.dumps(receipts, ensure_ascii=False),
            finance_digest,
            json.dumps(documents, ensure_ascii=False),
            json.dumps(versions, ensure_ascii=False),
            1,
        ),
    )

    ensure_schema(conn)
    loaded = load_source_snapshot(conn, snapshot_id)

    assert loaded is not None
    assert loaded.identity_digest == legacy_identity
    assert loaded.preflight_identity_digest == ""
    with pytest.raises(CacheIdentityUnavailable, match="출처 신원"):
        CacheLookupKey.from_preflight(
            billing_bucket_id="historical-bucket",
            corp_id="00126380",
            namespace=namespace,
            preflight_identity_digest=loaded.preflight_identity_digest,
            preflight_cache_usable=loaded.cache_usable,
            engine_epoch_digest="a" * 64,
        )


def test_공식자료결합지문은_최초저장부터_cache_key까지_같은값을쓴다(
    conn: sqlite3.Connection,
    namespace: CacheNamespace,
    now: dt.datetime,
    tmp_path: Path,
) -> None:
    base = ReportSourceIdentity(
        dart_receipt_numbers=("20260828000123",),
        financial_payload_digest=financial_payload_digest(_financial_payload()),
    )
    combined_digest = base.cache_digest_with_official_snapshot("c" * 64)
    source = SourceSnapshot.capture(
        dart_receipt_nos=base.dart_receipt_numbers,
        financial_payload=None,
        financial_payload_sha256=base.financial_payload_digest,
        captured_at=now,
        source_as_of=now.date(),
        preflight_identity_digest=combined_digest,
    )
    content = ContentSnapshot.create(
        payload=b'{"company":"combined-source"}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
        engine_epoch_digest="a" * 64,
    )
    save_source_snapshot(conn, source)
    save_cache_namespace(conn, namespace)
    save_content_snapshot(conn, content)
    artifact_id = _store_artifact(
        conn,
        content,
        now=now,
        root=tmp_path / "combined-source-artifacts",
    )
    combined_key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-combined",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=combined_digest,
        preflight_cache_usable=True,
        engine_epoch_digest=content.engine_epoch_digest,
    )
    bind_cache_entry(
        conn,
        key=combined_key,
        content=content,
        artifact_id=artifact_id,
        cached_at=now,
    )
    assert load_source_snapshot(conn, source.snapshot_id) == source
    assert load_cache_hit(
        conn,
        key=combined_key,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        delivered_at=now,
    ) is not None

    # DART·재무가 같아도 공식자료 snapshot을 뺀 옛 base 열쇠나 다른 공식자료
    # 열쇠에는 이 content를 결속할 수 없다.
    for wrong_digest in (
        base.cache_digest,
        base.cache_digest_with_official_snapshot("d" * 64),
    ):
        wrong_key = CacheLookupKey.from_preflight(
            billing_bucket_id="bucket-wrong",
            corp_id="00126380",
            namespace=namespace,
            preflight_identity_digest=wrong_digest,
            preflight_cache_usable=True,
            engine_epoch_digest=content.engine_epoch_digest,
        )
        with pytest.raises(LifecycleStoreError, match="생성 전 출처"):
            bind_cache_entry(
                conn,
                key=wrong_key,
                content=content,
                artifact_id=artifact_id,
                cached_at=now,
            )


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
            engine_epoch_digest="a" * 64,
        )


def test_새content와_cache_key는_engine_epoch누락을_fail_closed한다(
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
) -> None:
    with pytest.raises(ValueError, match="engine epoch"):
        ContentSnapshot.create(
            payload=b'{"company":"sample"}',
            source_snapshot=source,
            cache_namespace=namespace,
            content_generated_at=now,
            engine_epoch_digest="",
        )


def test_배포전_역사content는_공개읽기만_유지하고_새cache권위는_주지않는다(
    conn: sqlite3.Connection,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
) -> None:
    payload = b'{"company":"historical"}'
    payload_digest = sha256_hex(payload)
    models = ("historical-model",)
    old_content_id = "content_" + canonical_digest(
        {
            "payload_sha256": payload_digest,
            "source_snapshot_id": source.snapshot_id,
            "cache_namespace_id": namespace.namespace_id,
            "content_generated_at": utc_text(now, label="내용 생성"),
            "actual_models": models,
        }
    )
    historical = ContentSnapshot(
        content_id=old_content_id,
        payload=payload,
        payload_sha256=payload_digest,
        source_snapshot_id=source.snapshot_id,
        source_identity_digest=source.identity_digest,
        cache_namespace_id=namespace.namespace_id,
        content_generated_at=now,
        actual_models=models,
        engine_epoch_digest="",
    )
    save_source_snapshot(conn, source)
    save_cache_namespace(conn, namespace)
    with pytest.raises(LifecycleStoreError, match="engine epoch"):
        save_content_snapshot(conn, historical)

    # 배포 전에 이미 있던 행을 그대로 흉내 낸다. 공개 링크의 과거 원본은
    # 읽을 수 있어야 하지만 빈 epoch를 현재 cache key로 승격하면 안 된다.
    conn.execute(
        f"""
        INSERT INTO {TABLE_CONTENT_SNAPSHOTS} (
            content_id, payload, payload_sha256, source_snapshot_id,
            source_identity_digest, cache_namespace_id,
            content_generated_at, actual_models_json, engine_epoch_digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
        """,
        (
            historical.content_id,
            historical.payload,
            historical.payload_sha256,
            historical.source_snapshot_id,
            historical.source_identity_digest,
            historical.cache_namespace_id,
            utc_text(now, label="내용 생성"),
            json.dumps(models, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    assert load_content_snapshot(conn, historical.content_id) == historical
    current_key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=namespace,
        preflight_identity_digest=_preflight_digest(source),
        preflight_cache_usable=True,
        engine_epoch_digest="a" * 64,
    )
    with pytest.raises(LifecycleStoreError, match="engine epoch"):
        bind_cache_entry(
            conn,
            key=current_key,
            content=historical,
            artifact_id="artifact-never-reached",
            cached_at=now,
        )
    with pytest.raises(CacheIdentityUnavailable, match="engine epoch"):
        CacheLookupKey.from_preflight(
            billing_bucket_id="bucket-a",
            corp_id="00126380",
            namespace=namespace,
            preflight_identity_digest=_preflight_digest(source),
            preflight_cache_usable=True,
            engine_epoch_digest="",
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
        engine_epoch_digest=content.engine_epoch_digest,
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
        engine_epoch_digest=content.engine_epoch_digest,
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
        engine_epoch_digest=content.engine_epoch_digest,
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
        engine_epoch_digest=content.engine_epoch_digest,
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
        engine_epoch_digest=content.engine_epoch_digest,
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
        engine_epoch_digest=content.engine_epoch_digest,
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
            engine_epoch_digest="a" * 64,
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
        "engine_epoch_digest",
    )

    competitor = ContentSnapshot.create(
        payload=b'{"company":"sample","sections":["bucket-b"]}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
        engine_epoch_digest=content.engine_epoch_digest,
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
            engine_epoch_digest=content.engine_epoch_digest,
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
