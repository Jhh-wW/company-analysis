"""연결 열기·표 만들기 시험."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.features.budget import spend_store
from src.features.export_pdf import release_store
from src.features.export_notion import store as notion_store
from src.features.sharelink import store as share_store
from src.features.storage import constants, db
from src.features.storage import sessions


def test_connect_creates_missing_parent_dir_and_file(tmp_path: Path) -> None:
    """DB 파일이 없어도, 폴더가 없어도 처음 연결하면 만들어진다."""
    target = tmp_path / "nested" / "storage.db"
    assert not target.exists()

    with db.connect(target) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert target.exists()
    assert {
        constants.TABLE_REPORTS,
        constants.TABLE_LAYER1_CACHE,
        constants.TABLE_LAYER2_CACHE,
        constants.TABLE_ALIAS_CACHE,
        constants.TABLE_SESSIONS,
        notion_store.TABLE_NOTION_EXPORTS,
    } <= tables

    with db.connect(target) as conn:
        session_columns = {
            row["name"]: row["pk"]
            for row in conn.execute(
                f"PRAGMA table_info({constants.TABLE_SESSIONS})"
            )
        }
    assert session_columns["token_hash"] == 1
    assert "token" not in session_columns


def test_connect_reopen_keeps_data(tmp_path: Path) -> None:
    """서버를 껐다 켜는 것을 흉내 — 연결을 닫고 다시 열어도 표는 그대로다."""
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {constants.TABLE_ALIAS_CACHE} (alias_key, corp_id, created_at) "
            "VALUES ('a', 'CORP1', '2026-08-15T00:00:00')"
        )

    with db.connect(target) as conn:
        row = conn.execute(
            f"SELECT corp_id FROM {constants.TABLE_ALIAS_CACHE} WHERE alias_key = 'a'"
        ).fetchone()

    assert row is not None
    assert row["corp_id"] == "CORP1"


def test_connect_twice_is_idempotent(tmp_path: Path) -> None:
    """표를 두 번 만들어도(멱등) 에러가 나지 않는다."""
    target = tmp_path / "storage.db"
    with db.connect(target):
        pass
    with db.connect(target):
        pass  # 두 번째도 예외 없이 지나가야 한다


def test_legacy_session_migration_preserves_other_data_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """결정 1-A는 옛 세션만 지우고 보고서·링크·예산·승인을 보존한다."""
    target = tmp_path / "legacy-with-product-data.db"
    legacy = sqlite3.connect(target)
    try:
        legacy.execute(
            "CREATE TABLE sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "subject TEXT NOT NULL, is_admin INTEGER NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        legacy.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-raw-cookie",
                "admin@example.com",
                "google:admin-1",
                1,
                2_000_000_000.0,
            ),
        )
        legacy.execute(
            "CREATE TABLE reports ("
            "report_id TEXT PRIMARY KEY, corp_id TEXT NOT NULL, job TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, generated_at TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        legacy.execute(
            "INSERT INTO reports VALUES "
            "('report-keep', 'corp-keep', 'job-keep', '{}', 'generated', 'created')"
        )
        legacy.execute(share_store.CREATE_SQL)
        legacy.execute(
            "INSERT INTO share_links "
            "(key, company, job, report_id, note, created_at) "
            "VALUES ('link-keep', '회사', '직무', 'report-keep', '보존', 'created')"
        )
        legacy.execute(spend_store.CREATE_SQL)
        legacy.execute(
            "INSERT INTO budget_spend_events "
            "(run_id, phase, day, bucket_id, cost_krw, created_at) "
            "VALUES ('run-keep', 'pipeline', '2026-08-20', 'bucket-hash', 123, 'created')"
        )
        legacy.execute(release_store.CREATE_SQL)
        legacy.execute(
            "INSERT INTO pdf_release_records "
            "(report_id, pdf_sha256, approval_json, approval_created_at) "
            "VALUES (?, ?, '{}', 'created')",
            ("report-keep", "a" * 64),
        )
        legacy.commit()
    finally:
        legacy.close()

    replacement = sessions.SessionRecord(
        "new-raw-cookie",
        "admin@example.com",
        "google:admin-1",
        True,
        2_000_000_000.0,
    )
    with db.connect(target) as migrated:
        columns = {
            row["name"]: row["pk"]
            for row in migrated.execute("PRAGMA table_info(sessions)")
        }
        assert columns["token_hash"] == 1
        assert "token" not in columns
        assert migrated.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert migrated.execute(
            "SELECT payload_json FROM reports WHERE report_id='report-keep'"
        ).fetchone()[0] == "{}"
        assert migrated.execute(
            "SELECT note FROM share_links WHERE key='link-keep'"
        ).fetchone()[0] == "보존"
        assert migrated.execute(
            "SELECT cost_krw FROM budget_spend_events WHERE run_id='run-keep'"
        ).fetchone()[0] == 123
        assert migrated.execute(
            "SELECT approval_json FROM pdf_release_records "
            "WHERE report_id='report-keep'"
        ).fetchone()[0] == "{}"
        sessions.save_session(migrated, replacement)

    # 새 스키마가 마이그레이션 완료 표식이다. 재연결해도 새 세션을 다시 지우지 않는다.
    with db.connect(target) as reopened:
        assert sessions.load_session(
            reopened, replacement.token, now=1.0
        ) == replacement
        assert reopened.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
        assert reopened.execute("SELECT COUNT(*) FROM share_links").fetchone()[0] == 1
        assert reopened.execute(
            "SELECT COUNT(*) FROM budget_spend_events"
        ).fetchone()[0] == 1
        assert reopened.execute(
            "SELECT COUNT(*) FROM pdf_release_records"
        ).fetchone()[0] == 1


def test_session_migration_create_failure_rolls_back_then_restart_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """ALTER 뒤 CREATE가 실패해도 원문 표가 예약 이름으로 노출되지 않는다."""
    target = tmp_path / "migration-create-failure.db"
    raw_token = "rollback-raw-cookie"
    initial = sqlite3.connect(target)
    try:
        initial.execute(
            "CREATE TABLE sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "subject TEXT NOT NULL, is_admin INTEGER NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        initial.execute(
            "INSERT INTO sessions VALUES (?, 'admin@example.com', "
            "'google:admin-1', 1, 2000000000.0)",
            (raw_token,),
        )
        initial.execute("CREATE TABLE keep_data (value TEXT NOT NULL)")
        initial.execute("INSERT INTO keep_data VALUES ('preserve-me')")
        initial.commit()
    finally:
        initial.close()

    valid_create = db._CREATE_SESSIONS_SQL  # noqa: SLF001 - DDL 장애 주입 경계
    monkeypatch.setattr(db, "_CREATE_SESSIONS_SQL", "CREATE TABLE invalid (")
    with pytest.raises(sqlite3.OperationalError):
        with db.connect(target):
            pass

    rolled_back = sqlite3.connect(target)
    try:
        tables = {
            row[0]
            for row in rolled_back.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1] for row in rolled_back.execute("PRAGMA table_info(sessions)")
        }
        assert "sessions" in tables
        assert db._LEGACY_SESSIONS_TABLE not in tables  # noqa: SLF001
        assert "token" in columns and "token_hash" not in columns
        assert (
            rolled_back.execute("SELECT token FROM sessions").fetchone()[0]
            == raw_token
        )
        assert (
            rolled_back.execute("SELECT value FROM keep_data").fetchone()[0]
            == "preserve-me"
        )
    finally:
        rolled_back.close()

    monkeypatch.setattr(db, "_CREATE_SESSIONS_SQL", valid_create)
    with db.connect(target) as recovered:
        assert not recovered.in_transaction, "migration SAVEPOINT는 요청 전에 끝나야 한다"
        tables = {
            row["name"]
            for row in recovered.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert db._LEGACY_SESSIONS_TABLE not in tables  # noqa: SLF001
        assert recovered.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert (
            recovered.execute("SELECT value FROM keep_data").fetchone()[0]
            == "preserve-me"
        )

    with sqlite3.connect(target) as verified:
        assert raw_token not in "\n".join(verified.iterdump())


def test_interrupted_hashed_and_exact_raw_legacy_recovers_without_losing_new_data(
    tmp_path: Path,
) -> None:
    """원자화 전 중단 상태는 새 해시 세션을 살리고 원문 legacy만 폐기한다."""
    target = tmp_path / "interrupted-migration.db"
    old_raw_token = "interrupted-old-raw-cookie"
    new_raw_token = "new-session-must-survive"
    token_hash = hashlib.sha256(new_raw_token.encode("utf-8")).hexdigest()
    interrupted = sqlite3.connect(target)
    try:
        interrupted.execute(db._CREATE_SESSIONS_SQL)  # noqa: SLF001
        interrupted.execute(
            "INSERT INTO sessions VALUES (?, 'new@example.com', "
            "'google:new-1', 1, 2000000000.0)",
            (token_hash,),
        )
        interrupted.execute(
            f"CREATE TABLE {db._LEGACY_SESSIONS_TABLE} ("  # noqa: SLF001
            "token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "subject TEXT NOT NULL, is_admin INTEGER NOT NULL, "
            "expires_at REAL NOT NULL)"
        )
        interrupted.execute(
            f"INSERT INTO {db._LEGACY_SESSIONS_TABLE} VALUES "  # noqa: SLF001
            "(?, 'old@example.com', 'google:old-1', 1, 2000000000.0)",
            (old_raw_token,),
        )
        interrupted.execute("CREATE TABLE keep_data (value TEXT NOT NULL)")
        interrupted.execute("INSERT INTO keep_data VALUES ('preserve-me')")
        interrupted.commit()
    finally:
        interrupted.close()

    expected = sessions.SessionRecord(
        new_raw_token,
        "new@example.com",
        "google:new-1",
        True,
        2_000_000_000.0,
    )
    with db.connect(target) as recovered:
        assert not recovered.in_transaction, "복구 SAVEPOINT는 요청 전에 끝나야 한다"
        assert sessions.load_session(recovered, new_raw_token, now=1.0) == expected
        tables = {
            row["name"]
            for row in recovered.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert db._LEGACY_SESSIONS_TABLE not in tables  # noqa: SLF001
        assert (
            recovered.execute("SELECT value FROM keep_data").fetchone()[0]
            == "preserve-me"
        )

    with db.connect(target) as reopened:
        assert sessions.load_session(reopened, new_raw_token, now=1.0) == expected
        assert reopened.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    with sqlite3.connect(target) as verified:
        dump = "\n".join(verified.iterdump())
        assert old_raw_token not in dump
        assert new_raw_token not in dump


def test_unexpected_reserved_legacy_schema_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "unexpected-legacy-schema.db"
    prepared = sqlite3.connect(target)
    try:
        prepared.execute(db._CREATE_SESSIONS_SQL)  # noqa: SLF001
        prepared.execute(
            f"CREATE TABLE {db._LEGACY_SESSIONS_TABLE} ("  # noqa: SLF001
            "token TEXT PRIMARY KEY, unexpected TEXT NOT NULL)"
        )
        prepared.execute(
            f"INSERT INTO {db._LEGACY_SESSIONS_TABLE} VALUES "  # noqa: SLF001
            "('do-not-guess', 'preserve')"
        )
        prepared.commit()
    finally:
        prepared.close()

    with pytest.raises(RuntimeError, match="legacy=unexpected"):
        with db.connect(target):
            pass

    with sqlite3.connect(target) as verified:
        assert verified.execute(
            f"SELECT token FROM {db._LEGACY_SESSIONS_TABLE}"  # noqa: SLF001
        ).fetchone()[0] == "do-not-guess"


def test_raw_sessions_plus_reserved_legacy_combo_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "unexpected-two-raw-tables.db"
    prepared = sqlite3.connect(target)
    raw_schema = (
        "(token TEXT PRIMARY KEY, email TEXT NOT NULL, subject TEXT NOT NULL, "
        "is_admin INTEGER NOT NULL, expires_at REAL NOT NULL)"
    )
    try:
        prepared.execute(f"CREATE TABLE sessions {raw_schema}")
        prepared.execute(
            f"CREATE TABLE {db._LEGACY_SESSIONS_TABLE} {raw_schema}"  # noqa: SLF001
        )
        prepared.commit()
    finally:
        prepared.close()

    with pytest.raises(RuntimeError, match="sessions=raw, legacy=raw"):
        with db.connect(target):
            pass

    with sqlite3.connect(target) as verified:
        tables = {
            row[0]
            for row in verified.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"sessions", db._LEGACY_SESSIONS_TABLE} <= tables  # noqa: SLF001


def test_default_db_path_is_under_app_data_dir(monkeypatch) -> None:
    # ★ 환경변수를 «걷어내고» 진짜 기본값을 본다.
    #   (시험 전체가 임시 DB를 쓰도록 conftest.py가 이 변수를 걸어 둔다)
    monkeypatch.delenv(constants.ENV_DB_PATH, raising=False)
    path = db.default_db_path()
    assert path.name == "storage.db"
    assert path.parent.name == "data"
