from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.features.report_delivery.canonical import utc_text
from src.features.report_delivery.singleflight import (
    AcquireDisposition,
    INDEX_ONE_ACTIVE_GENERATION,
    LeaseKey,
    TABLE_SINGLEFLIGHT_LEASES,
    acquire,
    complete,
    completed_result_matches,
    ensure_schema,
    expire_completed_result,
    fail,
    heartbeat,
)


def _key(bucket: str = "bucket-hash-a") -> LeaseKey:
    return LeaseKey(
        billing_bucket_id=bucket,
        corp_id="00126380",
        cache_namespace_id="cache-namespace-a",
        source_identity_digest="source-digest-a",
        engine_epoch_digest="a" * 64,
    )


def _legacy_ensure_schema(conn: sqlite3.Connection) -> None:
    """d00e538 바이너리의 epoch 없는 4열 PK schema를 그대로 모사한다."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_SINGLEFLIGHT_LEASES} (
            billing_bucket_id TEXT NOT NULL,
            corp_id TEXT NOT NULL,
            cache_namespace_id TEXT NOT NULL,
            source_identity_digest TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('active', 'completed', 'failed')),
            owner_id TEXT NOT NULL,
            lease_token TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            completed_content_id TEXT NOT NULL,
            completed_artifact_id TEXT NOT NULL,
            failure_code TEXT NOT NULL,
            PRIMARY KEY (
                billing_bucket_id, corp_id, cache_namespace_id,
                source_identity_digest
            )
        )
        """
    )


