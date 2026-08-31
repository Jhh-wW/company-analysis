from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path

import pytest

from src.features.report_delivery.artifact import (
    ArtifactBlobIntent,
    ArtifactError,
    ArtifactRetention,
    ArtifactVersion,
    FilesystemArtifactBlobBackend,
    TABLE_ARTIFACTS,
    TABLE_BLOB_INTENTS,
    TABLE_BLOB_INTENT_EVENTS,
    create_blob_write_intent,
    ensure_artifact_schema,
    reconcile_blob_write_intents,
    store_approved_pdf,
)
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.models import ContentSnapshot
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.report_delivery.store import (
    save_cache_namespace,
    save_content_snapshot,
    save_source_snapshot,
)


_PDF = b"%PDF-1.4\n% crash-recovery approved bytes\n%%EOF\n"


def _connect(path: Path, *, timeout: float = 2.0) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _content(path: Path, now: dt.datetime) -> ContentSnapshot:
    source = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload={"status": "000", "list": [{"value": "1"}]},
        captured_at=now,
        source_as_of=now.date(),
        adapter_versions={"test": "intent-v1"},
    )
    namespace = CacheNamespace.create(
        product="artifact-intent-test",
        schema_version="v2",
        deployment_revision="intent-test-commit",
        requested_models={"pipeline": "offline-test"},
        output_settings={"temperature": 0},
    )
    content = ContentSnapshot.create(
        payload=b'{"company":"intent-test"}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
        engine_epoch_digest="a" * 64,
        actual_models=("offline-test",),
    )
    with _connect(path) as conn:
        ensure_artifact_schema(conn)
        save_source_snapshot(conn, source)
        save_cache_namespace(conn, namespace)
        save_content_snapshot(conn, content)
    return content


def _intent(
    path: Path,
    backend: FilesystemArtifactBlobBackend,
    *,
    created_at: dt.datetime,
    data: bytes = _PDF,
) -> ArtifactBlobIntent:
    # 본 출고 transaction과 다른 연결이 commit된 뒤 intent를 반환한다.
    with _connect(path) as conn:
        intent = create_blob_write_intent(
            conn,
            backend,
            pdf_bytes=data,
            created_at=created_at,
        )
    return intent


def _version() -> ArtifactVersion:
    return ArtifactVersion(
        renderer_version="renderer-intent-v1",
        font_bundle_version="fonts-intent-v1",
        checker_version="checker-intent-v1",
    )


def _store(
    path: Path,
    backend: FilesystemArtifactBlobBackend,
    content: ContentSnapshot,
    intent: ArtifactBlobIntent,
    *,
    created_at: dt.datetime,
) -> str:
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        metadata = store_approved_pdf(
            conn,
            backend,
            blob_intent=intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=_PDF,
            version=_version(),
            created_at=created_at,
            retention=ArtifactRetention(policy_id="no-auto-delete", retain_until=None),
        )
    return metadata.artifact_id


def _reconcile(
    path: Path,
    backend: FilesystemArtifactBlobBackend,
    *,
    now: dt.datetime,
):
    with _connect(path) as conn:
        ensure_artifact_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        return reconcile_blob_write_intents(
            conn,
            backend,
            now=now,
            grace=dt.timedelta(hours=24),
        )


