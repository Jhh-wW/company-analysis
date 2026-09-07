"""실행 진단 기록이 파이프라인에서 실행 기록 저장소까지 실제로 닿는지 본다.

★ 이 시험이 지키는 것 — 진단은 파이프라인이 도는 «다른 스레드»에서 만들어진다.
  그 스레드에서 자리를 열지 않으면 조용히 빈 칸이 되고, 화면은 「기록 없음」만
  보여 준다. 원인 추적이 두 번 막힌 자리가 정확히 여기다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.features.observability import run_diagnostics
from src.features.observability import run_steps_store
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.web import job_runtime, runtime


_RUN_ID = "0123456789abcdef0123456789abcdef"
_STEPS = [
    {"step": "5b_뉴스_수집", "검색": 7, "선별": 2, "조각": 1},
    {"step": "6_수집_홈페이지", "주소": "https://ex.example.com/a"},
    {"step": "7_이름후보", "후보": 4, "조각": 2},
]


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """진짜 운영 저장소·이력 파일을 건드리지 않는다."""
    db_path = tmp_path / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv("OBSERVABILITY_RECORDS_PATH", str(tmp_path / "runs.jsonl"))
    return db_path


class _DiagnosticPipeline:
    """진짜 파이프라인처럼 자리를 열고 단계를 쌓은 뒤 닫는다."""

    def __init__(self, *, outcome: Outcome = Outcome.FAILED) -> None:
        self._outcome = outcome

    def run(self, _user_input, card, _on_step=None) -> RunResult:
        diagnostics = run_diagnostics.begin_run()
        try:
            run_diagnostics.current_steps().extend(_STEPS)
            if self._outcome is Outcome.FAILED:
                raise RuntimeError("본조사 실패")
            return RunResult(outcome=self._outcome)
        except RuntimeError:
            return RunResult(outcome=Outcome.FAILED)
        finally:
            diagnostics.finish(corp_code=card.ref)


def _job(job_id: str = _RUN_ID) -> job_runtime.Job:
    return job_runtime.Job(
        job_id=job_id,
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


def _run(job: job_runtime.Job, monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setattr(runtime, "_PIPELINE", _DiagnosticPipeline(**kwargs))
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)
    asyncio.run(job_runtime._run_job(job))


def test_실패로_끝난_실행도_단계기록이_저장된다(
    isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job()

    _run(job, monkeypatch)

    with storage_db.connect() as conn:
        saved = run_steps_store.load(conn, _RUN_ID)
    assert saved is not None
    assert saved.steps == _STEPS
    assert saved.step_count == len(_STEPS)


def test_보고서로_끝난_실행도_단계기록이_저장된다(
    isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job("fedcba98765432100123456789abcdef")

    _run(job, monkeypatch, outcome=Outcome.REPORT)

    with storage_db.connect() as conn:
        saved = run_steps_store.load(conn, job.job_id)
    assert saved is not None
    assert [item["step"] for item in saved.steps] == [item["step"] for item in _STEPS]


def test_두_번_마감해도_기록은_한_벌만_남는다(
    isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    job = _job()
    _run(job, monkeypatch)

    job.diagnostics_persisted = False
    job.diagnostic_steps = [{"step": "덮어쓰려는기록"}]
    job_runtime._persist_run_diagnostics(job)

    with storage_db.connect() as conn:
        saved = run_steps_store.load(conn, _RUN_ID)
    assert saved is not None
    assert saved.steps == _STEPS


def test_단계를_하나도_안_남긴_실행은_빈_행을_만들지_않는다(
    isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    class _SilentPipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            return RunResult(outcome=Outcome.FAILED)

    job = _job("aaaabbbbccccddddeeeeffff00001111")
    monkeypatch.setattr(runtime, "_PIPELINE", _SilentPipeline())
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    with storage_db.connect() as conn:
        assert run_steps_store.load(conn, job.job_id) is None


def test_pipeline예외는_메시지없이_안전한_실패경계로_저장된다(
    isolated_paths: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = "https://private.example/report?token=secret 원문"

    class _BrokenPipeline:
        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            raise RuntimeError(secret)

    job = _job("11112222333344445555666677778888")
    monkeypatch.setattr(runtime, "_PIPELINE", _BrokenPipeline())
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    with storage_db.connect() as conn:
        saved = run_steps_store.load(conn, job.job_id)
    assert saved is not None
    assert saved.steps == [
        {
            "step": "runtime_failure",
            "phase": "pipeline",
            "state": "failed",
            "role": "worker",
            "exception_class": "RuntimeError",
            "reason_code": "unexpected_pipeline_failure",
        }
    ]
    assert secret not in repr(saved.steps)
