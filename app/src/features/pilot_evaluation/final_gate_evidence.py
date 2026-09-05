"""파일럿 실행의 최종 게이트 부속 원장을 닫힌 코드로 교차검증한다."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Final, Mapping

from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DIAGNOSTIC_COLUMNS,
    FINAL_GATE_DIAGNOSTIC_CORP_CODE_LENGTH,
    FINAL_GATE_DIAGNOSTIC_LEGACY_COLUMNS,
    FINAL_GATE_DIAGNOSTIC_LEGACY_SCHEMA_VERSION,
    FINAL_GATE_DIAGNOSTIC_MAX_COMPANY_LENGTH,
    FINAL_GATE_DIAGNOSTIC_MAX_END_STEP_LENGTH,
    FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION,
    FINAL_GATE_DIAGNOSTIC_TABLE,
    SAFE_FINAL_GATE_REASONS,
)


#: 게이트 중단의 옛 이름. 과거 저장본(checkpoint/SQLite)을 읽을 때만 인식하고
#: 새로 기록하지 않는다.
LEGACY_GATE_STOPPED_OUTCOME: Final[str] = "자료부족_중단"


class FinalGateEvidenceError(RuntimeError):
    """최종 게이트 행이 lifecycle·종료값과 정확히 결속되지 않았다."""


def _table_columns(conn: sqlite3.Connection) -> frozenset[str] | None:
    exists = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (FINAL_GATE_DIAGNOSTIC_TABLE,),
        ).fetchone()
        is not None
    )
    if not exists:
        return None
    return frozenset(
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({FINAL_GATE_DIAGNOSTIC_TABLE})"
        )
    )


def validate_table_if_present(conn: sqlite3.Connection) -> bool:
    """표가 있으면 exact schema를 검사하고, 없으면 생성하지 않는다."""

    columns = _table_columns(conn)
    if columns is None:
        return False
    if columns == FINAL_GATE_DIAGNOSTIC_LEGACY_COLUMNS:
        return True
    if columns == FINAL_GATE_DIAGNOSTIC_COLUMNS:
        return True
    raise FinalGateEvidenceError("최종 게이트 진단 표 필드가 다릅니다")


def _validate_schema_bound_metadata(
    *,
    columns: frozenset[str],
    schema_version: object,
    metadata: tuple[object, ...],
) -> None:
    if type(schema_version) is not int:
        raise FinalGateEvidenceError("지원하지 않는 최종 게이트 진단 형식입니다")
    if columns == FINAL_GATE_DIAGNOSTIC_LEGACY_COLUMNS:
        if schema_version != FINAL_GATE_DIAGNOSTIC_LEGACY_SCHEMA_VERSION:
            raise FinalGateEvidenceError("지원하지 않는 최종 게이트 진단 형식입니다")
        return
    if schema_version == FINAL_GATE_DIAGNOSTIC_LEGACY_SCHEMA_VERSION:
        if any(str(value) for value in metadata):
            raise FinalGateEvidenceError("v1 최종 게이트 진단에 v2 필드가 있습니다")
        return
    if schema_version != FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION:
        raise FinalGateEvidenceError("지원하지 않는 최종 게이트 진단 형식입니다")

    corp_code, confirmed_company, end_step = map(str, metadata)
    if (
        len(corp_code) != FINAL_GATE_DIAGNOSTIC_CORP_CODE_LENGTH
        or not corp_code.isascii()
        or not corp_code.isdigit()
    ):
        raise FinalGateEvidenceError("최종 게이트 진단 회사 고유번호가 올바르지 않습니다")
    if (
        not confirmed_company
        or confirmed_company != " ".join(confirmed_company.split())
        or len(confirmed_company) > FINAL_GATE_DIAGNOSTIC_MAX_COMPANY_LENGTH
    ):
        raise FinalGateEvidenceError("최종 게이트 진단 확정 회사명이 올바르지 않습니다")
    if (
        not end_step
        or end_step != end_step.strip()
        or len(end_step) > FINAL_GATE_DIAGNOSTIC_MAX_END_STEP_LENGTH
    ):
        raise FinalGateEvidenceError("최종 게이트 진단 종료 단계가 올바르지 않습니다")


def read_bound_reason(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    outcome: str,
    gate_stopped_outcome: str,
    lifecycle_record: Mapping[str, object],
) -> str:
    """종료값별 행 존재성과 lifecycle 시각을 한 SQLite snapshot에서 검사한다."""

    table_exists = validate_table_if_present(conn)
    columns = _table_columns(conn) if table_exists else None
    selected_columns = (
        "run_id, schema_version, reason_code, recorded_at"
        if columns == FINAL_GATE_DIAGNOSTIC_LEGACY_COLUMNS
        else (
            "run_id, schema_version, reason_code, recorded_at, "
            "corp_code, confirmed_company, end_step"
        )
    )
    rows = (
        conn.execute(
            f"SELECT {selected_columns} "
            f"FROM {FINAL_GATE_DIAGNOSTIC_TABLE} WHERE run_id=?",
            (run_id,),
        ).fetchall()
        if table_exists
        else []
    )
    # 과거 저장본은 옛 이름으로 게이트 중단을 기록했다. 읽기 호환으로만 인식한다.
    is_gate_stopped = outcome in (gate_stopped_outcome, LEGACY_GATE_STOPPED_OUTCOME)
    if not is_gate_stopped:
        if rows:
            raise FinalGateEvidenceError(
                "비게이트 종료에 최종 게이트 진단 행이 있습니다"
            )
        return ""
    if len(rows) != 1:
        raise FinalGateEvidenceError(
            "게이트 중단에는 최종 게이트 진단 행이 정확히 하나여야 합니다"
        )

    row = rows[0]
    stored_run_id = str(row[0])
    schema_version = row[1]
    reason_code = str(row[2])
    recorded_at = str(row[3])
    metadata = tuple(row[4:])
    lifecycle_at = str(lifecycle_record.get("at", ""))
    if stored_run_id != run_id:
        raise FinalGateEvidenceError("최종 게이트 진단 실행 번호가 lifecycle과 다릅니다")
    assert columns is not None
    _validate_schema_bound_metadata(
        columns=columns,
        schema_version=schema_version,
        metadata=metadata,
    )
    if reason_code not in SAFE_FINAL_GATE_REASONS:
        raise FinalGateEvidenceError("허용되지 않은 최종 게이트 사유입니다")
    try:
        parsed_at = dt.datetime.fromisoformat(recorded_at)
        parsed_lifecycle_at = dt.datetime.fromisoformat(lifecycle_at)
    except (TypeError, ValueError) as exc:
        raise FinalGateEvidenceError(
            "최종 게이트 진단 또는 lifecycle 시각이 올바르지 않습니다"
        ) from exc
    if (
        parsed_at.tzinfo is None
        or parsed_at.utcoffset() is None
        or parsed_lifecycle_at.tzinfo is None
        or parsed_lifecycle_at.utcoffset() is None
        or recorded_at != lifecycle_at
    ):
        raise FinalGateEvidenceError(
            "최종 게이트 진단 시각이 lifecycle 최종 시각과 다릅니다"
        )
    return reason_code
