"""유료 파일럿 체크포인트와 운영 DB를 결속하는 영속 스키마."""

from __future__ import annotations

import sqlite3
from typing import Final


PILOT_BINDING_TABLE: Final[str] = "canonical_pilot25_bindings"
PILOT_BINDING_SCHEMA_VERSION: Final[int] = 4
CREATE_PILOT_BINDING_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {PILOT_BINDING_TABLE} (
    pilot_key                 TEXT PRIMARY KEY,
    schema_version            INTEGER NOT NULL,
    binding_id                TEXT NOT NULL,
    manifest_sha256           TEXT NOT NULL,
    origin                    TEXT NOT NULL,
    server_instance_sha256    TEXT NOT NULL,
    data_path_sha256          TEXT NOT NULL,
    checkpoint_path_sha256    TEXT NOT NULL,
    checkpoint_content_sha256 TEXT NOT NULL,
    created_at                TEXT NOT NULL
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_PILOT_BINDING_SQL)
