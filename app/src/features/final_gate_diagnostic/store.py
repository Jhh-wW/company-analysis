"""파이프라인 최종 게이트 사유의 닫힌 코드를 SQLite에 보존한다."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import Final

from src.shared.final_gate_diagnostics import SAFE_FINAL_GATE_REASONS


TABLE_FINAL_GATE_DIAGNOSTICS: Final[str] = "pipeline_final_gate_diagnostics"
SCHEMA_VERSION: Final[int] = 1
_TABLE_COLUMNS: Final[frozenset[str]] = frozenset(
    {"run_id", "schema_version", "reason_code", "recorded_at"}
)


class FinalGateDiagnosticStoreError(RuntimeError):
    """최종 게이트 원장이 손상됐거나 기존 값을 바꾸려 했다."""


@dataclass(frozen=True)
class PersistedFinalGateDiagnostic:
    run_id: str
    schema_version: int
    reason_code: str
    recorded_at: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """쓰기 연결 안에서 신규 부속 원장을 준비한다."""

    allowed = ", ".join(f"'{value}'" for value in sorted(SAFE_FINAL_GATE_REASONS))
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_FINAL_GATE_DIAGNOSTICS} (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK (schema_version = {SCHEMA_VERSION}),
            reason_code TEXT NOT NULL CHECK (reason_code IN ({allowed})),
            recorded_at TEXT NOT NULL
        )
        """
    )
    _validate_table_columns(conn)


def record_once(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    reason_code: str,
    recorded_at: str,
) -> bool:
    """한 실행의 원문 없는 최종 게이트 코드를 한 번만 기록한다."""

    diagnostic = PersistedFinalGateDiagnostic(
        run_id=str(run_id).strip(),
        schema_version=SCHEMA_VERSION,
        reason_code=str(reason_code).strip(),
        recorded_at=str(recorded_at).strip(),
    )
    _validate_diagnostic(diagnostic)
    ensure_schema(conn)
    values = (
        diagnostic.run_id,
        diagnostic.schema_version,
        diagnostic.reason_code,
        diagnostic.recorded_at,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_FINAL_GATE_DIAGNOSTICS}
            (run_id, schema_version, reason_code, recorded_at)
        VALUES (?, ?, ?, ?)
        """,
        values,
    )
    if cursor.rowcount == 1:
        return True
    existing = conn.execute(
        f"""
        SELECT run_id, schema_version, reason_code, recorded_at
          FROM {TABLE_FINAL_GATE_DIAGNOSTICS}
         WHERE run_id = ?
        """,
        (diagnostic.run_id,),
    ).fetchone()
    if existing is not None and tuple(existing) == values:
        return False
    raise FinalGateDiagnosticStoreError(
        "같은 실행 번호의 최종 게이트 진단을 다른 값으로 바꿀 수 없습니다"
    )


def read_for_run(
    conn: sqlite3.Connection, run_id: str
) -> PersistedFinalGateDiagnostic | None:
    """표나 값을 만들지 않고 기존 최종 게이트 진단을 복원한다."""

    if not _table_exists(conn):
        return None
    _validate_table_columns(conn)
    row = conn.execute(
        f"""
        SELECT run_id, schema_version, reason_code, recorded_at
          FROM {TABLE_FINAL_GATE_DIAGNOSTICS}
         WHERE run_id = ?
        """,
        (str(run_id).strip(),),
    ).fetchone()
    if row is None:
        return None
    diagnostic = PersistedFinalGateDiagnostic(
        run_id=str(row[0]),
        schema_version=row[1],
        reason_code=str(row[2]),
        recorded_at=str(row[3]),
    )
    _validate_diagnostic(diagnostic)
    return diagnostic


def _table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_FINAL_GATE_DIAGNOSTICS,),
        ).fetchone()
        is not None
    )


def _validate_table_columns(conn: sqlite3.Connection) -> None:
    columns = frozenset(
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({TABLE_FINAL_GATE_DIAGNOSTICS})"
        )
    )
    if columns != _TABLE_COLUMNS:
        raise FinalGateDiagnosticStoreError("최종 게이트 진단 표 필드가 다릅니다")


def _validate_diagnostic(diagnostic: PersistedFinalGateDiagnostic) -> None:
    if (
        not diagnostic.run_id
        or len(diagnostic.run_id) > 128
        or re.fullmatch(r"[A-Za-z0-9._:-]+", diagnostic.run_id) is None
    ):
        raise FinalGateDiagnosticStoreError("실행 번호가 올바르지 않습니다")
    if type(diagnostic.schema_version) is not int or diagnostic.schema_version != 1:
        raise FinalGateDiagnosticStoreError("지원하지 않는 최종 게이트 진단 형식입니다")
    if diagnostic.reason_code not in SAFE_FINAL_GATE_REASONS:
        raise FinalGateDiagnosticStoreError("허용되지 않은 최종 게이트 사유입니다")
    try:
        parsed_at = dt.datetime.fromisoformat(diagnostic.recorded_at)
    except (TypeError, ValueError) as exc:
        raise FinalGateDiagnosticStoreError(
            "최종 게이트 진단 시각이 올바르지 않습니다"
        ) from exc
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        raise FinalGateDiagnosticStoreError(
            "최종 게이트 진단 시각에는 시간대가 필요합니다"
        )
