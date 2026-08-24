from __future__ import annotations

import sqlite3

import pytest

from src.features.pilot_evaluation.final_gate_evidence import (
    LEGACY_GATE_STOPPED_OUTCOME,
    FinalGateEvidenceError,
    read_bound_reason,
    validate_table_if_present,
)
from src.features.pipeline.port import Outcome
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION,
    FINAL_GATE_DIAGNOSTIC_TABLE,
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
)


RUN_ID = "1" * 32
AT = "2026-08-22T00:00:00+00:00"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"CREATE TABLE {FINAL_GATE_DIAGNOSTIC_TABLE} ("
        "run_id TEXT, schema_version INTEGER, reason_code TEXT, recorded_at TEXT)"
    )
    return conn


def _insert(conn: sqlite3.Connection, *, at: str = AT) -> None:
    conn.execute(
        f"INSERT INTO {FINAL_GATE_DIAGNOSTIC_TABLE} VALUES (?, ?, ?, ?)",
        (
            RUN_ID,
            FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION,
            FINAL_GATE_REASON_COMPARISON_BLOCKED,
            at,
        ),
    )


def test_GATE_STOPPED는_정확히_한_행과_lifecycle_시각을_요구한다() -> None:
    with _connection() as conn:
        _insert(conn)
        reason = read_bound_reason(
            conn,
            run_id=RUN_ID,
            outcome=Outcome.GATE_STOPPED.value,
            gate_stopped_outcome=Outcome.GATE_STOPPED.value,
            lifecycle_record={"at": AT},
        )
    assert reason == FINAL_GATE_REASON_COMPARISON_BLOCKED


def test_과거저장본의_옛이름_자료부족중단도_게이트중단으로_읽는다() -> None:
    # 이름 변경("자료부족_중단"→"게이트_중단") 전에 저장된 checkpoint/SQLite를
    # 다시 검증할 때도 같은 결속 규칙이 적용돼야 한다 (읽기 호환).
    with _connection() as conn:
        _insert(conn)
        reason = read_bound_reason(
            conn,
            run_id=RUN_ID,
            outcome=LEGACY_GATE_STOPPED_OUTCOME,
            gate_stopped_outcome=Outcome.GATE_STOPPED.value,
            lifecycle_record={"at": AT},
        )
    assert LEGACY_GATE_STOPPED_OUTCOME != Outcome.GATE_STOPPED.value
    assert reason == FINAL_GATE_REASON_COMPARISON_BLOCKED


def test_GATE_STOPPED_행이_없거나_둘이면_거부한다() -> None:
    with _connection() as conn:
        with pytest.raises(FinalGateEvidenceError, match="정확히 하나"):
            read_bound_reason(
                conn,
                run_id=RUN_ID,
                outcome=Outcome.GATE_STOPPED.value,
                gate_stopped_outcome=Outcome.GATE_STOPPED.value,
                lifecycle_record={"at": AT},
            )
        _insert(conn)
        _insert(conn)
        with pytest.raises(FinalGateEvidenceError, match="정확히 하나"):
            read_bound_reason(
                conn,
                run_id=RUN_ID,
                outcome=Outcome.GATE_STOPPED.value,
                gate_stopped_outcome=Outcome.GATE_STOPPED.value,
                lifecycle_record={"at": AT},
            )


def test_비게이트_종료에_행이_있으면_거부한다() -> None:
    with _connection() as conn:
        _insert(conn)
        with pytest.raises(FinalGateEvidenceError, match="비게이트"):
            read_bound_reason(
                conn,
                run_id=RUN_ID,
                outcome=Outcome.REPORT.value,
                gate_stopped_outcome=Outcome.GATE_STOPPED.value,
                lifecycle_record={"at": AT},
            )


def test_게이트_시각이_lifecycle과_다르면_거부한다() -> None:
    with _connection() as conn:
        _insert(conn, at="2026-08-22T00:00:01+00:00")
        with pytest.raises(FinalGateEvidenceError, match="시각"):
            read_bound_reason(
                conn,
                run_id=RUN_ID,
                outcome=Outcome.GATE_STOPPED.value,
                gate_stopped_outcome=Outcome.GATE_STOPPED.value,
                lifecycle_record={"at": AT},
            )


def test_표가_없으면_읽기검사가_표를_만들지_않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert validate_table_if_present(conn) is False
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert tables == []
