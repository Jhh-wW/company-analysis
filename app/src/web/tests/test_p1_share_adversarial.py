"""web 조립 경계에서 공유 capability GET 저장소를 공격적으로 검수한다."""

from __future__ import annotations

import datetime as dt
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.core import clock
from src.features.sharelink import access_control
from src.features.sharelink import constants
from src.features.sharelink import store as share_store


CAPABILITY = "0123456789abcdef0123456789abcdef"
CREATED_AT = "2026-08-23T09:00:00+09:00"


def _connect(path: Path, *, foreign_keys: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _create_database(path: Path) -> None:
    with _connect(path) as connection:
        share_store.ensure_schema(connection)
        assert share_store.insert_new(
            connection,
            key=CAPABILITY,
            company="공격 검수 회사",
            job="보안 검수",
            now_iso=CREATED_AT,
        )


def _counts(connection: sqlite3.Connection) -> tuple[int, int, int]:
    return tuple(
        int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in (
            share_store.TABLE_OPEN_EVENTS,
            share_store.TABLE_OPEN_WINDOWS,
            share_store.TABLE_ACCESS_SUBJECTS,
        )
    )


def test_FK_OFF에서도_긴시간공격의_window와요청자행은_링크별상한이다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "share.sqlite3"
    _create_database(database)
    start = dt.datetime(2026, 8, 23, 9, 0, tzinfo=clock.KST)
    client_host = "203.0.113.17"
    requester_hash = access_control.requester_hash_of(CAPABILITY, client_host)

    # SQLite 기본값인 foreign_keys=OFF에서도 CASCADE에 의존해 orphan을 남기면 안 된다.
    with _connect(database, foreign_keys=False) as connection:
        for index in range(constants.OPEN_WINDOW_ROWS_PER_LINK + 6):
            now_iso = (
                start + dt.timedelta(seconds=(constants.ACCESS_WINDOW_SECONDS + 1) * index)
            ).isoformat(timespec="seconds")
            assert share_store.mark_opened(
                connection,
                CAPABILITY,
                now_iso,
                requester_hash=requester_hash,
            )

        assert _counts(connection) == (
            0,
            constants.OPEN_WINDOW_ROWS_PER_LINK,
            constants.OPEN_WINDOW_ROWS_PER_LINK,
        )
        stored = share_store.load(connection, CAPABILITY)
        assert stored is not None
        assert stored.opened_count == constants.OPEN_WINDOW_ROWS_PER_LINK + 6

    raw_database = database.read_bytes()
    assert CAPABILITY.encode("ascii") not in raw_database
    assert client_host.encode("ascii") not in raw_database


def test_프로세스재시작후에도_같은요청자_window상한은_DB에서유지된다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "share.sqlite3"
    _create_database(database)
    requester_hash = access_control.requester_hash_of(CAPABILITY, "198.51.100.9")
    now_iso = "2026-08-23T10:00:05+09:00"

    with _connect(database) as connection:
        for _ in range(constants.ACCESS_PER_REQUESTER_LIMIT):
            assert share_store.mark_opened(
                connection,
                CAPABILITY,
                now_iso,
                requester_hash=requester_hash,
            )

    # 새 connection은 프로세스 재시작 뒤 새 DB 연결을 흉내 낸다.
    with _connect(database) as restarted:
        assert not share_store.mark_opened(
            restarted,
            CAPABILITY,
            now_iso,
            requester_hash=requester_hash,
        )
        assert _counts(restarted) == (0, 1, 1)
        window_count = restarted.execute(
            f"SELECT opened_count FROM {share_store.TABLE_OPEN_WINDOWS}"
        ).fetchone()[0]
        subject_count = restarted.execute(
            f"SELECT opened_count FROM {share_store.TABLE_ACCESS_SUBJECTS}"
        ).fetchone()[0]
        assert window_count == subject_count == constants.ACCESS_PER_REQUESTER_LIMIT


def test_다중SQLite연결의_동시GET도_capability_window상한을_넘지않는다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "share.sqlite3"
    _create_database(database)
    attempts = constants.OPEN_WINDOW_MAX_COUNT + 20
    now_iso = "2026-08-23T11:00:20+09:00"

    def attempt(index: int) -> bool:
        requester_hash = access_control.requester_hash_of(
            CAPABILITY,
            f"203.0.113.{index + 1}",
        )
        with _connect(database) as connection:
            return share_store.mark_opened(
                connection,
                CAPABILITY,
                now_iso,
                requester_hash=requester_hash,
            )

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = sum(pool.map(attempt, range(attempts)))

    assert accepted == constants.OPEN_WINDOW_MAX_COUNT
    with _connect(database) as connection:
        assert _counts(connection) == (0, 1, constants.OPEN_WINDOW_MAX_COUNT)
        window_count = connection.execute(
            f"SELECT opened_count FROM {share_store.TABLE_OPEN_WINDOWS}"
        ).fetchone()[0]
        stored = share_store.load(connection, CAPABILITY)
        assert stored is not None
        assert window_count == stored.opened_count == accepted


def test_폐기_만료_없는capability는_집계상태를_전혀바꾸지않는다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "share.sqlite3"
    _create_database(database)
    expired = "fedcba9876543210fedcba9876543210"
    missing = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    with _connect(database) as connection:
        assert share_store.insert_new(
            connection,
            key=expired,
            company="만료 회사",
            job="검수",
            now_iso="2020-01-01T00:00:00+09:00",
        )
        assert share_store.delete(
            connection,
            CAPABILITY,
            revoked_at="2026-08-23T08:59:00+09:00",
        )
        before = (
            _counts(connection),
            connection.execute(
                f"SELECT key_hash, opened_count, first_opened_at, last_opened_at "
                f"FROM {share_store.TABLE_SHARE_LINKS} ORDER BY key_hash"
            ).fetchall(),
        )
        for key, host in (
            (CAPABILITY, "192.0.2.1"),
            (expired, "192.0.2.2"),
            (missing, "192.0.2.3"),
        ):
            assert not share_store.mark_opened(
                connection,
                key,
                "2026-08-23T12:00:00+09:00",
                requester_hash=access_control.requester_hash_of(key, host),
            )
        after = (
            _counts(connection),
            connection.execute(
                f"SELECT key_hash, opened_count, first_opened_at, last_opened_at "
                f"FROM {share_store.TABLE_SHARE_LINKS} ORDER BY key_hash"
            ).fetchall(),
        )
        assert after == before


def test_partial_UNIQUE_index선점은_bounded_schema로_오인하지않는다() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(share_store.CREATE_SQL)
        connection.execute(share_store.CREATE_OPEN_WINDOWS_SQL)
        connection.execute(
            f"CREATE UNIQUE INDEX {share_store.INDEX_OPEN_WINDOWS_LINK_WINDOW} "
            f"ON {share_store.TABLE_OPEN_WINDOWS}(link_key_hash, window_started_at) "
            "WHERE opened_count = 1"
        )
        with pytest.raises(RuntimeError, match="unique index"):
            share_store.ensure_schema(connection)
    finally:
        connection.close()
