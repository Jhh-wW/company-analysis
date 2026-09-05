"""근거 재판정 결과 캐시의 키·왕복·손상 복구 계약."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from src.features.storage import evidence_reclassify_cache as cache


RECEIPT_A = "20260318000001"
RECEIPT_B = "20260318000002"
PROMPT_VERSION = "evidence-reclassify-v1"
MODEL = "claude-haiku"
PARAGRAPH_HASH = hashlib.sha256("입력 문단".encode()).hexdigest()


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _payload() -> cache.Cached:
    return cache.Cached(
        validated_items=[
            {
                "paragraph_id": "p-1",
                "section_id": "3",
                "slot_id": "portfolio",
                "quote": "제품별 매출액은 다음과 같다.",
            }
        ],
        rejection_diagnostics={
            "rejected_count": 1,
            "reason_counts": {"quote_not_found": 1},
        },
        generated_at="2026-09-05T12:00:00+09:00",
        input_paragraph_hash=PARAGRAPH_HASH,
    )


def test_저장한_검증결과와_진단을_그대로_조회한다(
    conn: sqlite3.Connection,
) -> None:
    key = cache.key_for([RECEIPT_A, RECEIPT_B], PROMPT_VERSION, MODEL)
    payload = _payload()

    cache.save(conn, key, payload)

    assert cache.load(conn, key) == payload
    columns = {
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({cache.TABLE_EVIDENCE_RECLASSIFICATION_CACHE})"
        )
    }
    assert columns == {
        "cache_key",
        "validated_items_json",
        "rejection_diagnostics_json",
        "generated_at",
        "input_paragraph_hash",
    }


def test_접수번호_순서와_중복은_키를_바꾸지_않는다() -> None:
    expected = cache.key_for(
        [RECEIPT_A, RECEIPT_B],
        PROMPT_VERSION,
        MODEL,
    )

    assert cache.key_for(
        [RECEIPT_B, RECEIPT_A, RECEIPT_B],
        PROMPT_VERSION,
        MODEL,
    ) == expected


def test_프롬프트_버전이_다르면_캐시가_미스다(
    conn: sqlite3.Connection,
) -> None:
    stored_key = cache.key_for([RECEIPT_A], PROMPT_VERSION, MODEL)
    changed_key = cache.key_for([RECEIPT_A], "evidence-reclassify-v2", MODEL)
    cache.save(conn, stored_key, _payload())

    assert cache.load(conn, changed_key) is None


def test_모델이_다르면_키도_다르다() -> None:
    first = cache.key_for([RECEIPT_A], PROMPT_VERSION, MODEL)

    assert cache.key_for([RECEIPT_A], PROMPT_VERSION, "other-model") != first


@pytest.mark.parametrize(
    "column",
    ["validated_items_json", "rejection_diagnostics_json"],
)
def test_손상_json은_예외가_아니라_캐시_미스다(
    conn: sqlite3.Connection,
    column: str,
) -> None:
    key = cache.key_for([RECEIPT_A], PROMPT_VERSION, MODEL)
    cache.save(conn, key, _payload())
    conn.execute(
        f"""
        UPDATE {cache.TABLE_EVIDENCE_RECLASSIFICATION_CACHE}
           SET {column} = ?
         WHERE cache_key = ?
        """,
        ("{손상", key),
    )

    assert cache.load(conn, key) is None


@pytest.mark.parametrize("receipt_number", ["", "123", "2026031800000A"])
def test_14자리_숫자가_아닌_접수번호는_거부한다(
    receipt_number: str,
) -> None:
    with pytest.raises(ValueError, match="14자리"):
        cache.key_for([receipt_number], PROMPT_VERSION, MODEL)
