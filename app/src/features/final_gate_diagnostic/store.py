"""파이프라인 최종 게이트 사유의 닫힌 코드를 SQLite에 보존한다."""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from typing import Final

from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DIAGNOSTIC_COLUMNS,
    FINAL_GATE_DIAGNOSTIC_CORP_CODE_LENGTH,
    FINAL_GATE_DIAGNOSTIC_LEGACY_COLUMNS,
    FINAL_GATE_DIAGNOSTIC_LEGACY_SCHEMA_VERSION,
    FINAL_GATE_DIAGNOSTIC_MAX_COMPANY_LENGTH,
    FINAL_GATE_DIAGNOSTIC_MAX_END_STEP_LENGTH,
    FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION,
    FINAL_GATE_DIAGNOSTIC_SUPPORTED_SCHEMA_VERSIONS,
    FINAL_GATE_DIAGNOSTIC_TABLE,
    SAFE_FINAL_GATE_REASONS,
)


TABLE_FINAL_GATE_DIAGNOSTICS: Final[str] = FINAL_GATE_DIAGNOSTIC_TABLE
LEGACY_SCHEMA_VERSION: Final[int] = FINAL_GATE_DIAGNOSTIC_LEGACY_SCHEMA_VERSION
SCHEMA_VERSION: Final[int] = FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = (
    FINAL_GATE_DIAGNOSTIC_SUPPORTED_SCHEMA_VERSIONS
)
_TABLE_COLUMNS: Final[frozenset[str]] = FINAL_GATE_DIAGNOSTIC_COLUMNS
_LEGACY_TABLE_COLUMNS: Final[frozenset[str]] = FINAL_GATE_DIAGNOSTIC_LEGACY_COLUMNS
_MIGRATION_TABLE: Final[str] = "pipeline_final_gate_diagnostics_v1_migration"
_MIGRATION_SAVEPOINT: Final[str] = "final_gate_diagnostics_v2"


class FinalGateDiagnosticStoreError(RuntimeError):
    """최종 게이트 원장이 손상됐거나 기존 값을 바꾸려 했다."""


@dataclass(frozen=True)
class PersistedFinalGateDiagnostic:
    run_id: str
    schema_version: int
    corp_code: str
    confirmed_company: str
    end_step: str
    reason_code: str
    recorded_at: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """v1 행을 보존하면서 회사 식별값을 받는 현재 원장을 준비한다."""

    if not _table_exists(conn):
        _create_table(conn)
        return
    columns = _table_columns(conn, TABLE_FINAL_GATE_DIAGNOSTICS)
    if columns not in {_LEGACY_TABLE_COLUMNS, _TABLE_COLUMNS}:
        raise FinalGateDiagnosticStoreError("최종 게이트 진단 표 필드가 다릅니다")
    definition = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_FINAL_GATE_DIAGNOSTICS,),
        ).fetchone()[0]
        or ""
    )
    normalized = "".join(definition.lower().split())
    has_current_version_constraint = (
        f"check(schema_versionin({LEGACY_SCHEMA_VERSION},{SCHEMA_VERSION}))"
        in normalized
    )
    has_current_reason_constraint = all(
        f"'{reason}'" in definition for reason in SAFE_FINAL_GATE_REASONS
    )
    if (
        columns == _TABLE_COLUMNS
        and has_current_version_constraint
        and has_current_reason_constraint
    ):
        return
    if columns == _LEGACY_TABLE_COLUMNS and (
        f"check(schema_version={LEGACY_SCHEMA_VERSION})" not in normalized
    ):
        raise FinalGateDiagnosticStoreError(
            "지원하지 않는 최종 게이트 진단 표 제약입니다"
        )
    _migrate_table(conn, source_columns=columns)


def _create_table(conn: sqlite3.Connection) -> None:
    allowed = ", ".join(f"'{value}'" for value in sorted(SAFE_FINAL_GATE_REASONS))
    conn.execute(
        f"""
        CREATE TABLE {TABLE_FINAL_GATE_DIAGNOSTICS} (
            run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL CHECK (
                schema_version IN ({LEGACY_SCHEMA_VERSION}, {SCHEMA_VERSION})
            ),
            corp_code TEXT NOT NULL DEFAULT '',
            confirmed_company TEXT NOT NULL DEFAULT '',
            end_step TEXT NOT NULL DEFAULT '',
            reason_code TEXT NOT NULL CHECK (reason_code IN ({allowed})),
            recorded_at TEXT NOT NULL,
            CHECK (
                (schema_version = {LEGACY_SCHEMA_VERSION}
                 AND corp_code = '' AND confirmed_company = '' AND end_step = '')
                OR
                (schema_version = {SCHEMA_VERSION}
                 AND corp_code <> '' AND confirmed_company <> '' AND end_step <> '')
            )
        )
        """
    )
    _validate_table_columns(conn, TABLE_FINAL_GATE_DIAGNOSTICS)


