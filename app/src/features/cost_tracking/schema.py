"""비용 추적 feature의 의존성 없는 운영 SQLite schema 계약."""

from __future__ import annotations

import sqlite3
from typing import Final


AI_EVENT_TABLE: Final[str] = "ai_variable_cost_events"
RUN_COST_TABLE: Final[str] = "report_cost_summaries"
SERVER_COST_TABLE: Final[str] = "monthly_server_fixed_costs"

CREATE_AI_EVENT_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {AI_EVENT_TABLE} (
    event_id              TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    sequence              INTEGER NOT NULL,
    stage                 TEXT NOT NULL,
    model_id              TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL,
    output_tokens         INTEGER NOT NULL,
    cache_creation_tokens INTEGER NOT NULL,
    cache_read_tokens     INTEGER NOT NULL,
    batch_applied         INTEGER NOT NULL,
    cost_krw              REAL NOT NULL,
    failed_call           INTEGER NOT NULL,
    cache_hit             INTEGER NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (run_id, sequence)
)
"""
CREATE_RUN_COST_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {RUN_COST_TABLE} (
    run_id                   TEXT PRIMARY KEY,
    outcome                  TEXT NOT NULL,
    internal_ai_cost_krw     REAL NOT NULL,
    customer_charge_krw      REAL NOT NULL DEFAULT 0,
    charge_eligible          INTEGER NOT NULL DEFAULT 0,
    automatic_release_sha256 TEXT NOT NULL DEFAULT '',
    charge_reason            TEXT NOT NULL,
    updated_at               TEXT NOT NULL
)
"""
CREATE_SERVER_COST_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {SERVER_COST_TABLE} (
    month       TEXT PRIMARY KEY,
    amount_krw  REAL NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_AI_EVENT_SQL)
    conn.execute(CREATE_RUN_COST_SQL)
    conn.execute(CREATE_SERVER_COST_SQL)
