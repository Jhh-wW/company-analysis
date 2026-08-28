"""보고서 delivery 슬라이스가 소유한 표를 한 번에 준비한다."""

from __future__ import annotations

import sqlite3

from src.features.report_delivery.artifact import ensure_artifact_schema
from src.features.report_delivery.singleflight import ensure_lease_schema
from src.features.report_delivery.store import ensure_schema


def ensure_report_delivery_schema(conn: sqlite3.Connection) -> None:
    """기존 저장소 bootstrap adapter가 호출할 단일 멱등 진입점."""

    ensure_schema(conn)
    ensure_artifact_schema(conn)
    ensure_lease_schema(conn)
