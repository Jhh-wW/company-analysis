from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import release_readiness as readiness
from ops import backup_manifest as manifest


SCHEMA = """
CREATE TABLE reports (report_id TEXT PRIMARY KEY);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY);
CREATE TABLE share_links (key_hash TEXT PRIMARY KEY);
CREATE TABLE share_link_open_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_key_hash TEXT NOT NULL,
    opened_at TEXT NOT NULL
);
CREATE TABLE share_link_run_history (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL
);
CREATE TABLE budget_spend_events (id INTEGER PRIMARY KEY);
CREATE TABLE budget_spend_inflight (run_id TEXT PRIMARY KEY);
CREATE TABLE job_interruptions (job_id TEXT PRIMARY KEY);
CREATE TABLE observability_run_lifecycle (
    run_id TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
CREATE TABLE dashboard_service_state (
    singleton INTEGER PRIMARY KEY,
    status TEXT NOT NULL
);
CREATE TABLE dashboard_member_usage (
    run_id TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
CREATE TABLE dashboard_operation_claims (
    operation_key TEXT PRIMARY KEY,
    status TEXT NOT NULL
);
CREATE TABLE notion_export_operations (
    operation_key TEXT PRIMARY KEY,
    state TEXT NOT NULL
);
"""

NOW = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)
TEST_SIGNING_KEY = b"local-test-manifest-signing-key-32-bytes"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sidecar(database: Path, digest: str | None = None, *, name: str | None = None) -> Path:
    checksum = database.with_name(database.name + ".sha256")
    checksum.write_text(
        f"{digest or _digest(database)}  {name or database.name}\n",
        encoding="ascii",
    )
    return checksum


