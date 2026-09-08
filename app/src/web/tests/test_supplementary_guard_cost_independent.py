"""보완조사 최종 guard와 웹 요청 종료 정산의 독립 경계 검증."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from src.features.cost_tracking.schema import AI_EVENT_TABLE, RUN_COST_TABLE
from src.features.cost_tracking.store import AiCostEvent
from src.features.pipeline.port import Grade, Outcome, Report, RunResult
from src.features.pipeline.supplementary_research_runtime import (
    enforce_supplementary_research_release,
)
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_IDENTIFY, SPEND_PHASE_PIPELINE
from src.core import clock
from src.features.storage import db as storage_db
from src.web import job_runtime, main, runtime
from src.web.tests import test_paid_stage_guards as paid_guards


_PIPELINE_COST_KRW = 17.25


def _report_without_official_context() -> Report:
    """공식 증거·인용 등록부가 없는 Report를 만든다(양성 출고는 검증하지 않는다)."""

    return Report(
        company="가나다전자",
        job="영업",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        company_id="corp-001",
    )


class _GuardedPaidPipeline(paid_guards.FakePaidPipeline):
    """provider 대신 실제 최종 guard를 호출하는 작은 웹 경계용 double."""

    def find_company_metered(self, user_input):
        result = super().find_company_metered(user_input)
        return replace(
            result,
            ai_cost_events=(
                AiCostEvent(
                    stage="company_identify",
                    model_id="identify-fixture-model",
                    input_tokens=6,
                    output_tokens=3,
                    cost_krw=10.0,
                ),
            ),
        )

    def run(self, *_args, **_kwargs) -> RunResult:
        self.run_calls += 1
        event = AiCostEvent(
            stage="supplementary_research",
            model_id="guard-fixture-model",
            input_tokens=10,
            output_tokens=5,
            cost_krw=_PIPELINE_COST_KRW,
        )
        result = RunResult(
            outcome=Outcome.REPORT,
            report=_report_without_official_context(),
            charged=True,
            cost_krw=_PIPELINE_COST_KRW,
            model="guard-fixture-model",
            ai_cost_events=(event,),
        )
        return enforce_supplementary_research_release(
            result,
            official_evidence=None,
            steps=[],
        )


def test_invalid_research_context_blocks_release_and_charge_but_preserves_phase_costs(
    monkeypatch,
) -> None:
    """요청 종료 경계에서 guard 결과, AI 원가 원장, phase 정산을 함께 확인한다."""

    pipeline = _GuardedPaidPipeline(lookup_cost=10.0, pipeline_cost=_PIPELINE_COST_KRW)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with TestClient(main.app) as client:
        paid_guards._발급(client)
        token, ref = paid_guards._확인값(paid_guards._confirm(client).text)
        response = client.post(
            "/run",
            data=paid_guards._run_form(token, ref),
            follow_redirects=False,
        )
        job_id = paid_guards._기다린다(client, response)

        job = job_runtime._JOBS[job_id]
        assert job.result is not None
        assert job.result.outcome is Outcome.GATE_STOPPED
        assert job.result.report is None
        assert job.result.charged is False
        assert job.report_persisted is None
        assert job.result.cost_krw == 10.0 + _PIPELINE_COST_KRW
        assert job.result.ai_cost_events
        assert job.result.ai_cost_events[-1].cost_krw == _PIPELINE_COST_KRW
        assert job.paid_phase_settled is True

        # 결과 페이지는 보고서를 내보내지 않고 중단 화면만 제공해야 한다.
        result_response = client.get(f"/result/{job_id}")
        assert result_response.status_code == 200

    with storage_db.connect() as conn:
        cost_row = tuple(conn.execute(
            f"SELECT outcome, internal_ai_cost_krw, customer_charge_krw, "
            f"charge_eligible FROM {RUN_COST_TABLE} WHERE run_id = ?",
            (job_id,),
        ).fetchone())
        event_rows = [tuple(row) for row in conn.execute(
            f"SELECT stage, model_id, cost_krw FROM {AI_EVENT_TABLE} "
            "WHERE run_id = ? ORDER BY sequence",
            (job_id,),
        ).fetchall()]
        spend_store.ensure_schema(conn)
        snapshot = spend_store.load_day(conn, clock.today_kst())
        unresolved = spend_store.load_unresolved_day(conn, clock.today_kst())
        phase_rows = [tuple(row) for row in conn.execute(
            "SELECT phase, cost_krw FROM budget_spend_events "
            "WHERE run_id = ? ORDER BY phase",
            (job_id,),
        ).fetchall()]

    assert cost_row == (Outcome.GATE_STOPPED.value, 27.25, 0.0, 0)
    assert event_rows == [
        ("company_identify", "identify-fixture-model", 10.0),
        ("supplementary_research", "guard-fixture-model", 17.25),
    ]
    assert snapshot.by_run == {job_id: 27.25}
    assert unresolved == frozenset()
    assert dict(phase_rows) == {
        SPEND_PHASE_IDENTIFY: 10.0,
        SPEND_PHASE_PIPELINE: 17.25,
    }