def _legacy_insert_active(
    conn: sqlite3.Connection,
    *,
    namespace_id: str,
    owner_id: str,
    now: dt.datetime,
    expires_at: dt.datetime,
    or_ignore: bool = True,
) -> sqlite3.Cursor:
    """d00e538 acquire의 engine_epoch_digest 없는 INSERT를 실행한다."""

    _legacy_ensure_schema(conn)
    ignore = " OR IGNORE" if or_ignore else ""
    now_text = utc_text(now, label="구 lease 시작")
    expires_text = utc_text(expires_at, label="구 lease 만료")
    return conn.execute(
        f"""
        INSERT{ignore} INTO {TABLE_SINGLEFLIGHT_LEASES} (
            billing_bucket_id, corp_id, cache_namespace_id,
            source_identity_digest, state, owner_id, lease_token,
            fencing_token, acquired_at, heartbeat_at, expires_at,
            completed_content_id, completed_artifact_id, failure_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "bucket-hash-a",
            "00126380",
            namespace_id,
            "source-digest-a",
            "active",
            owner_id,
            f"{owner_id}-token",
            1,
            now_text,
            now_text,
            expires_text,
            "",
            "",
            "",
        ),
    )


@contextmanager
def _file_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(path), timeout=1.0)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def test_same_bucket_and_generation_waits_for_one_owner(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    first = acquire(
        conn,
        key=_key(),
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )
    second = acquire(
        conn,
        key=_key(),
        owner_id="worker-b",
        now=now + dt.timedelta(seconds=1),
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert first.disposition is AcquireDisposition.ACQUIRED
    assert first.handle is not None
    assert second.disposition is AcquireDisposition.WAIT
    assert second.handle is not None
    assert second.handle.lease_token == first.handle.lease_token


def test_different_billing_buckets_never_share_one_paid_owner(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    first = acquire(
        conn,
        key=_key("bucket-hash-a"),
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )
    second = acquire(
        conn,
        key=_key("bucket-hash-b"),
        owner_id="worker-b",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert first.disposition is AcquireDisposition.ACQUIRED
    assert second.disposition is AcquireDisposition.ACQUIRED
    assert first.handle is not None and second.handle is not None
    assert first.handle.lease_token != second.handle.lease_token


def test_다른engine_epoch는_활성provider만_기다리고_그결과는_재사용하지않는다(
    conn: sqlite3.Connection,
    now: dt.datetime,
) -> None:
    key_a = _key()
    key_b = LeaseKey(
        billing_bucket_id=key_a.billing_bucket_id,
        corp_id=key_a.corp_id,
        cache_namespace_id=key_a.cache_namespace_id,
        source_identity_digest=key_a.source_identity_digest,
        engine_epoch_digest="b" * 64,
    )

    owner_a = acquire(
        conn,
        key=key_a,
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )
    waiting_b = acquire(
        conn,
        key=key_b,
        owner_id="worker-b",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )
    owner_b = acquire(
        conn,
        key=key_b,
        owner_id="worker-b",
        now=now + dt.timedelta(seconds=31),
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert owner_a.disposition is AcquireDisposition.ACQUIRED
    assert waiting_b.disposition is AcquireDisposition.WAIT
    assert waiting_b.handle is None
    assert owner_b.disposition is AcquireDisposition.ACQUIRED
    assert owner_a.handle is not None and owner_b.handle is not None
    assert owner_a.handle.lease_token != owner_b.handle.lease_token


def test_교대배포로_namespace와epoch가_둘다달라도_활성provider를_기다린다(
    conn: sqlite3.Connection,
    now: dt.datetime,
) -> None:
    key_a = _key()
    key_b = LeaseKey(
        billing_bucket_id=key_a.billing_bucket_id,
        corp_id=key_a.corp_id,
        cache_namespace_id="cache-namespace-b",
        source_identity_digest=key_a.source_identity_digest,
        engine_epoch_digest="b" * 64,
    )

    owner_a = acquire(
        conn,
        key=key_a,
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )
    waiting_b = acquire(
        conn,
        key=key_b,
        owner_id="worker-b",
        now=now + dt.timedelta(seconds=1),
        lease_ttl=dt.timedelta(seconds=30),
    )
    owner_b = acquire(
        conn,
        key=key_b,
        owner_id="worker-b",
        now=now + dt.timedelta(seconds=31),
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert owner_a.disposition is AcquireDisposition.ACQUIRED
    assert waiting_b.disposition is AcquireDisposition.WAIT
    assert waiting_b.handle is None
    assert owner_b.disposition is AcquireDisposition.ACQUIRED


def test_옛4열PK_singleflight표는_활성행을_격리하고_5열epoch표로_교체한다(
    conn: sqlite3.Connection,
    now: dt.datetime,
) -> None:
    conn.execute(
        f"""
        CREATE TABLE {TABLE_SINGLEFLIGHT_LEASES} (
            billing_bucket_id TEXT NOT NULL,
            corp_id TEXT NOT NULL,
            cache_namespace_id TEXT NOT NULL,
            source_identity_digest TEXT NOT NULL,
            state TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            lease_token TEXT NOT NULL,
            fencing_token INTEGER NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            completed_content_id TEXT NOT NULL,
            completed_artifact_id TEXT NOT NULL,
            failure_code TEXT NOT NULL,
            PRIMARY KEY (
                billing_bucket_id, corp_id, cache_namespace_id,
                source_identity_digest
            )
        )
        """
    )
    now_text = utc_text(now, label="옛 lease 시작")
    expires_text = utc_text(
        now + dt.timedelta(seconds=30),
        label="옛 lease 만료",
    )
    conn.execute(
        f"INSERT INTO {TABLE_SINGLEFLIGHT_LEASES} VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "bucket-hash-a",
            "00126380",
            "cache-namespace-a",
            "source-digest-a",
            "active",
            "legacy-worker",
            "legacy-token",
            7,
            now_text,
            now_text,
            expires_text,
            "",
            "",
            "",
        ),
    )

    ensure_schema(conn)

    primary_key = tuple(
        str(row[1])
        for row in sorted(
            (
                row
                for row in conn.execute(
                    f'PRAGMA table_info("{TABLE_SINGLEFLIGHT_LEASES}")'
                ).fetchall()
                if int(row[5]) > 0
            ),
            key=lambda row: int(row[5]),
        )
    )
    assert primary_key == (
        "billing_bucket_id",
        "corp_id",
        "cache_namespace_id",
        "source_identity_digest",
        "engine_epoch_digest",
    )
    legacy = conn.execute(
        f"SELECT engine_epoch_digest, owner_id FROM {TABLE_SINGLEFLIGHT_LEASES}"
    ).fetchone()
    assert legacy == ("", "legacy-worker")
    waiting = acquire(
        conn,
        key=_key(),
        owner_id="worker-after-migration",
        now=now + dt.timedelta(seconds=1),
        lease_ttl=dt.timedelta(seconds=30),
    )
    acquired = acquire(
        conn,
        key=_key(),
        owner_id="worker-after-expiry",
        now=now + dt.timedelta(seconds=31),
        lease_ttl=dt.timedelta(seconds=30),
    )
    assert waiting.disposition is AcquireDisposition.WAIT
    assert waiting.handle is None
    assert acquired.disposition is AcquireDisposition.ACQUIRED


def test_구바이너리가_먼저잡은_실제SQLite를_신바이너리가_기다린다(
    tmp_path: Path,
    now: dt.datetime,
) -> None:
    database = tmp_path / "legacy-first.sqlite3"
    with _file_connection(database) as legacy_conn:
        inserted = _legacy_insert_active(
            legacy_conn,
            namespace_id="cache-namespace-legacy",
            owner_id="legacy-worker",
            now=now,
            expires_at=now + dt.timedelta(seconds=30),
        )
        assert inserted.rowcount == 1

    current_key = LeaseKey(
        billing_bucket_id="bucket-hash-a",
        corp_id="00126380",
        cache_namespace_id="cache-namespace-current",
        source_identity_digest="source-digest-a",
        engine_epoch_digest="b" * 64,
    )
    with _file_connection(database) as current_conn:
        waiting = acquire(
            current_conn,
            key=current_key,
            owner_id="current-worker",
            now=now + dt.timedelta(seconds=1),
            lease_ttl=dt.timedelta(seconds=30),
        )
        indexes = {
            str(row[1])
            for row in current_conn.execute(
                f'PRAGMA index_list("{TABLE_SINGLEFLIGHT_LEASES}")'
            ).fetchall()
        }
        active_count = current_conn.execute(
            f"SELECT COUNT(*) FROM {TABLE_SINGLEFLIGHT_LEASES} "
            "WHERE state = 'active'"
        ).fetchone()[0]

    assert waiting.disposition is AcquireDisposition.WAIT
    assert waiting.handle is None
    assert INDEX_ONE_ACTIVE_GENERATION in indexes
    assert active_count == 1


def test_신바이너리가_먼저잡으면_구바이너리의_빈epoch_INSERT도_DB가_거절한다(
    tmp_path: Path,
    now: dt.datetime,
) -> None:
    database = tmp_path / "current-first.sqlite3"
    current_key = LeaseKey(
        billing_bucket_id="bucket-hash-a",
        corp_id="00126380",
        cache_namespace_id="cache-namespace-current",
        source_identity_digest="source-digest-a",
        engine_epoch_digest="b" * 64,
    )
    with _file_connection(database) as current_conn:
        current = acquire(
            current_conn,
            key=current_key,
            owner_id="current-worker",
            now=now,
            lease_ttl=dt.timedelta(seconds=30),
        )
        assert current.disposition is AcquireDisposition.ACQUIRED

    with _file_connection(database) as legacy_conn:
        ignored = _legacy_insert_active(
            legacy_conn,
            namespace_id="cache-namespace-legacy",
            owner_id="legacy-worker",
            now=now + dt.timedelta(seconds=1),
            expires_at=now + dt.timedelta(seconds=31),
        )
        assert ignored.rowcount == 0
        with pytest.raises(sqlite3.IntegrityError):
            _legacy_insert_active(
                legacy_conn,
                namespace_id="cache-namespace-legacy",
                owner_id="legacy-worker-direct",
                now=now + dt.timedelta(seconds=1),
                expires_at=now + dt.timedelta(seconds=31),
                or_ignore=False,
            )
        active_rows = legacy_conn.execute(
            f"""
            SELECT cache_namespace_id, engine_epoch_digest, owner_id
            FROM {TABLE_SINGLEFLIGHT_LEASES}
            WHERE state = 'active'
            """
        ).fetchall()

    assert active_rows == [
        ("cache-namespace-current", "b" * 64, "current-worker")
    ]


def test_만료시각과_정확히_같은_takeover는_정리와_획득을_한거래에서_끝낸다(
    tmp_path: Path,
    now: dt.datetime,
) -> None:
    database = tmp_path / "takeover-boundary.sqlite3"
    with _file_connection(database) as first_conn:
        first = acquire(
            first_conn,
            key=_key(),
            owner_id="worker-expiring",
            now=now,
            lease_ttl=dt.timedelta(seconds=30),
        )
        assert first.handle is not None

    with _file_connection(database) as takeover_conn:
        takeover = acquire(
            takeover_conn,
            key=_key(),
            owner_id="worker-boundary",
            now=now + dt.timedelta(seconds=30),
            lease_ttl=dt.timedelta(seconds=30),
        )
        active_count = takeover_conn.execute(
            f"SELECT COUNT(*) FROM {TABLE_SINGLEFLIGHT_LEASES} "
            "WHERE state = 'active'"
        ).fetchone()[0]

    assert takeover.disposition is AcquireDisposition.TAKEOVER
    assert takeover.handle is not None
    assert takeover.handle.fencing_token == first.handle.fencing_token + 1
    assert active_count == 1


def test_만료시각과_같은_foreign행은_닫고_새epoch하나만_획득한다(
    tmp_path: Path,
    now: dt.datetime,
) -> None:
    database = tmp_path / "foreign-boundary.sqlite3"
    with _file_connection(database) as first_conn:
        first = acquire(
            first_conn,
            key=_key(),
            owner_id="worker-old-epoch",
            now=now,
            lease_ttl=dt.timedelta(seconds=30),
        )
        assert first.disposition is AcquireDisposition.ACQUIRED

    new_key = LeaseKey(
        billing_bucket_id="bucket-hash-a",
        corp_id="00126380",
        cache_namespace_id="cache-namespace-b",
        source_identity_digest="source-digest-a",
        engine_epoch_digest="b" * 64,
    )
    with _file_connection(database) as second_conn:
        second = acquire(
            second_conn,
            key=new_key,
            owner_id="worker-new-epoch",
            now=now + dt.timedelta(seconds=30),
            lease_ttl=dt.timedelta(seconds=30),
        )
        rows = second_conn.execute(
            f"""
            SELECT cache_namespace_id, engine_epoch_digest, state, failure_code
            FROM {TABLE_SINGLEFLIGHT_LEASES}
            ORDER BY cache_namespace_id
            """
        ).fetchall()

    assert second.disposition is AcquireDisposition.ACQUIRED
    assert rows == [
        ("cache-namespace-a", "a" * 64, "failed", "LEASE_EXPIRED"),
        ("cache-namespace-b", "b" * 64, "active", ""),
    ]


def test_foreign_epoch의_완료ID는_재사용하지않고_새owner를_얻는다(
    tmp_path: Path,
    now: dt.datetime,
) -> None:
    database = tmp_path / "foreign-completed.sqlite3"
    with _file_connection(database) as first_conn:
        first = acquire(
            first_conn,
            key=_key(),
            owner_id="worker-old-epoch",
            now=now,
            lease_ttl=dt.timedelta(seconds=30),
        )
        assert first.handle is not None
        assert complete(
            first_conn,
            handle=first.handle,
            content_snapshot_id="foreign-content",
            artifact_id="foreign-artifact",
            now=now + dt.timedelta(seconds=1),
            result_fanout_ttl=dt.timedelta(seconds=30),
        )

    new_key = LeaseKey(
        billing_bucket_id="bucket-hash-a",
        corp_id="00126380",
        cache_namespace_id="cache-namespace-b",
        source_identity_digest="source-digest-a",
        engine_epoch_digest="b" * 64,
    )
    with _file_connection(database) as second_conn:
        second = acquire(
            second_conn,
            key=new_key,
            owner_id="worker-new-epoch",
            now=now + dt.timedelta(seconds=2),
            lease_ttl=dt.timedelta(seconds=30),
        )

    assert second.disposition is AcquireDisposition.ACQUIRED
    assert second.handle is not None
    assert second.completed_content_id == ""
    assert second.completed_artifact_id == ""


def test_중복ACTIVE가_남은결함DB는_하나만_장벽으로_보존하고_제약을_건다(
    tmp_path: Path,
    now: dt.datetime,
) -> None:
    database = tmp_path / "duplicate-active-migration.sqlite3"
    with _file_connection(database) as broken_conn:
        _legacy_insert_active(
            broken_conn,
            namespace_id="cache-namespace-a",
            owner_id="worker-a",
            now=now,
            expires_at=now + dt.timedelta(seconds=20),
        )
        _legacy_insert_active(
            broken_conn,
            namespace_id="cache-namespace-b",
            owner_id="worker-b",
            now=now,
            expires_at=now + dt.timedelta(seconds=30),
        )

    with _file_connection(database) as migrated_conn:
        ensure_schema(migrated_conn)
        rows = migrated_conn.execute(
            f"""
            SELECT cache_namespace_id, state, failure_code
            FROM {TABLE_SINGLEFLIGHT_LEASES}
            ORDER BY cache_namespace_id
            """
        ).fetchall()
        indexes = {
            str(row[1])
            for row in migrated_conn.execute(
                f'PRAGMA index_list("{TABLE_SINGLEFLIGHT_LEASES}")'
            ).fetchall()
        }

    assert rows == [
        ("cache-namespace-a", "failed", "DUPLICATE_ACTIVE_MIGRATED"),
        ("cache-namespace-b", "active", ""),
    ]
    assert INDEX_ONE_ACTIVE_GENERATION in indexes


def test_crashed_owner_is_taken_over_with_higher_fencing_token(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    first = acquire(
        conn,
        key=_key(),
        owner_id="worker-crashed",
        now=now,
        lease_ttl=dt.timedelta(seconds=30),
    )
    assert first.handle is not None
    takeover_time = now + dt.timedelta(seconds=31)

    takeover = acquire(
        conn,
        key=_key(),
        owner_id="worker-recovery",
        now=takeover_time,
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert takeover.disposition is AcquireDisposition.TAKEOVER
    assert takeover.handle is not None
    assert takeover.handle.fencing_token == first.handle.fencing_token + 1
    assert (
        heartbeat(
            conn,
            handle=first.handle,
            now=takeover_time,
            lease_ttl=dt.timedelta(seconds=30),
        )
        is None
    )
    assert not complete(
        conn,
        handle=first.handle,
        content_snapshot_id="content-from-stale-worker",
        artifact_id="artifact-from-stale-worker",
        now=takeover_time,
        result_fanout_ttl=dt.timedelta(seconds=30),
    )


def test_completed_result_is_fanned_out_then_expires_for_fresh_cache_decision(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    acquired = acquire(
        conn,
        key=_key(),
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=60),
    )
    assert acquired.handle is not None
    assert complete(
        conn,
        handle=acquired.handle,
        content_snapshot_id="content-complete",
        artifact_id="artifact-complete",
        now=now + dt.timedelta(seconds=10),
        result_fanout_ttl=dt.timedelta(seconds=20),
    )

    waiter = acquire(
        conn,
        key=_key(),
        owner_id="worker-waiter",
        now=now + dt.timedelta(seconds=15),
        lease_ttl=dt.timedelta(seconds=30),
    )
    after_fanout = acquire(
        conn,
        key=_key(),
        owner_id="worker-new-generation",
        now=now + dt.timedelta(seconds=31),
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert waiter.disposition is AcquireDisposition.COMPLETED
    assert waiter.completed_content_id == "content-complete"
    assert waiter.completed_artifact_id == "artifact-complete"
    assert after_fanout.disposition is AcquireDisposition.TAKEOVER
    assert after_fanout.handle is not None
    assert after_fanout.handle.fencing_token == acquired.handle.fencing_token + 1


def test_완료증거는_열쇠와_두원본과_유효시간이_전부_같아야한다(
    conn: sqlite3.Connection,
    now: dt.datetime,
) -> None:
    acquired = acquire(
        conn,
        key=_key(),
        owner_id="worker-proof",
        now=now,
        lease_ttl=dt.timedelta(seconds=60),
    )
    assert acquired.handle is not None
    assert complete(
        conn,
        handle=acquired.handle,
        content_snapshot_id="content-proof",
        artifact_id="artifact-proof",
        now=now + dt.timedelta(seconds=5),
        result_fanout_ttl=dt.timedelta(seconds=20),
    )

    assert completed_result_matches(
        conn,
        key=_key(),
        content_snapshot_id="content-proof",
        artifact_id="artifact-proof",
        now=now + dt.timedelta(seconds=10),
    )
    assert not completed_result_matches(
        conn,
        key=_key(),
        content_snapshot_id="content-proof",
        artifact_id="artifact-other",
        now=now + dt.timedelta(seconds=10),
    )
    assert not completed_result_matches(
        conn,
        key=_key(),
        content_snapshot_id="content-proof",
        artifact_id="artifact-proof",
        now=now + dt.timedelta(seconds=26),
    )


def test_손상cache와_같은완료결과만_즉시만료하고_fencing은_이어간다(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    acquired = acquire(
        conn,
        key=_key(),
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=60),
    )
    assert acquired.handle is not None
    assert complete(
        conn,
        handle=acquired.handle,
        content_snapshot_id="content-corrupt",
        artifact_id="artifact-corrupt",
        now=now + dt.timedelta(seconds=5),
        result_fanout_ttl=dt.timedelta(minutes=2),
    )

    # 다른 두 원본 ID로는 살아 있는 완료 fan-out을 건드릴 수 없다.
    assert not expire_completed_result(
        conn,
        key=_key(),
        content_snapshot_id="content-other",
        artifact_id="artifact-corrupt",
        now=now + dt.timedelta(seconds=6),
    )
    assert expire_completed_result(
        conn,
        key=_key(),
        content_snapshot_id="content-corrupt",
        artifact_id="artifact-corrupt",
        now=now + dt.timedelta(seconds=6),
    )

    takeover = acquire(
        conn,
        key=_key(),
        owner_id="worker-recovery",
        now=now + dt.timedelta(seconds=7),
        lease_ttl=dt.timedelta(seconds=60),
    )
    assert takeover.disposition is AcquireDisposition.TAKEOVER
    assert takeover.handle is not None
    assert takeover.handle.fencing_token == acquired.handle.fencing_token + 1


def test_failed_result_is_shared_briefly_then_retry_can_take_over(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    acquired = acquire(
        conn,
        key=_key(),
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=60),
    )
    assert acquired.handle is not None
    assert fail(
        conn,
        handle=acquired.handle,
        failure_code="PROVIDER_TIMEOUT",
        now=now + dt.timedelta(seconds=10),
        failure_fanout_ttl=dt.timedelta(seconds=20),
    )

    waiter = acquire(
        conn,
        key=_key(),
        owner_id="worker-waiter",
        now=now + dt.timedelta(seconds=15),
        lease_ttl=dt.timedelta(seconds=30),
    )
    retry = acquire(
        conn,
        key=_key(),
        owner_id="worker-retry",
        now=now + dt.timedelta(seconds=31),
        lease_ttl=dt.timedelta(seconds=30),
    )

    assert waiter.disposition is AcquireDisposition.FAILED
    assert waiter.failure_code == "PROVIDER_TIMEOUT"
    assert retry.disposition is AcquireDisposition.TAKEOVER


def test_heartbeat_extends_only_current_unexpired_owner(
    conn: sqlite3.Connection, now: dt.datetime
) -> None:
    acquired = acquire(
        conn,
        key=_key(),
        owner_id="worker-a",
        now=now,
        lease_ttl=dt.timedelta(seconds=10),
    )
    assert acquired.handle is not None
    extended = heartbeat(
        conn,
        handle=acquired.handle,
        now=now + dt.timedelta(seconds=5),
        lease_ttl=dt.timedelta(seconds=10),
    )
    assert extended is not None

    before_new_expiry = acquire(
        conn,
        key=_key(),
        owner_id="worker-b",
        now=now + dt.timedelta(seconds=11),
        lease_ttl=dt.timedelta(seconds=10),
    )
    after_new_expiry = acquire(
        conn,
        key=_key(),
        owner_id="worker-c",
        now=now + dt.timedelta(seconds=16),
        lease_ttl=dt.timedelta(seconds=10),
    )

    assert before_new_expiry.disposition is AcquireDisposition.WAIT
    assert after_new_expiry.disposition is AcquireDisposition.TAKEOVER