def _create_database(path: Path, *, service_state: str = "maintenance") -> Path:
    """preflight와 거부 회귀에 쓰는 과거 최소 모양 fixture."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO dashboard_service_state VALUES (1, ?)",
            (service_state,),
        )
    return path


def _create_storage_backup(tmp_path: Path) -> tuple[Path, Path]:
    """실제 앱 storage bootstrap으로 원본을 만들고 Backup API 사본을 만든다."""

    storage_db = readiness._load_storage_db_module()  # noqa: SLF001
    from tools import backup_sqlite  # noqa: PLC0415

    source = tmp_path / "runtime-source" / "storage.db"
    with storage_db.connect(source) as connection:
        connection.execute(
            "INSERT INTO alias_cache (alias_key, corp_id, created_at) "
            "VALUES ('ops-ready', 'corp-ready', '2026-08-23T00:00:00+00:00')"
        )
    backup = backup_sqlite.create_backup(source, tmp_path / "data")
    return backup.backup_path, backup.checksum_path


def _manifest_bundle(
    tmp_path: Path,
    database: Path,
    checksum: Path,
    *,
    backup_id: str = "backup-current",
):
    control = tmp_path / "independent-control"
    control.mkdir(exist_ok=True)
    boundary = manifest.ManifestBoundary(
        boundary_id="manifest-boundary",
        authority_id="manifest-security-owner",
        retention_days=35,
        append_only=True,
        signed=True,
        conditional_append=True,
        production_ready=False,
    )
    signer = manifest.HMACManifestSigner(
        key_id="test-key-v1",
        key=TEST_SIGNING_KEY,
    )
    sink = manifest.LocalAppendOnlyManifestSink(
        control / "backup-manifest.jsonl",
        boundary=boundary,
    )
    ledger = manifest.ManifestLedger(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
        allow_test_sink=True,
    )
    record = ledger.append_backup(
        scope="storage-db",
        backup_id=backup_id,
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key=f"company-analysis/{database.name}",
        checksum_key=f"company-analysis/{database.name}.sha256",
        database_name=database.name,
        database_sha256=_digest(database),
        database_size_bytes=database.stat().st_size,
        checksum_sha256=_digest(checksum),
        created_at=NOW,
        data_boundary_id="backup-data-boundary",
        data_authority_id="backup-data-writer",
    )
    gate = manifest.IndependentManifestGate(
        sink=sink,
        signer=signer,
        minimum_retention_days=35,
        allow_test_sink=True,
    )
    expectation = manifest.ManifestExpectation(
        backup_id=backup_id,
        scope="storage-db",
        storage_provider="s3",
        storage_bucket="private-backups",
        object_key=f"company-analysis/{database.name}",
        checksum_key=f"company-analysis/{database.name}.sha256",
        data_boundary_id="backup-data-boundary",
        data_authority_id="backup-data-writer",
        minimum_sequence=record.sequence,
        now=NOW + timedelta(minutes=1),
    )
    return sink, gate, expectation


def test_actual_storage_bootstrap_backup_passes_manifest_gate(tmp_path: Path) -> None:
    database, checksum = _create_storage_backup(tmp_path)
    expected = _digest(database)
    _sink, gate, expectation = _manifest_bundle(tmp_path, database, checksum)

    result = readiness.verify_backup(
        database,
        checksum,
        expected,
        manifest_gate=gate,
        manifest_expectation=expectation,
        manifest_data_root=database.parent,
    )

    assert result["status"] == "통과"
    assert result["sha256"] == expected
    assert result["manifest_sequence"] == 1
    assert int(result["table_count"]) > 10
    assert int(result["index_count"]) > 0
    assert int(result["trigger_count"]) > 0
    assert _digest(database) == expected


def test_minimal_named_tables_fail_actual_storage_bootstrap(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "data" / "minimal.sqlite3")
    checksum = _write_sidecar(database)
    original_digest = _digest(database)
    _sink, gate, expectation = _manifest_bundle(tmp_path, database, checksum)

    with pytest.raises(readiness.ReadinessError, match="storage bootstrap"):
        readiness.verify_backup(
            database,
            checksum,
            original_digest,
            manifest_gate=gate,
            manifest_expectation=expectation,
            manifest_data_root=database.parent,
        )

    assert _digest(database) == original_digest


def test_manifest_missing_is_fail_closed(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "data" / "backup.sqlite3")
    checksum = _write_sidecar(database)

    with pytest.raises(readiness.ReadinessError, match="독립 서명 manifest"):
        readiness.verify_backup(database, checksum, _digest(database))


def test_recomputed_sidecar_cannot_replace_independent_digest(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "data" / "backup.sqlite3")
    checksum = _write_sidecar(database)
    _sink, gate, expectation = _manifest_bundle(tmp_path, database, checksum)

    # 공격자가 DB와 같은 저장소의 sidecar를 함께 바꾼 상황을 재현한다.
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO reports VALUES ('changed')")
    checksum = _write_sidecar(database)

    with pytest.raises(readiness.ReadinessError, match="manifest"):
        readiness.verify_backup(
            database,
            checksum,
            _digest(database),
            manifest_gate=gate,
            manifest_expectation=expectation,
            manifest_data_root=database.parent,
        )


def test_signed_manifest_tampering_is_rejected(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "data" / "backup.sqlite3")
    checksum = _write_sidecar(database)
    sink, gate, expectation = _manifest_bundle(tmp_path, database, checksum)
    manifest_path = sink.local_storage_path()
    assert manifest_path is not None
    payload = json.loads(manifest_path.read_text(encoding="ascii"))
    payload["database_size_bytes"] += 1
    manifest_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )

    with pytest.raises(readiness.ReadinessError, match="서명"):
        readiness.verify_backup(
            database,
            checksum,
            _digest(database),
            manifest_gate=gate,
            manifest_expectation=expectation,
            manifest_data_root=database.parent,
        )


def test_restore_dry_run_uses_and_cleans_temporary_copy(tmp_path: Path) -> None:
    database, checksum = _create_storage_backup(tmp_path)
    source_digest = _digest(database)
    _sink, gate, expectation = _manifest_bundle(tmp_path, database, checksum)
    temp_parent = tmp_path / "restore-only"
    temp_parent.mkdir()

    result = readiness.restore_dry_run(
        database,
        checksum,
        source_digest,
        temp_parent=temp_parent,
        manifest_gate=gate,
        manifest_expectation=expectation,
        manifest_data_root=database.parent,
    )

    assert result["status"] == "임시 복구 통과"
    assert int(result["row_count"]) >= 1
    assert result["restored_app_table_count"] == result["table_count"]
    assert int(result["restored_app_index_count"]) > 0
    assert int(result["restored_app_trigger_count"]) > 0
    assert list(temp_parent.iterdir()) == []
    assert _digest(database) == source_digest


def test_supported_session_migration_runs_only_on_clone(tmp_path: Path) -> None:
    storage_db = readiness._load_storage_db_module()  # noqa: SLF001
    from tools import backup_sqlite  # noqa: PLC0415

    source = tmp_path / "legacy-source" / "storage.db"
    with storage_db.connect(source):
        pass
    with sqlite3.connect(source) as connection:
        connection.execute("DROP TABLE sessions")
        connection.execute(
            "CREATE TABLE sessions ("
            "token TEXT PRIMARY KEY, email TEXT NOT NULL, subject TEXT NOT NULL, "
            "is_admin INTEGER NOT NULL, expires_at REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES "
            "('legacy-cookie', 'admin@example.com', 'google:1', 1, 2000000000.0)"
        )
    backup = backup_sqlite.create_backup(source, tmp_path / "data")
    database = backup.backup_path
    checksum = backup.checksum_path
    original_digest = _digest(database)
    _sink, gate, expectation = _manifest_bundle(tmp_path, database, checksum)

    result = readiness.verify_backup(
        database,
        checksum,
        original_digest,
        manifest_gate=gate,
        manifest_expectation=expectation,
        manifest_data_root=database.parent,
    )

    assert result["status"] == "통과"
    assert _digest(database) == original_digest
    with sqlite3.connect(database) as original:
        columns = {row[1] for row in original.execute("PRAGMA table_info(sessions)")}
        assert "token" in columns
        assert "token_hash" not in columns


def test_quiet_maintenance_preflight_is_read_only(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "app.sqlite3")
    before = _digest(database)

    result = readiness.preflight(
        database,
        tmp_path,
        require_maintenance=True,
        min_free_bytes=0,
        max_disk_used_percent=100,
        max_database_bytes=10**9,
        max_wal_bytes=10**9,
        max_link_open_events=10,
    )

    assert result["status"] == "통과"
    assert result["blockers"] == []
    assert _digest(database) == before
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()


def test_preflight_blocks_active_work_normal_mode_and_open_event_growth(
    tmp_path: Path,
) -> None:
    database = _create_database(tmp_path / "app.sqlite3", service_state="normal")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO budget_spend_inflight VALUES ('cost-run')")
        connection.execute(
            "INSERT INTO share_link_run_history VALUES ('link-run', 'running')"
        )
        connection.execute(
            "INSERT INTO observability_run_lifecycle VALUES ('obs-run', 'pending')"
        )
        connection.execute(
            "INSERT INTO dashboard_member_usage VALUES ('member-run', 'reserved')"
        )
        connection.execute(
            "INSERT INTO dashboard_operation_claims VALUES ('op-run', 'running')"
        )
        connection.execute(
            "INSERT INTO notion_export_operations VALUES ('notion-run', 'in_progress')"
        )
        connection.executemany(
            "INSERT INTO share_link_open_events (link_key_hash, opened_at) VALUES ('hash', 'now')",
            [() for _ in range(3)],
        )

    result = readiness.preflight(
        database,
        tmp_path,
        require_maintenance=True,
        min_free_bytes=0,
        max_disk_used_percent=100,
        max_database_bytes=10**9,
        max_wal_bytes=10**9,
        max_link_open_events=2,
    )

    assert result["status"] == "차단"
    assert len(result["blockers"]) == 8
    assert any("maintenance" in item for item in result["blockers"])
    assert any("열람 이벤트" in item for item in result["blockers"])


def test_sidecar_must_bind_exact_database_filename(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "data" / "backup.sqlite3")
    valid_checksum = _write_sidecar(database)
    _sink, gate, expectation = _manifest_bundle(tmp_path, database, valid_checksum)
    checksum = _write_sidecar(database, name="another.sqlite3")

    with pytest.raises(readiness.ReadinessError, match="파일명"):
        readiness.verify_backup(
            database,
            checksum,
            _digest(database),
            manifest_gate=gate,
            manifest_expectation=expectation,
            manifest_data_root=database.parent,
        )


def test_preflight_cli_returns_blocking_exit_code(tmp_path: Path, capsys) -> None:
    database = _create_database(tmp_path / "app.sqlite3", service_state="normal")

    exit_code = readiness.main(
        [
            "preflight",
            "--database",
            str(database),
            "--data-root",
            str(tmp_path),
            "--require-maintenance",
            "--min-free-bytes",
            "0",
            "--max-disk-used-percent",
            "100",
            "--max-database-bytes",
            str(10**9),
            "--max-wal-bytes",
            str(10**9),
        ]
    )

    assert exit_code == 2
    assert '"status": "차단"' in capsys.readouterr().out
