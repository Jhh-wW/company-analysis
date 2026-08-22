from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import sqlite3

import pytest

from src.features.spanselect import diagnostic_store
from src.shared.span_selection_diagnostics import SpanSelectionRoundDiagnostic


RUN_ID = "0123456789abcdef0123456789abcdef"
RECORDED_AT = "2026-08-22T20:00:00+09:00"
SECOND_RUN_ID = "fedcba9876543210fedcba9876543210"


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


def _rejected_round(
    counts: tuple[tuple[str, int], ...], *, rejected: int
) -> SpanSelectionRoundDiagnostic:
    return SpanSelectionRoundDiagnostic(
        round_number=1,
        requested_max_tokens=6000,
        output_tokens=100,
        provider_stop_reason="end_turn",
        output_limit_reached=False,
        parse_failed=False,
        provider_selected=max(1, rejected),
        validation_kept=0,
        validation_rejected=rejected,
        empty_reason="all_candidates_rejected",
        validation_rejection_reason_counts=counts,
    )


def _v1_rounds_json() -> str:
    payload = asdict(_round())
    payload.pop("validation_rejection_reason_counts")
    return json.dumps(
        [payload],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _create_v1_table(conn: sqlite3.Connection, rounds_json: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE {diagnostic_store.TABLE_SPAN_SELECTION_DIAGNOSTICS} (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK (schema_version = 1),
            result_reason TEXT NOT NULL,
            rounds_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO {diagnostic_store.TABLE_SPAN_SELECTION_DIAGNOSTICS}
            (run_id, schema_version, result_reason, rounds_json, recorded_at)
        VALUES (?, 1, ?, ?, ?)
        """,
        (RUN_ID, "output_limit_suspected", rounds_json, RECORDED_AT),
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
    assert restored.schema_version == 2
    assert restored.result_reason == "output_limit_suspected"
    assert restored.rounds == (_round(),)


def test_v2_거절사유집계는_SQLite_JSON을_거쳐_같은튜플로_복원된다(
    tmp_path,
) -> None:
    db_path = tmp_path / "storage.db"
    expected = _rejected_round(
        (
            ("other_validation_failure", 1),
            ("subject_label_not_in_source", 2),
        ),
        rejected=3,
    )
    with sqlite3.connect(db_path) as conn:
        diagnostic_store.record_once(
            conn,
            run_id=RUN_ID,
            result_reason="all_candidates_rejected",
            rounds=(expected,),
            recorded_at=RECORDED_AT,
        )

    with sqlite3.connect(db_path) as conn:
        restored = diagnostic_store.read_for_run(conn, RUN_ID)

    assert restored is not None
    assert restored.schema_version == 2
    assert restored.rounds == (expected,)


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


def test_v1_진단은_읽을때_DB를_바꾸지않고_빈사유집계로_복원된다(
    tmp_path,
) -> None:
    db_path = tmp_path / "storage.db"
    legacy_json = _v1_rounds_json()
    with sqlite3.connect(db_path) as conn:
        _create_v1_table(conn, legacy_json)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    uri = db_path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        restored = diagnostic_store.read_for_run(conn, RUN_ID)

    after = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert before == after
    assert restored is not None
    assert restored.schema_version == 1
    assert restored.rounds[0].validation_rejection_reason_counts == ()


def test_v1_표는_쓸때만_v2와_공존하도록_옮기고_JSON을_그대로_보존한다(
    tmp_path,
) -> None:
    db_path = tmp_path / "storage.db"
    legacy_json = _v1_rounds_json()
    with sqlite3.connect(db_path) as conn:
        _create_v1_table(conn, legacy_json)

    with sqlite3.connect(db_path) as conn:
        assert diagnostic_store.record_once(
            conn,
            run_id=SECOND_RUN_ID,
            result_reason="output_limit_suspected",
            rounds=(_round(),),
            recorded_at=RECORDED_AT,
        )
        old_version, old_json = conn.execute(
            f"SELECT schema_version, rounds_json "
            f"FROM {diagnostic_store.TABLE_SPAN_SELECTION_DIAGNOSTICS} "
            "WHERE run_id=?",
            (RUN_ID,),
        ).fetchone()
        new_version = conn.execute(
            f"SELECT schema_version "
            f"FROM {diagnostic_store.TABLE_SPAN_SELECTION_DIAGNOSTICS} "
            "WHERE run_id=?",
            (SECOND_RUN_ID,),
        ).fetchone()[0]
        definition = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (diagnostic_store.TABLE_SPAN_SELECTION_DIAGNOSTICS,),
        ).fetchone()[0]

    assert old_version == 1
    assert old_json == legacy_json
    assert new_version == 2
    assert "schema_version IN (1, 2)" in definition
    with sqlite3.connect(db_path) as conn:
        assert diagnostic_store.read_for_run(conn, RUN_ID).schema_version == 1
        assert diagnostic_store.read_for_run(conn, SECOND_RUN_ID).schema_version == 2


@pytest.mark.parametrize(
    ("counts", "rejected"),
    [
        (("허용되지 않은 원문", 1), 1),
        (
            (
                ("subject_label_not_in_source", 1),
                ("subject_label_not_in_source", 1),
            ),
            2,
        ),
        (("subject_label_not_in_source", 0), 0),
        (
            (
                ("subject_label_not_in_source", 1),
                ("other_validation_failure", 1),
            ),
            2,
        ),
        (("subject_label_not_in_source", 1), 2),
    ],
)
def test_v2_거절사유집계는_닫힌코드와_정렬과_합계를_엄격검사한다(
    counts,
    rejected,
) -> None:
    # 단일 pair도 잘못된 바깥 모양으로 전달해 strict shape 검사를 함께 확인한다.
    malformed_or_counts = counts
    if counts and isinstance(counts[0], str):
        malformed_or_counts = (counts,)
    round_diagnostic = _rejected_round(
        malformed_or_counts,
        rejected=rejected,
    )
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(diagnostic_store.SpanDiagnosticStoreError):
            diagnostic_store.record_once(
                conn,
                run_id=RUN_ID,
                result_reason="all_candidates_rejected",
                rounds=(round_diagnostic,),
                recorded_at=RECORDED_AT,
            )


def test_같은실행에서_거절사유집계만_바꿔도_덮어쓰지않는다() -> None:
    original = _rejected_round(
        (("subject_label_not_in_source", 1),),
        rejected=1,
    )
    changed = replace(
        original,
        validation_rejection_reason_counts=(("other_validation_failure", 1),),
    )
    with sqlite3.connect(":memory:") as conn:
        diagnostic_store.record_once(
            conn,
            run_id=RUN_ID,
            result_reason="all_candidates_rejected",
            rounds=(original,),
            recorded_at=RECORDED_AT,
        )
        with pytest.raises(diagnostic_store.SpanDiagnosticStoreError):
            diagnostic_store.record_once(
                conn,
                run_id=RUN_ID,
                result_reason="all_candidates_rejected",
                rounds=(changed,),
                recorded_at=RECORDED_AT,
            )


def test_provider_선택수는_유지와_거절의_합과_정확히_같아야_한다() -> None:
    missing_one = replace(
        _rejected_round(
            (("other_validation_failure", 1),),
            rejected=1,
        ),
        provider_selected=2,
    )

    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(
            diagnostic_store.SpanDiagnosticStoreError,
            match="provider 선택 수와 다릅니다",
        ):
            diagnostic_store.record_once(
                conn,
                run_id=RUN_ID,
                result_reason="all_candidates_rejected",
                rounds=(missing_one,),
                recorded_at=RECORDED_AT,
            )
