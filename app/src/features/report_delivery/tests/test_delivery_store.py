from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from src.features.report_delivery.models import (
    ContentSnapshot,
    Delivery,
    DeliveryPolicy,
    DeliveryPolicyError,
)
from src.features.report_delivery.store import (
    DELIVERY_INTENT_COMPLETE,
    DELIVERY_INTENT_FAILED,
    DELIVERY_INTENT_REQUIRED,
    ImmutableRecordConflict,
    LifecycleStoreError,
    LifecycleStoreCorrupt,
    TABLE_CONTENT_SNAPSHOTS,
    TABLE_DELIVERIES,
    delivery_count_for_content,
    load_content_snapshot,
    load_delivery,
    load_delivery_by_public_id,
    load_delivery_intent,
    mark_delivery_complete,
    mark_delivery_failed,
    mark_delivery_required,
    save_delivery,
)


def test_59_day_cached_content_gets_full_new_delivery_lifetime(
    content: ContentSnapshot, now: dt.datetime
) -> None:
    delivered_at = now + dt.timedelta(days=59)
    policy = DeliveryPolicy(
        content_max_age=dt.timedelta(days=60),
        public_link_lifetime=dt.timedelta(days=60),
    )

    delivery = Delivery.issue(
        public_id="public-new-link",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=delivered_at,
        policy=policy,
        reused_from_cache=True,
    )

    assert delivery.expires_at == delivered_at + dt.timedelta(days=60)
    assert delivery.expires_at != content.content_generated_at + dt.timedelta(days=60)
    assert delivery.cache_origin_content_id == content.content_id


def test_exact_content_age_boundary_is_not_repackaged(
    content: ContentSnapshot, now: dt.datetime
) -> None:
    policy = DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60))

    with pytest.raises(DeliveryPolicyError, match="최대 나이"):
        Delivery.issue(
            public_id="public-stale",
            billing_bucket_id="bucket-hash-a",
            content=content,
            delivered_at=now + dt.timedelta(days=60),
            policy=policy,
            reused_from_cache=True,
        )


def test_multiple_deliveries_reference_one_content_without_payload_copy(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
) -> None:
    policy = DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60))
    first = Delivery.issue(
        public_id="public-a",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=now + dt.timedelta(days=1),
        policy=policy,
        reused_from_cache=True,
    )
    second = Delivery.issue(
        public_id="public-b",
        billing_bucket_id="bucket-hash-b",
        content=content,
        delivered_at=now + dt.timedelta(days=2),
        policy=policy,
        reused_from_cache=True,
    )

    save_delivery(conn, first)
    save_delivery(conn, second)

    assert delivery_count_for_content(conn, content.content_id) == 2
    assert load_delivery(conn, first.delivery_id) == first
    assert load_delivery(conn, second.delivery_id) == second
    report_rows = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_CONTENT_SNAPSHOTS} WHERE content_id = ?",
        (content.content_id,),
    ).fetchone()
    assert report_rows == (1,)


def test_public_id_cannot_be_rebound_to_a_new_delivery(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
) -> None:
    policy = DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60))
    original = Delivery.issue(
        public_id="public-fixed",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=now,
        policy=policy,
        reused_from_cache=False,
    )
    conflicting = Delivery.issue(
        public_id="public-fixed",
        billing_bucket_id="bucket-hash-b",
        content=content,
        delivered_at=now + dt.timedelta(minutes=1),
        policy=policy,
        reused_from_cache=True,
    )
    save_delivery(conn, original)

    with pytest.raises(ImmutableRecordConflict, match="delivery ID"):
        save_delivery(conn, conflicting)


def test_delivery_can_be_loaded_by_web_job_public_id(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
) -> None:
    delivery = Delivery.issue(
        public_id="web-job-public-123",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=now,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=False,
    )
    save_delivery(conn, delivery)

    assert load_delivery_by_public_id(conn, "web-job-public-123") == delivery


def test_unknown_public_id_returns_none(conn: sqlite3.Connection) -> None:
    assert load_delivery_by_public_id(conn, "not-issued") is None


def test_새보고서_delivery의무는_실패해도_legacy와_구분해_남는다(
    conn: sqlite3.Connection,
    now: dt.datetime,
) -> None:
    required = mark_delivery_required(
        conn,
        public_id="new-report-failed",
        required_at=now,
    )
    assert required.state == DELIVERY_INTENT_REQUIRED

    failed = mark_delivery_failed(
        conn,
        public_id=required.public_id,
        failure_code="artifact_finalization_failed",
        failed_at=now + dt.timedelta(seconds=1),
    )

    assert failed.state == DELIVERY_INTENT_FAILED
    assert load_delivery_intent(conn, required.public_id) == failed


def test_delivery의무는_실제_delivery와_같이만_완료된다(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
) -> None:
    public_id = "new-report-complete"
    mark_delivery_required(conn, public_id=public_id, required_at=now)
    with pytest.raises(LifecycleStoreError, match="실제 delivery"):
        mark_delivery_complete(
            conn,
            public_id=public_id,
            completed_at=now + dt.timedelta(seconds=1),
        )

    delivery = Delivery.issue(
        public_id=public_id,
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=now,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=False,
    )
    save_delivery(conn, delivery)
    completed = mark_delivery_complete(
        conn,
        public_id=public_id,
        completed_at=now + dt.timedelta(seconds=1),
    )

    assert completed.state == DELIVERY_INTENT_COMPLETE


def test_public_id_lookup_rejects_corrupt_delivery_metadata(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
    now: dt.datetime,
) -> None:
    delivery = Delivery.issue(
        public_id="web-job-corrupt",
        billing_bucket_id="bucket-hash-a",
        content=content,
        delivered_at=now,
        policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
        reused_from_cache=False,
    )
    save_delivery(conn, delivery)
    conn.execute(
        f"UPDATE {TABLE_DELIVERIES} "
        "SET billing_bucket_id = ? WHERE delivery_id = ?",
        ("tampered-bucket", delivery.delivery_id),
    )

    with pytest.raises(LifecycleStoreCorrupt, match="delivery metadata"):
        load_delivery_by_public_id(conn, delivery.public_id)


def test_content_checksum_corruption_is_not_returned_as_valid(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
) -> None:
    conn.execute(
        f"UPDATE {TABLE_CONTENT_SNAPSHOTS} SET payload = ? WHERE content_id = ?",
        (b"tampered", content.content_id),
    )

    with pytest.raises(LifecycleStoreCorrupt, match="checksum"):
        load_content_snapshot(conn, content.content_id)


def test_content_source_reference_corruption_is_not_returned_as_valid(
    conn: sqlite3.Connection,
    content: ContentSnapshot,
) -> None:
    conn.execute(
        f"""
        UPDATE {TABLE_CONTENT_SNAPSHOTS}
        SET source_identity_digest = ? WHERE content_id = ?
        """,
        ("0" * 64, content.content_id),
    )

    with pytest.raises(LifecycleStoreCorrupt, match="출처 snapshot 신원"):
        load_content_snapshot(conn, content.content_id)
