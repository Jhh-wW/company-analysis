"""span-selection의 비민감 진단을 실행 원장과 같은 SQLite에 보존한다."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from typing import Final, Iterable

from src.core.persisted_json import validate_persisted_json_text
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
    SAFE_VALIDATION_REJECTION_REASONS,
    UNKNOWN_PROVIDER_STOP_REASON,
    SpanSelectionRoundDiagnostic,
)


TABLE_SPAN_SELECTION_DIAGNOSTICS: Final[str] = (
    "pilot_span_selection_diagnostics"
)
LEGACY_SCHEMA_VERSION: Final[int] = 1
SCHEMA_VERSION: Final[int] = 2
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset(
    {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}
)
MAX_DIAGNOSTIC_ROUNDS: Final[int] = 10
_MIGRATION_TABLE: Final[str] = "pilot_span_selection_diagnostics_v1_migration"
_MIGRATION_SAVEPOINT: Final[str] = "span_selection_diagnostics_v2"
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
_ROUND_FIELDS_V2: Final[frozenset[str]] = frozenset(
    field.name for field in fields(SpanSelectionRoundDiagnostic)
)
_ROUND_FIELDS_V1: Final[frozenset[str]] = frozenset(
    _ROUND_FIELDS_V2 - {"validation_rejection_reason_counts"}
)
_TABLE_COLUMNS: Final[frozenset[str]] = frozenset(
    {"run_id", "schema_version", "result_reason", "rounds_json", "recorded_at"}
)


class SpanDiagnosticStoreError(RuntimeError):
    """진단 원장이 손상됐거나 같은 실행에 다른 값을 쓰려 했다."""


@dataclass(frozen=True)
class PersistedSpanSelectionDiagnostics:
    run_id: str
    schema_version: int
    result_reason: str
    rounds: tuple[SpanSelectionRoundDiagnostic, ...]
    recorded_at: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """쓰기 직전에 v1 행을 보존하면서 v2를 함께 받는 표를 준비한다."""

    definition = _table_definition(conn, TABLE_SPAN_SELECTION_DIAGNOSTICS)
    if definition is None:
        _create_current_table(conn)
        return
    _validate_table_columns(conn, TABLE_SPAN_SELECTION_DIAGNOSTICS)
    normalized = "".join(definition.lower().split())
    if "check(schema_versionin(1,2))" in normalized:
        return
    if "check(schema_version=1)" not in normalized:
        raise SpanDiagnosticStoreError(
            "지원하지 않는 span-selection 진단 표 제약입니다"
        )
    _migrate_v1_table_for_write(conn)


def _create_current_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE {TABLE_SPAN_SELECTION_DIAGNOSTICS} (
            run_id       TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL
                CHECK (schema_version IN ({LEGACY_SCHEMA_VERSION}, {SCHEMA_VERSION})),
            result_reason TEXT NOT NULL,
            rounds_json   TEXT NOT NULL,
            recorded_at   TEXT NOT NULL
        )
        """
    )


def _migrate_v1_table_for_write(conn: sqlite3.Connection) -> None:
    versions = {
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT schema_version "
            f"FROM {TABLE_SPAN_SELECTION_DIAGNOSTICS}"
        )
    }
    if not versions <= {LEGACY_SCHEMA_VERSION}:
        raise SpanDiagnosticStoreError(
            "v1 span-selection 진단 표에 지원하지 않는 행이 있습니다"
        )
    if _table_definition(conn, _MIGRATION_TABLE) is not None:
        raise SpanDiagnosticStoreError(
            "span-selection 진단 표 마이그레이션 임시 표가 이미 있습니다"
        )

    conn.execute(f"SAVEPOINT {_MIGRATION_SAVEPOINT}")
    try:
        conn.execute(
            f"ALTER TABLE {TABLE_SPAN_SELECTION_DIAGNOSTICS} "
            f"RENAME TO {_MIGRATION_TABLE}"
        )
        _create_current_table(conn)
        conn.execute(
            f"""
            INSERT INTO {TABLE_SPAN_SELECTION_DIAGNOSTICS}
                (run_id, schema_version, result_reason, rounds_json, recorded_at)
            SELECT run_id, schema_version, result_reason, rounds_json, recorded_at
              FROM {_MIGRATION_TABLE}
            """
        )
        conn.execute(f"DROP TABLE {_MIGRATION_TABLE}")
        conn.execute(f"RELEASE SAVEPOINT {_MIGRATION_SAVEPOINT}")
    except sqlite3.Error as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {_MIGRATION_SAVEPOINT}")
        conn.execute(f"RELEASE SAVEPOINT {_MIGRATION_SAVEPOINT}")
        raise SpanDiagnosticStoreError(
            "span-selection 진단 표를 v2로 옮기지 못했습니다"
        ) from exc


