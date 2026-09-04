from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.features.admin_dashboard import store as dashboard_store
from src.features.admin_dashboard import maintenance as dashboard_maintenance
from src.features.report_delivery import artifact, retention
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheLookupKey, CacheNamespace
from src.features.report_delivery.models import ContentSnapshot, Delivery, DeliveryPolicy
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.web import report_delivery_adapter, report_retention_adapter


NOW = dt.datetime(2026, 7, 1, 11, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
PURGE_AT = NOW + dt.timedelta(days=30)
PDF = b"%PDF-1.4\n% retention approved original\n%%EOF\n"
CAPACITY_512_MIB = 512 * 1024 * 1024


@dataclass(frozen=True)
class StoredCurrent:
    public_id: str
    content: ContentSnapshot
    delivery: Delivery
    artifact_id: str
    backend: artifact.FilesystemArtifactBlobBackend


def _factory(path: Path):
    return lambda: storage_db.connect(path)


def _namespace() -> CacheNamespace:
    return CacheNamespace.create(
        product="retention-test",
        schema_version="v2",
        deployment_revision="retention-test-commit",
        requested_models={"writer": "offline-test"},
        output_settings={"temperature": 0},
    )


def _source() -> SourceSnapshot:
    return SourceSnapshot.capture(
        dart_receipt_nos=("20260701000123",),
        financial_payload={"status": "000", "list": [{"amount": "1"}]},
        captured_at=NOW,
        source_as_of=NOW.date(),
        adapter_versions={"test": "retention-v1"},
    )


def _insert_legacy_payload(conn: sqlite3.Connection, public_id: str) -> None:
    conn.execute(
        f"""
        INSERT OR IGNORE INTO {storage_constants.TABLE_REPORTS}
            (report_id, corp_id, job, payload_json, generated_at, created_at)
        VALUES (?, 'CORP-RETENTION', '', ?, '2026-07-01', ?)
        """,
        (public_id, '{"private":"legacy-copy"}', NOW.isoformat()),
    )


def _store_current(
    db_path: Path,
    data_root: Path,
    *,
    public_id: str,
    payload: bytes | None = None,
    pdf_bytes: bytes = PDF,
    legal_hold: bool = False,
) -> StoredCurrent:
    backend = artifact.FilesystemArtifactBlobBackend(
        data_root / "report-artifacts",
        capacity_bytes=CAPACITY_512_MIB,
    )
    source = _source()
    namespace = _namespace()
    content = ContentSnapshot.create(
        payload=payload or ('{"private":"%s"}' % public_id).encode(),
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=NOW,
        engine_epoch_digest="a" * 64,
        actual_models=("offline-test",),
    )
    policy = DeliveryPolicy(
        content_max_age=dt.timedelta(days=30),
        public_link_lifetime=dt.timedelta(days=365),
    )
    delivery = Delivery.issue(
        public_id=public_id,
        billing_bucket_id="bucket-retention",
        content=content,
        delivered_at=NOW,
        policy=policy,
        reused_from_cache=False,
    )
    with storage_db.connect(db_path) as conn:
        delivery_store.save_source_snapshot(conn, source)
        delivery_store.save_cache_namespace(conn, namespace)
        delivery_store.save_content_snapshot(conn, content)
        delivery_store.mark_delivery_required(
            conn,
            public_id=public_id,
            required_at=NOW,
        )
        delivery_store.save_delivery(conn, delivery)
        _insert_legacy_payload(conn, public_id)
    with storage_db.connect(db_path) as conn:
        blob_intent = artifact.create_blob_write_intent(
            conn,
            backend,
            pdf_bytes=pdf_bytes,
            created_at=NOW,
        )
    with storage_db.connect(db_path) as conn:
        metadata = artifact.store_approved_pdf(
            conn,
            backend,
            blob_intent=blob_intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=pdf_bytes,
            version=artifact.ArtifactVersion(
                renderer_version="renderer-retention-v1",
                font_bundle_version="font-retention-v1",
                checker_version="checker-retention-v1",
            ),
            created_at=NOW,
            retention=artifact.ArtifactRetention(
                policy_id=retention.TRASH_RETENTION_POLICY_ID,
                retain_until=None,
                legal_hold=legal_hold,
            ),
        )
        artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=metadata.artifact_id,
        )
        delivery_store.mark_delivery_complete(
            conn,
            public_id=public_id,
            completed_at=NOW,
        )
        assert dashboard_store.trash_report(
            conn,
            report_id=public_id,
            actor_email="admin@example.com",
            reason="retention test",
            now_iso=NOW.isoformat(timespec="seconds"),
        )
    return StoredCurrent(public_id, content, delivery, metadata.artifact_id, backend)


