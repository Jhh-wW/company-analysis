"""웹 마감과 single-flight 실패 사유가 안전하게 이어지는지 검사한다."""

from __future__ import annotations

import asyncio

import pytest

from src.features.observability import run_diagnostics
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.shared import generation_coordination
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
)
from src.web import job_runtime, runtime


def _job() -> job_runtime.Job:
    return job_runtime.Job(
        job_id="0123456789abcdef0123456789abcdef",
        user_input=UserInput(company="회사", job="직무", region="서울"),
        card=CompanyCard(
            legal_name="확정 회사",
            typed_name="회사",
            address="서울",
            ceo="대표",
            founded="20200101",
            ref="00126380",
        ),
    )


def test_singleflight_owner중단은_최종게이트의_닫힌사유를_우선한다():
    job = _job()
    job.result = RunResult(
        outcome=Outcome.GATE_STOPPED,
        final_gate_reason=FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    )
    job.diagnostic_steps = [
        {
            "step": "v2_FULL_독립문서사전검사_차단",
            "독립문서수": 5,
            "사유코드": "preflight_document_sources_insufficient",
        }
    ]

    assert (
        job_runtime._singleflight_failure_code(job)
        == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    )


def test_singleflight_owner중단은_임의사유나_원문을_fanout하지_않는다():
    job = _job()
    job.result = RunResult(
        outcome=Outcome.GATE_STOPPED,
        final_gate_reason="https://private.example/report?token=secret 원문",
    )

    assert job_runtime._singleflight_failure_code(job) == "generation_failed"


def test_generation_owner_failed는_waiter가_읽을_원사유를_속성으로_보존한다():
    error = generation_coordination.GenerationOwnerFailed(
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    )

    assert error.failure_code == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT


def test_run_job은_owner의_공식근거중단사유를_singleflight에_전파한다(
    monkeypatch,
):
    class GatePipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            diagnostics = run_diagnostics.begin_run()
            try:
                run_diagnostics.current_steps().append(
                    {
                        "step": "v2_FULL_독립문서사전검사_차단",
                        "독립문서수": 5,
                        "사유코드": "preflight_document_sources_insufficient",
                    }
                )
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    final_gate_reason=(
                        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
                    ),
                )
            finally:
                diagnostics.finish(corp_code="00126380")

    class OwnerSession:
        owns_generation = True

        def __init__(self) -> None:
            self.failed_with = ""

        def fail(self, failure_code: str) -> None:
            self.failed_with = failure_code
            self.owns_generation = False

        @staticmethod
        def abandon() -> None:
            return None

    session = OwnerSession()
    job = _job()
    job.generation_session = session
    monkeypatch.setattr(runtime, "_PIPELINE", GatePipeline())
    monkeypatch.setattr(job_runtime.cost_store, "record_run_costs", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_persist_run_diagnostics", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_release_job_slot", lambda _job: None)

    asyncio.run(job_runtime._run_job(job))

    assert session.failed_with == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    assert job.result is not None
    assert job.result.outcome is Outcome.GATE_STOPPED


def test_waiter도_owner의_공식근거중단을_기술실패가_아닌_gate로_받는다(
    monkeypatch,
):
    class WaiterPipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            raise generation_coordination.GenerationOwnerFailed(
                FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
            )

    job = _job()
    monkeypatch.setattr(runtime, "_PIPELINE", WaiterPipeline())
    monkeypatch.setattr(job_runtime.cost_store, "record_run_costs", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_persist_run_diagnostics", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_release_job_slot", lambda _job: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.outcome is Outcome.GATE_STOPPED
    assert (
        job.result.final_gate_reason
        == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    assert job.diagnostic_steps == [
        {
            "step": "runtime_failure",
            "phase": "coordination",
            "state": "failed",
            "role": "waiter",
            "exception_class": "GenerationOwnerFailed",
            "reason_code": "generation_owner_failed",
            "owner_reason_code": "official_evidence_insufficient",
        }
    ]


def test_원가저장_예외가_후단을_건너뛰어도_owner를_즉시_fail한다(monkeypatch):
    class GatePipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                final_gate_reason=(
                    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
                ),
            )

    class OwnerSession:
        owns_generation = True

        def __init__(self) -> None:
            self.failed_with = ""
            self.abandoned = False

        def fail(self, failure_code: str) -> None:
            self.failed_with = failure_code
            self.owns_generation = False

        def abandon(self) -> None:
            self.abandoned = True

    def storage_failed(*_args, **_kwargs):
        raise RuntimeError("https://private.example/cost?token=secret")

    session = OwnerSession()
    job = _job()
    job.generation_session = session
    monkeypatch.setattr(runtime, "_PIPELINE", GatePipeline())
    monkeypatch.setattr(job_runtime.cost_store, "record_run_costs", storage_failed)
    monkeypatch.setattr(job_runtime, "_persist_run_diagnostics", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_release_job_slot", lambda _job: None)

    with pytest.raises(RuntimeError):
        asyncio.run(job_runtime._run_job(job))

    assert session.failed_with == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    assert session.abandoned is False
    failure = job.diagnostic_steps[-1]
    assert failure["phase"] == "save"
    assert failure["reason_code"] == "cost_persistence_failed"
    assert "private.example" not in repr(failure)


def test_owner_fail과_abandon이_모두깨져도_진단저장과_slot반환은_계속된다(
    monkeypatch,
):
    class FailedPipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            return RunResult(outcome=Outcome.FAILED)

    class BrokenOwnerSession:
        owns_generation = True

        @staticmethod
        def fail(_failure_code: str) -> None:
            raise RuntimeError("https://private.example/fail?token=secret")

        @staticmethod
        def abandon() -> None:
            raise RuntimeError("https://private.example/abandon?token=secret")

    calls: list[str] = []
    job = _job()
    job.generation_session = BrokenOwnerSession()
    monkeypatch.setattr(runtime, "_PIPELINE", FailedPipeline())
    monkeypatch.setattr(
        job_runtime.cost_store,
        "record_run_costs",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(
        job_runtime,
        "_persist_run_diagnostics",
        lambda _job: calls.append("diagnostics"),
    )
    monkeypatch.setattr(
        job_runtime,
        "_release_job_slot",
        lambda _job: calls.append("slot"),
    )

    asyncio.run(job_runtime._run_job(job))

    assert job.finished is True
    assert calls == ["diagnostics", "slot"]
    assert job.generation_abandoned is True
    assert len(job.diagnostic_steps) == 2
    assert {
        (step["phase"], step["reason_code"])
        for step in job.diagnostic_steps
    } == {("coordination", "generation_finalize_failed")}
    assert "private.example" not in repr(job.diagnostic_steps)