def _migrate_table(
    conn: sqlite3.Connection, *, source_columns: frozenset[str]
) -> None:
    """기존 행을 보존하며 v2 필드와 최신 닫힌 사유 제약을 함께 적용한다."""

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_MIGRATION_TABLE,),
    ).fetchone():
        raise FinalGateDiagnosticStoreError("최종 게이트 진단 임시 표가 이미 있습니다")
    stored_reasons = {
        str(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT reason_code FROM {TABLE_FINAL_GATE_DIAGNOSTICS}"
        )
    }
    if not stored_reasons <= SAFE_FINAL_GATE_REASONS:
        raise FinalGateDiagnosticStoreError("지원하지 않는 기존 최종 게이트 사유가 있습니다")
    stored_versions = {
        row[0]
        for row in conn.execute(
            f"SELECT DISTINCT schema_version FROM {TABLE_FINAL_GATE_DIAGNOSTICS}"
        )
    }
    if not stored_versions <= SUPPORTED_SCHEMA_VERSIONS:
        raise FinalGateDiagnosticStoreError(
            "지원하지 않는 기존 최종 게이트 진단 형식이 있습니다"
        )
    if source_columns == _LEGACY_TABLE_COLUMNS and stored_versions - {
        LEGACY_SCHEMA_VERSION
    }:
        raise FinalGateDiagnosticStoreError(
            "v1 최종 게이트 진단 표에 현재 형식 행이 있습니다"
        )

    conn.execute(f"SAVEPOINT {_MIGRATION_SAVEPOINT}")
    try:
        conn.execute(
            f"ALTER TABLE {TABLE_FINAL_GATE_DIAGNOSTICS} RENAME TO {_MIGRATION_TABLE}"
        )
        _create_table(conn)
        if source_columns == _LEGACY_TABLE_COLUMNS:
            conn.execute(
                f"""
                INSERT INTO {TABLE_FINAL_GATE_DIAGNOSTICS}
                    (run_id, schema_version, corp_code, confirmed_company,
                     end_step, reason_code, recorded_at)
                SELECT run_id, schema_version, '', '', '', reason_code, recorded_at
                  FROM {_MIGRATION_TABLE}
                """
            )
        else:
            conn.execute(
                f"""
                INSERT INTO {TABLE_FINAL_GATE_DIAGNOSTICS}
                    (run_id, schema_version, corp_code, confirmed_company,
                     end_step, reason_code, recorded_at)
                SELECT run_id, schema_version, corp_code, confirmed_company,
                       end_step, reason_code, recorded_at
                  FROM {_MIGRATION_TABLE}
                """
            )
        conn.execute(f"DROP TABLE {_MIGRATION_TABLE}")
        conn.execute(f"RELEASE SAVEPOINT {_MIGRATION_SAVEPOINT}")
    except (sqlite3.Error, FinalGateDiagnosticStoreError) as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {_MIGRATION_SAVEPOINT}")
        conn.execute(f"RELEASE SAVEPOINT {_MIGRATION_SAVEPOINT}")
        raise FinalGateDiagnosticStoreError(
            "최종 게이트 진단 표를 현재 형식으로 옮기지 못했습니다"
        ) from exc


