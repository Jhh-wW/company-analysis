from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from src.features.final_gate_diagnostic import store as final_gate_store
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.spanselect import diagnostic_store as span_store
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.shared.span_selection_diagnostics import SpanSelectionRoundDiagnostic
from src.web import job_runtime, recording, runtime


RUN_ID = "abcdef0123456789abcdef0123456789"
CORP_CODE = "00126380"
CONFIRMED_COMPANY = "가나다전자"


def _round() -> SpanSelectionRoundDiagnostic:
    return SpanSelectionRoundDiagnostic(
        round_number=1,
        requested_max_tokens=6000,
        output_tokens=1200,
        provider_stop_reason="end_turn",
        output_limit_reached=False,
        parse_failed=False,
        provider_selected=3,
        validation_kept=3,
        validation_rejected=0,
        empty_reason="validated_items_kept",
    )


def _gate_result(*, reason: str = "publish_blocked") -> RunResult:
    return RunResult(
        outcome=Outcome.GATE_STOPPED,
        span_selection_diagnostics=(_round(),),
        span_selection_result_reason="majority_kept",
        final_gate_reason=reason,
    )


def _configure_paths(tmp_path, monkeypatch) -> Path:
    db_path = tmp_path / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv("OBSERVABILITY_RECORDS_PATH", str(tmp_path / "runs.jsonl"))
    return db_path


def test_최종게이트와_기존원장이_같은실행시각으로_함께_남는다(
    tmp_path, monkeypatch
) -> None:
    db_path = _configure_paths(tmp_path, monkeypatch)

    assert recording.record_run(
        UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        _gate_result(),
        1.0,
        run_id=RUN_ID,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
    )

    with sqlite3.connect(db_path) as conn:
        final_record = lifecycle.read_final(conn, RUN_ID)
        span_diagnostic = span_store.read_for_run(conn, RUN_ID)
        gate_diagnostic = final_gate_store.read_for_run(conn, RUN_ID)
        columns = {
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({final_gate_store.TABLE_FINAL_GATE_DIAGNOSTICS})"
            )
        }
        lifecycle_json = str(
            conn.execute(
                "SELECT final_record_json FROM observability_run_lifecycle "
                "WHERE run_id=?",
                (RUN_ID,),
            ).fetchone()[0]
        )

    assert final_record is not None
    assert span_diagnostic is not None
    assert gate_diagnostic == final_gate_store.PersistedFinalGateDiagnostic(
        run_id=RUN_ID,
        schema_version=2,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
        end_step=obs.END_STEP_GATE,
        reason_code="publish_blocked",
        recorded_at=final_record.at,
    )
    assert span_diagnostic.recorded_at == final_record.at
    assert columns == {
        "run_id",
        "schema_version",
        "corp_code",
        "confirmed_company",
        "end_step",
        "reason_code",
        "recorded_at",
    }
    assert not {
        "prompt",
        "response",
        "company",
        "message",
        "text",
        "원문",
        "reason_detail",
    } & columns
    assert "final_gate_reason" not in lifecycle_json
    assert "publish_blocked" not in lifecycle_json


def test_최종게이트_저장실패는_lifecycle과_span도_같이_rollback한다(
    tmp_path, monkeypatch
) -> None:
    db_path = _configure_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(
        final_gate_store,
        "record_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("최종 게이트 진단 저장 실패")
        ),
    )

    assert not recording.record_run(
        UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        _gate_result(),
        1.0,
        run_id=RUN_ID,
        corp_code=CORP_CODE,
        confirmed_company=CONFIRMED_COMPANY,
    )

    with sqlite3.connect(db_path) as conn:
        lifecycle_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='observability_run_lifecycle'"
        ).fetchone()
        lifecycle_row = (
            conn.execute(
                "SELECT 1 FROM observability_run_lifecycle WHERE run_id=?",
                (RUN_ID,),
            ).fetchone()
            if lifecycle_table is not None
            else None
        )
        assert lifecycle_row is None
        assert span_store.read_for_run(conn, RUN_ID) is None
        assert final_gate_store.read_for_run(conn, RUN_ID) is None


