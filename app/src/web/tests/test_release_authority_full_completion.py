"""FULL 완료 실패가 모든 audience에서 메모리 결과를 일관되게 닫는지 본다 (P1-3).

★ 지키는 것: FULL(``release_mode == "FULL"``) 생성물은 저장(``_save_report``)이나
  출고 확정(``_finalize_report_delivery``)이 실패하면 audience(PUBLIC·MEMBER·
  LINK·ADMIN)와 무관하게 ``job.result``가 ``Outcome.REPORT`` + 본문을 그대로
  들고 있지 않는다. 이전 코드는 PUBLIC의 「저장 실패」 한 갈래만 정리했고,
  나머지 audience·나머지 실패 지점(출고 확정 실패)은 outcome=REPORT + report
  잔존을 그대로 뒀다. 출고가 확정되지 않은 결과를 메모리에 남겨 두면 화면이
  그 본문을 그대로 그릴 수 있으므로, 실패한 FULL 결과는 audience와 실패
  지점을 가리지 않고 비운다
  (`docs/출력물 기준/90_공통_규칙/런타임_출고_계약.md` §6 출고 게이트).

★ 반대 경우 시험: demo/v1(비-FULL) 보고서는 이 정리 대상이 아니다.
  「FULL 밖 demo/non-FULL 동작은 불변이다」를 지키기 위해 비-FULL LINK/ADMIN은
  기존처럼 메모리 결과가 남는 것을 그대로 확인한다. 정리 조건이 audience만으로
  넓어지면(FULL 여부를 무시하면) 이 시험이 깨진다.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from src.features.pipeline.port import (
    CompanyCard,
    Grade,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.report_access.models import ReportAudience
from src.shared.report_evidence.constants import ReleaseMode
from src.web import job_runtime, runtime


def _minimal_report(*, release_mode: str) -> Report:
    """생산 검증 로직은 건드리지 않고 audience 정리 배선만 시험하는 최소 Report.

    ``_report_requires_atomic_completion``은 ``release_mode`` 문자열만 보므로
    FULL 전용 ``generation_evidence``(GenerationProducerEvidence)는 이 시험의
    대상이 아니다 — 그건 P1-1·P1-2 전용 시험이 별도로 지킨다.
    """

    return Report(
        company="가나다전자",
        job="개발",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        release_mode=release_mode,
    )


def _audience_job(*, report: Report, audience: ReportAudience, job_id: str) -> job_runtime.Job:
    """LINK·MEMBER 실제 결속은 이 시험의 대상이 아니라 비워 둔다.

    ``_save_report``·``_finalize_report_delivery``를 모두 monkeypatch로
    대체하므로 ``stage_report_storage``의 audience별 결속 검사 자체가
    실행되지 않는다 — 이 시험이 지키는 것은 ``_run_job``의 audience-blind
    정리 배선이지 LINK/MEMBER 정산(그건 test_report_delivery_integration.py의
    ``test_worker는_한번잡은_완료시각으로_모든권한종류를_실제출고한다`` 몫)이 아니다.
    """

    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="release-authority-corp",
        ),
        report_audience=audience,
        member_email=("member@example.com" if audience is ReportAudience.MEMBER else ""),
    )


class _ReportPipeline:
    """어떤 audience로 들어와도 같은 Report를 돌려주는 가짜 파이프라인."""

    def __init__(self, report: Report) -> None:
        self._report = report

    def run(self, *_args, **_kwargs):
        return RunResult(outcome=Outcome.REPORT, report=self._report)


_ALL_AUDIENCES = (
    ReportAudience.PUBLIC,
    ReportAudience.MEMBER,
    ReportAudience.LINK,
    ReportAudience.ADMIN,
)


@pytest.mark.parametrize("audience", _ALL_AUDIENCES)
def test_FULL_저장실패는_audience와_무관하게_메모리결과와_차감을_닫는다(
    monkeypatch, audience
):
    report = _minimal_report(release_mode=ReleaseMode.FULL.value)
    job = _audience_job(report=report, audience=audience, job_id=uuid.uuid4().hex)

    finalized = 0
    failed_intents = 0

    def forbidden_finalize(_job):
        nonlocal finalized
        finalized += 1
        return True

    def failed_delivery(_job):
        nonlocal failed_intents
        failed_intents += 1

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline(report))
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: False)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", forbidden_finalize)
    monkeypatch.setattr(job_runtime, "_fail_report_delivery", failed_delivery)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    # 저장이 실패했으니 출고 확정은 한 번도 불리면 안 된다.
    assert finalized == 0
    assert failed_intents == 1
    assert job.result is not None
    assert job.result.outcome is Outcome.FAILED
    assert job.result.report is None
    assert job.result.charged is False
    assert job.delivery_persisted is False


@pytest.mark.parametrize("audience", _ALL_AUDIENCES)
def test_FULL_출고확정예외는_audience와_무관하게_메모리결과와_차감을_닫는다(
    monkeypatch, audience
):
    """구형 코드는 이 지점(출고 확정 실패)을 PUBLIC조차 정리하지 않았다."""

    report = _minimal_report(release_mode=ReleaseMode.FULL.value)
    job = _audience_job(report=report, audience=audience, job_id=uuid.uuid4().hex)

    failed_intents = 0

    def raising_finalize(_job):
        raise RuntimeError("시험: 출고 확정 실패")

    def failed_delivery(_job):
        nonlocal failed_intents
        failed_intents += 1

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline(report))
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", raising_finalize)
    monkeypatch.setattr(job_runtime, "_fail_report_delivery", failed_delivery)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert failed_intents == 1
    assert job.result is not None
    assert job.result.outcome is Outcome.FAILED
    assert job.result.report is None
    assert job.result.charged is False
    assert job.delivery_persisted is False


@pytest.mark.parametrize("audience", (ReportAudience.LINK, ReportAudience.ADMIN))
def test_비FULL_출고확정예외는_기존대로_메모리결과를_남긴다(monkeypatch, audience):
    """반대 경우 시험 — FULL이 아니면 「demo/non-FULL 동작 무변」을 지킨다.

    이 시험은 정리 조건이 ``release_mode``가 아니라 audience만으로 넓어지면
    깨진다. 그래야 「FULL에만 적용」이 실제로 스코프대로 좁혀졌다는 증거가 된다.
    """

    report = _minimal_report(release_mode="")
    job = _audience_job(report=report, audience=audience, job_id=uuid.uuid4().hex)

    def raising_finalize(_job):
        raise RuntimeError("시험: 출고 확정 실패")

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline(report))
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", raising_finalize)
    monkeypatch.setattr(job_runtime, "_fail_report_delivery", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    # 기존(비-FULL) 계약: outcome은 REPORT로 남고 본문도 메모리에 남는다.
    assert job.result.outcome is Outcome.REPORT
    assert job.result.report is report
    assert job.delivery_persisted is False
