from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from ops import release_readiness as readiness


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
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO dashboard_service_state VALUES (1, ?)",
            (service_state,),
        )
    return path


def test_verify_backup_requires_matching_independent_digest(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "backup.sqlite3")
    expected = _digest(database)
    checksum = _write_sidecar(database)

    result = readiness.verify_backup(database, checksum, expected)

    assert result["status"] == "통과"
    assert result["sha256"] == expected


def test_recomputed_sidecar_cannot_replace_independent_digest(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "backup.sqlite3")
    original_digest = _digest(database)

    # 공격자가 DB와 같은 저장소의 sidecar를 함께 바꾼 상황을 재현한다.
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO reports VALUES ('changed')")
    checksum = _write_sidecar(database)

    with pytest.raises(readiness.ReadinessError, match="독립 보관"):
        readiness.verify_backup(database, checksum, original_digest)


def test_restore_dry_run_uses_and_cleans_temporary_copy(tmp_path: Path) -> None:
    database = _create_database(tmp_path / "backup.sqlite3")
    source_digest = _digest(database)
    checksum = _write_sidecar(database)
    temp_parent = tmp_path / "restore-only"
    temp_parent.mkdir()

    result = readiness.restore_dry_run(
        database,
        checksum,
        source_digest,
        temp_parent=temp_parent,
    )

    assert result["status"] == "임시 복구 통과"
    assert result["row_count"] == 1
    assert list(temp_parent.iterdir()) == []
    assert _digest(database) == source_digest


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
    database = _create_database(tmp_path / "backup.sqlite3")
    checksum = _write_sidecar(database, name="another.sqlite3")

    with pytest.raises(readiness.ReadinessError, match="파일명"):
        readiness.verify_backup(database, checksum, _digest(database))


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
