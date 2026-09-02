"""새 attempt 비용 원장이 실제 paid_runtime 입장·마감에 닿는 회귀시험."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store, state_machine
from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.observability import lifecycle
from src.features.pipeline.port import Outcome, RunResult, UserInput
from src.features.storage import db as storage_db
from src.web import main, paid_runtime
from src.web.recording import record_run, records_path


_BUCKET = "link:attempt-runtime"


def _begin_expired_phase(*, run_id: str, dispatched: bool) -> None:
    day = dt.date(2026, 8, 28)
    with storage_db.connect() as conn:
        state_machine.begin_phase(
            conn,
            run_id=run_id,
            phase=SPEND_PHASE_PIPELINE,
            day=day,
            bucket=_BUCKET,
            reservation_krw=900.0,
            bucket_limit_krw=3_000.0,
            run_limit_krw=None,
            lease_owner_id="worker:expired",
            lease_expires_at="2026-08-28T09:05:00+09:00",
            started_at="2026-08-28T09:00:00+09:00",
        )
        if dispatched:
            state_machine.begin_attempt(
                conn,
                run_id=run_id,
                phase=SPEND_PHASE_PIPELINE,
                attempt_id=f"{run_id}:attempt",
                provider="anthropic",
                operation="writer",
                estimated_krw=900.0,
                lease_owner_id="worker:expired",
                created_at="2026-08-28T09:01:00+09:00",
            )
            state_machine.mark_dispatch_intent(
                conn,
                attempt_id=f"{run_id}:attempt",
                lease_owner_id="worker:expired",
                recorded_at="2026-08-28T09:01:01+09:00",
            )


def _cutover() -> None:
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()


def _record_usage_less_failure(ticket: paid_runtime.PaidPhase) -> str:
    """실제 gateway 순서로 usage 없는 provider 실패를 원장에 남긴다."""

    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "pipeline", ticket.reserved_krw)
    callbacks.heartbeat(attempt_id)
    callbacks.mark_dispatch_intent(attempt_id)
    callbacks.record_observation(
        attempt_id,
        ProviderObservation(
            transport_state=TransportState.TRANSPORT_AMBIGUOUS,
            billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
            known_cost_krw=0.0,
            liability_krw=ticket.reserved_krw,
            status_code=None,
            error_type="APITimeoutError",
            request_id="",
        ),
    )
    # 기존 외곽 finally가 다시 마감해도 이미 닫힌 attempt를 덧쓰지 않는다.
    paid_runtime._settle_paid_phase(
        ticket,
        amount_krw=0.0,
        billing_uncertain=True,
    )
    return attempt_id


def test_usage없는_실패는_전역잠금이_아니라_해당금액의_부채가_된다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="unknown-one",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None

    _record_usage_less_failure(ticket)

    with storage_db.connect() as conn:
        exposure = state_machine.load_exposure(
            conn,
            day=clock.today_kst(),
            bucket_id=spend_store.bucket_id(_BUCKET),
        )
    assert exposure.known_cost_krw == 0.0
    assert exposure.liability_krw == pytest.approx(900.0)
    assert exposure.reservation_krw == 0.0
    assert paid_runtime._BUDGET_STORE_HEALTHY is True
    assert paid_runtime._UNRESOLVED_BUCKETS == set()
    assert paid_runtime.paid_research_block() == (False, "")

    # 같은 통장도 확정액+부채+새 예약이 입장 기준 안이면 다시 쓸 수 있다.
    second = paid_runtime._begin_paid_phase(
        run_id="after-unknown",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert second is not None
    paid_runtime._cancel_paid_phase(second)


def test_보수부채는_그_통장의_입장합계에서는_빠지지_않는다() -> None:
    _cutover()
    first = paid_runtime._begin_paid_phase(
        run_id="full-liability",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=900.0,
        requested_cost_krw=900.0,
    )
    assert first is not None
    _record_usage_less_failure(first)

    assert paid_runtime._begin_paid_phase(
        run_id="blocked-by-real-exposure",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=900.0,
        requested_cost_krw=900.0,
    ) is None


def test_전송전_만료예약은_새입장을_막기전에_스스로_회수한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = dt.datetime(2026, 8, 28, 9, 6, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    monkeypatch.setattr(clock, "today_kst", lambda: fixed_now.date())
    monkeypatch.setattr(clock, "now_kst", lambda: fixed_now)
    monkeypatch.setattr(
        clock,
        "iso_now_kst",
        lambda: fixed_now.isoformat(timespec="seconds"),
    )
    _cutover()
    _begin_expired_phase(run_id="expired-before-dispatch", dispatched=False)

    ticket = paid_runtime._begin_paid_phase(
        run_id="after-expired-before-dispatch",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=1_000.0,
        requested_cost_krw=200.0,
    )

    assert ticket is not None
    with storage_db.connect() as conn:
        expired = state_machine.get_phase(
            conn,
            run_id="expired-before-dispatch",
            phase=SPEND_PHASE_PIPELINE,
        )
        exposure = state_machine.load_exposure(
            conn,
            day=fixed_now.date(),
            bucket_id=spend_store.bucket_id(_BUCKET),
        )
    assert expired.state is state_machine.PhaseState.FAILED
    assert exposure.liability_krw == 0.0
    assert exposure.reservation_krw == pytest.approx(200.0)
    paid_runtime._cancel_paid_phase(ticket)


def test_전송뒤_만료예약은_부채로_남기고_거절돼도_정리를_보존한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = dt.datetime(2026, 8, 28, 9, 6, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    monkeypatch.setattr(clock, "today_kst", lambda: fixed_now.date())
    monkeypatch.setattr(clock, "now_kst", lambda: fixed_now)
    monkeypatch.setattr(
        clock,
        "iso_now_kst",
        lambda: fixed_now.isoformat(timespec="seconds"),
    )
    _cutover()
    _begin_expired_phase(run_id="expired-after-dispatch", dispatched=True)

    rejected = paid_runtime._begin_paid_phase(
        run_id="blocked-after-expired-dispatch",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=1_000.0,
        requested_cost_krw=200.0,
    )

    assert rejected is None
    with storage_db.connect() as conn:
        expired = state_machine.get_phase(
            conn,
            run_id="expired-after-dispatch",
            phase=SPEND_PHASE_PIPELINE,
        )
        exposure = state_machine.load_exposure(
            conn,
            day=fixed_now.date(),
            bucket_id=spend_store.bucket_id(_BUCKET),
        )
    assert expired.state is state_machine.PhaseState.FAILED
    assert exposure.liability_krw == pytest.approx(900.0)
    assert exposure.reservation_krw == 0.0


def test_POST_사전검사는_만료예약과_메모리_합계를_함께_새로고친다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kst = dt.timezone(dt.timedelta(hours=9))
    current = [dt.datetime(2026, 8, 28, 9, 4, tzinfo=kst)]
    monkeypatch.setattr(clock, "today_kst", lambda: current[0].date())
    monkeypatch.setattr(clock, "now_kst", lambda: current[0])
    monkeypatch.setattr(
        clock,
        "iso_now_kst",
        lambda: current[0].isoformat(timespec="seconds"),
    )
    _cutover()
    _begin_expired_phase(run_id="expires-between-posts", dispatched=False)
    paid_runtime._seed_attempt_ledger(current[0].date())
    stored_bucket = spend_store.bucket_id(_BUCKET)
    share_spent = paid_runtime._LINK_SPEND.by_key.get(stored_bucket)
    assert share_spent is not None
    assert share_spent == pytest.approx(900.0)

    current[0] = dt.datetime(2026, 8, 28, 9, 6, tzinfo=kst)

    assert paid_runtime.reap_expired_paid_phases() is True
    assert paid_runtime._LINK_SPEND.by_key.get(stored_bucket, 0.0) == 0.0


def test_외곽_확정비용_fallback은_두번_마감해도_한번만_센다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="known-once",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None

    paid_runtime._settle_paid_phase(ticket, amount_krw=42.0, billing_uncertain=False)
    paid_runtime._settle_paid_phase(ticket, amount_krw=42.0, billing_uncertain=False)

    with storage_db.connect() as conn:
        attempts = state_machine.list_attempts(
            conn,
            run_id=ticket.run_id,
            phase=ticket.phase,
        )
        exposure = state_machine.load_exposure(
            conn,
            day=ticket.day,
            bucket_id=ticket.bucket_id,
        )
    assert len(attempts) == 1
    assert exposure.known_cost_krw == pytest.approx(42.0)
    assert exposure.liability_krw == 0.0
    assert exposure.reservation_krw == 0.0


def test_provider전_취소는_비용이나_부채를_만들지_않는다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="cancel-before-send",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    paid_runtime._cancel_paid_phase(ticket)

    with storage_db.connect() as conn:
        phase = state_machine.get_phase(
            conn,
            run_id=ticket.run_id,
            phase=ticket.phase,
        )
        attempts = state_machine.list_attempts(
            conn,
            run_id=ticket.run_id,
            phase=ticket.phase,
        )
    assert phase.state is state_machine.PhaseState.FAILED
    assert phase.reservation_krw == 0.0
    assert attempts == ()


def test_provider전_원장실패는_보내지_않았으므로_0원으로_마감한다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="planned-not-sent",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "pipeline", 450.0)

    paid_runtime._settle_paid_phase(ticket, amount_krw=0.0, billing_uncertain=True)

    with storage_db.connect() as conn:
        attempt = state_machine.get_attempt(conn, attempt_id=attempt_id)
        exposure = state_machine.load_run_exposure(conn, run_id=ticket.run_id)
    assert attempt.transport_state is state_machine.TransportState.LOCAL_FAILURE
    assert attempt.billing_state is state_machine.BillingState.KNOWN_ZERO
    assert exposure.known_cost_krw == 0.0
    assert exposure.liability_krw == 0.0
    assert exposure.reservation_krw == 0.0


def test_전송의도뒤_관측저장실패는_그_호출예약만_부채로_남긴다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="sent-observation-missing",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "pipeline", 450.0)
    callbacks.heartbeat(attempt_id)
    callbacks.mark_dispatch_intent(attempt_id)

    paid_runtime._settle_paid_phase(ticket, amount_krw=0.0, billing_uncertain=True)

    with storage_db.connect() as conn:
        attempt = state_machine.get_attempt(conn, attempt_id=attempt_id)
        exposure = state_machine.load_run_exposure(conn, run_id=ticket.run_id)
    assert attempt.transport_state is state_machine.TransportState.TRANSPORT_AMBIGUOUS
    assert (
        attempt.billing_state
        is state_machine.BillingState.CONSERVATIVE_LIABILITY
    )
    assert exposure.liability_krw == pytest.approx(450.0)
    assert exposure.reservation_krw == 0.0


def test_재시작때_JSONL최종비용보다_attempt원장이_작으면_조용히_열지않는다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="silent-undercount",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    paid_runtime._settle_paid_phase(
        ticket, amount_krw=20.0, billing_uncertain=False
    )
    record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.GATE_STOPPED, cost_krw=40.0, model="model"),
        1.0,
        run_id=ticket.run_id,
    )

    paid_runtime._seed_ledger()

    assert paid_runtime._LEDGER.spent_krw == pytest.approx(20.0)
    assert paid_runtime._BUDGET_STORE_HEALTHY is False


def test_cutover뒤_재시작검증은_무제한_JSONL대신_SQLite정본을_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="sqlite-observation-source",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    paid_runtime._settle_paid_phase(
        ticket, amount_krw=20.0, billing_uncertain=False
    )
    record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.GATE_STOPPED, cost_krw=40.0, model="model"),
        1.0,
        run_id=ticket.run_id,
    )

    monkeypatch.setattr(
        paid_runtime,
        "read_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cutover 뒤 JSONL 전체 읽기는 금지합니다")
        ),
    )

    paid_runtime._seed_ledger()

    assert paid_runtime._LEDGER.spent_krw == pytest.approx(20.0)
    assert paid_runtime._BUDGET_STORE_HEALTHY is False


def test_cutover뒤_새관측은_SQLite에만_남고_호환_JSONL을_더키우지않는다() -> None:
    _cutover()

    assert record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.NOT_FOUND, cost_krw=0.0, model="model"),
        1.0,
        run_id="sqlite-only-observation",
    )

    assert not records_path().exists()
    with storage_db.connect() as conn:
        stored = lifecycle.read_final(conn, "sqlite-only-observation")
    assert stored is not None


def test_관리자_확인은_attempt와_세가지_행동을_구분해_감사기록한다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="admin-resolve-zero",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    _record_usage_less_failure(ticket)
    items, available = paid_runtime.list_unresolved_spend()
    assert available is True
    assert len(items) == 1
    attempt_id = items[0].attempt_id

    resolved, notice = paid_runtime.resolve_budget_liability(
        attempt_id=attempt_id,
        action=state_machine.ResolutionAction.CONFIRM_ZERO,
        actual_cost_krw=None,
        actor_id="person:admin-digest",
        reason_code="provider-zero-confirmed",
    )

    assert resolved is True
    assert "0원" in notice
    with storage_db.connect() as conn:
        attempt = state_machine.get_attempt(conn, attempt_id=attempt_id)
        remaining = state_machine.list_reconcilable(conn)
    assert attempt.billing_state is state_machine.BillingState.KNOWN_ZERO
    assert attempt.actor_id == "person:admin-digest"
    assert attempt.reason_code == "provider-zero-confirmed"
    assert remaining == ()


def test_관리자_화면은_보수부채를_전역중단과_구분하고_명시적으로_정산한다() -> None:
    _cutover()
    ticket = paid_runtime._begin_paid_phase(
        run_id="admin-route-zero",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_BUCKET,
        cap_krw=3_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    _record_usage_less_failure(ticket)
    items, _available = paid_runtime.list_unresolved_spend()
    attempt_id = items[0].attempt_id

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        csrf = auth_logic.csrf_token_for_session(session.token)
        # ★ 2026-09-02 G-S8 기대값 이전 — 미확정 대사 배너는 `/admin/costs`에 있다.
        page = client.get("/admin/costs")
        assert page.status_code == 200
        assert "이 한 건 때문에 모든 조사를 멈추지 않습니다" in page.text
        assert "실패 단서" in page.text
        assert "APITimeoutError" in page.text
        assert "청구 0원 확인" in page.text

        response = client.post(
            "/admin/budget/settle",
            data={
                "csrf_token": csrf,
                "attempt_id": attempt_id,
                "resolution_action": "CONFIRM_ZERO",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    with storage_db.connect() as conn:
        attempt = state_machine.get_attempt(conn, attempt_id=attempt_id)
    assert attempt.billing_state is state_machine.BillingState.KNOWN_ZERO