def _add_delivery(
    db_path: Path,
    stored: StoredCurrent,
    *,
    public_id: str,
    delivered_at: dt.datetime = NOW,
) -> Delivery:
    policy = DeliveryPolicy(
        content_max_age=dt.timedelta(days=30),
        public_link_lifetime=dt.timedelta(days=365),
    )
    delivery = Delivery.issue(
        public_id=public_id,
        billing_bucket_id="bucket-retention",
        content=stored.content,
        delivered_at=delivered_at,
        policy=policy,
        reused_from_cache=True,
    )
    with storage_db.connect(db_path) as conn:
        delivery_store.mark_delivery_required(
            conn,
            public_id=public_id,
            required_at=delivered_at,
        )
        delivery_store.save_delivery(conn, delivery)
        artifact.bind_artifact_to_delivery(
            conn,
            delivery_id=delivery.delivery_id,
            artifact_id=stored.artifact_id,
        )
        delivery_store.mark_delivery_complete(
            conn,
            public_id=public_id,
            completed_at=delivered_at,
        )
        _insert_legacy_payload(conn, public_id)
    return delivery


def _configured(monkeypatch, data_root: Path) -> None:
    monkeypatch.setenv("APP_DATA_ROOT", str(data_root))
    monkeypatch.setenv("REPORT_ARTIFACT_CAPACITY_BYTES", str(CAPACITY_512_MIB))


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_current_last_reference_purges_payload_metadata_and_exact_pdf_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "retention.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="current-last")
    before = stored.backend.usage()
    assert before.used_bytes == len(PDF)
    assert before.remaining_bytes == CAPACITY_512_MIB - len(PDF)

    purged = report_retention_adapter.purge_expired_reports(
        _factory(db_path),
        now_iso=PURGE_AT.isoformat(timespec="seconds"),
    )

    assert purged == 1
    after = stored.backend.usage()
    assert after.used_bytes == 0
    assert after.remaining_bytes == CAPACITY_512_MIB
    with storage_db.connect(db_path) as conn:
        assert dashboard_store.trash_record(conn, stored.public_id).status == "purged"
        assert _count(conn, delivery_store.TABLE_DELIVERIES) == 0
        assert _count(conn, artifact.TABLE_DELIVERY_ARTIFACTS) == 0
        assert _count(conn, artifact.TABLE_ARTIFACTS) == 0
        assert _count(conn, delivery_store.TABLE_CONTENT_SNAPSHOTS) == 0
        assert _count(conn, storage_constants.TABLE_REPORTS) == 0
        assert retention.retired_public_id(conn, stored.public_id)
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is None
        assert dashboard_store.report_is_trashed(conn, stored.public_id)
        with pytest.raises(sqlite3.IntegrityError, match="retired public id"):
            delivery_store.mark_delivery_required(
                conn,
                public_id=stored.public_id,
                required_at=PURGE_AT + dt.timedelta(seconds=1),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                f"UPDATE {retention.TABLE_RETIREMENT_INTENTS} SET public_id='changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(f"DELETE FROM {retention.TABLE_RETIREMENT_EVENTS}")
        with pytest.raises(sqlite3.IntegrityError, match="permanent"):
            conn.execute(f"DELETE FROM {retention.TABLE_RETIRED_PUBLIC_IDS}")


def test_legacy_cleanup_entrypoint_refuses_to_mark_current_delivery_purged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "wrong-entrypoint.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="must-use-intent")
    with storage_db.connect(db_path) as conn:
        with pytest.raises(RuntimeError, match="retirement intent"):
            dashboard_store.purge_expired_trash(
                conn,
                now_iso=PURGE_AT.isoformat(timespec="seconds"),
            )
    with storage_db.connect(db_path) as conn:
        assert dashboard_store.trash_record(conn, stored.public_id).status == "trashed"
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is not None
    assert stored.backend.usage().used_bytes == len(PDF)


def test_daily_maintenance_uses_current_aware_cleanup_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "maintenance-current.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="maintenance-current")

    result = dashboard_maintenance.run_daily_cleanup(
        _factory(db_path),
        today=PURGE_AT.date(),
        now_iso=PURGE_AT.isoformat(timespec="seconds"),
        cleanup_runner=report_retention_adapter.purge_expired_reports,
    )

    assert result.status == "ok"
    assert result.purged_reports == 1
    assert stored.backend.usage().used_bytes == 0


def test_shared_artifact_survives_first_delivery_and_last_delivery_reclaims_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "shared.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    first = _store_current(db_path, data_root, public_id="shared-first")
    second_id = "shared-second"
    second = _add_delivery(db_path, first, public_id=second_id)

    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path), now_iso=PURGE_AT.isoformat(timespec="seconds")
    ) == 1
    assert first.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert delivery_store.load_delivery(conn, first.delivery.delivery_id) is None
        assert delivery_store.load_delivery(conn, second.delivery_id) is not None
        assert artifact.load_artifact_metadata(conn, first.artifact_id) is not None
        assert delivery_store.load_content_snapshot(conn, first.content.content_id) is not None
        assert dashboard_store.trash_report(
            conn,
            report_id=second_id,
            actor_email="admin@example.com",
            reason="last shared reference",
            now_iso=(NOW + dt.timedelta(days=1)).isoformat(timespec="seconds"),
        )

    second_purge = PURGE_AT + dt.timedelta(days=1)
    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path), now_iso=second_purge.isoformat(timespec="seconds")
    ) == 1
    assert first.backend.usage().used_bytes == 0
    with storage_db.connect(db_path) as conn:
        assert artifact.load_artifact_metadata(conn, first.artifact_id) is None
        assert delivery_store.load_content_snapshot(conn, first.content.content_id) is None