def record_once(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    corp_code: str,
    confirmed_company: str,
    end_step: str,
    reason_code: str,
    recorded_at: str,
) -> bool:
    """한 실행의 회사 식별값과 원문 없는 최종 게이트 코드를 한 번만 기록한다."""

    diagnostic = PersistedFinalGateDiagnostic(
        run_id=str(run_id).strip(),
        schema_version=SCHEMA_VERSION,
        corp_code=str(corp_code).strip(),
        confirmed_company=" ".join(str(confirmed_company).split()),
        end_step=str(end_step).strip(),
        reason_code=str(reason_code).strip(),
        recorded_at=str(recorded_at).strip(),
    )
    _validate_diagnostic(diagnostic)
    ensure_schema(conn)
    values = (
        diagnostic.run_id,
        diagnostic.schema_version,
        diagnostic.corp_code,
        diagnostic.confirmed_company,
        diagnostic.end_step,
        diagnostic.reason_code,
        diagnostic.recorded_at,
    )
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_FINAL_GATE_DIAGNOSTICS}
            (run_id, schema_version, corp_code, confirmed_company, end_step,
             reason_code, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    if cursor.rowcount == 1:
        return True
    existing = conn.execute(
        f"""
        SELECT run_id, schema_version, corp_code, confirmed_company, end_step,
               reason_code, recorded_at
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
    columns = _table_columns(conn, TABLE_FINAL_GATE_DIAGNOSTICS)
    clean_run_id = str(run_id).strip()
    if columns == _LEGACY_TABLE_COLUMNS:
        row = conn.execute(
            f"""
            SELECT run_id, schema_version, reason_code, recorded_at
              FROM {TABLE_FINAL_GATE_DIAGNOSTICS}
             WHERE run_id = ?
            """,
            (clean_run_id,),
        ).fetchone()
        restored_values = (
            (str(row[0]), row[1], "", "", "", str(row[2]), str(row[3]))
            if row is not None
            else None
        )
    elif columns == _TABLE_COLUMNS:
        row = conn.execute(
            f"""
            SELECT run_id, schema_version, corp_code, confirmed_company,
                   end_step, reason_code, recorded_at
              FROM {TABLE_FINAL_GATE_DIAGNOSTICS}
             WHERE run_id = ?
            """,
            (clean_run_id,),
        ).fetchone()
        restored_values = tuple(row) if row is not None else None
    else:
        raise FinalGateDiagnosticStoreError("최종 게이트 진단 표 필드가 다릅니다")
    if restored_values is None:
        return None
    diagnostic = PersistedFinalGateDiagnostic(
        run_id=str(restored_values[0]),
        schema_version=restored_values[1],
        corp_code=str(restored_values[2]),
        confirmed_company=str(restored_values[3]),
        end_step=str(restored_values[4]),
        reason_code=str(restored_values[5]),
        recorded_at=str(restored_values[6]),
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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> frozenset[str]:
    return frozenset(
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({table_name})"
        )
    )


def _validate_table_columns(conn: sqlite3.Connection, table_name: str) -> None:
    columns = _table_columns(conn, table_name)
    if columns != _TABLE_COLUMNS:
        raise FinalGateDiagnosticStoreError("최종 게이트 진단 표 필드가 다릅니다")


def _validate_diagnostic(diagnostic: PersistedFinalGateDiagnostic) -> None:
    if (
        not diagnostic.run_id
        or len(diagnostic.run_id) > 128
        or re.fullmatch(r"[A-Za-z0-9._:-]+", diagnostic.run_id) is None
    ):
        raise FinalGateDiagnosticStoreError("실행 번호가 올바르지 않습니다")
    if (
        type(diagnostic.schema_version) is not int
        or diagnostic.schema_version not in SUPPORTED_SCHEMA_VERSIONS
    ):
        raise FinalGateDiagnosticStoreError("지원하지 않는 최종 게이트 진단 형식입니다")
    if diagnostic.schema_version == LEGACY_SCHEMA_VERSION:
        if any(
            (diagnostic.corp_code, diagnostic.confirmed_company, diagnostic.end_step)
        ):
            raise FinalGateDiagnosticStoreError(
                "v1 최종 게이트 진단에는 회사 식별값이 없어야 합니다"
            )
    else:
        if re.fullmatch(
            rf"[0-9]{{{FINAL_GATE_DIAGNOSTIC_CORP_CODE_LENGTH}}}",
            diagnostic.corp_code,
        ) is None:
            raise FinalGateDiagnosticStoreError("회사 고유번호가 올바르지 않습니다")
        if (
            not diagnostic.confirmed_company
            or len(diagnostic.confirmed_company)
            > FINAL_GATE_DIAGNOSTIC_MAX_COMPANY_LENGTH
        ):
            raise FinalGateDiagnosticStoreError("확정 회사명이 올바르지 않습니다")
        if (
            not diagnostic.end_step
            or len(diagnostic.end_step) > FINAL_GATE_DIAGNOSTIC_MAX_END_STEP_LENGTH
        ):
            raise FinalGateDiagnosticStoreError("종료 단계가 올바르지 않습니다")
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
