from __future__ import annotations

import sqlite3

from src.features.report_delivery.artifact import (
    TABLE_ARTIFACTS,
    TABLE_BLOB_INTENTS,
    TABLE_BLOB_INTENT_EVENTS,
    TABLE_DELIVERY_ARTIFACTS,
    ensure_schema as ensure_artifact_schema_only,
)
from src.features.report_delivery.schema import ensure_report_delivery_schema
from src.features.report_delivery.singleflight import (
    TABLE_SINGLEFLIGHT_LEASES,
    ensure_schema as ensure_singleflight_schema_only,
)
from src.features.report_delivery.store import (
    TABLE_CACHE_ENTRIES,
    TABLE_CACHE_INVALIDATIONS,
    TABLE_CACHE_NAMESPACES,
    TABLE_CONTENT_SNAPSHOTS,
    TABLE_DELIVERIES,
    TABLE_DELIVERY_INTENTS,
    TABLE_SOURCE_SNAPSHOTS,
    ensure_schema as ensure_lifecycle_schema_only,
)


def test_schema_bootstrap_is_idempotent_and_complete(conn: sqlite3.Connection) -> None:
    ensure_report_delivery_schema(conn)
    ensure_report_delivery_schema(conn)
    actual = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }

    assert {
        TABLE_SOURCE_SNAPSHOTS,
        TABLE_CACHE_NAMESPACES,
        TABLE_CONTENT_SNAPSHOTS,
        TABLE_DELIVERIES,
        TABLE_DELIVERY_INTENTS,
        TABLE_CACHE_ENTRIES,
        TABLE_CACHE_INVALIDATIONS,
        TABLE_ARTIFACTS,
        TABLE_BLOB_INTENTS,
        TABLE_BLOB_INTENT_EVENTS,
        TABLE_DELIVERY_ARTIFACTS,
        TABLE_SINGLEFLIGHT_LEASES,
    } <= actual


def test_each_create_table_module_exposes_only_its_registry_tables() -> None:
    cases = (
        (
            ensure_lifecycle_schema_only,
            {
                TABLE_SOURCE_SNAPSHOTS,
                TABLE_CACHE_NAMESPACES,
                TABLE_CONTENT_SNAPSHOTS,
                TABLE_DELIVERIES,
                TABLE_DELIVERY_INTENTS,
                TABLE_CACHE_ENTRIES,
                TABLE_CACHE_INVALIDATIONS,
            },
        ),
        (
            ensure_artifact_schema_only,
            {
                TABLE_ARTIFACTS,
                TABLE_BLOB_INTENTS,
                TABLE_BLOB_INTENT_EVENTS,
                TABLE_DELIVERY_ARTIFACTS,
            },
        ),
        (ensure_singleflight_schema_only, {TABLE_SINGLEFLIGHT_LEASES}),
    )
    for bootstrap, expected in cases:
        with sqlite3.connect(":memory:") as connection:
            bootstrap(connection)
            actual = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        assert actual == expected