def test_active_cache_protects_shared_original_but_tombstone_blocks_old_url_revival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "cache.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="cache-owner")
    key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-retention",
        corp_id="CORP-RETENTION",
        namespace=_namespace(),
        preflight_identity_digest=_source().preflight_identity_digest,
        preflight_cache_usable=True,
        engine_epoch_digest=stored.content.engine_epoch_digest,
    )
    with storage_db.connect(db_path) as conn:
        delivery_store.bind_cache_entry(
            conn,
            key=key,
            content=stored.content,
            artifact_id=stored.artifact_id,
            cached_at=NOW,
        )

    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path), now_iso=PURGE_AT.isoformat(timespec="seconds")
    ) == 1
    assert stored.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert _count(conn, delivery_store.TABLE_CACHE_ENTRIES) == 1
        assert delivery_store.load_content_snapshot(conn, stored.content.content_id) is not None
        assert artifact.load_artifact_metadata(conn, stored.artifact_id) is not None
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is None
        assert dashboard_store.report_is_trashed(conn, stored.public_id)
        with pytest.raises(sqlite3.IntegrityError, match="retired public id"):
            delivery_store.mark_delivery_required(
                conn,
                public_id=stored.public_id,
                required_at=PURGE_AT + dt.timedelta(seconds=1),
            )
    with storage_db.connect(db_path) as conn:
        conn.execute(f"DELETE FROM {delivery_store.TABLE_CACHE_ENTRIES}")
    recovered = report_retention_adapter.reconcile_retirement_intents(
        _factory(db_path), now=PURGE_AT + dt.timedelta(seconds=2)
    )
    assert recovered.blobs_deleted == 1
    assert stored.backend.usage().used_bytes == 0
    with storage_db.connect(db_path) as conn:
        assert artifact.load_artifact_metadata(conn, stored.artifact_id) is None
        assert delivery_store.load_content_snapshot(conn, stored.content.content_id) is None


