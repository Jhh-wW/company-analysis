from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from src.features.pilot_evaluation.checkpoint import SCHEMA_VERSION
from src.features.pilot_evaluation.manifest import (
    CANONICAL_PILOT_CASES,
    manifest_sha256,
)
from src.features.pilot_evaluation.quality_store import (
    PilotQualityStore,
    QUALITY_CASE_IDS,
    QUALITY_FILENAME,
    QualityStoreError,
    quality_path_for_checkpoint,
)
from src.features.pilot_evaluation.runner import (
    PILOT_BINDING_KEY,
    PILOT_BINDING_SCHEMA_VERSION,
    PILOT_BINDING_TABLE,
)
from src.features.pipeline.port import Outcome
from src.shared.automatic_release_record import (
    AUTOMATIC_CHECKER_VERSION,
    REQUIRED_AUTOMATIC_CHECKS,
    AutomaticCheckResult,
    AutomaticReleaseRecord,
    automatic_release_json,
    automatic_release_record_sha256,
)
from tools.manage_pilot_quality import main as quality_cli_main


AT = "2026-08-22T00:00:00+00:00"
ORIGIN = "http://127.0.0.1:8020"
BINDING_ID = "a" * 32
SERVER_SHA256 = "b" * 64


def _automatic_release_values(
    report_payload_json: str,
) -> tuple[str, str, str, str, str, str]:
    checks = tuple(
        AutomaticCheckResult(name, True, "c" * 64)
        for name in REQUIRED_AUTOMATIC_CHECKS
    )
    report_payload = json.loads(report_payload_json)
    report_sha256 = hashlib.sha256(
        json.dumps(
            report_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "checker_version": AUTOMATIC_CHECKER_VERSION,
        "report_sha256": report_sha256,
        "pdf_sha256": "e" * 64,
        "page_count": 1,
        "page_png_sha256s": ("f" * 64,),
        "expected_fact_ids": ("F-1",),
        "checks": checks,
        "released_at": AT,
    }
    unsigned = AutomaticReleaseRecord(**payload, record_sha256="")
    record = AutomaticReleaseRecord(
        **payload,
        record_sha256=automatic_release_record_sha256(unsigned),
    )
    return (
        record.report_sha256,
        record.pdf_sha256,
        record.checker_version,
        automatic_release_json(record),
        record.record_sha256,
        record.released_at,
    )


def _path_sha256(path: Path) -> str:
    return hashlib.sha256(
        str(path.resolve()).casefold().encode("utf-8")
    ).hexdigest()


def _write_checkpoint(
    checkpoint: Path,
    storage_db: Path,
    *,
    stopped: frozenset[str] = frozenset(),
    binding_id: str = BINDING_ID,
) -> dict[str, object]:
    cases: dict[str, dict[str, object]] = {}
    for index, case in enumerate(CANONICAL_PILOT_CASES, start=1):
        if case.case_id in QUALITY_CASE_IDS:
            run_id = f"{index:032x}"
            is_stopped = case.case_id in stopped
            cases[case.case_id] = {
                "case_id": case.case_id,
                "state": "completed",
                "run_id": run_id,
                "report_id": "" if is_stopped else run_id,
                "outcome": (
                    Outcome.GATE_STOPPED.value
                    if is_stopped
                    else Outcome.REPORT.value
                ),
                "internal_ai_cost_krw": 250.0,
                "billing_uncertain": False,
                "selected_corp_code": case.corp_code,
                "legal_name": case.expected_legal_name,
                "paid_boundary_at": AT,
                "result_http_status": 200,
                "error_code": "",
                "updated_at": AT,
            }
        else:
            cases[case.case_id] = {
                "case_id": case.case_id,
                "state": "pending",
                "run_id": "",
                "report_id": "",
                "outcome": "",
                "internal_ai_cost_krw": None,
                "billing_uncertain": False,
                "selected_corp_code": "",
                "legal_name": "",
                "paid_boundary_at": "",
                "result_http_status": None,
                "error_code": "",
                "updated_at": AT,
            }
    snapshot: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "binding_id": binding_id,
        "manifest_sha256": manifest_sha256(CANONICAL_PILOT_CASES),
        "origin": ORIGIN,
        "server_instance_sha256": SERVER_SHA256,
        "data_path_sha256": _path_sha256(storage_db),
        "created_at": AT,
        "updated_at": AT,
        "cases": cases,
    }
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot


