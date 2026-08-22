from __future__ import annotations

import sqlite3

import pytest

from src.features.spanselect import diagnostic_store
from src.shared.span_selection_diagnostics import SpanSelectionRoundDiagnostic


RUN_ID = "0123456789abcdef0123456789abcdef"
RECORDED_AT = "2026-08-22T20:00:00+09:00"


def _round(number: int = 1) -> SpanSelectionRoundDiagnostic:
    return SpanSelectionRoundDiagnostic(
        round_number=number,
        requested_max_tokens=6000,
        output_tokens=6000,
        provider_stop_reason="max_tokens",
        output_limit_reached=True,
        parse_failed=True,
        provider_selected=0,
        validation_kept=0,
        validation_rejected=0,
        empty_reason="output_limit_empty",
    )


def test_진단은_SQLite_재연결뒤에도_같은값으로_복원된다(tmp_path) -> None:
    db_path = tmp_path / "storage.db"
    with sqlite3.connect(db_path) as conn:
        assert diagnostic_store.record_once(
            conn,
            run_id=RUN_ID,
            result_reason="output_limit_suspected",
            rounds=(_round(),),
            recorded_at=RECORDED_AT,
        )

    with sqlite3.connect(db_path) as conn:
        restored = diagnostic_store.read_for_run(conn, RUN_ID)

    assert restored is not None
    assert restored.run_id == RUN_ID
    assert restored.result_reason == "output_limit_suspected"
    assert restored.rounds == (_round(),)


def test_같은실행의_다른진단은_덮어쓰지않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        diagnostic_store.record_once(
            conn,
            run_id=RUN_ID,
            result_reason="output_limit_suspected",
            rounds=(_round(),),
            recorded_at=RECORDED_AT,
        )
        with pytest.raises(diagnostic_store.SpanDiagnosticStoreError):
            diagnostic_store.record_once(
                conn,
                run_id=RUN_ID,
                result_reason="provider_parse_failure",
                rounds=(_round(),),
                recorded_at=RECORDED_AT,
            )

