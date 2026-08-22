"""PDF 출고 feature의 의존성 없는 운영 SQLite schema 계약."""

from __future__ import annotations

import sqlite3
from typing import Final


TABLE_NAME: Final[str] = "pdf_release_records"
DECISION_TABLE_NAME: Final[str] = "pdf_release_role_decisions"
PARTICIPANT_TABLE_NAME: Final[str] = "pdf_release_participants"
AUTOMATIC_TABLE_NAME: Final[str] = "pdf_automatic_release_records"

CREATE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    report_id          TEXT NOT NULL,
    pdf_sha256         TEXT NOT NULL,
    approval_json      TEXT NOT NULL,
    approval_created_at TEXT NOT NULL,
    release_json       TEXT,
    release_sha256     TEXT,
    released_at        TEXT,
    PRIMARY KEY (report_id, pdf_sha256)
)
"""
CREATE_DECISION_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {DECISION_TABLE_NAME} (
    report_id              TEXT NOT NULL,
    pdf_sha256             TEXT NOT NULL,
    role                   TEXT NOT NULL,
    page_hashes_json       TEXT NOT NULL,
    reviewed_pages_json    TEXT NOT NULL,
    expected_fact_ids_json TEXT NOT NULL,
    reviewed_fact_ids_json TEXT NOT NULL,
    fact_failed_count      INTEGER NOT NULL,
    reviewer               TEXT NOT NULL,
    approved_at            TEXT NOT NULL,
    visual_review_kind     TEXT NOT NULL,
    PRIMARY KEY (report_id, pdf_sha256, role),
    UNIQUE (report_id, pdf_sha256, reviewer)
)
"""
CREATE_PARTICIPANT_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {PARTICIPANT_TABLE_NAME} (
    report_id      TEXT NOT NULL,
    pdf_sha256     TEXT NOT NULL,
    role           TEXT NOT NULL,
    person_id      TEXT NOT NULL,
    assigned_at    TEXT NOT NULL,
    PRIMARY KEY (report_id, pdf_sha256, role)
)
"""
CREATE_AUTOMATIC_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {AUTOMATIC_TABLE_NAME} (
    report_id       TEXT NOT NULL,
    report_sha256   TEXT NOT NULL,
    pdf_sha256      TEXT NOT NULL,
    checker_version TEXT NOT NULL,
    release_json    TEXT NOT NULL,
    release_sha256  TEXT NOT NULL,
    released_at     TEXT NOT NULL,
    PRIMARY KEY (report_id, report_sha256, pdf_sha256, checker_version)
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_SQL)
    conn.execute(CREATE_DECISION_SQL)
    conn.execute(CREATE_PARTICIPANT_SQL)
    conn.execute(CREATE_AUTOMATIC_SQL)
