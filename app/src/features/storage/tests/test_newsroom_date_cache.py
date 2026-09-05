"""뉴스룸 날짜 캐시의 DDL과 저장 왕복 계약."""

from __future__ import annotations

import sqlite3

import pytest

from src.features.storage import newsroom_date_cache as cache


KEY = "newsroom-date-v1:" + ("a" * 64)
PAYLOAD = {
    "date": "2026-05-12",
    "origin": "ai_assisted",
    "context": "게시일 2026.05.12 회사 소식",
    "created_at": "2026-09-06T00:00:00+00:00",
}


def test_schema는_본문해시키와_검증결과만_보존한다() -> None:
    with sqlite3.connect(":memory:") as conn:
        cache.ensure_schema(conn)

        columns = {
            str(row[1]): (str(row[2]), int(row[3]), int(row[5]))
            for row in conn.execute(f"PRAGMA table_info({cache.TABLE_NAME})")
        }

    assert columns == {
        "cache_key": ("TEXT", 0, 1),
        "date": ("TEXT", 1, 0),
        "origin": ("TEXT", 1, 0),
        "context": ("TEXT", 1, 0),
        "created_at": ("TEXT", 1, 0),
    }


def test_save와_load가_검증결과를_왕복한다() -> None:
    with sqlite3.connect(":memory:") as conn:
        cache.save(conn, KEY, PAYLOAD)

        loaded = cache.load(conn, KEY)

    assert loaded == PAYLOAD


def test_같은_키를_다시_저장하면_한_행만_남는다() -> None:
    replacement = {
        **PAYLOAD,
        "date": "2026-05-13",
        "context": "게시일 2026.05.13 회사 소식",
    }
    with sqlite3.connect(":memory:") as conn:
        cache.save(conn, KEY, PAYLOAD)
        cache.save(conn, KEY, replacement)

        count = conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_NAME}"
        ).fetchone()[0]
        loaded = cache.load(conn, KEY)

    assert count == 1
    assert loaded == replacement


def test_없는_키는_none이다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert cache.load(conn, KEY) is None


@pytest.mark.parametrize(
    ("key", "payload"),
    [
        ("짧은키", PAYLOAD),
        (KEY, {**PAYLOAD, "date": "2026-02-30"}),
        (KEY, {**PAYLOAD, "origin": "guess"}),
        (KEY, {**PAYLOAD, "context": ""}),
        (KEY, {**PAYLOAD, "created_at": "2026-09-06"}),
    ],
)
def test_불완전한_키와_값은_저장하지_않는다(
    key: str,
    payload: dict[str, str],
) -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(ValueError):
            cache.save(conn, key, payload)
