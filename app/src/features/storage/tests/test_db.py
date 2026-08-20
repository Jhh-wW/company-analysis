"""연결 열기·표 만들기 시험."""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


def test_default_db_path_is_under_app_data_dir(monkeypatch) -> None:
    # ★ 환경변수를 «걷어내고» 진짜 기본값을 본다.
    #   (시험 전체가 임시 DB를 쓰도록 conftest.py가 이 변수를 걸어 둔다)
    monkeypatch.delenv(constants.ENV_DB_PATH, raising=False)
    path = db.default_db_path()
    assert path.name == "storage.db"
    assert path.parent.name == "data"
