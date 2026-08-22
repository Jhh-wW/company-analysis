"""복구 dry-run이 실제 앱 스키마 기동을 증명하는지 독립 공격 검수한다."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops import release_readiness as readiness


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "app"


class _AcceptingManifestGate:
    """이 시험에서는 manifest 이후의 앱 schema 경계만 고립해 공격한다."""

    def verify(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            backup_id="schema-adversarial",
            sequence=1,
            key_id="test-schema-key",
        )


def _storage_db_module():
    app_root = str(APP_ROOT)
    inserted = app_root not in sys.path
    if inserted:
        sys.path.insert(0, app_root)
    try:
        from src.features.storage import db as storage_db  # noqa: PLC0415

        return storage_db
    finally:
        if inserted:
            sys.path.remove(app_root)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_runtime_database(path: Path) -> Path:
    storage_db = _storage_db_module()
    with storage_db.connect(path):
        pass
    return path


def _write_sidecar(database: Path) -> Path:
    checksum = database.with_name(database.name + ".sha256")
    checksum.write_text(
        f"{_digest(database)}  {database.name}\n",
        encoding="ascii",
    )
    return checksum


def _directory_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.name: item.read_bytes()
        for item in sorted(path.iterdir())
        if item.is_file()
    }


def _restore(
    database: Path,
    temp_parent: Path,
) -> dict[str, object]:
    checksum = _write_sidecar(database)
    return readiness.restore_dry_run(
        database,
        checksum,
        _digest(database),
        temp_parent=temp_parent,
        manifest_gate=_AcceptingManifestGate(),
        manifest_expectation=object(),  # fake gate가 schema 경계만 고립한다.
        manifest_data_root=database.parent,
    )


def _assert_source_and_temp_unchanged(
    database: Path,
    before: dict[str, bytes],
    temp_parent: Path,
) -> None:
    assert _directory_bytes(database.parent) == before
    assert list(temp_parent.iterdir()) == []


def _reject_without_mutation(
    database: Path,
    temp_parent: Path,
) -> None:
    _write_sidecar(database)
    before = _directory_bytes(database.parent)

    with pytest.raises(readiness.ReadinessError):
        _restore(database, temp_parent)

    _assert_source_and_temp_unchanged(database, before, temp_parent)


def _create_required_names_only_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        for table in sorted(readiness.REQUIRED_TABLES):
            if table == "sessions":
                connection.execute(
                    "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY)"
                )
            elif table == "share_links":
                connection.execute(
                    "CREATE TABLE share_links (key_hash TEXT PRIMARY KEY)"
                )
            else:
                escaped = table.replace('"', '""')
                connection.execute(
                    f'CREATE TABLE "{escaped}" (id INTEGER PRIMARY KEY)'
                )
    return path


def _poison_index(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX idx_share_link_open_events_link_time")
        connection.execute(
            "CREATE INDEX idx_share_link_open_events_link_time "
            "ON share_link_open_events(opened_at)"
        )


def _poison_trigger(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER share_link_open_events_no_insert")
        connection.execute(
            "CREATE TRIGGER share_link_open_events_no_insert "
            "BEFORE INSERT ON share_link_open_events BEGIN SELECT 1; END"
        )


def test_required_table_names_and_two_hash_columns_are_not_app_schema(
    tmp_path: Path,
) -> None:
    database = _create_required_names_only_database(
        tmp_path / "source" / "minimal.sqlite3"
    )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_reports_primary_key_only_is_rejected_by_canonical_table_xinfo(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "reports.sqlite3")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE reports")
        connection.execute("CREATE TABLE reports (report_id TEXT PRIMARY KEY)")
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize(
    "poison",
    (_poison_index, _poison_trigger),
    ids=("same-name-index", "same-name-trigger"),
)
def test_same_named_poisoned_schema_object_is_rejected(
    tmp_path: Path,
    poison: Callable[[Path], None],
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "poison.sqlite3")
    poison(database)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_unsupported_reserved_migration_state_is_rejected_without_source_write(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "migration.sqlite3")
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE sessions_legacy_raw_token (
                token_hash TEXT PRIMARY KEY,
                email      TEXT NOT NULL,
                subject    TEXT NOT NULL,
                is_admin   INTEGER NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_supported_legacy_migration_runs_on_clone_only_and_passes(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "legacy.sqlite3")
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE sessions")
        connection.execute(
            """
            CREATE TABLE sessions (
                token      TEXT PRIMARY KEY,
                email      TEXT NOT NULL,
                subject    TEXT NOT NULL,
                is_admin   INTEGER NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            ("synthetic-legacy-token", "legacy@example.invalid", "sub", 0, 1.0),
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()
    _write_sidecar(database)
    before = _directory_bytes(database.parent)

    result = _restore(database, temp_parent)

    assert result["status"] == "임시 복구 통과"
    _assert_source_and_temp_unchanged(database, before, temp_parent)
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_xinfo(sessions)")
        }
    assert "token" in columns and "token_hash" not in columns


def test_runtime_database_bootstraps_only_temporary_clones_and_passes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "runtime.sqlite3")
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()
    _write_sidecar(database)
    before = _directory_bytes(database.parent)
    storage_db = _storage_db_module()
    real_connect = storage_db.connect
    opened_paths: list[Path] = []

    @contextmanager
    def observed_connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
        assert db_path is not None
        opened_paths.append(Path(db_path).resolve())
        with real_connect(db_path) as connection:
            yield connection

    monkeypatch.setattr(storage_db, "connect", observed_connect)

    result = _restore(database, temp_parent)

    assert result["status"] == "임시 복구 통과"
    assert opened_paths, "실제 storage.db.connect bootstrap이 호출돼야 합니다"
    assert all(path != database.resolve() for path in opened_paths)
    _assert_source_and_temp_unchanged(database, before, temp_parent)