def _checkpoint_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_storage(
    checkpoint: Path,
    storage_db: Path,
    snapshot: dict[str, object],
    *,
    stopped: frozenset[str] = frozenset(),
) -> None:
    with sqlite3.connect(storage_db) as conn:
        conn.executescript(
            f"""
            CREATE TABLE {PILOT_BINDING_TABLE} (
                pilot_key TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                binding_id TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,
                origin TEXT NOT NULL,
                server_instance_sha256 TEXT NOT NULL,
                data_path_sha256 TEXT NOT NULL,
                checkpoint_path_sha256 TEXT NOT NULL,
                checkpoint_content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE observability_run_lifecycle (
                run_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                confirmed_cost_krw REAL NOT NULL,
                final_record_json TEXT
            );
            CREATE TABLE observability_run_lifecycle_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL
            );
            CREATE TABLE budget_spend_events (run_id TEXT, cost_krw REAL);
            CREATE TABLE budget_spend_inflight (run_id TEXT);
            CREATE TABLE reports (
                report_id TEXT PRIMARY KEY,
                corp_id TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE report_cost_summaries (
                run_id TEXT PRIMARY KEY,
                outcome TEXT,
                internal_ai_cost_krw REAL,
                automatic_release_sha256 TEXT
            );
            CREATE TABLE pdf_automatic_release_records (
                report_id TEXT NOT NULL,
                report_sha256 TEXT NOT NULL,
                pdf_sha256 TEXT NOT NULL,
                checker_version TEXT NOT NULL,
                release_json TEXT NOT NULL,
                release_sha256 TEXT NOT NULL,
                released_at TEXT NOT NULL,
                PRIMARY KEY (report_id, report_sha256, pdf_sha256, checker_version)
            );
            """
        )
        conn.execute(
            f"INSERT INTO {PILOT_BINDING_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                PILOT_BINDING_KEY,
                PILOT_BINDING_SCHEMA_VERSION,
                snapshot["binding_id"],
                snapshot["manifest_sha256"],
                snapshot["origin"],
                snapshot["server_instance_sha256"],
                snapshot["data_path_sha256"],
                _path_sha256(checkpoint),
                _checkpoint_sha256(checkpoint),
                AT,
            ),
        )
        rows = snapshot["cases"]
        assert isinstance(rows, dict)
        for case in CANONICAL_PILOT_CASES[:10]:
            row = rows[case.case_id]
            assert isinstance(row, dict)
            run_id = str(row["run_id"])
            final_record = json.dumps(
                {
                    "run_id": run_id,
                    "cost_krw": 250.0,
                    "elapsed_sec": 1200.0,
                    "end_step": "05_생성",
                    "model": "test-model",
                    "fragments_collected": 1,
                    "fragments_cited": 1,
                    "sentences_made": 1,
                    "sentences_passed": 1,
                    "cells_filled": 1,
                }
            )
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'final', 10.0, ?)",
                (run_id, final_record),
            )
            conn.execute(
                "INSERT INTO observability_run_lifecycle_audit "
                "(run_id, from_state, to_state) VALUES (?, NULL, 'pending')",
                (run_id,),
            )
            conn.execute(
                "INSERT INTO observability_run_lifecycle_audit "
                "(run_id, from_state, to_state) VALUES (?, 'pending', 'final')",
                (run_id,),
            )
            conn.execute(
                "INSERT INTO budget_spend_events VALUES (?, 250.0)",
                (run_id,),
            )
            outcome = str(row["outcome"])
            report_payload_json = json.dumps(
                {"case_id": case.case_id, "company": case.expected_legal_name},
                ensure_ascii=False,
            )
            release_values = _automatic_release_values(report_payload_json)
            release_sha256 = "" if case.case_id in stopped else release_values[4]
            conn.execute(
                "INSERT INTO report_cost_summaries VALUES (?, ?, 250.0, ?)",
                (run_id, outcome, release_sha256),
            )
            if case.case_id not in stopped:
                conn.execute(
                    "INSERT INTO reports VALUES (?, ?, ?)",
                    (run_id, case.corp_code, report_payload_json),
                )
                conn.execute(
                    "INSERT INTO pdf_automatic_release_records VALUES "
                    "(?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        *release_values,
                    ),
                )


