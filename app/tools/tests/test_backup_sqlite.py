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
