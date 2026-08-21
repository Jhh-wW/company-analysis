from __future__ import annotations

import sqlite3

import pytest

from src.core.pricing import detailed_usage_cost_krw
from src.features.cost_tracking import store
from src.features.pipeline.port import Outcome


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_cache_hit_일반호출_batch_비용을_단일정본에서_계산한다():
    normal = detailed_usage_cost_krw(
        "claude-sonnet-4-6", input_tokens=1000, output_tokens=100
    )
    cache_read = detailed_usage_cost_krw(
        "claude-sonnet-4-6",
        input_tokens=0,
        output_tokens=100,
        cache_read_tokens=1000,
    )
    batch = detailed_usage_cost_krw(
        "claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=100,
        batch=True,
    )

    assert cache_read < normal
    assert batch == pytest.approx(normal / 2, abs=0.01)


def test_알수없는모델은_보수적단가를_쓴다():
    known = detailed_usage_cost_krw(
        "claude-sonnet-4-6", input_tokens=1000, output_tokens=100
    )
    unknown = detailed_usage_cost_krw(
        "not-a-model", input_tokens=1000, output_tokens=100
    )
    assert unknown > known


def test_GATE_STOPPED_고객청구0원이지만_실패호출내부원가는_보존한다():
    event = store.AiCostEvent(
        stage="span_selection",
        model_id="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=20,
        cost_krw=4.62,
        failed_call=True,
    )
    conn = _conn()
    try:
        store.record_run_costs(
            conn,
            run_id="gate-stopped-1",
            outcome=Outcome.GATE_STOPPED,
            internal_ai_cost_krw=4.62,
            events=(event,),
        )
        summary = conn.execute(
            "SELECT internal_ai_cost_krw, customer_charge_krw, charge_eligible "
            "FROM report_cost_summaries WHERE run_id='gate-stopped-1'"
        ).fetchone()
        failed_cost = conn.execute(
            "SELECT cost_krw, failed_call FROM ai_variable_cost_events "
            "WHERE run_id='gate-stopped-1'"
        ).fetchone()
        assert tuple(summary) == (4.62, 0.0, 0)
        assert tuple(failed_cost) == (4.62, 1)
    finally:
        conn.close()


def test_자동출고전에는_청구불가_출고후에도_가격미확정이면_0원이다():
    conn = _conn()
    try:
        store.record_run_costs(
            conn,
            run_id="released-1",
            outcome=Outcome.REPORT,
            internal_ai_cost_krw=123.45,
        )
        decision = store.mark_automatic_release(
            conn,
            run_id="released-1",
            automatic_release_sha256="a" * 64,
        )
        row = conn.execute(
            "SELECT internal_ai_cost_krw, customer_charge_krw, charge_eligible, "
            "automatic_release_sha256 FROM report_cost_summaries "
            "WHERE run_id='released-1'"
        ).fetchone()
        assert decision.eligible is True
        assert decision.amount_krw == 0
        assert tuple(row) == (123.45, 0.0, 1, "a" * 64)
    finally:
        conn.close()


def test_서버월고정비는_AI변동원가와_다른표에만_기록한다():
    conn = _conn()
    try:
        store.record_monthly_server_cost(
            conn, month="2026-08", amount_krw=50000, note="Render"
        )
        assert conn.execute(
            "SELECT amount_krw FROM monthly_server_fixed_costs"
        ).fetchone()[0] == 50000
        assert conn.execute(
            "SELECT COUNT(*) FROM ai_variable_cost_events"
        ).fetchone()[0] == 0
    finally:
        conn.close()