def _evidence(
    tmp_path: Path,
    *,
    stopped: frozenset[str] = frozenset(),
    binding_id: str = BINDING_ID,
) -> tuple[Path, Path]:
    checkpoint = tmp_path / "canonical-pilot25-checkpoint.json"
    storage_db = tmp_path / "storage.db"
    snapshot = _write_checkpoint(
        checkpoint,
        storage_db,
        stopped=stopped,
        binding_id=binding_id,
    )
    _create_storage(
        checkpoint,
        storage_db,
        snapshot,
        stopped=stopped,
    )
    return checkpoint, storage_db


def _reseal(checkpoint: Path, storage_db: Path) -> None:
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            f"UPDATE {PILOT_BINDING_TABLE} SET checkpoint_content_sha256=? "
            "WHERE pilot_key=?",
            (_checkpoint_sha256(checkpoint), PILOT_BINDING_KEY),
        )


def _record(
    store: PilotQualityStore,
    case_id: str,
    *,
    user_judgment: str = "release",
) -> None:
    store.record(
        case_id=case_id,
        user_judgment=user_judgment,
        wrong_legal_entity_released=False,
        partial_report_released=False,
        major_fact_citation_numeric_error_auto_passed=False,
        now=AT,
    )


def test_빈_JSON은_checkpoint로_받지않는다(tmp_path: Path) -> None:
    checkpoint = tmp_path / "canonical-pilot25-checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")

    with pytest.raises(QualityStoreError, match="최상위"):
        quality_path_for_checkpoint(checkpoint)


def test_자동결과와_원가는_봉인된_checkpoint와_DB에서만_파생한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    db_before = _checkpoint_sha256(storage_db)
    store = PilotQualityStore(checkpoint, storage_db)

    _record(store, "P01")

    snapshot = json.loads(store.path.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P01"]
    assert store.path == tmp_path / QUALITY_FILENAME
    assert row["source_run_id"] == f"{1:032x}"
    assert row["legal_entity_correct"] is True
    assert row["completed"] is True
    assert row["automatic_judgment"] == "release"
    assert row["elapsed_sec"] == 1200.0
    assert row["internal_ai_cost_krw"] == 250.0
    assert _checkpoint_sha256(storage_db) == db_before


def test_P01부터_P10_전부_terminal이_아니면_첫기록부터_거부한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    snapshot["cases"]["P10"]["state"] = "pending"
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="P01~P10 전부"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_P11흔적이_있으면_승인범위밖_provider_0을_증명하지못해_거부한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    snapshot["cases"]["P11"]["run_id"] = "e" * 32
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="P11~P25"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_P11갱신시각이_초기값과_다르면_provider_0을_증명하지못한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    snapshot["updated_at"] = "2026-08-22T00:01:00+00:00"
    snapshot["cases"]["P11"]["updated_at"] = snapshot["updated_at"]
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="P11~P25.*갱신 시각"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_승인case의_유료경계시각이_배치범위밖이면_거부한다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    snapshot["cases"]["P01"]["paid_boundary_at"] = "2026-08-21T23:59:59+00:00"
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="유료 경계 시각"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_checkpoint_내용변조와_다른경로_DB를_거부한다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    snapshot["cases"]["P01"]["internal_ai_cost_krw"] = 1.0
    checkpoint.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(QualityStoreError, match="binding"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    copied_db = other_dir / "storage.db"
    copied_db.write_bytes(storage_db.read_bytes())
    with pytest.raises(QualityStoreError, match="경로 결속"):
        PilotQualityStore(checkpoint, copied_db).aggregate()


def test_DB_비용변조는_이미만든_품질파일의_집계도_막는다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P01")
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            "UPDATE budget_spend_events SET cost_krw=249 WHERE run_id=?",
            (f"{1:032x}",),
        )

    with pytest.raises(QualityStoreError, match="비용 원장"):
        store.aggregate()


def test_임의64hex가_아닌_실제_자동출고_JSON과_지문을_검증한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            "UPDATE pdf_automatic_release_records SET release_json='{}' "
            "WHERE report_id=?",
            (f"{1:032x}",),
        )

    with pytest.raises(QualityStoreError, match="자동출고 기록이 손상"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_원가요약의_출고지문과_실제출고행_존재가_다르면_거부한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            "DELETE FROM pdf_automatic_release_records WHERE report_id=?",
            (f"{1:032x}",),
        )

    with pytest.raises(QualityStoreError, match="실제 기록의 존재"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_출고뒤_보고서원문이_바뀌면_품질완료로_인정하지않는다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            "UPDATE reports SET payload_json=? WHERE report_id=?",
            (json.dumps({"tampered": True}), f"{1:032x}"),
        )

    with pytest.raises(QualityStoreError, match="보고서 원문과 자동출고 지문"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_사람판정뒤_보고서와_출고행을_새유효조합으로_바꿔도_집계를_거부한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P01")
    report_id = f"{1:032x}"
    replacement_payload = json.dumps(
        {"case_id": "P01", "company": "교체된 보고서"}, ensure_ascii=False
    )
    replacement_release = _automatic_release_values(replacement_payload)
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            "UPDATE reports SET payload_json=? WHERE report_id=?",
            (replacement_payload, report_id),
        )
        conn.execute(
            "UPDATE pdf_automatic_release_records SET "
            "report_sha256=?, pdf_sha256=?, checker_version=?, release_json=?, "
            "release_sha256=?, released_at=? WHERE report_id=?",
            (*replacement_release, report_id),
        )
        conn.execute(
            "UPDATE report_cost_summaries SET automatic_release_sha256=? WHERE run_id=?",
            (replacement_release[4], report_id),
        )

    with pytest.raises(QualityStoreError, match="자동판정이 봉인 실행 증거와 다릅니다"):
        store.aggregate()


