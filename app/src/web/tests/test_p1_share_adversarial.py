"""공유 LINK가 개인정보 없이도 비용 경계와 보존 계약을 지키는지 공격한다."""

from __future__ import annotations

import datetime as dt
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.core import clock
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


def test_FK_OFF_긴시간공격에도_window만_링크별상한이고_subject는_항상빈다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "share.sqlite3"
    _create_database(database)
    start = dt.datetime(2026, 8, 23, 9, 0, tzinfo=clock.KST)

    with _connect(database, foreign_keys=False) as connection:
        for index in range(constants.OPEN_WINDOW_ROWS_PER_LINK + 6):
            now_iso = (
                start + dt.timedelta(seconds=(constants.ACCESS_WINDOW_SECONDS + 1) * index)
            ).isoformat(timespec="seconds")
            assert share_store.mark_opened(connection, CAPABILITY, now_iso)

        assert _counts(connection) == (
            0,
            constants.OPEN_WINDOW_ROWS_PER_LINK,
            0,
        )
        stored = share_store.load(connection, CAPABILITY)
        assert stored is not None
        assert stored.opened_count == constants.OPEN_WINDOW_ROWS_PER_LINK + 6

    assert CAPABILITY.encode("ascii") not in database.read_bytes()


def test_기존_subject만_secure_delete하고_window와전체횟수는_보존한다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-subject.sqlite3"
    key_hash = share_store.key_hash_of(CAPABILITY)
    first = "2026-08-23T10:00:05+09:00"
    last = "2026-08-23T10:00:25+09:00"

    with _connect(database) as connection:
        connection.execute(share_store.CREATE_SQL)
        connection.execute(share_store.CREATE_OPEN_WINDOWS_SQL)
        connection.execute(share_store.CREATE_OPEN_WINDOWS_UNIQUE_INDEX_SQL)
        connection.execute(share_store.CREATE_ACCESS_SUBJECTS_SQL)
        connection.execute(share_store.CREATE_ACCESS_SUBJECTS_UNIQUE_INDEX_SQL)
        connection.execute(
            f"""INSERT INTO {share_store.TABLE_SHARE_LINKS}
                (key_hash, company, job, created_at, opened_count,
                 first_opened_at, last_opened_at)
                VALUES (?, '과거 회사', '과거 직무', ?, 7, ?, ?)""",
            (key_hash, CREATED_AT, first, last),
        )
        window_id = int(
            connection.execute(
                f"""INSERT INTO {share_store.TABLE_OPEN_WINDOWS}
                    (link_key_hash, window_started_at, opened_count,
                     first_opened_at, last_opened_at)
                    VALUES (?, ?, 7, ?, ?) RETURNING id""",
                (key_hash, "2026-08-23T01:00:00+00:00", first, last),
            ).fetchone()[0]
        )
        connection.executemany(
            f"""INSERT INTO {share_store.TABLE_ACCESS_SUBJECTS}
                (window_id, requester_hash, opened_count) VALUES (?, ?, ?)""",
            ((window_id, "a" * 64, 3), (window_id, "b" * 64, 4)),
        )
        connection.commit()

        share_store.ensure_schema(connection)

        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute(
            f"SELECT COUNT(*) FROM {share_store.TABLE_ACCESS_SUBJECTS}"
        ).fetchone()[0] == 0
        assert connection.execute(
            f"""SELECT opened_count, first_opened_at, last_opened_at
                FROM {share_store.TABLE_OPEN_WINDOWS}"""
        ).fetchone() == (7, first, last)
        stored = share_store.load(connection, CAPABILITY)
        assert stored is not None
        assert (stored.opened_count, stored.first_opened_at, stored.last_opened_at) == (
            7,
            first,
            last,
        )

    raw_database = database.read_bytes()
    assert ("a" * 64).encode("ascii") not in raw_database
    assert ("b" * 64).encode("ascii") not in raw_database


def test_개인정보_tombstone_migration은_멱등이고_두번째에는_행삭제도없다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "idempotent.sqlite3"
    with _connect(database) as connection:
        share_store.ensure_schema(connection)
        connection.commit()
        changes_before = connection.total_changes

        share_store.ensure_schema(connection)

        assert connection.total_changes == changes_before
        assert _counts(connection) == (0, 0, 0)


def test_옛코드가_subject를_다시쓰려하면_DB가_fail_closed한다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rollback.sqlite3"
    _create_database(database)
    with _connect(database) as connection:
        assert share_store.mark_opened(
            connection,
            CAPABILITY,
            "2026-08-23T10:00:05+09:00",
        )
        window_id = int(
            connection.execute(
                f"SELECT id FROM {share_store.TABLE_OPEN_WINDOWS}"
            ).fetchone()[0]
        )

        with pytest.raises(sqlite3.IntegrityError, match="수집은 폐기"):
            connection.execute(
                f"""INSERT INTO {share_store.TABLE_ACCESS_SUBJECTS}
                    (window_id, requester_hash, opened_count) VALUES (?, ?, 1)""",
                (window_id, "c" * 64),
            )

        trigger_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
                (share_store.TABLE_ACCESS_SUBJECTS,),
            )
        }
        assert trigger_names == {
            f"{share_store.TABLE_ACCESS_SUBJECTS}_no_insert",
            f"{share_store.TABLE_ACCESS_SUBJECTS}_no_update",
        }
        assert _counts(connection) == (0, 1, 0)


def test_다중SQLite연결의_동시GET도_전역60개를_정확히_넘지않는다(
    tmp_path: Path,
) -> None:
    database = tmp_path / "share.sqlite3"
    _create_database(database)
    attempts = constants.ACCESS_PER_CAPABILITY_LIMIT + 20
    now_iso = "2026-08-23T11:00:20+09:00"

    def attempt(_index: int) -> bool:
        with _connect(database) as connection:
            return share_store.mark_opened(connection, CAPABILITY, now_iso)

    with ThreadPoolExecutor(max_workers=16) as pool:
        accepted = sum(pool.map(attempt, range(attempts)))

    assert accepted == constants.ACCESS_PER_CAPABILITY_LIMIT
    with _connect(database) as connection:
        assert _counts(connection) == (0, 1, 0)
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
        for key in (CAPABILITY, expired, missing):
            assert not share_store.mark_opened(
                connection,
                key,
                "2026-08-23T12:00:00+09:00",
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
