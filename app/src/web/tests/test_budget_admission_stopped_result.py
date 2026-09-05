"""본조사 시작 전 예산 거절의 결과·기록·화면 계약을 검증한다."""

from __future__ import annotations

import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.cost_tracking import store as cost_store
from src.features.final_gate_diagnostic import store as final_gate_store
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.storage import db as storage_db
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED,
)
from src.web import generation_singleflight, job_runtime, main, runtime
from src.web.tests.report_route_support import bind_public_report_access


def _job(job_id: str | None = None) -> job_runtime.Job:
    return job_runtime.Job(
        job_id=job_id or uuid.uuid4().hex,
        user_input=UserInput(company="예산시험회사", job="", region="서울"),
        card=CompanyCard(
            legal_name="예산시험회사",
            typed_name="예산시험회사",
            address="서울",
            ceo="",
            founded="",
            ref="budget-test-company",
        ),
    )


def test_본조사_예약거절은_pipeline_provider를_부르기_전에_끝난다(
    monkeypatch,
) -> None:
    class _PaidPipeline:
        supports_deferred_paid_phase = False

        def __init__(self) -> None:
            self.run_calls = 0

        def run(self, *_args, **_kwargs) -> RunResult:
            self.run_calls += 1
            return RunResult(outcome=Outcome.REPORT)

    pipeline = _PaidPipeline()
    job = _job()
    job.is_paid = True
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "_frozen_job_build_identity", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_require_open_share_link", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_begin_paid_phase", lambda **_kwargs: None)

    with pytest.raises(generation_singleflight.PaidGenerationAdmissionUnavailable):
        job_runtime._run_pipeline_worker(job)

    assert pipeline.run_calls == 0


def test_본조사_예약거절은_새_사유와_AI비용_0원으로_기록된다(
    monkeypatch,
) -> None:
    job = _job("budget-admission-denied")

    def _denied(_job: job_runtime.Job) -> RunResult:
        raise generation_singleflight.PaidGenerationAdmissionUnavailable(
            "시험용 시작 전 예약 거절"
        )

    monkeypatch.setattr(job_runtime, "_run_pipeline_worker", _denied)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.outcome is Outcome.GATE_STOPPED
    assert (
        job.result.final_gate_reason
        == FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED
    )
    assert job.result.ai_cost_events == ()
    assert job.result.cost_krw == 0.0
    assert job.result.charged is False

    with storage_db.connect() as conn:
        run_cost = conn.execute(
            f"SELECT outcome, internal_ai_cost_krw, customer_charge_krw, "
            f"charge_eligible FROM {cost_store.RUN_COST_TABLE} WHERE run_id=?",
            (job.job_id,),
        ).fetchone()
        event_count = conn.execute(
            f"SELECT COUNT(*) FROM {cost_store.AI_EVENT_TABLE} WHERE run_id=?",
            (job.job_id,),
        ).fetchone()[0]
        final_record = lifecycle.read_final(conn, job.job_id)
        diagnostic = final_gate_store.read_for_run(conn, job.job_id)

    assert tuple(run_cost) == (Outcome.GATE_STOPPED.value, 0.0, 0.0, 0)
    assert event_count == 0
    assert final_record is not None
    assert final_record.end_step == obs.END_STEP_GATE
    assert final_record.cost_krw == 0.0
    assert diagnostic is not None
    assert diagnostic.reason_code == FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED


def test_본조사_예약거절_화면은_오늘예산과_자정_행동만_안내한다() -> None:
    job = _job()
    job.finished = True
    job.result = RunResult(
        outcome=Outcome.GATE_STOPPED,
        message="이 문장은 화면 문구의 권위가 아니다.",
        final_gate_reason=FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED,
    )
    session = auth_logic.create_session("admin@example.com", True)
    job_runtime._JOBS[job.job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job.job_id)
            public_response = client.get(f"/result/{job.job_id}")
            client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
            admin_response = client.get(f"/result/{job.job_id}")
    finally:
        job_runtime._JOBS.pop(job.job_id, None)
        auth_logic.delete_session(session.token)

    expected_body = (
        "이 계정의 하루 조사 비용 한도에 도달해 조사를 시작하지 않았습니다. "
        "자정(한국 시간)이 지나면 다시 할 수 있습니다. "
        "이용 횟수는 차감되지 않았습니다."
    )
    assert public_response.status_code == 200
    assert "오늘 조사 예산을 다 썼습니다" in public_response.text
    assert expected_body in public_response.text
    assert re.search(r">\s*처음 화면으로\s*</a>", public_response.text)
    assert "잠시 후 다시" not in public_response.text
    assert "입력을 확인" not in public_response.text
    assert "오류" not in public_response.text
    assert FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED not in public_response.text

    assert admin_response.status_code == 200
    assert "사유 코드" in admin_response.text
    assert FINAL_GATE_REASON_START_BUDGET_RESERVATION_DENIED in admin_response.text


def test_다른_일반실패는_기존_문구와_버튼을_유지한다() -> None:
    job = _job()
    job.finished = True
    job.result = RunResult(
        outcome=Outcome.FAILED,
        message="보고서를 만들다 오류가 났습니다. 잠시 후 다시 시도해주세요.",
    )
    job_runtime._JOBS[job.job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job.job_id)
            response = client.get(f"/result/{job.job_id}")
    finally:
        job_runtime._JOBS.pop(job.job_id, None)

    assert response.status_code == 200
    assert "조사를 멈췄습니다" in response.text
    assert "보고서를 만들다 오류가 났습니다" in response.text
    assert re.search(r">\s*회사·주소 다시 입력\s*</a>", response.text)