@pytest.mark.parametrize(
    ("field_name", "value"),
    (("error_code", "data_shortage"), ("result_http_status", None)),
)
def test_completed_비REPORT는_러너의_exact_종료모양만_허용한다(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path, stopped=frozenset({"P01"}))
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    snapshot["cases"]["P01"][field_name] = value
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="비REPORT 완료 증거 모양"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01")


def test_identity_mismatch인데_실제자동출고가_있으면_stop으로_숨기지않고_안전사건을_강제한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P01"]
    row["state"] = "identity_mismatch"
    row["report_id"] = ""
    row["error_code"] = "stored_report_identity_mismatch"
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with sqlite3.connect(storage_db) as conn:
        conn.execute(
            "UPDATE reports SET corp_id='99999999' WHERE report_id=?",
            (f"{1:032x}",),
        )
    _reseal(checkpoint, storage_db)
    store = PilotQualityStore(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="법인 불일치"):
        _record(store, "P01", user_judgment="stop")
    stored = store.record(
        case_id="P01",
        user_judgment="stop",
        wrong_legal_entity_released=True,
        partial_report_released=False,
        major_fact_citation_numeric_error_auto_passed=False,
        now=AT,
    )
    assert stored["legal_entity_correct"] is False
    assert stored["automatic_judgment"] == "stop"
    assert stored["automatic_release_observed"] is True


def test_identity_mismatch_REPORT는_저장보고서_corp가_실제로_달라야한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P01"]
    row["state"] = "identity_mismatch"
    row["report_id"] = ""
    row["selected_corp_code"] = "99999999"
    row["error_code"] = "stored_report_identity_mismatch"
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="식별불일치 REPORT"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01", user_judgment="stop")


def test_no_run_identity_terminal은_0원_미출고_stop증거로만_기록한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P02"]
    run_id = row["run_id"]
    row.update(
        state="identity_ref_unverified",
        run_id="",
        report_id="",
        outcome="IDENTITY_REF_UNVERIFIED",
        internal_ai_cost_krw=None,
        selected_corp_code="",
        legal_name="",
        result_http_status=None,
        error_code="candidate_ref_not_observed",
    )
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with sqlite3.connect(storage_db) as conn:
        for table in (
            "observability_run_lifecycle",
            "budget_spend_events",
            "reports",
            "report_cost_summaries",
            "pdf_automatic_release_records",
        ):
            key = "report_id" if table in {"reports", "pdf_automatic_release_records"} else "run_id"
            conn.execute(f"DELETE FROM {table} WHERE {key}=?", (run_id,))
    _reseal(checkpoint, storage_db)

    stored = PilotQualityStore(checkpoint, storage_db).record(
        case_id="P02",
        user_judgment="stop",
        wrong_legal_entity_released=False,
        partial_report_released=False,
        major_fact_citation_numeric_error_auto_passed=False,
        now=AT,
    )
    assert stored["source_run_id"] == ""
    assert stored["internal_ai_cost_krw"] == 0.0
    assert stored["automatic_release_observed"] is False


