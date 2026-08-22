"""봉인된 단일 파일럿 배치에 결속해 사람 품질판정만 저장한다."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Iterator, Mapping

from src.features.pilot_evaluation.checkpoint import (
    PENDING_STATE,
    SCHEMA_VERSION as CHECKPOINT_SCHEMA_VERSION,
    TERMINAL_STATES,
)
from src.features.pilot_evaluation.contract import (
    PilotCase,
    PilotResult,
    PilotSummary,
    evaluate_pilot,
)
from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
    manifest_sha256,
)
from src.features.pilot_evaluation.runner import (
    PILOT_BINDING_KEY,
    PILOT_BINDING_SCHEMA_VERSION,
    PILOT_BINDING_TABLE,
)
from src.shared.automatic_release_record import (
    AutomaticReleaseRecord,
    validate_persisted_automatic_release,
)
from src.shared.company_identity import verified_official_company_names_equivalent


SCHEMA_VERSION: Final[int] = 3
QUALITY_FILENAME: Final[str] = "canonical-pilot25-quality.json"
MAX_EVIDENCE_BYTES: Final[int] = 4 * 1024 * 1024
_AUTOMATIC_RELEASE_TABLE: Final[str] = "pdf_automatic_release_records"
_REPORT_OUTCOME: Final[str] = "보고서"
_OUTCOME_CODES: Final[Mapping[str, str]] = {
    "보고서": "report",
    "회사_못찾음": "not_found",
    "거부_공공기관": "reject_public",
    "거부_공시없음": "reject_no_disclosure",
    "공고_폐기": "posting_discarded",
    "자료부족_중단": "gate_stopped",
    "생성_실패": "failed",
}
_NO_RUN_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"identity_mismatch", "identity_ref_unverified"}
)
_END_STEP_CONFIRM: Final[str] = "03_확인"
QUALITY_CASE_IDS: Final[tuple[str, ...]] = tuple(
    case.case_id
    for case in CANONICAL_PILOT_CASES
    if case.case_id in APPROVED_PAID_CASE_IDS
)
QUALITY_JUDGMENTS: Final[frozenset[str]] = frozenset({"release", "stop"})
_HEX_32_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{32}")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_ERROR_TYPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9_.:]{0,63}"
)
_CHECKPOINT_TOP_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "binding_id",
        "manifest_sha256",
        "origin",
        "server_instance_sha256",
        "data_path_sha256",
        "created_at",
        "updated_at",
        "cases",
    }
)
_CHECKPOINT_ROW_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "case_id",
        "state",
        "run_id",
        "report_id",
        "outcome",
        "internal_ai_cost_krw",
        "billing_uncertain",
        "selected_corp_code",
        "legal_name",
        "paid_boundary_at",
        "result_http_status",
        "error_code",
        "updated_at",
    }
)
_ROW_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "case_id",
        "source_binding_id",
        "source_checkpoint_sha256",
        "source_run_id",
        "legal_entity_correct",
        "completed",
        "stopped",
        "error_type",
        "automatic_judgment",
        "automatic_release_observed",
        "automatic_release_record_sha256",
        "report_sha256",
        "pdf_sha256",
        "user_judgment",
        "judgments_agree",
        "elapsed_sec",
        "internal_ai_cost_krw",
        "wrong_legal_entity_released",
        "partial_report_released",
        "major_fact_citation_numeric_error_auto_passed",
        "reviewed_at",
    }
)
_TOP_LEVEL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "manifest_sha256",
        "approved_case_ids",
        "source_binding_id",
        "source_checkpoint_sha256",
        "source_storage_binding_sha256",
        "created_at",
        "updated_at",
        "cases",
    }
)
_BOOLEAN_ROW_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "legal_entity_correct",
        "completed",
        "stopped",
        "automatic_release_observed",
        "judgments_agree",
        "wrong_legal_entity_released",
        "partial_report_released",
        "major_fact_citation_numeric_error_auto_passed",
    }
)


class QualityStoreError(RuntimeError):
    """품질판정과 봉인 실행 증거가 다르거나 손상됐다."""


@dataclass(frozen=True)
class AutomaticCaseEvidence:
    case_id: str
    binding_id: str
    checkpoint_sha256: str
    run_id: str
    legal_entity_correct: bool
    completed: bool
    stopped: bool
    error_type: str
    automatic_judgment: str
    automatic_release_observed: bool
    automatic_release_record_sha256: str
    report_sha256: str
    pdf_sha256: str
    elapsed_sec: float
    internal_ai_cost_krw: float


@dataclass(frozen=True)
class BoundPilotEvidence:
    binding_id: str
    checkpoint_sha256: str
    storage_binding_sha256: str
    cases: Mapping[str, AutomaticCaseEvidence]


@dataclass(frozen=True)
class QualityAggregate:
    ready: bool
    recorded_case_ids: tuple[str, ...]
    missing_case_ids: tuple[str, ...]
    summary: PilotSummary | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalized_judgment(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _validated_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise QualityStoreError(f"품질판정 JSON의 {field_name} 시각이 올바르지 않습니다")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QualityStoreError(
            f"품질판정 JSON의 {field_name} 시각이 올바르지 않습니다"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QualityStoreError(
            f"품질판정 JSON의 {field_name} 시각에는 시간대가 필요합니다"
        )
    return parsed


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_terminal_result_status(value: object) -> bool:
    return type(value) is int and value in {200, 409}


def _is_exact_zero_cost(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and float(value) == 0.0
    )


def _path_sha256(path: Path) -> str:
    return _sha256_bytes(str(path.resolve()).casefold().encode("utf-8"))


def _load_checkpoint(path: Path) -> tuple[dict[str, object], str]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise QualityStoreError("품질판정과 결속할 checkpoint 파일이 없습니다")
    try:
        with resolved.open("rb") as handle:
            raw = handle.read(MAX_EVIDENCE_BYTES + 1)
    except OSError as exc:
        raise QualityStoreError("checkpoint를 안전하게 읽지 못했습니다") from exc
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise QualityStoreError("checkpoint가 허용 크기를 넘었습니다")
    try:
        snapshot = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QualityStoreError("checkpoint JSON이 올바르지 않습니다") from exc
    if not isinstance(snapshot, dict) or set(snapshot) != _CHECKPOINT_TOP_FIELDS:
        raise QualityStoreError("checkpoint 최상위 모양이 정본과 다릅니다")
    if snapshot.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise QualityStoreError("지원하지 않는 checkpoint 형식입니다")
    if snapshot.get("manifest_sha256") != manifest_sha256(CANONICAL_PILOT_CASES):
        raise QualityStoreError("checkpoint manifest가 현재 정본과 다릅니다")
    binding_id = snapshot.get("binding_id")
    if not isinstance(binding_id, str) or _HEX_32_RE.fullmatch(binding_id) is None:
        raise QualityStoreError("checkpoint binding ID가 올바르지 않습니다")
    for field_name in ("server_instance_sha256", "data_path_sha256"):
        value = snapshot.get(field_name)
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise QualityStoreError(f"checkpoint {field_name} 값이 올바르지 않습니다")
    created_at = _validated_timestamp(
        snapshot.get("created_at"), field_name="checkpoint.created_at"
    )
    updated_at = _validated_timestamp(
        snapshot.get("updated_at"), field_name="checkpoint.updated_at"
    )
    if updated_at < created_at:
        raise QualityStoreError("checkpoint 갱신 시각이 생성 시각보다 이릅니다")
    rows = snapshot.get("cases")
    expected_ids = tuple(case.case_id for case in CANONICAL_PILOT_CASES)
    if not isinstance(rows, dict) or tuple(rows) != expected_ids:
        raise QualityStoreError("checkpoint case 구성이 P01~P25 정본과 다릅니다")
    for case_id, row in rows.items():
        if not isinstance(row, dict) or set(row) != _CHECKPOINT_ROW_FIELDS:
            raise QualityStoreError(f"{case_id} checkpoint 행 모양이 다릅니다")
        if row.get("case_id") != case_id:
            raise QualityStoreError(f"{case_id} checkpoint 내부 ID가 다릅니다")
        row_updated_at = _validated_timestamp(
            row.get("updated_at"), field_name=f"checkpoint.{case_id}.updated_at"
        )
        if row_updated_at < created_at or row_updated_at > updated_at:
            raise QualityStoreError(f"{case_id} checkpoint 갱신 시각이 배치 범위를 벗어납니다")
        if case_id not in APPROVED_PAID_CASE_IDS and row.get("updated_at") != snapshot.get(
            "created_at"
        ):
            raise QualityStoreError("P11~P25의 checkpoint 갱신 시각이 초기값과 다릅니다")
    return snapshot, _sha256_bytes(raw)


def quality_path_for_checkpoint(checkpoint_path: Path) -> Path:
    """정본 checkpoint를 검증하고 같은 배치 폴더의 고정 품질 경로를 만든다."""

    resolved = checkpoint_path.resolve()
    _load_checkpoint(resolved)
    return resolved.with_name(QUALITY_FILENAME)


def _read_bound_pilot_evidence(
    checkpoint_path: Path, storage_db_path: Path
) -> BoundPilotEvidence:
    checkpoint, checkpoint_sha256 = _load_checkpoint(checkpoint_path)
    resolved_db = storage_db_path.resolve()
    if not resolved_db.is_file() or resolved_db.is_symlink():
        raise QualityStoreError("품질판정과 결속할 평가 SQLite가 없습니다")
    expected_manifest = manifest_sha256(CANONICAL_PILOT_CASES)
    binding_id = str(checkpoint["binding_id"])
    checkpoint_path_sha256 = _path_sha256(checkpoint_path)
    data_path_sha256 = _path_sha256(resolved_db)
    if checkpoint.get("data_path_sha256") != data_path_sha256:
        raise QualityStoreError("checkpoint와 평가 SQLite 경로 결속이 다릅니다")
    try:
        uri = resolved_db.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            # 여러 원장 SELECT가 하나의 SQLite snapshot을 보도록 읽기 transaction을
            # 명시적으로 연다. Python sqlite3는 SELECT만으로는 자동 BEGIN하지 않는다.
            conn.execute("BEGIN")
            binding = conn.execute(
                f"SELECT schema_version, binding_id, manifest_sha256, origin, "
                "server_instance_sha256, data_path_sha256, checkpoint_path_sha256, "
                "checkpoint_content_sha256 "
                f"FROM {PILOT_BINDING_TABLE} WHERE pilot_key=?",
                (PILOT_BINDING_KEY,),
            ).fetchone()
            if binding is None:
                raise QualityStoreError("평가 SQLite에 파일럿 binding이 없습니다")
            binding_values = tuple(str(value) for value in binding)
            expected_binding = (
                str(PILOT_BINDING_SCHEMA_VERSION),
                binding_id,
                expected_manifest,
                str(checkpoint["origin"]),
                str(checkpoint["server_instance_sha256"]),
                data_path_sha256,
                checkpoint_path_sha256,
                checkpoint_sha256,
            )
            if binding_values != expected_binding:
                raise QualityStoreError("checkpoint와 평가 SQLite binding이 다릅니다")
            storage_binding_sha256 = _sha256_bytes(
                json.dumps(
                    binding_values,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            evidence = _derive_case_evidence(
                conn,
                checkpoint=checkpoint,
                binding_id=binding_id,
                checkpoint_sha256=checkpoint_sha256,
            )
    except QualityStoreError:
        raise
    except sqlite3.Error as exc:
        raise QualityStoreError("평가 SQLite 증거를 안전하게 읽지 못했습니다") from exc
    # 읽는 동안 checkpoint가 바뀌면 DB와 결속된 처음 지문을 신뢰하지 않는다.
    _, verified_checkpoint_sha256 = _load_checkpoint(checkpoint_path)
    if verified_checkpoint_sha256 != checkpoint_sha256:
        raise QualityStoreError("증거 확인 중 checkpoint가 바뀌었습니다")
    return BoundPilotEvidence(
        binding_id=binding_id,
        checkpoint_sha256=checkpoint_sha256,
        storage_binding_sha256=storage_binding_sha256,
        cases=evidence,
    )


def _derive_case_evidence(
    conn: sqlite3.Connection,
    *,
    checkpoint: Mapping[str, object],
    binding_id: str,
    checkpoint_sha256: str,
) -> Mapping[str, AutomaticCaseEvidence]:
    rows = checkpoint["cases"]
    assert isinstance(rows, dict)
    manifest_by_id = {case.case_id: case for case in CANONICAL_PILOT_CASES}
    evidence: dict[str, AutomaticCaseEvidence] = {}
    for case_id, row in rows.items():
        assert isinstance(row, dict)
        if case_id not in APPROVED_PAID_CASE_IDS:
            untouched = (
                row.get("state") == PENDING_STATE
                and row.get("run_id") == ""
                and row.get("report_id") == ""
                and row.get("outcome") == ""
                and row.get("internal_ai_cost_krw") is None
                and row.get("billing_uncertain") is False
                and row.get("paid_boundary_at") == ""
                and row.get("selected_corp_code") == ""
                and row.get("legal_name") == ""
                and row.get("result_http_status") is None
                and row.get("error_code") == ""
            )
            if not untouched:
                raise QualityStoreError("P11~P25는 이번 봉인 배치에서 미실행이어야 합니다")
            continue
        state = str(row.get("state", ""))
        if state not in TERMINAL_STATES or row.get("billing_uncertain") is not False:
            raise QualityStoreError(
                "P01~P10 전부 같은 봉인 배치에서 terminal·비용확정이어야 합니다"
            )
        paid_boundary_at = _validated_timestamp(
            row.get("paid_boundary_at"),
            field_name=f"checkpoint.{case_id}.paid_boundary_at",
        )
        checkpoint_created_at = _validated_timestamp(
            checkpoint.get("created_at"), field_name="checkpoint.created_at"
        )
        row_updated_at = _validated_timestamp(
            row.get("updated_at"), field_name=f"checkpoint.{case_id}.updated_at"
        )
        if not checkpoint_created_at <= paid_boundary_at <= row_updated_at:
            raise QualityStoreError(f"{case_id} 유료 경계 시각이 실행 범위를 벗어납니다")
        run_id = str(row.get("run_id", ""))
        if not run_id:
            evidence[case_id] = _derive_no_run_case(
                case_id=case_id,
                row=row,
                binding_id=binding_id,
                checkpoint_sha256=checkpoint_sha256,
            )
        elif _HEX_32_RE.fullmatch(run_id) is not None:
            evidence[case_id] = _derive_one_case(
                conn,
                case_id=case_id,
                row=row,
                manifest_case=manifest_by_id[case_id],
                binding_id=binding_id,
                checkpoint_sha256=checkpoint_sha256,
            )
        else:
            raise QualityStoreError(f"{case_id} 실행 증거의 run ID가 올바르지 않습니다")
    return evidence


def _derive_no_run_case(
    *,
    case_id: str,
    row: Mapping[str, object],
    binding_id: str,
    checkpoint_sha256: str,
) -> AutomaticCaseEvidence:
    state = str(row.get("state", ""))
    if state == "identity_ref_unverified":
        expected_outcome = "IDENTITY_REF_UNVERIFIED"
        allowed_error_types = frozenset({"candidate_ref_not_observed"})
    elif state == "identity_mismatch":
        expected_outcome = ""
        allowed_error_types = frozenset(
            {"expected_corp_code_not_unique", "expected_direct_corp_code_not_unique"}
        )
    else:
        raise QualityStoreError(f"{case_id} 실행 전 terminal 상태가 허용되지 않습니다")
    error_type = str(row.get("error_code", "")).strip()
    if (
        state not in _NO_RUN_TERMINAL_STATES
        or str(row.get("outcome", "")) != expected_outcome
        or str(row.get("report_id", ""))
        or row.get("internal_ai_cost_krw") is not None
        or row.get("result_http_status") is not None
        or str(row.get("selected_corp_code", ""))
        or str(row.get("legal_name", ""))
        or error_type not in allowed_error_types
    ):
        raise QualityStoreError(f"{case_id} 실행 전 terminal 증거 모양이 다릅니다")
    # no-run terminal은 checkpoint 외에 같은 case를 가장할 DB run ID가 없다.
    # 격리 배치의 승인 run ID는 다른 P01~P10 행에서만 온다.
    return AutomaticCaseEvidence(
        case_id=case_id,
        binding_id=binding_id,
        checkpoint_sha256=checkpoint_sha256,
        run_id="",
        legal_entity_correct=False,
        completed=False,
        stopped=True,
        error_type=error_type,
        automatic_judgment="stop",
        automatic_release_observed=False,
        automatic_release_record_sha256="",
        report_sha256="",
        pdf_sha256="",
        elapsed_sec=0.0,
        internal_ai_cost_krw=0.0,
    )


def _derive_one_case(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    row: Mapping[str, object],
    manifest_case: object,
    binding_id: str,
    checkpoint_sha256: str,
) -> AutomaticCaseEvidence:
    run_id = str(row["run_id"])
    lifecycle = conn.execute(
        "SELECT state, confirmed_cost_krw, final_record_json "
        "FROM observability_run_lifecycle WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if lifecycle is None or str(lifecycle[0]) != "final":
        raise QualityStoreError(f"{case_id} lifecycle이 final로 결속되지 않았습니다")
    try:
        lifecycle_confirmed_cost = float(lifecycle[1])
        final_record = json.loads(str(lifecycle[2]))
        lifecycle_run_id = str(final_record["run_id"])
        lifecycle_cost = float(final_record["cost_krw"])
        elapsed_sec = float(final_record["elapsed_sec"])
        checkpoint_cost = float(row["internal_ai_cost_krw"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise QualityStoreError(f"{case_id} lifecycle·checkpoint 수치가 손상됐습니다") from exc
    numeric_values = (
        lifecycle_confirmed_cost,
        lifecycle_cost,
        elapsed_sec,
        checkpoint_cost,
    )
    if (
        lifecycle_run_id != run_id
        or any(not math.isfinite(value) or value < 0 for value in numeric_values)
        or lifecycle_confirmed_cost > lifecycle_cost + 1e-6
        or not math.isclose(lifecycle_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6)
    ):
        raise QualityStoreError(f"{case_id} lifecycle·checkpoint 비용이 다릅니다")
    spend = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(cost_krw), 0) "
        "FROM budget_spend_events WHERE run_id=?",
        (run_id,),
    ).fetchone()
    inflight = conn.execute(
        "SELECT COUNT(*) FROM budget_spend_inflight WHERE run_id=?",
        (run_id,),
    ).fetchone()
    try:
        spend_count = int(spend[0]) if spend is not None else 0
        spend_cost = float(spend[1]) if spend is not None else 0.0
        inflight_count = int(inflight[0]) if inflight is not None else 0
    except (TypeError, ValueError, OverflowError) as exc:
        raise QualityStoreError(f"{case_id} 비용 원장 수치가 손상됐습니다") from exc
    if (
        inflight_count != 0
        or not math.isfinite(spend_cost)
        or spend_cost < 0
        or not math.isclose(spend_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6)
    ):
        raise QualityStoreError(f"{case_id} 비용 원장이 checkpoint와 다릅니다")
    summary = (
        conn.execute(
            "SELECT outcome, internal_ai_cost_krw, automatic_release_sha256 "
            "FROM report_cost_summaries WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if _table_exists(conn, "report_cost_summaries")
        else None
    )
    state = str(row.get("state", ""))
    outcome = str(row.get("outcome", ""))
    release_sha256 = ""
    if summary is not None:
        try:
            summary_cost = float(summary[1])
        except (TypeError, ValueError, OverflowError) as exc:
            raise QualityStoreError(f"{case_id} 보고서 원가 요약이 손상됐습니다") from exc
        if (
            str(summary[0]) != outcome
            or not math.isfinite(summary_cost)
            or not math.isclose(summary_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6)
        ):
            raise QualityStoreError(f"{case_id} 보고서 원가 요약이 다릅니다")
        release_sha256 = str(summary[2] or "")
    report_columns = _table_columns(conn, "reports")
    if "payload_json" in report_columns:
        report = conn.execute(
            "SELECT report_id, corp_id, payload_json FROM reports WHERE report_id=?",
            (run_id,),
        ).fetchone()
    else:
        report = conn.execute(
            "SELECT report_id, corp_id FROM reports WHERE report_id=?",
            (run_id,),
        ).fetchone()
    release_rows = (
        conn.execute(
            f"SELECT report_sha256, pdf_sha256, checker_version, release_json, "
            f"release_sha256, released_at FROM {_AUTOMATIC_RELEASE_TABLE} "
            "WHERE report_id=?",
            (run_id,),
        ).fetchall()
        if _table_exists(conn, _AUTOMATIC_RELEASE_TABLE)
        else []
    )
    if len(release_rows) > 1:
        raise QualityStoreError(f"{case_id} 자동출고 대상 지문이 둘 이상입니다")
    automatic_release: AutomaticReleaseRecord | None = None
    if release_rows:
        release_values = tuple(release_rows[0])
        try:
            automatic_release = validate_persisted_automatic_release(
                report_sha256=release_values[0],
                pdf_sha256=release_values[1],
                checker_version=release_values[2],
                release_json=release_values[3],
                release_sha256=release_values[4],
                released_at=release_values[5],
            )
        except (TypeError, ValueError) as exc:
            raise QualityStoreError(f"{case_id} 자동출고 기록이 손상됐습니다") from exc
        if automatic_release.record_sha256 != release_sha256:
            raise QualityStoreError(f"{case_id} 자동출고 지문과 비용 요약이 다릅니다")
    if bool(release_sha256) is not bool(automatic_release):
        raise QualityStoreError(f"{case_id} 자동출고 지문과 실제 기록의 존재가 다릅니다")
    if report is not None:
        if len(report) != 3:
            raise QualityStoreError(f"{case_id} 보고서 원문 증거 열이 없습니다")
        try:
            report_payload = json.loads(str(report[2]))
            canonical_report = json.dumps(
                report_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise QualityStoreError(f"{case_id} 보고서 원문 JSON이 손상됐습니다") from exc
        report_payload_sha256 = _sha256_bytes(canonical_report)
        if (
            automatic_release is not None
            and automatic_release.report_sha256 != report_payload_sha256
        ):
            raise QualityStoreError(f"{case_id} 보고서 원문과 자동출고 지문이 다릅니다")
    expected_corp_code = str(getattr(manifest_case, "corp_code"))
    expected_legal_name = str(getattr(manifest_case, "expected_legal_name"))
    legal_entity_correct = bool(
        str(row.get("selected_corp_code", "")) == expected_corp_code
        and verified_official_company_names_equivalent(
            row.get("legal_name", ""),
            expected_legal_name,
            observed_corp_code=row.get("selected_corp_code", ""),
            expected_corp_code=expected_corp_code,
        )
        and (report is None or str(report[1]) == expected_corp_code)
    )
    error_code = str(row.get("error_code", "")).strip()
    completed = False
    release_observed = automatic_release is not None
    automatic_release_record_sha256 = (
        automatic_release.record_sha256 if automatic_release is not None else ""
    )
    automatic_report_sha256 = (
        automatic_release.report_sha256 if automatic_release is not None else ""
    )
    automatic_pdf_sha256 = (
        automatic_release.pdf_sha256 if automatic_release is not None else ""
    )
    if state == "completed":
        if summary is None or outcome not in _OUTCOME_CODES:
            raise QualityStoreError(f"{case_id} completed 원가·종료 요약이 없습니다")
        if outcome == _REPORT_OUTCOME:
            if (
                report is None
                or str(report[0]) != run_id
                or str(report[1]) != expected_corp_code
                or str(row.get("report_id", "")) != run_id
            ):
                raise QualityStoreError(f"{case_id} REPORT 법인·ID 결속이 다릅니다")
            if not error_code:
                if row.get("result_http_status") != 200 or automatic_release is None:
                    raise QualityStoreError(f"{case_id} REPORT 자동출고 증거가 없습니다")
                completed = True
            elif error_code == "automatic_release_blocked":
                if (
                    not _is_terminal_result_status(row.get("result_http_status"))
                    or automatic_release is not None
                    or release_sha256
                ):
                    raise QualityStoreError(f"{case_id} 출고차단인데 출고 지문이 있습니다")
            else:
                raise QualityStoreError(f"{case_id} REPORT 오류 코드 모양이 다릅니다")
        elif (
            error_code
            or not _is_terminal_result_status(row.get("result_http_status"))
            or report is not None
            or automatic_release is not None
            or release_sha256
            or str(row.get("report_id", ""))
        ):
            raise QualityStoreError(f"{case_id} 비REPORT 완료 증거 모양이 다릅니다")
    elif state in _NO_RUN_TERMINAL_STATES:
        allowed_identity_outcomes = {"", "IDENTITY_REF_UNVERIFIED", _REPORT_OUTCOME}
        if outcome not in allowed_identity_outcomes or not error_code:
            raise QualityStoreError(f"{case_id} 식별 terminal 종료 모양이 다릅니다")
        if outcome == _REPORT_OUTCOME:
            if (
                state != "identity_mismatch"
                or summary is None
                or report is None
                or str(report[0]) != run_id
                or str(row.get("report_id", ""))
                or error_code != "stored_report_identity_mismatch"
                or legal_entity_correct
                or str(report[1]) == expected_corp_code
            ):
                raise QualityStoreError(f"{case_id} 식별불일치 REPORT 증거가 다릅니다")
        elif (
            summary is not None
            or report is not None
            or automatic_release is not None
            or release_sha256
            or str(row.get("report_id", ""))
        ):
            raise QualityStoreError(f"{case_id} 실행 전 terminal에 보고서 증거가 있습니다")
        else:
            _validate_pre_run_identity_evidence(
                conn,
                case_id=case_id,
                row=row,
                expected_corp_code=expected_corp_code,
                expected_legal_name=expected_legal_name,
                final_record=final_record,
                lifecycle_confirmed_cost=lifecycle_confirmed_cost,
                checkpoint_cost=checkpoint_cost,
                spend_count=spend_count,
                inflight_count=inflight_count,
            )
    else:
        raise QualityStoreError(f"{case_id} 지원하지 않는 terminal 상태입니다")
    error_type = "" if completed else (
        error_code or _OUTCOME_CODES.get(outcome, "") or state
    )
    if error_type and _ERROR_TYPE_PATTERN.fullmatch(error_type) is None:
        raise QualityStoreError(f"{case_id} 오류 유형이 안전한 코드가 아닙니다")
    return AutomaticCaseEvidence(
        case_id=case_id,
        binding_id=binding_id,
        checkpoint_sha256=checkpoint_sha256,
        run_id=run_id,
        legal_entity_correct=legal_entity_correct,
        completed=completed,
        stopped=not completed,
        error_type=error_type,
        automatic_judgment="release" if completed else "stop",
        automatic_release_observed=release_observed,
        automatic_release_record_sha256=automatic_release_record_sha256,
        report_sha256=automatic_report_sha256,
        pdf_sha256=automatic_pdf_sha256,
        elapsed_sec=elapsed_sec,
        internal_ai_cost_krw=checkpoint_cost,
    )


def _validate_pre_run_identity_evidence(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    row: Mapping[str, object],
    expected_corp_code: str,
    expected_legal_name: str,
    final_record: object,
    lifecycle_confirmed_cost: float,
    checkpoint_cost: float,
    spend_count: int,
    inflight_count: int,
) -> None:
    """회사확인에서 끝난 0원 lifecycle과 checkpoint의 정확한 생산 모양을 검증한다."""

    state = str(row.get("state", ""))
    error_code = str(row.get("error_code", ""))
    outcome = str(row.get("outcome", ""))
    selected_corp_code = str(row.get("selected_corp_code", ""))
    legal_name = str(row.get("legal_name", ""))
    if state == "identity_ref_unverified":
        exact_checkpoint_shape = (
            error_code == "candidate_ref_not_observed"
            and outcome == "IDENTITY_REF_UNVERIFIED"
            and not selected_corp_code
            and not legal_name
        )
    elif state == "identity_mismatch" and error_code == "confirmed_corp_code_not_observed":
        exact_checkpoint_shape = (
            not outcome
            and selected_corp_code == expected_corp_code
            and not legal_name
        )
    elif state == "identity_mismatch" and error_code == "legal_name_mismatch":
        exact_checkpoint_shape = (
            not outcome
            and selected_corp_code == expected_corp_code
            and bool(legal_name)
            and not verified_official_company_names_equivalent(
                legal_name,
                expected_legal_name,
                observed_corp_code=selected_corp_code,
                expected_corp_code=expected_corp_code,
            )
        )
    else:
        exact_checkpoint_shape = False
    if not exact_checkpoint_shape or row.get("result_http_status") is not None:
        raise QualityStoreError(f"{case_id} 회사확인 종료 checkpoint 모양이 다릅니다")

    run_id = str(row.get("run_id", ""))
    if not _table_exists(conn, "observability_run_lifecycle_audit"):
        raise QualityStoreError(f"{case_id} 회사확인 lifecycle 감사표가 없습니다")
    audit_rows = conn.execute(
        "SELECT from_state, to_state FROM observability_run_lifecycle_audit "
        "WHERE run_id=? ORDER BY event_id",
        (run_id,),
    ).fetchall()
    ai_event_count = _run_row_count(conn, "ai_variable_cost_events", run_id)
    overrun_count = _run_row_count(conn, "budget_spend_overruns", run_id)
    zero_count_fields = (
        "fragments_collected",
        "fragments_cited",
        "sentences_made",
        "sentences_passed",
        "cells_filled",
    )
    exact_final_record = isinstance(final_record, dict) and (
        str(final_record.get("run_id", "")) == run_id
        and str(final_record.get("end_step", "")) == _END_STEP_CONFIRM
        and _is_exact_zero_cost(final_record.get("cost_krw"))
        and final_record.get("model") == ""
        and all(
            type(final_record.get(field)) is int and final_record.get(field) == 0
            for field in zero_count_fields
        )
    )
    if (
        not _is_exact_zero_cost(lifecycle_confirmed_cost)
        or not _is_exact_zero_cost(checkpoint_cost)
        or spend_count != 0
        or inflight_count != 0
        or ai_event_count != 0
        or overrun_count != 0
        or [(item[0], str(item[1])) for item in audit_rows]
        != [(None, "pending"), ("pending", "final")]
        or not exact_final_record
    ):
        raise QualityStoreError(f"{case_id} 회사확인 0원 lifecycle 증거가 다릅니다")


def _run_row_count(conn: sqlite3.Connection, table_name: str, run_id: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table_name} WHERE run_id=?", (run_id,)
    ).fetchone()
    return 0 if row is None else int(row[0])


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> frozenset[str]:
    return frozenset(str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})"))


class PilotQualityStore:
    """단일 봉인 P01~P10 증거에서 자동 필드를 파생하고 사람 판단만 받는다."""

    def __init__(self, checkpoint_path: Path, storage_db_path: Path) -> None:
        self.checkpoint_path = checkpoint_path.resolve()
        self.storage_db_path = storage_db_path.resolve()
        self.path = quality_path_for_checkpoint(self.checkpoint_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_fd: int | None = None

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise QualityStoreError(
                "다른 품질판정 기록 작업 또는 확인이 필요한 이전 lock이 있습니다"
            ) from exc
        try:
            os.write(self._lock_fd, str(os.getpid()).encode("ascii"))
            os.fsync(self._lock_fd)
            yield
        finally:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def record(
        self,
        *,
        case_id: str,
        user_judgment: str,
        wrong_legal_entity_released: bool,
        partial_report_released: bool,
        major_fact_citation_numeric_error_auto_passed: bool,
        replace: bool = False,
        now: str | None = None,
    ) -> Mapping[str, object]:
        """봉인 원장에서 자동값을 읽고 사람이 직접 본 네 값만 저장한다."""

        clean_case_id = case_id.strip().upper()
        if clean_case_id not in APPROVED_PAID_CASE_IDS:
            raise QualityStoreError("품질판정 저장은 P01~P10만 지원합니다")
        safety_flags = (
            wrong_legal_entity_released,
            partial_report_released,
            major_fact_citation_numeric_error_auto_passed,
        )
        if any(type(value) is not bool for value in safety_flags):
            raise QualityStoreError("품질판정의 안전사건 값은 boolean이어야 합니다")
        clean_user_judgment = _normalized_judgment(user_judgment)
        if clean_user_judgment not in QUALITY_JUDGMENTS:
            raise QualityStoreError("사용자판정은 release 또는 stop이어야 합니다")
        timestamp = now or utc_now_iso()
        _validated_timestamp(timestamp, field_name="reviewed_at")
        with self.exclusive():
            evidence = _read_bound_pilot_evidence(
                self.checkpoint_path, self.storage_db_path
            )
            automatic = evidence.cases[clean_case_id]
            if automatic.automatic_judgment == "stop" and clean_user_judgment == "release":
                raise QualityStoreError("자동 중단·미출고 결과를 release로 판정할 수 없습니다")
            if any(safety_flags) and not automatic.automatic_release_observed:
                raise QualityStoreError("미출고 결과에는 출고 안전사건을 기록할 수 없습니다")
            expected_wrong_release = bool(
                automatic.automatic_release_observed
                and not automatic.legal_entity_correct
            )
            if wrong_legal_entity_released is not expected_wrong_release:
                raise QualityStoreError("법인 불일치 자동출고 안전사건 판정이 증거와 다릅니다")
            row: dict[str, object] = {
                "case_id": clean_case_id,
                "source_binding_id": automatic.binding_id,
                "source_checkpoint_sha256": automatic.checkpoint_sha256,
                "source_run_id": automatic.run_id,
                "legal_entity_correct": automatic.legal_entity_correct,
                "completed": automatic.completed,
                "stopped": automatic.stopped,
                "error_type": automatic.error_type,
                "automatic_judgment": automatic.automatic_judgment,
                "automatic_release_observed": automatic.automatic_release_observed,
                "automatic_release_record_sha256": (
                    automatic.automatic_release_record_sha256
                ),
                "report_sha256": automatic.report_sha256,
                "pdf_sha256": automatic.pdf_sha256,
                "user_judgment": clean_user_judgment,
                "judgments_agree": automatic.automatic_judgment
                == clean_user_judgment,
                "elapsed_sec": automatic.elapsed_sec,
                "internal_ai_cost_krw": automatic.internal_ai_cost_krw,
                "wrong_legal_entity_released": wrong_legal_entity_released,
                "partial_report_released": partial_report_released,
                "major_fact_citation_numeric_error_auto_passed": (
                    major_fact_citation_numeric_error_auto_passed
                ),
                "reviewed_at": timestamp,
            }
            snapshot = self._load_or_create(evidence=evidence, now=timestamp)
            rows = snapshot["cases"]
            assert isinstance(rows, dict)
            if clean_case_id in rows and not replace:
                raise QualityStoreError(
                    f"{clean_case_id} 품질판정이 이미 있습니다. 정정이면 replace를 명시하세요"
                )
            rows[clean_case_id] = row
            snapshot["updated_at"] = timestamp
            self._validate_snapshot(snapshot, evidence=evidence)
            self._write(snapshot)
        return row

    def aggregate(self) -> QualityAggregate:
        """같은 봉인 배치의 10건 모두 사람이 판정된 뒤에만 집계한다."""

        with self.exclusive():
            evidence = _read_bound_pilot_evidence(
                self.checkpoint_path, self.storage_db_path
            )
            if not self.path.exists():
                return QualityAggregate(False, (), QUALITY_CASE_IDS, None)
            snapshot = self._load(evidence=evidence)
        rows = snapshot["cases"]
        assert isinstance(rows, dict)
        recorded = tuple(case_id for case_id in QUALITY_CASE_IDS if case_id in rows)
        missing = tuple(case_id for case_id in QUALITY_CASE_IDS if case_id not in rows)
        if missing:
            return QualityAggregate(False, recorded, missing, None)
        cases_by_id = {
            case.case_id: case
            for case in CANONICAL_PILOT_CASES
            if case.case_id in APPROVED_PAID_CASE_IDS
        }
        results: list[PilotResult] = []
        for case_id in QUALITY_CASE_IDS:
            manifest_case = cases_by_id[case_id]
            row = rows[case_id]
            assert isinstance(row, dict)
            results.append(
                PilotResult(
                    case=PilotCase(
                        case_id=case_id,
                        category=manifest_case.category,
                        company_name=manifest_case.input_name,
                    ),
                    legal_entity_correct=bool(row["legal_entity_correct"]),
                    completed=bool(row["completed"]),
                    stopped=bool(row["stopped"]),
                    error_type=str(row["error_type"]),
                    automatic_judgment=str(row["automatic_judgment"]),
                    user_judgment=str(row["user_judgment"]),
                    judgments_agree=bool(row["judgments_agree"]),
                    elapsed_sec=float(row["elapsed_sec"]),
                    internal_ai_cost_krw=float(row["internal_ai_cost_krw"]),
                    wrong_legal_entity_released=bool(
                        row["wrong_legal_entity_released"]
                    ),
                    partial_report_released=bool(row["partial_report_released"]),
                    major_fact_citation_numeric_error_auto_passed=bool(
                        row["major_fact_citation_numeric_error_auto_passed"]
                    ),
                )
            )
        return QualityAggregate(True, recorded, (), evaluate_pilot(results))

    def _load_or_create(
        self, *, evidence: BoundPilotEvidence, now: str
    ) -> dict[str, object]:
        if self.path.exists():
            return self._load(evidence=evidence)
        return {
            "schema_version": SCHEMA_VERSION,
            "manifest_sha256": manifest_sha256(CANONICAL_PILOT_CASES),
            "approved_case_ids": list(QUALITY_CASE_IDS),
            "source_binding_id": evidence.binding_id,
            "source_checkpoint_sha256": evidence.checkpoint_sha256,
            "source_storage_binding_sha256": evidence.storage_binding_sha256,
            "created_at": now,
            "updated_at": now,
            "cases": {},
        }

    def _load(self, *, evidence: BoundPilotEvidence) -> dict[str, object]:
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise QualityStoreError("품질판정 JSON을 안전하게 읽지 못했습니다") from exc
        self._validate_snapshot(snapshot, evidence=evidence)
        return snapshot

    @staticmethod
    def _validate_snapshot(
        snapshot: object, *, evidence: BoundPilotEvidence
    ) -> None:
        if not isinstance(snapshot, dict) or set(snapshot) != _TOP_LEVEL_FIELDS:
            raise QualityStoreError("품질판정 JSON 최상위 필드가 허용 계약과 다릅니다")
        if snapshot.get("schema_version") != SCHEMA_VERSION:
            raise QualityStoreError("지원하지 않는 품질판정 JSON 형식입니다")
        if snapshot.get("manifest_sha256") != manifest_sha256(CANONICAL_PILOT_CASES):
            raise QualityStoreError("품질판정 JSON의 manifest가 현재 정본과 다릅니다")
        if snapshot.get("approved_case_ids") != list(QUALITY_CASE_IDS):
            raise QualityStoreError("품질판정 JSON의 승인 case 범위가 다릅니다")
        source_values = (
            snapshot.get("source_binding_id"),
            snapshot.get("source_checkpoint_sha256"),
            snapshot.get("source_storage_binding_sha256"),
        )
        if source_values != (
            evidence.binding_id,
            evidence.checkpoint_sha256,
            evidence.storage_binding_sha256,
        ):
            raise QualityStoreError("품질판정 JSON이 다른 파일럿 배치 증거와 섞였습니다")
        created_at = _validated_timestamp(snapshot.get("created_at"), field_name="created_at")
        updated_at = _validated_timestamp(snapshot.get("updated_at"), field_name="updated_at")
        if updated_at < created_at:
            raise QualityStoreError("품질판정 JSON의 갱신 시각이 생성 시각보다 이릅니다")
        rows = snapshot.get("cases")
        if not isinstance(rows, dict):
            raise QualityStoreError("품질판정 JSON의 case 모양이 올바르지 않습니다")
        for case_id, row in rows.items():
            if case_id not in APPROVED_PAID_CASE_IDS:
                raise QualityStoreError("품질판정 JSON에 P01~P10 밖의 case가 있습니다")
            if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
                raise QualityStoreError(f"{case_id} 품질판정 필드가 허용 계약과 다릅니다")
            automatic = evidence.cases[case_id]
            expected_automatic = {
                "case_id": case_id,
                "source_binding_id": automatic.binding_id,
                "source_checkpoint_sha256": automatic.checkpoint_sha256,
                "source_run_id": automatic.run_id,
                "legal_entity_correct": automatic.legal_entity_correct,
                "completed": automatic.completed,
                "stopped": automatic.stopped,
                "error_type": automatic.error_type,
                "automatic_judgment": automatic.automatic_judgment,
                "automatic_release_observed": automatic.automatic_release_observed,
                "automatic_release_record_sha256": (
                    automatic.automatic_release_record_sha256
                ),
                "report_sha256": automatic.report_sha256,
                "pdf_sha256": automatic.pdf_sha256,
                "elapsed_sec": automatic.elapsed_sec,
                "internal_ai_cost_krw": automatic.internal_ai_cost_krw,
            }
            if any(row.get(key) != value for key, value in expected_automatic.items()):
                raise QualityStoreError(f"{case_id} 자동판정이 봉인 실행 증거와 다릅니다")
            if any(type(row[field]) is not bool for field in _BOOLEAN_ROW_FIELDS):
                raise QualityStoreError(f"{case_id} 품질판정 boolean 필드가 올바르지 않습니다")
            reviewed_at = _validated_timestamp(
                row.get("reviewed_at"), field_name=f"{case_id}.reviewed_at"
            )
            if reviewed_at < created_at or reviewed_at > updated_at:
                raise QualityStoreError(f"{case_id} 품질판정 시각이 파일 범위를 벗어납니다")
            user_judgment = row.get("user_judgment")
            if not isinstance(user_judgment, str) or user_judgment not in QUALITY_JUDGMENTS:
                raise QualityStoreError(f"{case_id} 사용자판정이 올바르지 않습니다")
            if row.get("judgments_agree") is not (
                automatic.automatic_judgment == user_judgment
            ):
                raise QualityStoreError(f"{case_id} 자동·사용자 판정 일치값이 다릅니다")
            if automatic.automatic_judgment == "stop" and user_judgment == "release":
                raise QualityStoreError(f"{case_id} 자동 중단을 release로 바꿀 수 없습니다")
            safety_flags = (
                row.get("wrong_legal_entity_released"),
                row.get("partial_report_released"),
                row.get("major_fact_citation_numeric_error_auto_passed"),
            )
            if any(safety_flags) and not automatic.automatic_release_observed:
                raise QualityStoreError(f"{case_id} 미출고 결과에 출고 안전사건이 있습니다")
            expected_wrong_release = bool(
                automatic.automatic_release_observed
                and not automatic.legal_entity_correct
            )
            if row.get("wrong_legal_entity_released") is not expected_wrong_release:
                raise QualityStoreError(f"{case_id} 법인 불일치 출고 판정이 증거와 다릅니다")

    def _write(self, snapshot: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    snapshot,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise


def aggregate_as_dict(aggregate: QualityAggregate) -> dict[str, object]:
    """CLI가 내부 dataclass를 안정적인 JSON으로 출력하도록 변환한다."""

    return {
        "ready": aggregate.ready,
        "recorded_case_ids": list(aggregate.recorded_case_ids),
        "missing_case_ids": list(aggregate.missing_case_ids),
        "summary": asdict(aggregate.summary) if aggregate.summary is not None else None,
    }
