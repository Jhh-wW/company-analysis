"""실행 진단 원본(steps)이 저장·조회 왕복에서 그대로 살아 오는지 본다."""

from __future__ import annotations

import sqlite3

import pytest

from src.features.observability import constants as obs
from src.features.observability import run_steps_store


@pytest.fixture
def conn() -> sqlite3.Connection:
    """진짜 운영 저장소를 건드리지 않는 메모리 연결."""
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _steps() -> list[dict]:
    return [
        {"step": "5b_뉴스_수집", "검색": 12, "제외": {"BODY_FETCH_FAILED": 2}},
        {"step": "7_이름후보", "후보": 9, "종류별": {"product": 6}},
        {"step": "v2_composer_완료", "생성문장": 41, "생존문장": 38},
    ]


def test_저장하고_다시_읽으면_같은_기록이_나온다(conn: sqlite3.Connection):
    assert run_steps_store.record_once(
        conn, run_id="run-1", steps=_steps(), recorded_at="2026-09-06T10:00:00+09:00"
    )

    saved = run_steps_store.load(conn, "run-1")

    assert saved is not None
    assert saved.steps == _steps()
    assert saved.step_count == 3
    assert saved.omitted_count == 0
    assert saved.truncated is False
    assert saved.recorded_at == "2026-09-06T10:00:00+09:00"
    assert saved.schema_version == obs.RUN_STEPS_SCHEMA_VERSION


def test_같은_실행번호로_다시_불러도_덮어쓰지_않는다(conn: sqlite3.Connection):
    run_steps_store.record_once(
        conn, run_id="run-1", steps=_steps(), recorded_at="2026-09-06T10:00:00+09:00"
    )

    inserted = run_steps_store.record_once(
        conn,
        run_id="run-1",
        steps=[{"step": "다른기록"}],
        recorded_at="2026-09-06T11:00:00+09:00",
    )

    assert inserted is False
    saved = run_steps_store.load(conn, "run-1")
    assert saved is not None
    assert saved.steps == _steps()


def test_없는_실행번호는_None이다(conn: sqlite3.Connection):
    run_steps_store.ensure_schema(conn)
    assert run_steps_store.load(conn, "run-없음") is None
    assert run_steps_store.load(conn, "   ") is None


def test_상한을_넘으면_뒤쪽_단계부터_덜어내고_밝힌다(conn: sqlite3.Connection):
    # 한 단계가 상한의 1/4쯤 되도록 부풀린다.
    filler = "가" * (obs.RUN_STEPS_MAX_BYTES // 12)
    big = [{"step": f"단계{index}", "본문": filler} for index in range(8)]

    run_steps_store.record_once(
        conn, run_id="run-big", steps=big, recorded_at="2026-09-06T10:00:00+09:00"
    )

    saved = run_steps_store.load(conn, "run-big")
    assert saved is not None
    assert saved.truncated is True
    assert saved.omitted_count > 0
    assert saved.step_count + saved.omitted_count == len(big)
    # 남긴 것은 «앞쪽»이다 — 수집·판정이 원인 추적에 더 쓸모 있다.
    assert saved.steps[0]["step"] == "단계0"


def test_실행번호나_시각이_비면_거절한다(conn: sqlite3.Connection):
    with pytest.raises(ValueError):
        run_steps_store.record_once(
            conn, run_id="  ", steps=_steps(), recorded_at="2026-09-06T10:00:00+09:00"
        )
    with pytest.raises(ValueError):
        run_steps_store.record_once(
            conn, run_id="run-1", steps=_steps(), recorded_at=""
        )


def test_깨진_원본은_빈_목록으로_뭉개지_않고_알린다(conn: sqlite3.Connection):
    run_steps_store.record_once(
        conn, run_id="run-1", steps=_steps(), recorded_at="2026-09-06T10:00:00+09:00"
    )
    conn.execute(
        f"UPDATE {run_steps_store.TABLE_RUN_STEPS} SET steps_json = ? WHERE run_id = ?",
        ("{망가짐", "run-1"),
    )

    with pytest.raises(run_steps_store.RunStepsStoreError):
        run_steps_store.load(conn, "run-1")
