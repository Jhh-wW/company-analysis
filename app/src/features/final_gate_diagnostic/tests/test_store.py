from __future__ import annotations

import sqlite3

import pytest

from src.features.final_gate_diagnostic import store


RUN_ID = "0123456789abcdef0123456789abcdef"
RECORDED_AT = "2026-08-22T20:30:00+09:00"


def test_최종게이트_진단은_재연결뒤에도_같은값으로_복원된다(tmp_path) -> None:
    db_path = tmp_path / "storage.db"
    with sqlite3.connect(db_path) as conn:
        assert store.record_once(
            conn,
            run_id=RUN_ID,
            reason_code="publish_blocked",
            recorded_at=RECORDED_AT,
        )

    with sqlite3.connect(db_path) as conn:
        restored = store.read_for_run(conn, RUN_ID)

    assert restored == store.PersistedFinalGateDiagnostic(
        run_id=RUN_ID,
        schema_version=1,
        reason_code="publish_blocked",
        recorded_at=RECORDED_AT,
    )


def test_읽기는_표를_새로_만들지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert store.read_for_run(conn, RUN_ID) is None
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (store.TABLE_FINAL_GATE_DIAGNOSTICS,),
        ).fetchone()


def test_임의_사유와_시간대없는_시각은_저장하지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            store.record_once(
                conn,
                run_id=RUN_ID,
                reason_code="원문이 섞일 수 있는 임의 사유",
                recorded_at=RECORDED_AT,
            )
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            store.record_once(
                conn,
                run_id=RUN_ID,
                reason_code="other_gate",
                recorded_at="2026-08-22T20:30:00",
            )


def test_실행번호에_회사명이나_이메일을_넣을수없다() -> None:
    with sqlite3.connect(":memory:") as conn:
        for unsafe_run_id in ("삼성전자", "person@example.com", "run id"):
            with pytest.raises(store.FinalGateDiagnosticStoreError):
                store.record_once(
                    conn,
                    run_id=unsafe_run_id,
                    reason_code="other_gate",
                    recorded_at=RECORDED_AT,
                )


def test_같은값은_멱등이고_다른사유는_덮어쓰지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert store.record_once(
            conn,
            run_id=RUN_ID,
            reason_code="comparison_blocked",
            recorded_at=RECORDED_AT,
        )
        conn.row_factory = sqlite3.Row
        assert not store.record_once(
            conn,
            run_id=RUN_ID,
            reason_code="comparison_blocked",
            recorded_at=RECORDED_AT,
        )
        with pytest.raises(store.FinalGateDiagnosticStoreError):
            store.record_once(
                conn,
                run_id=RUN_ID,
                reason_code="publish_blocked",
                recorded_at=RECORDED_AT,
            )
