"""FULL 완료 실패가 lifecycle 최종 행을 「완주」로 잘못 남기지 않는지 본다.

★ 지키는 것: ``record_run``(``src.web.recording``)이 쓰는 lifecycle 최종 행은
  ``pending``→``running``→``final`` 중 ``final``이 되면 다시 바꿀 수 없다
  (``src.features.observability.lifecycle.finalize_once``). 그런데 기존 코드는
  파이프라인이 REPORT를 돌려주자마자(저장·출고 확정 전에) 이 최종 행을 썼다.
  그래서 FULL 생성물이 파이프라인 이후 저장·출고에서 실패해도 이력에는
  ``end_step="완주"``·``grade="완성"``으로 영구히 남았다(실측 — PUBLIC delivery
  확정 실패를 주입해도 lifecycle은 「완주」였다).

  FULL 생성물만 저장·출고 결과를 알고 난 뒤에 lifecycle 최종 행을 쓴다. 실제
  내부 AI 원가 기록(``cost_store.record_run_costs``)은 이 순서와 무관하게
  그대로 파이프라인 직후에 남는다 — 옮기면 안 되는 것은 원가 기록이지
  lifecycle 기록이 아니다.

★ 음성 대조: demo/v1(비-FULL)은 여전히 파이프라인 직후 즉시 기록한다 —
  기존 lifecycle 시험(test_job_runtime_lifecycle.py)이 이미 그 순서를
  지키고 있으므로 여기서 다시 반복하지 않는다.
"""

from __future__ import annotations

import asyncio
import uuid

from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline.port import (
    CompanyCard,
    Grade,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.report_access.models import ReportAudience
from src.features.storage import db as storage_db
from src.shared.report_evidence.constants import ReleaseMode
from src.web import job_runtime, runtime


def _minimal_full_report() -> Report:
    return Report(
        company="가나다전자",
        job="개발",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        release_mode=ReleaseMode.FULL.value,
    )


class _ReportPipeline:
    def __init__(self, report: Report) -> None:
        self._report = report

    def run(self, *_args, **_kwargs):
        return RunResult(outcome=Outcome.REPORT, report=self._report)


def test_FULL_출고확정예외는_lifecycle_최종행을_완주로_남기지_않는다(monkeypatch):
    report = _minimal_full_report()
    job_id = uuid.uuid4().hex
    job = job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="lifecycle-order-corp",
        ),
        report_audience=ReportAudience.ADMIN,
    )

    def raising_finalize(_job):
        raise RuntimeError("시험: 출고 확정 실패")

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline(report))
    # record_run은 여기서 mock하지 않는다 — 실제 lifecycle 최종 행을 남겨야
    # end_step을 검증할 수 있다.
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", raising_finalize)
    monkeypatch.setattr(job_runtime, "_fail_report_delivery", lambda _job: None)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.outcome is Outcome.FAILED

    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        final = lifecycle.read_final(conn, job_id)

    assert final is not None
    # 핵심 단정: 실제로는 실패했으므로 「완주」로 남으면 안 된다.
    assert final.end_step != obs.END_STEP_COMPLETE
    assert final.end_step == obs.END_STEP_GENERATE
    assert final.grade != "완성"
