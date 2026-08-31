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


def test_청구결정_지문은_run_출고물_결정_모두에_결속된다():
    decision = store.CustomerChargeDecision(
        eligible=True,
        amount_krw=1000.0,
        reason="released",
    )
    baseline = store.charge_decision_sha256(
        run_id="charge-run",
        automatic_release_sha256="a" * 64,
        decision=decision,
    )

    assert baseline == store.charge_decision_sha256(
        run_id="charge-run",
        automatic_release_sha256="a" * 64,
        decision=store.CustomerChargeDecision(
            eligible=True,
            amount_krw=1000,
            reason="released",
        ),
    )
    assert baseline != store.charge_decision_sha256(
        run_id="other-run",
        automatic_release_sha256="a" * 64,
        decision=decision,
    )
    assert baseline != store.charge_decision_sha256(
        run_id="charge-run",
        automatic_release_sha256="b" * 64,
        decision=decision,
    )
    assert baseline != store.charge_decision_sha256(
        run_id="charge-run",
        automatic_release_sha256="a" * 64,
        decision=store.CustomerChargeDecision(
            eligible=True,
            amount_krw=1001,
            reason="released",
        ),
    )


def test_확정된_출고지문을_다른_PDF로_바꾸지_못한다():
    conn = _conn()
    try:
        store.record_run_costs(
            conn,
            run_id="immutable-release",
            outcome=Outcome.REPORT,
            internal_ai_cost_krw=12.3,
        )
        store.mark_automatic_release(
            conn,
            run_id="immutable-release",
            automatic_release_sha256="a" * 64,
        )

        with pytest.raises(store.CostAuthorityConflict):
            store.mark_automatic_release(
                conn,
                run_id="immutable-release",
                automatic_release_sha256="b" * 64,
            )

        row = conn.execute(
            "SELECT outcome, internal_ai_cost_krw, automatic_release_sha256 "
            "FROM report_cost_summaries WHERE run_id='immutable-release'"
        ).fetchone()
        assert tuple(row) == (Outcome.REPORT.value, 12.3, "a" * 64)
    finally:
        conn.close()


def test_같은_출고지문_재시도는_최초_청구결정을_바꾸지_않는다():
    conn = _conn()
    try:
        first = store.mark_automatic_release(
            conn,
            run_id="idempotent-release",
            automatic_release_sha256="c" * 64,
        )
        retry = store.mark_automatic_release(
            conn,
            run_id="idempotent-release",
            automatic_release_sha256="c" * 64,
            configured_price_krw=9999,
        )

        assert retry == first
        row = conn.execute(
            "SELECT customer_charge_krw, charge_eligible, charge_reason "
            "FROM report_cost_summaries WHERE run_id='idempotent-release'"
        ).fetchone()
        assert tuple(row) == (0.0, 1, "price_not_configured")
    finally:
        conn.close()


def test_출고가_확정된_조사를_나중에_실패로_바꾸지_못한다():
    conn = _conn()
    try:
        store.mark_automatic_release(
            conn,
            run_id="released-outcome",
            automatic_release_sha256="d" * 64,
        )

        with pytest.raises(store.CostAuthorityConflict):
            store.record_run_costs(
                conn,
                run_id="released-outcome",
                outcome=Outcome.FAILED,
                internal_ai_cost_krw=88.0,
            )

        row = conn.execute(
            "SELECT outcome, internal_ai_cost_krw, automatic_release_sha256 "
            "FROM report_cost_summaries WHERE run_id='released-outcome'"
        ).fetchone()
        assert tuple(row) == (Outcome.REPORT.value, 0.0, "d" * 64)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "column, value",
    (
        ("outcome", "failed"),
        ("customer_charge_krw", -1),
        ("charge_eligible", 2),
        ("charge_reason", ""),
    ),
)
def test_손상된_기존_청구행을_재시도로_정상인척하지_않는다(
    column: str,
    value: object,
):
    conn = _conn()
    try:
        store.mark_automatic_release(
            conn,
            run_id="corrupt-release",
            automatic_release_sha256="e" * 64,
        )
        conn.execute(
            f"UPDATE report_cost_summaries SET {column}=? WHERE run_id=?",
            (value, "corrupt-release"),
        )

        with pytest.raises(store.CostAuthorityConflict):
            store.mark_automatic_release(
                conn,
                run_id="corrupt-release",
                automatic_release_sha256="e" * 64,
            )
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