def test_no_run_identity_terminal의_상태별_오류코드는_exact여야한다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P01"]
    row.update(
        state="identity_mismatch",
        run_id="",
        report_id="",
        outcome="",
        internal_ai_cost_krw=None,
        selected_corp_code="",
        legal_name="",
        result_http_status=None,
        error_code="arbitrary_identity_error",
    )
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="실행 전 terminal 증거 모양"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01", user_judgment="stop")


def test_전부_GATE_STOPPED면_자동출고_표가_없어도_미출고로_검증한다(
    tmp_path: Path,
) -> None:
    stopped = frozenset(QUALITY_CASE_IDS)
    checkpoint, storage_db = _evidence(tmp_path, stopped=stopped)
    with sqlite3.connect(storage_db) as conn:
        conn.execute("DROP TABLE pdf_automatic_release_records")

    stored = PilotQualityStore(checkpoint, storage_db).record(
        case_id="P01",
        user_judgment="stop",
        wrong_legal_entity_released=False,
        partial_report_released=False,
        major_fact_citation_numeric_error_auto_passed=False,
        now=AT,
    )
    assert stored["completed"] is False
    assert stored["automatic_release_observed"] is False


def test_전부_run_id있는_식별terminal이면_보고서원가와_출고표가_없어도_검증한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    for case_id in QUALITY_CASE_IDS:
        row = snapshot["cases"][case_id]
        row.update(
            state="identity_ref_unverified",
            report_id="",
            outcome="IDENTITY_REF_UNVERIFIED",
            internal_ai_cost_krw=0.0,
            selected_corp_code="",
            legal_name="",
            result_http_status=None,
            error_code="candidate_ref_not_observed",
        )
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with sqlite3.connect(storage_db) as conn:
        conn.execute("DELETE FROM budget_spend_events")
        conn.execute("UPDATE observability_run_lifecycle SET confirmed_cost_krw=0")
        lifecycle_rows = conn.execute(
            "SELECT run_id, final_record_json FROM observability_run_lifecycle"
        ).fetchall()
        for run_id, raw in lifecycle_rows:
            record = json.loads(raw)
            record["cost_krw"] = 0.0
            record["end_step"] = "03_확인"
            record["model"] = ""
            for field in (
                "fragments_collected",
                "fragments_cited",
                "sentences_made",
                "sentences_passed",
                "cells_filled",
            ):
                record[field] = 0
            conn.execute(
                "UPDATE observability_run_lifecycle SET final_record_json=? WHERE run_id=?",
                (json.dumps(record), run_id),
            )
        conn.execute("DELETE FROM reports")
        conn.execute("DROP TABLE report_cost_summaries")
        conn.execute("DROP TABLE pdf_automatic_release_records")
    _reseal(checkpoint, storage_db)

    stored = PilotQualityStore(checkpoint, storage_db).record(
        case_id="P01",
        user_judgment="stop",
        wrong_legal_entity_released=False,
        partial_report_released=False,
        major_fact_citation_numeric_error_auto_passed=False,
        now=AT,
    )
    assert stored["source_run_id"]
    assert stored["error_type"] == "candidate_ref_not_observed"