def test_other_artifact_with_same_blob_pointer_protects_until_its_own_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "same-pointer.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    first = _store_current(
        db_path,
        data_root,
        public_id="same-pointer-first",
        payload=b'{"private":"first-content"}',
    )
    second = _store_current(
        db_path,
        data_root,
        public_id="same-pointer-second",
        payload=b'{"private":"second-content"}',
    )
    assert first.artifact_id != second.artifact_id
    with storage_db.connect(db_path) as conn:
        assert dashboard_store.restore_report_from_trash(
            conn,
            report_id=second.public_id,
            actor_email="admin@example.com",
            reason="keep second active",
            now_iso=(NOW + dt.timedelta(hours=1)).isoformat(timespec="seconds"),
        )

    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path), now_iso=PURGE_AT.isoformat(timespec="seconds")
    ) == 1
    assert first.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert artifact.load_artifact_metadata(conn, first.artifact_id) is None
        assert artifact.load_artifact_metadata(conn, second.artifact_id) is not None
        assert dashboard_store.trash_report(
            conn,
            report_id=second.public_id,
            actor_email="admin@example.com",
            reason="second is now last",
            now_iso=(NOW + dt.timedelta(days=1)).isoformat(timespec="seconds"),
        )

    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path),
        now_iso=(PURGE_AT + dt.timedelta(days=1)).isoformat(timespec="seconds"),
    ) == 1
    assert first.backend.usage().used_bytes == 0


@pytest.mark.parametrize("protection", ["legal_hold", "active_write_intent"])
def test_legal_hold_and_active_write_intent_never_delete_referenced_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protection: str,
) -> None:
    db_path = tmp_path / f"{protection}.db"
    data_root = tmp_path / protection
    _configured(monkeypatch, data_root)
    stored = _store_current(
        db_path,
        data_root,
        public_id=f"protected-{protection}",
        legal_hold=protection == "legal_hold",
    )
    if protection == "active_write_intent":
        with storage_db.connect(db_path) as conn:
            artifact.create_blob_write_intent(
                conn,
                stored.backend,
                pdf_bytes=PDF,
                created_at=PURGE_AT - dt.timedelta(minutes=1),
            )

    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path), now_iso=PURGE_AT.isoformat(timespec="seconds")
    ) == 1
    assert stored.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is None
        if protection == "legal_hold":
            metadata = artifact.load_artifact_metadata(conn, stored.artifact_id)
            assert metadata is not None and metadata.retention.legal_hold
            assert delivery_store.load_content_snapshot(conn, stored.content.content_id) is not None
        else:
            assert artifact.load_artifact_metadata(conn, stored.artifact_id) is None
            assert delivery_store.load_content_snapshot(conn, stored.content.content_id) is None


def _prepare_and_retire_db_only(
    db_path: Path,
    stored: StoredCurrent,
    *,
    current: dt.datetime,
) -> retention.RetirementIntent:
    with storage_db.connect(db_path) as conn:
        intent = retention.prepare_retirement(
            conn,
            stored.backend,
            public_id=stored.public_id,
            eligible_at=PURGE_AT,
            created_at=current,
        )
    with storage_db.connect(db_path) as conn:
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        result = retention.retire_database_records(
            conn,
            stored.backend,
            intent=intent,
            retired_at=current,
        )
        assert result.event_type == retention.EVENT_DB_RETIRED
        assert dashboard_store.purge_expired_trash_item(
            conn,
            report_id=stored.public_id,
            now_iso=current.isoformat(timespec="seconds"),
        )
    return intent


def test_db_commit_then_crash_is_completed_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "db-crash.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="db-crash")
    intent = _prepare_and_retire_db_only(db_path, stored, current=PURGE_AT)
    assert stored.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert retention.latest_event(conn, intent.retirement_id) == retention.EVENT_DB_RETIRED
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is None

    recovered = report_retention_adapter.reconcile_retirement_intents(
        _factory(db_path), now=PURGE_AT + dt.timedelta(seconds=1)
    )
    assert recovered.blobs_deleted == 1
    assert recovered.reclaimed_bytes == len(PDF)
    assert stored.backend.usage().used_bytes == 0
    with storage_db.connect(db_path) as conn:
        assert retention.latest_event(conn, intent.retirement_id) == retention.EVENT_COMPLETED_DELETED


