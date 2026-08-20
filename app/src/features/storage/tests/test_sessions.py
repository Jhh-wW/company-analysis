"""로그인 세션 저장·조회·만료 시험."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

from src.features.storage import db, sessions


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    record = sessions.SessionRecord(
        token="tok-1", email="user@example.com", subject="google:person-1",
        is_admin=False, expires_at=2_000_000_000.0
    )
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(conn, record)
        loaded = sessions.load_session(conn, "tok-1", now=1_000_000_000.0)
    assert loaded == record


def test_database_stores_only_lowercase_sha256_not_raw_cookie(tmp_path: Path) -> None:
    raw_token = "browser-cookie-raw-token"
    record = sessions.SessionRecord(
        token=raw_token,
        email="user@example.com",
        subject="google:person-1",
        is_admin=False,
        expires_at=2_000_000_000.0,
    )

    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(conn, record)
        row = conn.execute("SELECT token_hash FROM sessions").fetchone()
        dump = "\n".join(conn.iterdump())
        loaded = sessions.load_session(conn, raw_token, now=1.0)

    assert row is not None
    assert row["token_hash"] == hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", row["token_hash"])
    assert raw_token not in dump
    assert loaded == record, "호출부에는 DB 지문이 아니라 쿠키 원문 계약을 돌려준다"


def test_load_missing_token_returns_none(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        assert sessions.load_session(conn, "없는-토큰") is None


def test_load_none_token_returns_none(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        assert sessions.load_session(conn, None) is None


def test_expired_session_returns_none_and_is_deleted(tmp_path: Path) -> None:
    record = sessions.SessionRecord(
        token="tok-1", email="user@example.com", subject="google:person-1",
        is_admin=False, expires_at=1_000.0
    )
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(conn, record)
        # 만료 시각(1_000.0)이 지난 뒤(now=2_000.0)에 조회한다.
        assert sessions.load_session(conn, "tok-1", now=2_000.0) is None
        # 조회하는 김에 지워졌는지 — 만료 전 기준으로 다시 봐도 이제 없다.
        assert sessions.load_session(conn, "tok-1", now=500.0) is None


def test_admin_flag_roundtrips_correctly(tmp_path: Path) -> None:
    admin = sessions.SessionRecord(
        token="tok-admin", email="admin@example.com", subject="google:admin-1",
        is_admin=True, expires_at=2_000_000_000.0
    )
    normal = sessions.SessionRecord(
        token="tok-normal", email="user@example.com", subject="google:user-1",
        is_admin=False, expires_at=2_000_000_000.0
    )
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(conn, admin)
        sessions.save_session(conn, normal)
        loaded_admin = sessions.load_session(conn, "tok-admin", now=1.0)
        loaded_normal = sessions.load_session(conn, "tok-normal", now=1.0)
    assert loaded_admin is not None and loaded_admin.is_admin is True
    assert loaded_normal is not None and loaded_normal.is_admin is False


def test_save_same_token_twice_overwrites(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(
            conn, sessions.SessionRecord("tok-1", "a@example.com", "google:a", False, 2_000_000_000.0)
        )
        sessions.save_session(
            conn, sessions.SessionRecord("tok-1", "b@example.com", "google:b", True, 2_000_000_000.0)
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        loaded = sessions.load_session(conn, "tok-1", now=1.0)
    assert count == 1
    assert loaded is not None
    assert loaded.email == "b@example.com"
    assert loaded.is_admin is True


def test_delete_session_removes_it(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(
            conn, sessions.SessionRecord("tok-1", "a@example.com", "google:a", False, 2_000_000_000.0)
        )
        sessions.delete_session(conn, "tok-1")
        assert sessions.load_session(conn, "tok-1", now=1.0) is None


def test_delete_missing_token_does_not_raise(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.delete_session(conn, "없는-토큰")  # 예외가 나면 시험이 실패한다
        sessions.delete_session(conn, None)


def test_survives_reconnect_like_a_server_restart(tmp_path: Path) -> None:
    """서버를 껐다 켜도 로그인이 유지되는지 — 연결을 완전히 닫았다 다시 연다."""
    target = tmp_path / "storage.db"
    record = sessions.SessionRecord(
        "tok-1", "user@example.com", "google:person-1", False, 2_000_000_000.0
    )
    with db.connect(target) as conn:
        sessions.save_session(conn, record)

    with db.connect(target) as conn:
        loaded = sessions.load_session(conn, "tok-1", now=1.0)
    assert loaded == record


def test_legacy_email_only_session_is_migrated_then_rejected(tmp_path: Path) -> None:
    """구 DB 행을 이메일 기반 사람 ID로 승격하지 않고 재로그인시킨다."""

    target = tmp_path / "legacy.db"
    conn = sqlite3.connect(target)
    try:
        conn.execute(
            "CREATE TABLE sessions (token TEXT PRIMARY KEY, email TEXT NOT NULL, "
            "is_admin INTEGER NOT NULL, expires_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            ("old-token", "alias@example.com", 1, 2_000_000_000.0),
        )
        conn.commit()
    finally:
        conn.close()

    with db.connect(target) as migrated:
        columns = {
            row["name"]: row["pk"]
            for row in migrated.execute("PRAGMA table_info(sessions)")
        }
        assert columns["token_hash"] == 1
        assert "token" not in columns
        assert "subject" in columns
        assert sessions.load_session(migrated, "old-token", now=1.0) is None
        count = migrated.execute(
            "SELECT COUNT(*) AS n FROM sessions"
        ).fetchone()["n"]
    assert count == 0