def _latest_event(path: Path, intent_id: str) -> tuple[str, str]:
    with _connect(path) as conn:
        row = conn.execute(
            f"""
            SELECT event_type, artifact_id FROM {TABLE_BLOB_INTENT_EVENTS}
            WHERE intent_id = ? ORDER BY event_id DESC LIMIT 1
            """,
            (intent_id,),
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def test_큰transaction_rollback은_별도commit_intent를_남기고_grace후_blob만_회수한다(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.timezone.utc)
    db_path = tmp_path / "delivery.db"
    root = tmp_path / "artifacts"
    backend = FilesystemArtifactBlobBackend(root)
    content = _content(db_path, now)
    intent = _intent(db_path, backend, created_at=now)

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        store_approved_pdf(
            conn,
            backend,
            blob_intent=intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=_PDF,
            version=_version(),
            created_at=now,
            retention=ArtifactRetention(policy_id="no-auto-delete", retain_until=None),
        )
        # release/charge 뒤 단계의 장애를 흉낸 본 transaction rollback.
        conn.rollback()
    finally:
        conn.close()

    blob = root / intent.pointer.key
    assert blob.read_bytes() == _PDF
    assert _latest_event(db_path, intent.intent_id) == ("created", "")
    with _connect(db_path) as check:
        assert check.execute(f"SELECT COUNT(*) FROM {TABLE_ARTIFACTS}").fetchone()[0] == 0

    report = _reconcile(
        db_path,
        backend,
        now=now + dt.timedelta(hours=24, microseconds=1),
    )

    assert report.deleted == 1
    assert not blob.exists()
    assert backend.usage().used_bytes == 0
    assert _latest_event(db_path, intent.intent_id) == ("reclaimed_deleted", "")


def test_아직grace인_같은pointer_intent가_있으면_오래된intent도_지우지_않는다(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)
    db_path = tmp_path / "delivery.db"
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifacts")
    _content(db_path, now)
    old = _intent(db_path, backend, created_at=now)
    backend.put_immutable(sha256=old.pointer.sha256, data=_PDF)
    active = _intent(
        db_path,
        backend,
        created_at=now + dt.timedelta(hours=30),
    )

    report = _reconcile(
        db_path,
        backend,
        now=now + dt.timedelta(hours=48),
    )

    assert report.kept_active == 1
    assert (tmp_path / "artifacts" / old.pointer.key).exists()
    assert _latest_event(db_path, old.intent_id) == ("created", "")
    assert _latest_event(db_path, active.intent_id) == ("created", "")


def test_다른artifact가_같은pointer를_참조하면_삭제하지_않고_bound로_복구한다(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)
    db_path = tmp_path / "delivery.db"
    root = tmp_path / "artifacts"
    backend = FilesystemArtifactBlobBackend(root)
    content = _content(db_path, now)
    owner_intent = _intent(db_path, backend, created_at=now)
    artifact_id = _store(
        db_path,
        backend,
        content,
        owner_intent,
        created_at=now,
    )
    orphan_looking_intent = _intent(db_path, backend, created_at=now)

    report = _reconcile(
        db_path,
        backend,
        now=now + dt.timedelta(days=2),
    )

    assert report.bound_existing == 1
    assert (root / owner_intent.pointer.key).read_bytes() == _PDF
    assert _latest_event(db_path, orphan_looking_intent.intent_id) == (
        "bound",
        artifact_id,
    )


def test_pointer_hash_lengthroot_불일치는_삭제와_저장을_모두_막는다(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)
    db_path = tmp_path / "delivery.db"
    root = tmp_path / "artifacts"
    first_backend = FilesystemArtifactBlobBackend(root)
    other_backend = FilesystemArtifactBlobBackend(tmp_path / "other-root")
    content = _content(db_path, now)
    intent = _intent(db_path, first_backend, created_at=now)

    with _connect(db_path) as conn:
        with pytest.raises(ArtifactError, match="root·경로·hash·길이"):
            store_approved_pdf(
                conn,
                other_backend,
                blob_intent=intent,
                content_snapshot_id=content.content_id,
                pdf_bytes=_PDF,
                version=_version(),
                created_at=now,
                retention=ArtifactRetention(
                    policy_id="no-auto-delete",
                    retain_until=None,
                ),
            )

    first_backend.put_immutable(sha256=intent.pointer.sha256, data=_PDF)
    blob = root / intent.pointer.key
    blob.write_bytes(b"corrupt-but-never-auto-delete")
    report = _reconcile(
        db_path,
        first_backend,
        now=now + dt.timedelta(days=2),
    )
    assert report.kept_mismatch == 1
    assert blob.read_bytes() == b"corrupt-but-never-auto-delete"


def test_삭제직전_다른writer는_DB잠금뒤에_새intent로_다시결속한다(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)
    reconcile_at = now + dt.timedelta(days=2)
    db_path = tmp_path / "delivery.db"
    root = tmp_path / "artifacts"
    delete_entered = threading.Event()
    writer_attempting_db = threading.Event()

    class SignalingBackend(FilesystemArtifactBlobBackend):
        def delete_orphan_if_exact(self, pointer):
            # reconcile의 마지막 DB 재조회 뒤, 물리 삭제 직전 지점.
            delete_entered.set()
            assert writer_attempting_db.wait(timeout=2)
            return super().delete_orphan_if_exact(pointer)

    backend = SignalingBackend(root)
    content = _content(db_path, now)
    old = _intent(db_path, backend, created_at=now)
    backend.put_immutable(sha256=old.pointer.sha256, data=_PDF)
    writer_errors: list[BaseException] = []
    writer_intents: list[ArtifactBlobIntent] = []

    def writer() -> None:
        try:
            assert delete_entered.wait(timeout=2)
            writer_attempting_db.set()
            # reconcile가 잡은 BEGIN IMMEDIATE 뒤에서만 이 쓰기가 성공한다.
            intent = _intent(db_path, backend, created_at=reconcile_at)
            writer_intents.append(intent)
            _store(
                db_path,
                backend,
                content,
                intent,
                created_at=reconcile_at,
            )
        except BaseException as exc:  # pragma: no cover - 메인 thread가 검증
            writer_errors.append(exc)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    report = _reconcile(db_path, backend, now=reconcile_at)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert writer_errors == []
    assert report.deleted == 1
    assert len(writer_intents) == 1
    assert _latest_event(db_path, writer_intents[0].intent_id)[0] == "bound"
    assert (root / old.pointer.key).read_bytes() == _PDF
    with _connect(db_path) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {TABLE_ARTIFACTS}").fetchone()[0] == 1


def test_intent와_event는_update_delete할_수_없는_append_only다(
    tmp_path: Path,
) -> None:
    now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)
    db_path = tmp_path / "delivery.db"
    backend = FilesystemArtifactBlobBackend(tmp_path / "artifacts")
    _content(db_path, now)
    intent = _intent(db_path, backend, created_at=now)

    with _connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="invalid.*transition"):
            conn.execute(
                f"""
                INSERT INTO {TABLE_BLOB_INTENT_EVENTS}
                    (intent_id, event_type, artifact_id, recorded_at)
                VALUES (?, 'bound', 'artifact-not-real', ?)
                """,
                (intent.intent_id, now.isoformat()),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                f"UPDATE {TABLE_BLOB_INTENTS} SET blob_key = 'changed' WHERE intent_id = ?",
                (intent.intent_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                f"DELETE FROM {TABLE_BLOB_INTENT_EVENTS} WHERE intent_id = ?",
                (intent.intent_id,),
            )
