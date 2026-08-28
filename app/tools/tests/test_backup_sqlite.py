"""SQLite 장기 휴면 백업 도구 시험."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from src.features.backup import recovery_generation
from src.features.report_delivery.artifact import (
    ArtifactRetention,
    ArtifactVersion,
    FilesystemArtifactBlobBackend,
    OrphanDeleteResult,
    create_blob_write_intent,
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
from src.shared.bounded_file_lock import exclusive_file_lock

TOOL_PATH = Path(__file__).resolve().parents[1] / "backup_sqlite.py"
SPEC = importlib.util.spec_from_file_location("backup_sqlite", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
backup_sqlite = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup_sqlite
SPEC.loader.exec_module(backup_sqlite)


def _make_database(path: Path, value: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO records (value) VALUES (?)", (value,))
    conn.commit()
    return conn


def _make_database_with_artifact(
    path: Path,
    artifact_root: Path,
    *,
    pdf_bytes: bytes = b"%PDF-1.7\nimmutable approved bytes\n%%EOF\n",
):
    now = dt.datetime(2026, 8, 28, 3, 0, tzinfo=dt.timezone.utc)
    source = SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload={"status": "000", "list": [{"amount": "100"}]},
        captured_at=now,
        source_as_of=now.date(),
        adapter_versions={"dart": "2"},
    )
    namespace = CacheNamespace.create(
        product="company-analysis-v2",
        schema_version="v2",
        deployment_revision="test-revision",
        requested_models={"writer": "offline-test"},
        output_settings={"temperature": 0},
    )
    content = ContentSnapshot.create(
        payload=b'{"company":"backup-test","sections":[]}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
        actual_models=("offline-test",),
    )
    backend = FilesystemArtifactBlobBackend(artifact_root)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        save_source_snapshot(conn, source)
        save_cache_namespace(conn, namespace)
        save_content_snapshot(conn, content)
        intent = create_blob_write_intent(
            conn,
            backend,
            pdf_bytes=pdf_bytes,
            created_at=now,
        )
        conn.commit()  # blob보다 먼저 확정되는 실제 운영 경계
        metadata = store_approved_pdf(
            conn,
            backend,
            blob_intent=intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=pdf_bytes,
            version=ArtifactVersion(
                renderer_version="renderer-test",
                font_bundle_version="font-test",
                checker_version="checker-test",
            ),
            created_at=now,
            retention=ArtifactRetention(
                policy_id="report-30d",
                retain_until=now + dt.timedelta(days=30),
            ),
        )
        conn.commit()
    assert metadata.blob_pointer is not None
    return backend, metadata, pdf_bytes


def test_DB_기본경로는_명시경로가_데이터루트보다_우선한다(
    tmp_path: Path, monkeypatch
) -> None:
    data_root = tmp_path / "data-root"
    explicit = tmp_path / "custom" / "app.db"
    monkeypatch.setenv(backup_sqlite.ENV_DATA_ROOT, str(data_root))
    monkeypatch.setenv(backup_sqlite.ENV_DB_PATH, str(explicit))

    assert backup_sqlite.default_db_path() == explicit


def test_DB_명시경로가_없으면_영속_데이터루트를_쓴다(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(backup_sqlite.ENV_DB_PATH, raising=False)
    monkeypatch.setenv(backup_sqlite.ENV_DATA_ROOT, str(tmp_path))

    assert backup_sqlite.default_db_path() == tmp_path / "storage.db"


def test_열린_WAL_DB도_backup_API로_최신값을_담는다(tmp_path: Path) -> None:
    source = tmp_path / "storage.db"
    source_conn = _make_database(source, "첫 기록")
    try:
        source_conn.execute("INSERT INTO records (value) VALUES ('최신 기록')")
        source_conn.commit()

        result = backup_sqlite.create_backup(source, tmp_path / "private")

        assert backup_sqlite.verify_backup(result.backup_path) == result.sha256
        assert all(
            not Path(str(result.backup_path) + suffix).exists()
            for suffix in backup_sqlite.SQLITE_COMPANION_SUFFIXES
        )
        with backup_sqlite._standalone_readonly_connection(result.backup_path) as immutable:
            assert immutable.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
        with sqlite3.connect(result.backup_path) as backup_conn:
            assert backup_conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    finally:
        source_conn.close()


def test_DB와_checksum_게시후_부모directory를_성공전에_봉인한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "storage.db"
    output = tmp_path / "private"
    source_conn = _make_database(source, "directory durability")
    synced: list[Path] = []
    monkeypatch.setattr(
        backup_sqlite,
        "_fsync_directory",
        lambda path: synced.append(Path(path)),
    )
    try:
        result = backup_sqlite.create_backup(source, output)
    finally:
        source_conn.close()

    assert result.backup_path.is_file()
    assert result.checksum_path.is_file()
    assert synced == [output]


def test_파일이_바뀌면_체크섬_검증이_막는다(tmp_path: Path) -> None:
    source = tmp_path / "storage.db"
    source_conn = _make_database(source, "보존할 기록")
    try:
        result = backup_sqlite.create_backup(source, tmp_path / "private")
    finally:
        source_conn.close()

    with result.backup_path.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(backup_sqlite.BackupError, match="체크섬"):
        backup_sqlite.verify_backup(result.backup_path)


def test_손상파일에_맞춘_체크섬도_SQLite_무결성검사가_막는다(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged.sqlite3"
    damaged.write_bytes(b"not-a-sqlite-database")
    digest = backup_sqlite.sha256_file(damaged)
    backup_sqlite.checksum_path_for(damaged).write_text(
        f"{digest}  {damaged.name}\n",
        encoding="ascii",
    )

    with pytest.raises(backup_sqlite.BackupError, match="SQLite DB"):
        backup_sqlite.verify_backup(damaged)


def test_원문_공유열쇠가_있는_구형DB는_백업을_거부한다(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    raw_key = "ab" * 16
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE share_links ("
            "key TEXT PRIMARY KEY, company TEXT NOT NULL, job TEXT NOT NULL, "
            "report_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL, opened_count INTEGER NOT NULL DEFAULT 0, "
            "first_opened_at TEXT NOT NULL DEFAULT '', "
            "last_opened_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO share_links (key, company, job, created_at) "
            "VALUES (?, '회사', '직무', '2026-08-21T00:00:00+09:00')",
            (raw_key,),
        )

    with pytest.raises(backup_sqlite.BackupError, match="원문 열쇠"):
        backup_sqlite.create_backup(source, tmp_path / "external")


def test_해시_공유열쇠_백업에는_URL원문이_없다(tmp_path: Path) -> None:
    source = tmp_path / "hashed.db"
    raw_key = "cd" * 16
    key_hash = hashlib.sha256(raw_key.encode("ascii")).hexdigest()
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE share_links ("
            "key_hash TEXT PRIMARY KEY, company TEXT NOT NULL, job TEXT NOT NULL, "
            "report_id TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', "
            "created_at TEXT NOT NULL, opened_count INTEGER NOT NULL DEFAULT 0, "
            "first_opened_at TEXT NOT NULL DEFAULT '', "
            "last_opened_at TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO share_links (key_hash, company, job, created_at) "
            "VALUES (?, '회사', '직무', '2026-08-21T00:00:00+09:00')",
            (key_hash,),
        )

    result = backup_sqlite.create_backup(source, tmp_path / "external")

    assert backup_sqlite.verify_backup(result.backup_path) == result.sha256
    with sqlite3.connect(result.backup_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(share_links)")}
        stored = conn.execute("SELECT key_hash FROM share_links").fetchone()[0]
    assert columns == {
        "key_hash",
        "company",
        "job",
        "report_id",
        "note",
        "created_at",
        "opened_count",
        "first_opened_at",
        "last_opened_at",
    }
    assert stored == key_hash
    assert raw_key.encode("ascii") not in result.backup_path.read_bytes()


def test_공개복구API는_manifest_gate없이_대상파일을_전혀_만들지않는다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    source_conn = _make_database(source, "복구할 기록")
    try:
        backup = backup_sqlite.create_backup(source, tmp_path / "external")
    finally:
        source_conn.close()
    target.write_bytes(b"existing database bytes")
    original_bytes = target.read_bytes()
    with pytest.raises(backup_sqlite.BackupError, match="독립 서명 manifest gate"):
        backup_sqlite.restore_backup(backup.backup_path, target)
    assert target.read_bytes() == original_bytes

    absent_target = tmp_path / "absent-parent" / "restored.db"
    with pytest.raises(backup_sqlite.BackupError, match="독립 서명 manifest gate"):
        backup_sqlite.restore_backup(backup.backup_path, absent_target)
    assert not absent_target.parent.exists()


def test_restore_CLI도_manifest_wrapper없이_fail_closed한다(
    tmp_path: Path, capsys
) -> None:
    target = tmp_path / "must-not-exist.db"
    result = backup_sqlite.main(
        ["restore", str(tmp_path / "attacker.sqlite3"), "--target", str(target)]
    )

    assert result == 1
    assert not target.exists()
    captured = capsys.readouterr()
    assert "독립 서명 manifest gate" in captured.err
    assert "attacker.sqlite3" not in captured.err


def test_임시_WAL_DB의_백업_SHA256과_전체데이터가_일치한다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE records ("
            "id INTEGER PRIMARY KEY, value TEXT NOT NULL, payload BLOB NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO records (value, payload) VALUES (?, ?)",
            [
                ("첫 기록", b"\x00\x01"),
                ("한글·특수문자 !@#", bytes(range(32))),
                ("최신 WAL 기록", b"latest"),
            ],
        )
        conn.commit()
        expected = conn.execute(
            "SELECT id, value, payload FROM records ORDER BY id"
        ).fetchall()

        backup = backup_sqlite.create_backup(source, tmp_path / "backups")

    assert backup_sqlite.verify_backup(backup.backup_path) == backup.sha256
    assert backup.sha256 == hashlib.sha256(backup.backup_path.read_bytes()).hexdigest()

    with sqlite3.connect(
        backup.backup_path.resolve().as_uri() + "?mode=ro", uri=True
    ) as conn:
        actual = conn.execute(
            "SELECT id, value, payload FROM records ORDER BY id"
        ).fetchall()
    assert actual == expected


def test_verify_validation중_sidecar가_생겨도_성공하지않는다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    source_conn = _make_database(source, "검증 경합")
    try:
        backup = backup_sqlite.create_backup(source, tmp_path / "backups")
    finally:
        source_conn.close()
    original = backup_sqlite._assert_database_is_valid

    def inject_sidecar(path: Path) -> None:
        original(path)
        Path(str(path) + "-wal").touch()

    monkeypatch.setattr(backup_sqlite, "_assert_database_is_valid", inject_sidecar)
    with pytest.raises(backup_sqlite.BackupError, match="sidecar"):
        backup_sqlite.verify_backup(backup.backup_path)


def test_checksum게시중_sidecar가_생기면_backup성공과산출물게시를취소한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.db"
    source_conn = _make_database(source, "게시 경합")
    output = tmp_path / "backups"
    original = backup_sqlite._write_checksum

    def inject_sidecar(path: Path, digest: str, database_name: str) -> None:
        original(path, digest, database_name)
        database = path.with_name(database_name)
        Path(str(database) + "-wal").touch()

    monkeypatch.setattr(backup_sqlite, "_write_checksum", inject_sidecar)
    try:
        with pytest.raises(backup_sqlite.BackupError, match="sidecar"):
            backup_sqlite.create_backup(source, output)
    finally:
        source_conn.close()

    assert not list(output.glob("*.sqlite3"))
    assert not list(output.glob("*.sha256"))
    assert not list(output.glob("*.sqlite3-wal"))
    assert not list(output.glob("*.sqlite3-shm"))
    assert not list(output.glob("*.sqlite3-journal"))


def test_실패산출물정리는_정확한_DB가족만_지운다(tmp_path: Path) -> None:
    database = tmp_path / "backup.sqlite3"
    checksum = backup_sqlite.checksum_path_for(database)
    exact_artifacts = [
        database,
        checksum,
        *(
            Path(str(database) + suffix)
            for suffix in backup_sqlite.SQLITE_COMPANION_SUFFIXES
        ),
    ]
    for artifact in exact_artifacts:
        artifact.write_bytes(b"artifact")

    similarly_named = [
        tmp_path / "backup.sqlite3-wal.old",
        tmp_path / "backup.sqlite3-other",
        tmp_path / "backup.sqlite3.sha256.old",
    ]
    for artifact in similarly_named:
        artifact.write_bytes(b"keep")

    backup_sqlite._remove_backup_artifacts(database, checksum)

    assert all(not artifact.exists() for artifact in exact_artifacts)
    assert all(artifact.read_bytes() == b"keep" for artifact in similarly_named)


def test_복구세대는_DB_snapshot과_정확한_PDF_bytes를_함께_봉인한다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _backend, metadata, pdf_bytes = _make_database_with_artifact(
        source, artifact_root
    )

    result = backup_sqlite.create_recovery_generation(
        source,
        tmp_path / "backups",
        artifact_root=artifact_root,
    )
    verified = backup_sqlite.verify_recovery_generation(result.generation_path)

    assert verified.generation_id == result.generation_id
    assert verified.database_sha256 == result.database_sha256
    assert verified.artifact_count == 1
    assert verified.artifact_bytes == len(pdf_bytes)
    pointer = metadata.blob_pointer
    assert pointer is not None
    copied = result.generation_path / "a" / f"{pointer.sha256}.blob"
    assert copied.read_bytes() == pdf_bytes
    assert result.manifest_path.is_file()
    assert not any(
        path.name.endswith(("-wal", "-shm", "-journal"))
        for path in result.generation_path.rglob("*")
    )


def test_복구세대_restore차단은_기존대상과_세대원본을_전혀_바꾸지않는다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    result = backup_sqlite.create_recovery_generation(
        source,
        tmp_path / "backups",
        artifact_root=artifact_root,
    )
    generation_before = {
        path.relative_to(result.generation_path).as_posix(): path.read_bytes()
        for path in result.generation_path.rglob("*")
        if path.is_file()
    }
    source_before = source.read_bytes()
    target = tmp_path / "existing-target.db"
    target.write_bytes(b"must remain exactly unchanged")
    target_before = target.read_bytes()

    with pytest.raises(backup_sqlite.BackupError, match="독립 서명 manifest gate"):
        backup_sqlite.restore_backup(result.generation_path, target)

    absent_target = tmp_path / "absent-parent" / "restored.db"
    with pytest.raises(backup_sqlite.BackupError, match="독립 서명 manifest gate"):
        backup_sqlite.restore_backup(result.generation_path, absent_target)

    assert target.read_bytes() == target_before
    assert not absent_target.parent.exists()
    assert source.read_bytes() == source_before
    assert {
        path.relative_to(result.generation_path).as_posix(): path.read_bytes()
        for path in result.generation_path.rglob("*")
        if path.is_file()
    } == generation_before
    assert backup_sqlite.verify_recovery_generation(result.generation_path)


def test_artifact참조_DB는_DB한파일_백업과_검증을_성공시키지않는다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    output = tmp_path / "db-only"

    with pytest.raises(backup_sqlite.BackupError, match="DB 한 파일|DB-only|불완전"):
        backup_sqlite.create_backup(source, output)

    assert not list(output.glob("*.sqlite3"))
    assert not list(output.glob("*.sha256"))

    # 공격자가 SQLite Backup API로 DB만 따로 복사하고 checksum을 다시 만들어도 막는다.
    standalone = tmp_path / "attacker.sqlite3"
    with sqlite3.connect(source) as live, sqlite3.connect(standalone) as copied:
        live.backup(copied)
    digest = backup_sqlite.sha256_file(standalone)
    backup_sqlite.checksum_path_for(standalone).write_text(
        f"{digest}  {standalone.name}\n", encoding="ascii"
    )
    with pytest.raises(backup_sqlite.BackupError, match="DB 한 파일|DB-only|불완전"):
        backup_sqlite.verify_backup(standalone)


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_누락되거나_손상된_PDF가_있으면_부분_복구세대를_게시하지않는다(
    tmp_path: Path,
    failure: str,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _backend, metadata, _pdf_bytes = _make_database_with_artifact(
        source, artifact_root
    )
    pointer = metadata.blob_pointer
    assert pointer is not None
    blob = artifact_root / Path(pointer.key)
    if failure == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"different bytes")
    output = tmp_path / "backups"

    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.create_recovery_generation(
            source,
            output,
            artifact_root=artifact_root,
        )

    assert list(output.iterdir()) == []


def test_한_artifact에_서로다른_root가_결속되면_정본을_추측해_백업하지않는다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    backend, metadata, _pdf_bytes = _make_database_with_artifact(
        source,
        artifact_root,
    )
    pointer = metadata.blob_pointer
    assert pointer is not None
    with sqlite3.connect(source) as conn:
        conn.execute(
            "INSERT INTO artifact_blob_intents "
            "(intent_id, storage_identity, blob_key, bytes_sha256, "
            "byte_length, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "ambiguous-second-root",
                backend.storage_identity + "-different-root",
                pointer.key,
                pointer.sha256,
                pointer.byte_length,
                "2026-08-28T03:01:00.000000Z",
            ),
        )
        conn.execute(
            "INSERT INTO artifact_blob_intent_events "
            "(intent_id, event_type, artifact_id, recorded_at) "
            "VALUES (?, 'created', '', ?)",
            ("ambiguous-second-root", "2026-08-28T03:01:00.000000Z"),
        )
        conn.execute(
            "INSERT INTO artifact_blob_intent_events "
            "(intent_id, event_type, artifact_id, recorded_at) "
            "VALUES (?, 'bound', ?, ?)",
            (
                "ambiguous-second-root",
                metadata.artifact_id,
                "2026-08-28T03:01:01.000000Z",
            ),
        )

    output = tmp_path / "backups"
    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.create_recovery_generation(
            source,
            output,
            artifact_root=artifact_root,
        )
    assert list(output.iterdir()) == []


def test_manifest를_공격자가_다시써도_DB가_가리키는_PDF를_생략할수없다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    result = backup_sqlite.create_recovery_generation(
        source,
        tmp_path / "backups",
        artifact_root=artifact_root,
    )
    manifest = result.generation_path / recovery_generation.MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    omitted = payload["artifacts"].pop()
    identity_payload = dict(payload)
    identity_payload.pop("generation_id")
    payload["generation_id"] = recovery_generation._generation_identity(  # noqa: SLF001
        identity_payload
    )
    manifest_bytes = recovery_generation._canonical_json(payload) + b"\n"  # noqa: SLF001
    manifest.write_bytes(manifest_bytes)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    (result.generation_path / recovery_generation.MANIFEST_CHECKSUM_NAME).write_text(
        f"{manifest_digest}  {recovery_generation.MANIFEST_NAME}\n",
        encoding="ascii",
    )
    omitted_path = result.generation_path / Path(str(omitted["path"]))
    omitted_path.unlink()
    for parent in list(omitted_path.parents):
        if parent == result.generation_path:
            break
        try:
            parent.rmdir()
        except OSError:
            break

    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.verify_recovery_generation(result.generation_path)


def test_manifest밖의_추가파일과_hardlink는_복구_dry_run을_막는다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    result = backup_sqlite.create_recovery_generation(
        source,
        tmp_path / "backups",
        artifact_root=artifact_root,
    )
    extra = result.generation_path / "unlisted.secret"
    extra.write_text("not in manifest", encoding="utf-8")
    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.verify_recovery_generation(result.generation_path)
    extra.unlink()

    hardlink = result.generation_path / "database-hardlink"
    os.link(result.database_path, hardlink)
    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.verify_recovery_generation(result.generation_path)


def test_artifact경로_변조와_symlink를_따라가지않는다(tmp_path: Path) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "UPDATE report_delivery_artifacts SET blob_key = '../outside.pdf'"
        )
        conn.commit()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"must never be copied")

    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.create_recovery_generation(
            source,
            tmp_path / "backups",
            artifact_root=artifact_root,
        )
    assert outside.read_bytes() == b"must never be copied"


def test_artifact_copy중_retention이_DB와_root_lock을_잡아도_deadlock없다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    backend, metadata, _pdf_bytes = _make_database_with_artifact(
        source, artifact_root
    )
    pointer = metadata.blob_pointer
    assert pointer is not None
    copy_holds_root = threading.Event()
    allow_copy = threading.Event()
    retention_has_db = threading.Event()
    retention_done = threading.Event()
    original_copy = recovery_generation._copy_exact_blob  # noqa: SLF001

    def paused_copy(source_path, destination, reference) -> None:
        copy_holds_root.set()
        assert allow_copy.wait(timeout=5)
        original_copy(source_path, destination, reference)

    monkeypatch.setattr(recovery_generation, "_copy_exact_blob", paused_copy)
    backup_result: list[object] = []
    backup_error: list[BaseException] = []

    def run_backup() -> None:
        try:
            backup_result.append(
                backup_sqlite.create_recovery_generation(
                    source,
                    tmp_path / "backups",
                    artifact_root=artifact_root,
                )
            )
        except BaseException as exc:  # pragma: no cover - assertion reports detail
            backup_error.append(exc)

    def run_retention() -> None:
        with sqlite3.connect(source, timeout=5) as conn:
            conn.execute("BEGIN IMMEDIATE")
            retention_has_db.set()
            outcome = backend.delete_retired_if_exact(pointer)
            assert outcome is OrphanDeleteResult.DELETED
            conn.rollback()
        retention_done.set()

    backup_thread = threading.Thread(target=run_backup, daemon=True)
    backup_thread.start()
    assert copy_holds_root.wait(timeout=5)
    retention_thread = threading.Thread(target=run_retention, daemon=True)
    retention_thread.start()
    assert retention_has_db.wait(timeout=5)
    time.sleep(0.1)
    assert not retention_done.is_set()  # root lock에서 기다리되 DB 역잠금은 없다.
    allow_copy.set()
    backup_thread.join(timeout=10)
    retention_thread.join(timeout=10)

    assert not backup_thread.is_alive()
    assert not retention_thread.is_alive()
    assert backup_error == []
    assert len(backup_result) == 1
    generation = backup_result[0]
    assert isinstance(generation, backup_sqlite.RecoveryGenerationResult)
    assert backup_sqlite.verify_recovery_generation(generation.generation_path)


def test_생성중_crash는_manifest없는_부분세대를_게시하지않는다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    output = tmp_path / "backups"

    def crash_before_manifest(**_kwargs):
        raise recovery_generation.RecoveryGenerationError("simulated crash")

    monkeypatch.setattr(recovery_generation, "build_manifest", crash_before_manifest)
    with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
        backup_sqlite.create_recovery_generation(
            source,
            output,
            artifact_root=artifact_root,
        )

    assert list(output.iterdir()) == []


def test_백업도_artifact_root_lock을_무기한_기다리지않는다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "storage.db"
    artifact_root = tmp_path / "report-artifacts"
    _make_database_with_artifact(source, artifact_root)
    output = tmp_path / "backups"
    monkeypatch.setattr(
        recovery_generation,
        "ARTIFACT_ROOT_LOCK_TIMEOUT_SECONDS",
        0.2,
    )

    with exclusive_file_lock(
        artifact_root / ".artifact-root.lock",
        timeout_seconds=1,
    ):
        started = time.monotonic()
        with pytest.raises(backup_sqlite.BackupError, match="복구 세대"):
            backup_sqlite.create_recovery_generation(
                source,
                output,
                artifact_root=artifact_root,
            )
        elapsed = time.monotonic() - started

    assert 0.15 <= elapsed < 1.5
    assert list(output.iterdir()) == []