def test_게이트중단이_아닌결과에는_최종게이트_사유를_기록하지않는다(
    tmp_path, monkeypatch
) -> None:
    db_path = _configure_paths(tmp_path, monkeypatch)
    result = RunResult(
        outcome=Outcome.REPORT,
        final_gate_reason="comparison_blocked",
    )

    assert not recording.record_run(
        UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        result,
        1.0,
        run_id=RUN_ID,
    )
    assert not db_path.exists()


def test_기존_빈사유_게이트중단은_부속표없이_그대로_기록된다(
    tmp_path, monkeypatch
) -> None:
    db_path = _configure_paths(tmp_path, monkeypatch)

    assert recording.record_run(
        UserInput(company="", job="회사분석", region=""),
        RunResult(outcome=Outcome.GATE_STOPPED),
        1.0,
        run_id=RUN_ID,
    )

    with sqlite3.connect(db_path) as conn:
        assert lifecycle.read_final(conn, RUN_ID) is not None
        assert final_gate_store.read_for_run(conn, RUN_ID) is None


def test_비용미확정_게이트중단도_worker에서_실패이력과_진단을_함께_남긴다(
    monkeypatch,
    caplog,
) -> None:
    """게이트 결과를 FAILED로 보수적 정규해도 유료 호출 이력은 사라지지 않는다."""

    class UncertainGatePipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                billing_uncertain=True,
                final_gate_reason="publish_blocked",
                cost_krw=17.0,
            )

    job = job_runtime.Job(
        job_id=RUN_ID,
        user_input=UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        card=CompanyCard(
            legal_name="저장하지 않을 회사",
            typed_name="저장하지 않을 회사",
            address="",
            ceo="",
            founded="",
            ref="12345678",
        ),
    )
    monkeypatch.setattr(runtime, "_PIPELINE", UncertainGatePipeline())
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)
    caplog.set_level(logging.WARNING, logger=job_runtime.__name__)

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.outcome is Outcome.FAILED
    assert job.result.billing_uncertain is True
    assert job.result.final_gate_reason == "publish_blocked"
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        final_record = lifecycle.read_final(conn, RUN_ID)
        diagnostic = final_gate_store.read_for_run(conn, RUN_ID)
    assert final_record is not None
    assert final_record.end_step == obs.END_STEP_GENERATE
    assert diagnostic is not None
    assert diagnostic.corp_code == "12345678"
    assert diagnostic.confirmed_company == "저장하지 않을 회사"
    assert diagnostic.end_step == obs.END_STEP_GENERATE
    assert diagnostic.reason_code == "publish_blocked"
    diagnostic_logs = [
        record.getMessage()
        for record in caplog.records
        if record.name == job_runtime.__name__
        and record.getMessage().startswith("조사 실패를 기록했습니다")
    ]
    assert diagnostic_logs == [
        "조사 실패를 기록했습니다 corp_code=12345678 "
        "company=저장하지 않을 회사 reason_code=publish_blocked"
    ]


def test_일반_FAILED도_회사식별값과_안전한_기술사유를_한줄만_기록한다(
    monkeypatch,
    caplog,
) -> None:
    class FailedPipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            return RunResult(outcome=Outcome.FAILED)

    job = job_runtime.Job(
        job_id="failed-run-with-company",
        user_input=UserInput(company="입력 회사", job="회사분석", region=""),
        card=CompanyCard(
            legal_name="확정 회사",
            typed_name="입력 회사",
            address="",
            ceo="",
            founded="",
            ref="00126380",
        ),
    )
    monkeypatch.setattr(runtime, "_PIPELINE", FailedPipeline())
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)
    caplog.set_level(logging.WARNING, logger=job_runtime.__name__)

    asyncio.run(job_runtime._run_job(job))
    job_runtime._log_failed_run(job)

    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == job_runtime.__name__
        and record.getMessage().startswith("조사 실패를 기록했습니다")
    ] == [
        "조사 실패를 기록했습니다 corp_code=00126380 "
        "company=확정 회사 reason_code=generation_failed"
    ]
