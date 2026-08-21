"""SQLite 장기 휴면 백업 도구 시험."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

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
        with sqlite3.connect(result.backup_path) as backup_conn:
            assert backup_conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    finally:
        source_conn.close()


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


def test_복구는_기존_DB를_건드리지_않고_새_대상파일만_허용한다(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    source_conn = _make_database(source, "복구할 기록")
    try:
        backup = backup_sqlite.create_backup(source, tmp_path / "external")
    finally:
        source_conn.close()
    target_conn = _make_database(target, "현재 기록")
    try:
        original_bytes = target.read_bytes()
        with pytest.raises(backup_sqlite.BackupError, match="덮어쓰지 않습니다"):
            backup_sqlite.restore_backup(backup.backup_path, target)
        assert target.read_bytes() == original_bytes

        # 기존 DB 연결이 유휴 상태로 열려 있어도 새 파일 복구에는 영향을 주지 않는다.
        restored = tmp_path / "restored.db"
        result = backup_sqlite.restore_backup(backup.backup_path, restored)
        target_conn.execute("INSERT INTO records (value) VALUES ('계속된 기존 작업')")
        target_conn.commit()

        assert result.target_path == restored
        with sqlite3.connect(restored) as conn:
            assert conn.execute("SELECT value FROM records").fetchone()[0] == "복구할 기록"
        assert target_conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    finally:
        target_conn.close()


def test_손상된_기존_DB를_건드리지_않고_새_파일로_복구한다(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    source_conn = _make_database(source, "정상 백업")
    try:
        backup = backup_sqlite.create_backup(source, tmp_path / "external")
    finally:
        source_conn.close()

    damaged = tmp_path / "storage.db"
    damaged.write_bytes(b"damaged database")
    restored = tmp_path / "storage-restored.db"

    backup_sqlite.restore_backup(backup.backup_path, restored)

    assert damaged.read_bytes() == b"damaged database"
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT value FROM records").fetchone()[0] == "정상 백업"


def test_완성된_DB를_빈_중간상태_없이_한번에_게시한다(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.db"
    source_conn = _make_database(source, "완전한 기록")
    try:
        backup = backup_sqlite.create_backup(source, tmp_path / "external")
    finally:
        source_conn.close()

    target = tmp_path / "restored.db"
    real_link = backup_sqlite.os.link

    def checked_link(temp_path: Path, target_path: Path) -> None:
        assert not Path(target_path).exists()
        backup_sqlite._assert_database_is_valid(Path(temp_path))
        real_link(temp_path, target_path)

    monkeypatch.setattr(backup_sqlite.os, "link", checked_link)

    backup_sqlite.restore_backup(backup.backup_path, target)

    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM records").fetchone()[0] == "완전한 기록"