def test_file_delete_then_crash_is_idempotently_completed_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "file-crash.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="file-crash")
    intent = _prepare_and_retire_db_only(db_path, stored, current=PURGE_AT)

    class DeleteThenCrash(artifact.FilesystemArtifactBlobBackend):
        def delete_retired_if_exact(self, pointer):
            result = super().delete_retired_if_exact(pointer)
            assert result is artifact.OrphanDeleteResult.DELETED
            raise RuntimeError("파일 삭제 직후 프로세스 중단")

    crashing = DeleteThenCrash(data_root / "report-artifacts", capacity_bytes=CAPACITY_512_MIB)
    with pytest.raises(RuntimeError, match="프로세스 중단"):
        with storage_db.connect(db_path) as conn:
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            retention.reconcile_retired_blob(
                conn,
                crashing,
                intent=intent,
                reconciled_at=PURGE_AT + dt.timedelta(seconds=1),
            )
    assert stored.backend.usage().used_bytes == 0
    with storage_db.connect(db_path) as conn:
        assert retention.latest_event(conn, intent.retirement_id) == retention.EVENT_DB_RETIRED

    recovered = report_retention_adapter.reconcile_retirement_intents(
        _factory(db_path), now=PURGE_AT + dt.timedelta(seconds=2)
    )
    assert recovered.blobs_absent == 1
    with storage_db.connect(db_path) as conn:
        assert retention.latest_event(conn, intent.retirement_id) == retention.EVENT_COMPLETED_ABSENT


def test_symlink_or_corrupt_blob_is_never_automatically_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "corrupt.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="corrupt")
    intent = _prepare_and_retire_db_only(db_path, stored, current=PURGE_AT)
    pointer = intent.blob_pointer
    assert pointer is not None
    target = data_root / "report-artifacts" / pointer.key
    target.write_bytes(b"not-the-approved-pdf")

    report = report_retention_adapter.reconcile_retirement_intents(
        _factory(db_path), now=PURGE_AT + dt.timedelta(seconds=1)
    )
    assert report.blocked == 1
    assert target.read_bytes() == b"not-the-approved-pdf"
    with storage_db.connect(db_path) as conn:
        assert retention.latest_event(conn, intent.retirement_id) == retention.EVENT_BLOB_BLOCKED


def test_corrupt_content_provenance_blocks_all_automatic_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "corrupt-content.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="corrupt-content")
    with storage_db.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {delivery_store.TABLE_CONTENT_SNAPSHOTS} SET payload = ? "
            "WHERE content_id = ?",
            (b"tampered-private-payload", stored.content.content_id),
        )

    with pytest.raises(delivery_store.LifecycleStoreCorrupt, match="checksum"):
        report_retention_adapter.purge_expired_reports(
            _factory(db_path), now_iso=PURGE_AT.isoformat(timespec="seconds")
        )
    assert stored.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert dashboard_store.trash_record(conn, stored.public_id).status == "trashed"
        assert conn.execute(
            f"SELECT 1 FROM {delivery_store.TABLE_DELIVERIES} WHERE public_id = ?",
            (stored.public_id,),
        ).fetchone() is not None
        assert conn.execute(
            f"SELECT 1 FROM {artifact.TABLE_ARTIFACTS} WHERE artifact_id = ?",
            (stored.artifact_id,),
        ).fetchone() is not None


def test_symlink_blob_is_preserved_without_touching_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "symlink.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="symlink")
    intent = _prepare_and_retire_db_only(db_path, stored, current=PURGE_AT)
    pointer = intent.blob_pointer
    assert pointer is not None
    link = data_root / "report-artifacts" / pointer.key
    outside = tmp_path / "must-not-delete.pdf"
    outside.write_bytes(PDF)
    link.unlink()
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"이 Windows 실행 환경에서 symlink 생성이 허용되지 않음: {exc}")

    report = report_retention_adapter.reconcile_retirement_intents(
        _factory(db_path), now=PURGE_AT + dt.timedelta(seconds=1)
    )
    assert report.blocked == 1
    assert link.is_symlink()
    assert outside.read_bytes() == PDF


