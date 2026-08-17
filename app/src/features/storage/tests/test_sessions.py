"""로그인 세션 저장·조회·만료 시험."""

from __future__ import annotations

from pathlib import Path

from src.features.storage import db, sessions


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    record = sessions.SessionRecord(
        token="tok-1", email="user@example.com", is_admin=False, expires_at=2_000_000_000.0
    )
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(conn, record)
        loaded = sessions.load_session(conn, "tok-1", now=1_000_000_000.0)
    assert loaded == record


def test_load_missing_token_returns_none(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        assert sessions.load_session(conn, "없는-토큰") is None


def test_load_none_token_returns_none(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        assert sessions.load_session(conn, None) is None


def test_expired_session_returns_none_and_is_deleted(tmp_path: Path) -> None:
    record = sessions.SessionRecord(
        token="tok-1", email="user@example.com", is_admin=False, expires_at=1_000.0
    )
    with db.connect(tmp_path / "storage.db") as conn:
        sessions.save_session(conn, record)
        # 만료 시각(1_000.0)이 지난 뒤(now=2_000.0)에 조회한다.
        assert sessions.load_session(conn, "tok-1", now=2_000.0) is None
        # 조회하는 김에 지워졌는지 — 만료 전 기준으로 다시 봐도 이제 없다.
        assert sessions.load_session(conn, "tok-1", now=500.0) is None


def test_admin_flag_roundtrips_correctly(tmp_path: Path) -> None:
    admin = sessions.SessionRecord(
        token="tok-admin", email="admin@example.com", is_admin=True, expires_at=2_000_000_000.0
    )
    normal = sessions.SessionRecord(
        token="tok-normal", email="user@example.com", is_admin=False, expires_at=2_000_000_000.0
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
            conn, sessions.SessionRecord("tok-1", "a@example.com", False, 2_000_000_000.0)
        )
        sessions.save_session(
            conn, sessions.SessionRecord("tok-1", "b@example.com", True, 2_000_000_000.0)
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
            conn, sessions.SessionRecord("tok-1", "a@example.com", False, 2_000_000_000.0)
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
    record = sessions.SessionRecord("tok-1", "user@example.com", False, 2_000_000_000.0)
    with db.connect(target) as conn:
        sessions.save_session(conn, record)

    with db.connect(target) as conn:
        loaded = sessions.load_session(conn, "tok-1", now=1.0)
    assert loaded == record