def test_run_id있는_식별terminal은_0원_spend행조차_허용하지않는다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    snapshot = json.loads(checkpoint.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P01"]
    run_id = str(row["run_id"])
    row.update(
        state="identity_ref_unverified",
        report_id="",
        outcome="IDENTITY_REF_UNVERIFIED",
        internal_ai_cost_krw=0.0,
        selected_corp_code="",
        legal_name="",
        result_http_status=None,
        error_code="candidate_ref_not_observed",
    )
    checkpoint.write_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    with sqlite3.connect(storage_db) as conn:
        final_record = {
            "run_id": run_id,
            "cost_krw": 0.0,
            "elapsed_sec": 1200.0,
            "end_step": "03_확인",
            "model": "",
            "fragments_collected": 0,
            "fragments_cited": 0,
            "sentences_made": 0,
            "sentences_passed": 0,
            "cells_filled": 0,
        }
        conn.execute(
            "UPDATE observability_run_lifecycle SET confirmed_cost_krw=0, "
            "final_record_json=? WHERE run_id=?",
            (json.dumps(final_record), run_id),
        )
        conn.execute("UPDATE budget_spend_events SET cost_krw=0 WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM reports WHERE report_id=?", (run_id,))
        conn.execute("DELETE FROM report_cost_summaries WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM pdf_automatic_release_records WHERE report_id=?", (run_id,))
    _reseal(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="0원 lifecycle"):
        _record(PilotQualityStore(checkpoint, storage_db), "P01", user_judgment="stop")


def test_다른_binding의_품질행은_같은파일에서_합산하지않는다(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    checkpoint1, db1 = _evidence(first_dir, binding_id="a" * 32)
    checkpoint2, db2 = _evidence(second_dir, binding_id="c" * 32)
    store1 = PilotQualityStore(checkpoint1, db1)
    store2 = PilotQualityStore(checkpoint2, db2)
    _record(store1, "P01")
    store2.path.write_bytes(store1.path.read_bytes())

    with pytest.raises(QualityStoreError, match="다른 파일럿 배치"):
        store2.aggregate()


def test_자동중단을_사람release로_바꾸거나_미출고안전사건을_만들수없다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path, stopped=frozenset({"P10"}))
    store = PilotQualityStore(checkpoint, storage_db)

    with pytest.raises(QualityStoreError, match="release"):
        _record(store, "P10", user_judgment="release")
    with pytest.raises(QualityStoreError, match="미출고"):
        store.record(
            case_id="P10",
            user_judgment="stop",
            wrong_legal_entity_released=True,
            partial_report_released=False,
            major_fact_citation_numeric_error_auto_passed=False,
            now=AT,
        )


def test_품질JSON_사후변조로_자동중단을_release로_바꿀수없다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path, stopped=frozenset({"P10"}))
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P10", user_judgment="stop")
    snapshot = json.loads(store.path.read_text(encoding="utf-8"))
    snapshot["cases"]["P10"]["user_judgment"] = "release"
    snapshot["cases"]["P10"]["judgments_agree"] = False
    store.path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(QualityStoreError, match="자동 중단"):
        store.aggregate()


def test_10건_사람판정뒤에만_고정계약을_집계한다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P01")
    assert store.aggregate().ready is False
    for case_id in QUALITY_CASE_IDS[1:]:
        _record(store, case_id)

    aggregate = store.aggregate()

    assert aggregate.ready is True
    assert aggregate.summary is not None
    assert aggregate.summary.passed is True
    assert aggregate.summary.case_count == 10


def test_같은_case를_실수로_덮어쓰지않고_같은source만_정정한다(
    tmp_path: Path,
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P01")
    with pytest.raises(QualityStoreError, match="이미"):
        _record(store, "P01")

    store.record(
        case_id="P01",
        user_judgment="stop",
        wrong_legal_entity_released=False,
        partial_report_released=False,
        major_fact_citation_numeric_error_auto_passed=False,
        replace=True,
        now="2026-08-22T00:01:00+00:00",
    )
    assert store.aggregate().recorded_case_ids == ("P01",)


def test_과거시각_추가기록은_기존품질파일을_덮어쓰기전에_거부한다(tmp_path: Path) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P01")
    original = store.path.read_bytes()

    with pytest.raises(QualityStoreError, match="갱신 시각|파일 범위"):
        store.record(
            case_id="P02",
            user_judgment="release",
            wrong_legal_entity_released=False,
            partial_report_released=False,
            major_fact_citation_numeric_error_auto_passed=False,
            now="2026-08-21T23:59:59+00:00",
        )

    assert store.path.read_bytes() == original


def test_원자교체실패때_기존판정과_lock을_보존정리한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    store = PilotQualityStore(checkpoint, storage_db)
    _record(store, "P01")
    original = store.path.read_bytes()

    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("교체 실패")))
    with pytest.raises(OSError, match="교체 실패"):
        _record(store, "P02")

    assert store.path.read_bytes() == original
    assert not store.lock_path.exists()


def test_CLI는_storage결속을_요구하고_자동필드_override를_받지않는다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkpoint, storage_db = _evidence(tmp_path)
    base = [
        "--checkpoint",
        str(checkpoint),
        "--storage-db",
        str(storage_db),
        "record",
        "--case-id",
        "P01",
        "--user-judgment",
        "release",
        "--wrong-legal-entity-released",
        "no",
        "--partial-report-released",
        "no",
        "--major-fact-citation-numeric-error-auto-passed",
        "no",
    ]
    assert quality_cli_main(base) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["저장파일"] == str(tmp_path / QUALITY_FILENAME)

    with pytest.raises(SystemExit):
        quality_cli_main([*base, "--internal-ai-cost-krw", "0"])