def test_changed_artifact_root_blocks_db_and_file_retirement_until_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "root-mismatch.db"
    original_root = tmp_path / "original-data"
    _configured(monkeypatch, original_root)
    stored = _store_current(db_path, original_root, public_id="root-mismatch")
    _configured(monkeypatch, tmp_path / "wrong-data")

    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path), now_iso=PURGE_AT.isoformat(timespec="seconds")
    ) == 0
    assert stored.backend.usage().used_bytes == len(PDF)
    with storage_db.connect(db_path) as conn:
        assert dashboard_store.trash_record(conn, stored.public_id).status == "trashed"
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is not None
        row = conn.execute(
            f"SELECT retirement_id FROM {retention.TABLE_RETIREMENT_INTENTS} "
            "WHERE public_id = ?",
            (stored.public_id,),
        ).fetchone()
        assert row is not None
        assert retention.latest_event(conn, str(row[0])) == retention.EVENT_PREPARE_BLOCKED

    _configured(monkeypatch, original_root)
    assert report_retention_adapter.purge_expired_reports(
        _factory(db_path),
        now_iso=(PURGE_AT + dt.timedelta(seconds=1)).isoformat(timespec="seconds"),
    ) == 1
    assert stored.backend.usage().used_bytes == 0


def test_legacy_only_cleanup_keeps_existing_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    with storage_db.connect(db_path) as conn:
        _insert_legacy_payload(conn, "legacy-only")
        assert dashboard_store.trash_report(
            conn,
            report_id="legacy-only",
            actor_email="admin@example.com",
            reason="legacy cleanup",
            now_iso=NOW.isoformat(timespec="seconds"),
        )
        assert dashboard_store.purge_expired_trash(
            conn,
            now_iso=PURGE_AT.isoformat(timespec="seconds"),
        ) == 1
        assert _count(conn, storage_constants.TABLE_REPORTS) == 0
        assert dashboard_store.trash_record(conn, "legacy-only").status == "purged"


def test_30_day_boundary_compares_actual_instants_not_timezone_text(tmp_path: Path) -> None:
    db_path = tmp_path / "timezone.db"
    with storage_db.connect(db_path) as conn:
        _insert_legacy_payload(conn, "timezone-boundary")
        assert dashboard_store.trash_report(
            conn,
            report_id="timezone-boundary",
            actor_email="admin@example.com",
            reason="timezone boundary",
            now_iso="2026-07-01T02:00:00+00:00",
        )
        assert dashboard_store.purge_expired_trash(
            conn,
            now_iso="2026-07-31T10:59:59+09:00",
        ) == 0
        assert dashboard_store.purge_expired_trash(
            conn,
            now_iso="2026-07-31T11:00:00+09:00",
        ) == 1


def test_old_purged_marker_with_current_rows_is_repaired_on_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "old-leak.db"
    data_root = tmp_path / "data"
    _configured(monkeypatch, data_root)
    stored = _store_current(db_path, data_root, public_id="old-purged-leak")
    with storage_db.connect(db_path) as conn:
        # 이전 구현의 실제 반쪽 상태: legacy만 지운 뒤 purged라고 표시.
        conn.execute(
            f"DELETE FROM {storage_constants.TABLE_REPORTS} WHERE report_id = ?",
            (stored.public_id,),
        )
        conn.execute(
            f"""
            UPDATE {dashboard_store.TABLE_REPORT_TRASH}
            SET status='purged', purged_at=? WHERE report_id=?
            """,
            (PURGE_AT.isoformat(timespec="seconds"), stored.public_id),
        )
    assert stored.backend.usage().used_bytes == len(PDF)

    recovered = report_retention_adapter.reconcile_retirement_intents(
        _factory(db_path),
        now=PURGE_AT + dt.timedelta(seconds=1),
        repair_previously_purged=True,
    )
    assert recovered.blobs_deleted == 1
    assert stored.backend.usage().used_bytes == 0
    with storage_db.connect(db_path) as conn:
        assert delivery_store.load_delivery_by_public_id(conn, stored.public_id) is None
        assert delivery_store.load_content_snapshot(conn, stored.content.content_id) is None


def test_product_policy_is_real_30_day_trash_policy_not_pending() -> None:
    source = Path(report_delivery_adapter.__file__).read_text(encoding="utf-8")
    assert "policy_id=delivery_retention.TRASH_RETENTION_POLICY_ID" in source
    assert "policy-pending-no-auto-delete-v1" not in source
