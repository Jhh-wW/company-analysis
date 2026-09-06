"""검증된 뉴스룸 글자 날짜의 본문 해시 기반 SQLite 캐시."""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from typing import Final, Mapping


TABLE_NAME: Final[str] = "newsroom_date_cache"
_CACHE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9-]+:[0-9a-f]{64}$")
_ORIGINS: Final[frozenset[str]] = frozenset({"program", "ai_assisted"})
CREATE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    cache_key  TEXT PRIMARY KEY,
    date       TEXT NOT NULL,
    origin     TEXT NOT NULL CHECK(origin IN ('program', 'ai_assisted')),
    context    TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """뉴스룸 날짜 캐시 표를 멱등 생성한다."""

    conn.execute(CREATE_SQL)


def _validated_payload(payload: Mapping[str, str]) -> tuple[str, str, str, str]:
    try:
        stored_date = str(payload["date"]).strip()
        origin = str(payload["origin"]).strip()
        context = str(payload["context"])
        created_at = str(payload["created_at"]).strip()
    except (KeyError, TypeError) as exc:
        raise ValueError("뉴스룸 날짜 캐시 값이 완전하지 않습니다") from exc
    try:
        date.fromisoformat(stored_date)
    except ValueError as exc:
        raise ValueError("뉴스룸 날짜 캐시의 날짜 형식이 올바르지 않습니다") from exc
    if origin not in _ORIGINS:
        raise ValueError("뉴스룸 날짜 캐시의 판정 출처가 올바르지 않습니다")
    if not context:
        raise ValueError("뉴스룸 날짜 캐시에는 검증한 문맥이 필요합니다")
    try:
        parsed_created_at = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("뉴스룸 날짜 캐시 생성 시각이 올바르지 않습니다") from exc
    if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
        raise ValueError("뉴스룸 날짜 캐시 생성 시각에는 시간대가 필요합니다")
    return stored_date, origin, context, created_at


def load(conn: sqlite3.Connection, key: str) -> dict[str, str] | None:
    """캐시 키가 일치하는 검증 결과를 읽는다."""

    ensure_schema(conn)
    row = conn.execute(
        f"SELECT date, origin, context, created_at FROM {TABLE_NAME} WHERE cache_key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "date": str(row[0]),
        "origin": str(row[1]),
        "context": str(row[2]),
        "created_at": str(row[3]),
    }


def save(
    conn: sqlite3.Connection,
    key: str,
    payload: Mapping[str, str],
) -> None:
    """검증 결과를 같은 본문·프롬프트 키에 멱등 저장한다."""

    if _CACHE_KEY_PATTERN.fullmatch(key) is None:
        raise ValueError("뉴스룸 날짜 캐시 키가 올바르지 않습니다")
    stored_date, origin, context, created_at = _validated_payload(payload)
    ensure_schema(conn)
    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME}(cache_key, date, origin, context, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            date = excluded.date,
            origin = excluded.origin,
            context = excluded.context,
            created_at = excluded.created_at
        """,
        (key, stored_date, origin, context, created_at),
    )
