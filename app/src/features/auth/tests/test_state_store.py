"""OAuth state 서버 원장의 수명·상한·원자 소비 계약."""

from __future__ import annotations

import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.features.auth import constants, state_store
from tools import backup_sqlite


def _state() -> str:
    value = secrets.token_urlsafe(constants.STATE_TOKEN_BYTES)
    assert len(value) == constants.STATE_TOKEN_CHARS
    return value


def _connection(path: Path | str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5.0)
    state_store.ensure_schema(conn)
    conn.commit()
    return conn


@pytest.mark.parametrize(
    "value",
    (
        "",
        "short",
        "a" * (constants.STATE_TOKEN_CHARS - 1),
        "a" * (constants.STATE_TOKEN_CHARS + 1),
        "가" * constants.STATE_TOKEN_CHARS,
        "=" * constants.STATE_TOKEN_CHARS,
    ),
)
def test_state는_정확한_URL_safe_32byte_형식만_받는다(value: str) -> None:
    assert state_store.state_has_expected_shape(value) is False
    with pytest.raises(state_store.OAuthStateFormatError):
        state_store.hash_state(value)


def test_DB에는_state_원문이_아니라_해시와_수명만_남는다() -> None:
    raw = _state()
    with _connection() as conn:
        stored_hash = state_store.issue_state(conn, raw, now=1_000.0)
        row = conn.execute(
            f"SELECT * FROM {state_store.TABLE_OAUTH_LOGIN_STATES}"
        ).fetchone()
        columns = {
            str(item[1])
            for item in conn.execute(
                f"PRAGMA table_info({state_store.TABLE_OAUTH_LOGIN_STATES})"
            )
        }

    assert raw not in tuple(str(value) for value in row)
    assert stored_hash == state_store.hash_state(raw)
    assert columns == {
        "state_hash",
        "issued_at",
        "expires_at",
        "status",
        "consumed_at",
    }


def test_정상_state는_정확히_한번만_소비된다() -> None:
    raw = _state()
    with _connection() as conn:
        state_store.issue_state(conn, raw, now=1_000.0)
        assert state_store.consume_state(conn, raw, now=1_001.0) is True
        assert state_store.consume_state(conn, raw, now=1_002.0) is False


def test_미발급과_만료경계는_소비되지_않는다() -> None:
    issued = _state()
    with _connection() as conn:
        assert state_store.consume_state(conn, _state(), now=1_000.0) is False
        state_store.issue_state(conn, issued, now=1_000.0)
        assert state_store.consume_state(
            conn,
            issued,
            now=1_000.0 + constants.STATE_MAX_AGE_SEC,
        ) is False


def test_만료행을_정리한뒤에도_살아있는_발급수는_고정상한이다() -> None:
    with _connection() as conn:
        for index in range(constants.OAUTH_STATE_MAX_RECORDS):
            # 실제 난수 대신 형식이 맞고 서로 다른 43자 ASCII 토큰을 만든다.
            raw = f"{index:043d}"
            state_store.issue_state(conn, raw, now=1_000.0)
        assert state_store.count_records(conn) == constants.OAUTH_STATE_MAX_RECORDS
        with pytest.raises(state_store.OAuthStateCapacityError):
            state_store.issue_state(conn, "z" * 43, now=1_001.0)

        # 만료 시각과 정확히 같아진 행은 먼저 지워지고 새 발급 한 건만 남는다.
        state_store.issue_state(
            conn,
            "z" * 43,
            now=1_000.0 + constants.STATE_MAX_AGE_SEC,
        )
        assert state_store.count_records(conn) == 1


def test_소비한_state가_용량을_붙잡아_새로그인을_막지_않는다() -> None:
    """실패 callback을 상한만큼 만들어도 다음 로그인 시작은 즉시 가능하다."""

    issued: list[str] = []
    with _connection() as conn:
        for index in range(constants.OAUTH_STATE_MAX_RECORDS):
            raw = f"{index:043d}"
            issued.append(raw)
            state_store.issue_state(conn, raw, now=1_000.0)

        for raw in issued:
            assert state_store.consume_state(conn, raw, now=1_001.0) is True

        replacement = "z" * constants.STATE_TOKEN_CHARS
        state_store.issue_state(conn, replacement, now=1_002.0)

        assert state_store.count_records(conn) == 1
        assert state_store.consume_state(conn, replacement, now=1_003.0) is True


def test_직접_INSERT도_DB_trigger가_상한밖_성장을_막는다() -> None:
    with _connection() as conn:
        for index in range(constants.OAUTH_STATE_MAX_RECORDS):
            conn.execute(
                f"""
                INSERT INTO {state_store.TABLE_OAUTH_LOGIN_STATES}
                    (state_hash, issued_at, expires_at, status, consumed_at)
                VALUES (?, 1.0, 9999.0, ?, NULL)
                """,
                (f"{index:064x}", state_store.STATE_ISSUED),
            )
        with pytest.raises(sqlite3.IntegrityError, match="capacity"):
            conn.execute(
                f"""
                INSERT INTO {state_store.TABLE_OAUTH_LOGIN_STATES}
                    (state_hash, issued_at, expires_at, status, consumed_at)
                VALUES (?, 1.0, 9999.0, ?, NULL)
                """,
                ("f" * 64, state_store.STATE_ISSUED),
            )


def test_재시작뒤에도_발급기록을_소비할수있다(tmp_path: Path) -> None:
    db_path = tmp_path / "oauth.sqlite3"
    raw = _state()
    with _connection(db_path) as conn:
        state_store.issue_state(conn, raw, now=1_000.0)
    with _connection(db_path) as restarted:
        assert state_store.consume_state(restarted, raw, now=1_001.0) is True


def test_동시_callback은_정확히_하나만_소비한다(tmp_path: Path) -> None:
    db_path = tmp_path / "oauth-concurrent.sqlite3"
    raw = _state()
    with _connection(db_path) as conn:
        state_store.issue_state(conn, raw, now=1_000.0)

    def consume_once() -> bool:
        with _connection(db_path) as conn:
            return state_store.consume_state(conn, raw, now=1_001.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _index: consume_once(), range(8)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7


def test_SQLite_backup에도_해시원장이_그대로_포함되고_원문은_없다(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    raw = _state()
    with _connection(source) as conn:
        expected_hash = state_store.issue_state(conn, raw, now=1_000.0)
    result = backup_sqlite.create_backup(source, tmp_path / "private-backups")
    assert backup_sqlite.verify_backup(result.backup_path) == result.sha256
    with sqlite3.connect(result.backup_path) as copied:
        copied_hash = copied.execute(
            f"SELECT state_hash FROM {state_store.TABLE_OAUTH_LOGIN_STATES}"
        ).fetchone()[0]
    assert copied_hash == expected_hash
    assert raw.encode("ascii") not in result.backup_path.read_bytes()
