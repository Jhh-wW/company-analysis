"""span-selection의 비민감 진단을 실행 원장과 같은 SQLite에 보존한다."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from typing import Final, Iterable

from src.shared.span_selection_diagnostics import (
    MAJORITY_REASON_ALL_REJECTED,
    MAJORITY_REASON_KEPT,
    MAJORITY_REASON_NO_CONSENSUS,
    MAJORITY_REASON_OUTPUT_LIMIT,
    MAJORITY_REASON_PARSE_FAILURE,
    MAJORITY_REASON_PROVIDER_EMPTY,
    ROUND_REASON_ALL_REJECTED,
    ROUND_REASON_KEPT,
    ROUND_REASON_OUTPUT_LIMIT,
    ROUND_REASON_PARSE_FAILURE,
    ROUND_REASON_PROVIDER_EMPTY,
    SAFE_PROVIDER_STOP_REASONS,
    UNKNOWN_PROVIDER_STOP_REASON,
    SpanSelectionRoundDiagnostic,
)


TABLE_SPAN_SELECTION_DIAGNOSTICS: Final[str] = (
    "pilot_span_selection_diagnostics"
)
SCHEMA_VERSION: Final[int] = 1
MAX_DIAGNOSTIC_ROUNDS: Final[int] = 10
_RESULT_REASONS: Final[frozenset[str]] = frozenset(
    {
        MAJORITY_REASON_KEPT,
        MAJORITY_REASON_OUTPUT_LIMIT,
        MAJORITY_REASON_PARSE_FAILURE,
        MAJORITY_REASON_PROVIDER_EMPTY,
        MAJORITY_REASON_ALL_REJECTED,
        MAJORITY_REASON_NO_CONSENSUS,
    }
)
_ROUND_REASONS: Final[frozenset[str]] = frozenset(
    {
        ROUND_REASON_KEPT,
        ROUND_REASON_OUTPUT_LIMIT,
        ROUND_REASON_PARSE_FAILURE,
        ROUND_REASON_PROVIDER_EMPTY,
        ROUND_REASON_ALL_REJECTED,
    }
)
_STOP_REASONS: Final[frozenset[str]] = frozenset(
    {*SAFE_PROVIDER_STOP_REASONS, UNKNOWN_PROVIDER_STOP_REASON}
)
_ROUND_FIELDS: Final[frozenset[str]] = frozenset(
    field.name for field in fields(SpanSelectionRoundDiagnostic)
)


class SpanDiagnosticStoreError(RuntimeError):
    """진단 원장이 손상됐거나 같은 실행에 다른 값을 쓰려 했다."""


@dataclass(frozen=True)
class PersistedSpanSelectionDiagnostics:
    run_id: str
    result_reason: str
    rounds: tuple[SpanSelectionRoundDiagnostic, ...]
    recorded_at: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """기존 고정 RunRecord를 바꾸지 않고 별도 부속 원장을 만든다."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_SPAN_SELECTION_DIAGNOSTICS} (
            run_id       TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK (schema_version = {SCHEMA_VERSION}),
            result_reason TEXT NOT NULL,
            rounds_json   TEXT NOT NULL,
            recorded_at   TEXT NOT NULL
        )
        """
    )


def record_once(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    result_reason: str,
    rounds: Iterable[SpanSelectionRoundDiagnostic],
    recorded_at: str,
) -> bool:
    """한 실행의 닫힌 진단을 한 번만 기록한다.

    같은 값의 재기록은 멱등이고, 다른 값으로 덮어쓰려 하면 중단한다.
    원문·프롬프트·회사명은 인자와 저장 필드에 존재하지 않는다.
    """

    clean_run_id = str(run_id).strip()
    clean_rounds = tuple(rounds)
    _validate_snapshot(
        run_id=clean_run_id,
        result_reason=result_reason,
        rounds=clean_rounds,
        recorded_at=recorded_at,
    )
    ensure_schema(conn)
    rounds_json = json.dumps(
        [asdict(item) for item in clean_rounds],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    values = (
        clean_run_id,
        SCHEMA_VERSION,
        result_reason,
        rounds_json,
        recorded_at,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_SPAN_SELECTION_DIAGNOSTICS}
            (run_id, schema_version, result_reason, rounds_json, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        values,
    )
    if cursor.rowcount == 1:
        return True
    existing = conn.execute(
        f"""
        SELECT run_id, schema_version, result_reason, rounds_json, recorded_at
          FROM {TABLE_SPAN_SELECTION_DIAGNOSTICS}
         WHERE run_id = ?
        """,
        (clean_run_id,),
    ).fetchone()
    if existing == values:
        return False
    raise SpanDiagnosticStoreError(
        "같은 실행 번호의 span-selection 진단을 다른 값으로 바꿀 수 없습니다"
    )


def read_for_run(
    conn: sqlite3.Connection, run_id: str
) -> PersistedSpanSelectionDiagnostics | None:
    """연결을 다시 연 뒤에도 한 실행의 진단을 엄격하게 복원한다."""

    ensure_schema(conn)
    clean_run_id = str(run_id).strip()
    row = conn.execute(
        f"""
        SELECT run_id, schema_version, result_reason, rounds_json, recorded_at
          FROM {TABLE_SPAN_SELECTION_DIAGNOSTICS}
         WHERE run_id = ?
        """,
        (clean_run_id,),
    ).fetchone()
    if row is None:
        return None
    stored_run_id, schema_version, result_reason, rounds_json, recorded_at = row
    if schema_version != SCHEMA_VERSION:
        raise SpanDiagnosticStoreError("지원하지 않는 span-selection 진단 형식입니다")
    try:
        payload = json.loads(str(rounds_json))
    except json.JSONDecodeError as exc:
        raise SpanDiagnosticStoreError(
            "span-selection 라운드 진단 JSON이 손상됐습니다"
        ) from exc
    if not isinstance(payload, list):
        raise SpanDiagnosticStoreError("span-selection 라운드 진단 모양이 다릅니다")
    restored: list[SpanSelectionRoundDiagnostic] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) != _ROUND_FIELDS:
            raise SpanDiagnosticStoreError("span-selection 라운드 진단 필드가 다릅니다")
        try:
            restored.append(SpanSelectionRoundDiagnostic(**item))
        except TypeError as exc:
            raise SpanDiagnosticStoreError(
                "span-selection 라운드 진단 값을 복원하지 못했습니다"
            ) from exc
    snapshot = PersistedSpanSelectionDiagnostics(
        run_id=str(stored_run_id),
        result_reason=str(result_reason),
        rounds=tuple(restored),
        recorded_at=str(recorded_at),
    )
    _validate_snapshot(
        run_id=snapshot.run_id,
        result_reason=snapshot.result_reason,
        rounds=snapshot.rounds,
        recorded_at=snapshot.recorded_at,
    )
    return snapshot


def _validate_snapshot(
    *,
    run_id: str,
    result_reason: str,
    rounds: tuple[SpanSelectionRoundDiagnostic, ...],
    recorded_at: str,
) -> None:
    if not run_id or len(run_id) > 128:
        raise SpanDiagnosticStoreError("실행 번호가 올바르지 않습니다")
    if result_reason not in _RESULT_REASONS:
        raise SpanDiagnosticStoreError("허용되지 않은 span-selection 결과 사유입니다")
    try:
        parsed_at = dt.datetime.fromisoformat(recorded_at)
    except (TypeError, ValueError) as exc:
        raise SpanDiagnosticStoreError("span-selection 기록 시각이 올바르지 않습니다") from exc
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        raise SpanDiagnosticStoreError("span-selection 기록 시각에는 시간대가 필요합니다")
    if not 1 <= len(rounds) <= MAX_DIAGNOSTIC_ROUNDS:
        raise SpanDiagnosticStoreError("span-selection 라운드 수가 허용 범위를 벗어났습니다")
    for expected_number, item in enumerate(rounds, start=1):
        if type(item) is not SpanSelectionRoundDiagnostic:
            raise SpanDiagnosticStoreError("span-selection 라운드 진단 형식이 다릅니다")
        if item.round_number != expected_number:
            raise SpanDiagnosticStoreError("span-selection 라운드 번호가 연속적이지 않습니다")
        integer_values = (
            item.requested_max_tokens,
            item.output_tokens,
            item.provider_selected,
            item.validation_kept,
            item.validation_rejected,
        )
        if any(type(value) is not int or value < 0 for value in integer_values):
            raise SpanDiagnosticStoreError("span-selection 진단 수치가 올바르지 않습니다")
        if type(item.output_limit_reached) is not bool or type(item.parse_failed) is not bool:
            raise SpanDiagnosticStoreError("span-selection 진단 상태값이 올바르지 않습니다")
        if item.provider_stop_reason not in _STOP_REASONS:
            raise SpanDiagnosticStoreError("허용되지 않은 provider 종료 사유입니다")
        if item.empty_reason not in _ROUND_REASONS:
            raise SpanDiagnosticStoreError("허용되지 않은 라운드 결과 사유입니다")
        if item.validation_kept + item.validation_rejected > item.provider_selected:
            raise SpanDiagnosticStoreError("검증 집계가 provider 선택 수보다 큽니다")
