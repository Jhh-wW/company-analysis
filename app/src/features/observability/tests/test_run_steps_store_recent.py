"""최근 실행 진단 목록이 최신순으로, 원본 없이 나오는지 본다.

★ 이 시험이 지키는 것 — 실행 진단은 어느 링크로 만들었는지와 무관하게 쌓인다.
  목록이 최신순이 아니면 방금 실패한 실행이 스무 줄 아래로 밀려 못 찾는다.
"""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from src.features.observability import run_steps_store


@pytest.fixture
def conn() -> sqlite3.Connection:
    """진짜 운영 저장소를 건드리지 않는 메모리 연결."""
    connection = sqlite3.connect(":memory:")
    try:
        yield connection
    finally:
        connection.close()


def _record(conn: sqlite3.Connection, *, run_id: str, recorded_at: str) -> None:
    run_steps_store.record_once(
        conn,
        run_id=run_id,
        steps=[{"step": "5b_뉴스_수집", "검색": 12}, {"step": "7_이름후보", "후보": 9}],
        recorded_at=recorded_at,
    )


def test_최신_기록이_먼저_나오고_같은_시각은_실행번호_내림차순이다(
    conn: sqlite3.Connection,
):
    _record(conn, run_id="run-a", recorded_at="2026-09-06T10:00:00+09:00")
    _record(conn, run_id="run-c", recorded_at="2026-09-07T09:00:00+09:00")
    _record(conn, run_id="run-b", recorded_at="2026-09-07T09:00:00+09:00")

    최근 = run_steps_store.list_recent(conn, limit=10)

    assert [item.run_id for item in 최근] == ["run-c", "run-b", "run-a"]
    assert 최근[0].recorded_at == "2026-09-07T09:00:00+09:00"
    assert 최근[0].step_count == 2
    assert 최근[0].omitted_count == 0


def test_상한만큼만_가져온다(conn: sqlite3.Connection):
    _record(conn, run_id="run-a", recorded_at="2026-09-05T10:00:00+09:00")
    _record(conn, run_id="run-b", recorded_at="2026-09-06T10:00:00+09:00")
    _record(conn, run_id="run-c", recorded_at="2026-09-07T10:00:00+09:00")

    최근 = run_steps_store.list_recent(conn, limit=2)

    assert [item.run_id for item in 최근] == ["run-c", "run-b"]


def test_기록이_없으면_빈_목록이다(conn: sqlite3.Connection):
    assert run_steps_store.list_recent(conn, limit=5) == []


def test_상한이_1보다_작으면_거절한다(conn: sqlite3.Connection):
    with pytest.raises(ValueError):
        run_steps_store.list_recent(conn, limit=0)
    with pytest.raises(ValueError):
        run_steps_store.list_recent(conn, limit=-1)


def test_목록에는_진단_원본을_싣지_않는다(conn: sqlite3.Connection):
    """★ 목록 한 번에 원본 수십 건을 만들면 첫 화면이 그만큼 무거워진다."""

    _record(conn, run_id="run-a", recorded_at="2026-09-07T10:00:00+09:00")

    최근 = run_steps_store.list_recent(conn, limit=5)

    이름 = {field.name for field in dataclasses.fields(run_steps_store.RecentRunSteps)}
    assert 이름 == {"run_id", "recorded_at", "step_count", "omitted_count"}
    assert not hasattr(최근[0], "steps")