def _table_definition(
    conn: sqlite3.Connection, table_name: str
) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return None if row is None else str(row[0] or "")


def _validate_table_columns(
    conn: sqlite3.Connection, table_name: str
) -> None:
    columns = frozenset(
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table_name})")
    )
    if columns != _TABLE_COLUMNS:
        raise SpanDiagnosticStoreError(
            "span-selection 진단 표 필드가 다릅니다"
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
        schema_version=SCHEMA_VERSION,
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
    validate_persisted_json_text(rounds_json)
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

    if _table_definition(conn, TABLE_SPAN_SELECTION_DIAGNOSTICS) is None:
        return None
    _validate_table_columns(conn, TABLE_SPAN_SELECTION_DIAGNOSTICS)
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
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
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
        expected_fields = (
            _ROUND_FIELDS_V1
            if schema_version == LEGACY_SCHEMA_VERSION
            else _ROUND_FIELDS_V2
        )
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise SpanDiagnosticStoreError("span-selection 라운드 진단 필드가 다릅니다")
        try:
            values = dict(item)
            if schema_version == LEGACY_SCHEMA_VERSION:
                values["validation_rejection_reason_counts"] = ()
            else:
                values["validation_rejection_reason_counts"] = (
                    _restore_reason_counts(
                        values["validation_rejection_reason_counts"]
                    )
                )
            restored.append(SpanSelectionRoundDiagnostic(**values))
        except (KeyError, TypeError, ValueError) as exc:
            raise SpanDiagnosticStoreError(
                "span-selection 라운드 진단 값을 복원하지 못했습니다"
            ) from exc
    snapshot = PersistedSpanSelectionDiagnostics(
        run_id=str(stored_run_id),
        schema_version=schema_version,
        result_reason=str(result_reason),
        rounds=tuple(restored),
        recorded_at=str(recorded_at),
    )
    _validate_snapshot(
        schema_version=snapshot.schema_version,
        run_id=snapshot.run_id,
        result_reason=snapshot.result_reason,
        rounds=snapshot.rounds,
        recorded_at=snapshot.recorded_at,
    )
    return snapshot


def _validate_snapshot(
    *,
    schema_version: int,
    run_id: str,
    result_reason: str,
    rounds: tuple[SpanSelectionRoundDiagnostic, ...],
    recorded_at: str,
) -> None:
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SpanDiagnosticStoreError("지원하지 않는 span-selection 진단 형식입니다")
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
        if item.validation_kept + item.validation_rejected != item.provider_selected:
            raise SpanDiagnosticStoreError("검증 집계가 provider 선택 수와 다릅니다")
        _validate_reason_counts(item, schema_version=schema_version)


def _restore_reason_counts(value: object) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, list):
        raise SpanDiagnosticStoreError("span-selection 거절 사유 집계 모양이 다릅니다")
    restored: list[tuple[str, int]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise SpanDiagnosticStoreError(
                "span-selection 거절 사유 집계 모양이 다릅니다"
            )
        restored.append((item[0], item[1]))
    return tuple(restored)


def _validate_reason_counts(
    item: SpanSelectionRoundDiagnostic, *, schema_version: int
) -> None:
    counts = item.validation_rejection_reason_counts
    if type(counts) is not tuple:
        raise SpanDiagnosticStoreError("span-selection 거절 사유 집계 형식이 다릅니다")
    if schema_version == LEGACY_SCHEMA_VERSION:
        if counts:
            raise SpanDiagnosticStoreError("v1 span-selection 진단에는 사유 집계가 없습니다")
        return
    codes: list[str] = []
    total = 0
    for entry in counts:
        if type(entry) is not tuple or len(entry) != 2:
            raise SpanDiagnosticStoreError("span-selection 거절 사유 집계 형식이 다릅니다")
        code, count = entry
        if type(code) is not str or code not in SAFE_VALIDATION_REJECTION_REASONS:
            raise SpanDiagnosticStoreError("허용되지 않은 span-selection 거절 사유입니다")
        if type(count) is not int or count <= 0:
            raise SpanDiagnosticStoreError("span-selection 거절 사유 수가 올바르지 않습니다")
        codes.append(code)
        total += count
    if codes != sorted(set(codes)):
        raise SpanDiagnosticStoreError("span-selection 거절 사유가 중복되거나 정렬되지 않았습니다")
    if total != item.validation_rejected:
        raise SpanDiagnosticStoreError("span-selection 거절 사유 합계가 전체 거절 수와 다릅니다")
