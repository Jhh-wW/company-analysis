from __future__ import annotations

import datetime as dt
import sqlite3

from src.features.report_delivery.singleflight import (
    AcquireDisposition,
    LeaseKey,
    acquire,
    complete,
    completed_result_matches,
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
    )


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
