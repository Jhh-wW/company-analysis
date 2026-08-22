from __future__ import annotations

import sqlite3

import pytest

from src.features.pipeline.port import Outcome, RunResult, UserInput
from src.features.spanselect import diagnostic_store
from src.features.storage import constants as storage_constants
from src.shared.span_selection_diagnostics import SpanSelectionRoundDiagnostic
from src.web import recording


RUN_ID = "0123456789abcdef0123456789abcdef"


def _round() -> SpanSelectionRoundDiagnostic:
    return SpanSelectionRoundDiagnostic(
        round_number=1,
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


def _result() -> RunResult:
    return RunResult(
        outcome=Outcome.GATE_STOPPED,
        span_selection_diagnostics=(_round(),),
        span_selection_result_reason="output_limit_suspected",
    )


def test_웹실행마감과_진단이_같은_DB에_남고_재연결뒤복원된다(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv("OBSERVABILITY_RECORDS_PATH", str(tmp_path / "runs.jsonl"))

    assert recording.record_run(
        UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        _result(),
        1.0,
        run_id=RUN_ID,
    )

    with sqlite3.connect(db_path) as conn:
        restored = diagnostic_store.read_for_run(conn, RUN_ID)
        columns = {
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({diagnostic_store.TABLE_SPAN_SELECTION_DIAGNOSTICS})"
            )
        }
    assert restored is not None
    assert restored.rounds == (_round(),)
    assert columns == {
        "run_id",
        "schema_version",
        "result_reason",
        "rounds_json",
        "recorded_at",
    }
    assert not {"prompt", "response", "company", "text", "원문"} & columns


def test_진단저장실패면_새_lifecycle_finalize도_같이_rollback된다(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv("OBSERVABILITY_RECORDS_PATH", str(tmp_path / "runs.jsonl"))
    monkeypatch.setattr(
        diagnostic_store,
        "record_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("진단 저장 실패")),
    )

    assert not recording.record_run(
        UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        _result(),
        1.0,
        run_id=RUN_ID,
    )

    with sqlite3.connect(db_path) as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='observability_run_lifecycle'"
        ).fetchone()
        row = (
            conn.execute(
                "SELECT state FROM observability_run_lifecycle WHERE run_id=?",
                (RUN_ID,),
            ).fetchone()
            if table_exists is not None
            else None
        )
    assert row is None


def test_멱등_lifecycle호출은_같은진단만_legacy_backfill할수있다(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv("OBSERVABILITY_RECORDS_PATH", str(tmp_path / "runs.jsonl"))
    assert recording.record_run(
        UserInput(company="", job="회사분석", region=""),
        _result(),
        1.0,
        run_id=RUN_ID,
    )

    assert not recording.record_run(
        UserInput(company="", job="회사분석", region=""),
        _result(),
        1.0,
        run_id=RUN_ID,
    )

    changed = RunResult(
        outcome=Outcome.GATE_STOPPED,
        span_selection_diagnostics=(_round(),),
        span_selection_result_reason="provider_parse_failure",
    )
    assert not recording.record_run(
        UserInput(company="", job="회사분석", region=""),
        changed,
        1.0,
        run_id=RUN_ID,
    )
