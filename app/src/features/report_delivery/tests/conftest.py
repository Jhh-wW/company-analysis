from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.models import ContentSnapshot
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.report_delivery.store import (
    save_cache_namespace,
    save_content_snapshot,
    save_source_snapshot,
)


@pytest.fixture
def now() -> dt.datetime:
    return dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def source(now: dt.datetime) -> SourceSnapshot:
    return SourceSnapshot.capture(
        dart_receipt_nos=("20260828000123",),
        financial_payload={
            "status": "000",
            "message": "정상",
            "list": [
                {
                    "account_nm": "매출액",
                    "thstrm_amount": "1000000",
                    "thstrm_dt": "2025.12.31",
                }
            ],
        },
        captured_at=now,
        source_as_of=now.date(),
        adapter_versions={"dart": "2"},
    )


@pytest.fixture
def namespace() -> CacheNamespace:
    return CacheNamespace.create(
        product="company-analysis-v2",
        schema_version="v2",
        deployment_revision="05dfb49",
        requested_models={"writer": "claude-writer", "reviewer": "claude-reviewer"},
        output_settings={"temperature": 0, "writer_max_tokens": 8000},
    )


@pytest.fixture
def content(
    conn: sqlite3.Connection,
    source: SourceSnapshot,
    namespace: CacheNamespace,
    now: dt.datetime,
) -> ContentSnapshot:
    snapshot = ContentSnapshot.create(
        payload=b'{"company":"sample","sections":[]}',
        source_snapshot=source,
        cache_namespace=namespace,
        content_generated_at=now,
        actual_models=("claude-writer", "claude-reviewer"),
    )
    save_source_snapshot(conn, source)
    save_cache_namespace(conn, namespace)
    save_content_snapshot(conn, snapshot)
    return snapshot
