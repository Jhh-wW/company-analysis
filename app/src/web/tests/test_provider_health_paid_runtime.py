"""provider별 유한 차단기가 실제 비용 attempt 경계와 한 몸인지 검증한다."""

from __future__ import annotations

import datetime as dt
import sys
import types
from io import BytesIO

import pytest
from PIL import Image

from src.core import clock
from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)
from src.features.budget import spend_store, state_machine
from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.provider_health import constants as health_constants
from src.features.provider_health import store as health_store
from src.features.posting_image import constants as posting_constants
from src.features.posting_image import logic as posting_image_logic
from src.features.storage import db as storage_db
from src.web import paid_runtime


_SHARE_KEY = "link:provider-health-runtime"


def _cutover() -> None:
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()


def _ticket(run_id: str) -> paid_runtime.PaidPhase:
    ticket = paid_runtime._begin_paid_phase(
        run_id=run_id,
        phase=SPEND_PHASE_PIPELINE,
        share_key=_SHARE_KEY,
        cap_krw=9_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None
    return ticket


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output, "PNG")
    return output.getvalue()


def _timeout(ticket: paid_runtime.PaidPhase) -> str:
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "writer", 450.0)
    callbacks.heartbeat(attempt_id)
    callbacks.mark_dispatch_intent(attempt_id)
    callbacks.record_observation(
        attempt_id,
        ProviderObservation(
            transport_state=TransportState.TRANSPORT_AMBIGUOUS,
            billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
            known_cost_krw=0.0,
            liability_krw=450.0,
            status_code=None,
            error_type="APITimeoutError",
            request_id="",
        ),
    )
    paid_runtime._settle_paid_phase(
        ticket, amount_krw=0.0, billing_uncertain=True
    )
    return attempt_id


def test_두번의_타임아웃은_anthropic만_유한차단한다() -> None:
    _cutover()

    _timeout(_ticket("anthropic-timeout-one"))
    _timeout(_ticket("anthropic-timeout-two"))

    with storage_db.connect() as conn:
        anthropic = health_store.get_state(
            conn, health_constants.PROVIDER_ANTHROPIC
        )
        google = health_store.peek_permission(
            conn,
            health_constants.PROVIDER_GOOGLE_PLACES,
            now_iso=clock.iso_now_kst(),
        )
    assert anthropic.state is health_store.ProviderHealthState.OPEN
    assert anthropic.consecutive_failures == 2
    assert anthropic.last_failure_kind == health_store.ProviderFailureKind.TIMEOUT.value
    assert google.allowed is True


def test_열린_provider는_attempt를_0원으로_닫고_전송전에_거부한다() -> None:
    _cutover()
    now_iso = clock.iso_now_kst()
    with storage_db.connect() as conn:
        health_store.ensure_schema(conn)
        health_store.record_failure(
            conn,
            health_constants.PROVIDER_ANTHROPIC,
            failure_kind=health_store.ProviderFailureKind.TIMEOUT,
            now_iso=now_iso,
        )
        health_store.record_failure(
            conn,
            health_constants.PROVIDER_ANTHROPIC,
            failure_kind=health_store.ProviderFailureKind.TIMEOUT,
            now_iso=now_iso,
        )

    ticket = _ticket("anthropic-open-known-zero")
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    with pytest.raises(paid_runtime.ProviderCircuitOpen):
        callbacks.begin_attempt("anthropic", "writer", 450.0)

    with storage_db.connect() as conn:
        phase = state_machine.get_phase(
            conn, run_id=ticket.run_id, phase=ticket.phase
        )
        attempts = state_machine.list_attempts(
            conn, run_id=ticket.run_id, phase=ticket.phase
        )
        exposure = state_machine.load_run_exposure(conn, run_id=ticket.run_id)
    assert phase.state is state_machine.PhaseState.FAILED
    assert len(attempts) == 1
    assert attempts[0].transport_state is state_machine.TransportState.LOCAL_FAILURE
    assert attempts[0].billing_state is state_machine.BillingState.KNOWN_ZERO
    assert exposure.known_cost_krw == 0.0
    assert exposure.liability_krw == 0.0
    assert exposure.reservation_krw == 0.0


def test_usage누락_2xx는_비용부채여도_provider건강은_성공이다() -> None:
    _cutover()
    with storage_db.connect() as conn:
        health_store.ensure_schema(conn)
        health_store.record_failure(
            conn,
            health_constants.PROVIDER_ANTHROPIC,
            failure_kind=health_store.ProviderFailureKind.PROVIDER_RESPONSE,
            now_iso=clock.iso_now_kst(),
        )

    ticket = _ticket("missing-usage-provider-success")
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "writer", 450.0)
    callbacks.heartbeat(attempt_id)
    callbacks.mark_dispatch_intent(attempt_id)
    callbacks.record_observation(
        attempt_id,
        ProviderObservation(
            transport_state=TransportState.RESPONSE_RECEIVED,
            billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
            known_cost_krw=0.0,
            liability_krw=450.0,
            status_code=200,
            error_type="MissingUsage",
            request_id="request:one",
        ),
    )

    with storage_db.connect() as conn:
        attempt = state_machine.get_attempt(conn, attempt_id=attempt_id)
        provider = health_store.get_state(
            conn, health_constants.PROVIDER_ANTHROPIC
        )
    assert attempt.billing_state is state_machine.BillingState.CONSERVATIVE_LIABILITY
    assert provider.state is health_store.ProviderHealthState.HEALTHY
    assert provider.consecutive_failures == 0


def test_서로다른_400_두건은_provider_전체를_차단하지_않는다() -> None:
    _cutover()

    for suffix in ("one", "two"):
        ticket = _ticket(f"bad-request-{suffix}")
        callbacks = paid_runtime._provider_attempt_callbacks(ticket)
        attempt_id = callbacks.begin_attempt("anthropic", "writer", 450.0)
        callbacks.heartbeat(attempt_id)
        callbacks.mark_dispatch_intent(attempt_id)
        callbacks.record_observation(
            attempt_id,
            ProviderObservation(
                transport_state=TransportState.RESPONSE_RECEIVED,
                billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
                known_cost_krw=0.0,
                liability_krw=450.0,
                status_code=400,
                error_type="BadRequestError",
                request_id=f"request:{suffix}",
            ),
        )
        paid_runtime._settle_paid_phase(
            ticket, amount_krw=0.0, billing_uncertain=True
        )

    with storage_db.connect() as conn:
        provider = health_store.get_state(
            conn, health_constants.PROVIDER_ANTHROPIC
        )
    assert provider.state is health_store.ProviderHealthState.HEALTHY
    assert provider.consecutive_failures == 0


def test_인증과_계정설정_4xx는_요청오류와_달리_provider장애로_분리한다() -> None:
    _cutover()

    for suffix, status in (("unauthorized", 401), ("forbidden", 403)):
        ticket = _ticket(f"account-config-{suffix}")
        callbacks = paid_runtime._provider_attempt_callbacks(ticket)
        attempt_id = callbacks.begin_attempt("anthropic", "writer", 450.0)
        callbacks.heartbeat(attempt_id)
        callbacks.mark_dispatch_intent(attempt_id)
        callbacks.record_observation(
            attempt_id,
            ProviderObservation(
                transport_state=TransportState.RESPONSE_RECEIVED,
                billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
                known_cost_krw=0.0,
                liability_krw=450.0,
                status_code=status,
                error_type="APIStatusError",
                request_id=f"request:{suffix}",
            ),
        )
        paid_runtime._settle_paid_phase(
            ticket, amount_krw=0.0, billing_uncertain=True
        )

    with storage_db.connect() as conn:
        provider = health_store.get_state(
            conn, health_constants.PROVIDER_ANTHROPIC
        )
    assert provider.state is health_store.ProviderHealthState.OPEN
    assert provider.consecutive_failures == 2
    assert (
        provider.last_failure_kind
        == health_store.ProviderFailureKind.ACCOUNT_CONFIGURATION.value
    )


def test_probe획득과_dispatch기록은_한거래라_db실패시_probing이_남지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cutover()
    past = clock.now_kst() - dt.timedelta(
        seconds=health_constants.OPEN_COOLDOWN_SEC + 2
    )
    with storage_db.connect() as conn:
        health_store.ensure_schema(conn)
        health_store.record_failure(
            conn,
            health_constants.PROVIDER_ANTHROPIC,
            failure_kind=health_store.ProviderFailureKind.TIMEOUT,
            now_iso=past.isoformat(),
        )
        health_store.record_failure(
            conn,
            health_constants.PROVIDER_ANTHROPIC,
            failure_kind=health_store.ProviderFailureKind.TIMEOUT,
            now_iso=(past + dt.timedelta(seconds=1)).isoformat(),
        )

    ticket = _ticket("probe-dispatch-rollback")
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "writer", 450.0)

    def fail_dispatch(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise state_machine.AttemptStateError("강제 dispatch 기록 실패")

    monkeypatch.setattr(state_machine, "mark_dispatch_intent", fail_dispatch)
    with pytest.raises(state_machine.AttemptStateError):
        callbacks.mark_dispatch_intent(attempt_id)

    with storage_db.connect() as conn:
        state = health_store.get_state(
            conn, health_constants.PROVIDER_ANTHROPIC
        )
        permission = health_store.peek_permission(
            conn,
            health_constants.PROVIDER_ANTHROPIC,
            now_iso=clock.iso_now_kst(),
        )
    assert state.state is health_store.ProviderHealthState.OPEN
    assert state.probe_lease_until == ""
    assert permission.allowed is True
    assert permission.is_probe is True
    paid_runtime._cancel_paid_phase(ticket)


def test_실제_OCR배선도_db_attempt를_거쳐_비용과_provider건강을_함께_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cutover()
    usage = types.SimpleNamespace(input_tokens=1_000, output_tokens=100)
    response = types.SimpleNamespace(
        model=posting_constants.DEFAULT_EXTRACT_MODEL,
        usage=usage,
        content=[
            types.SimpleNamespace(
                type="text",
                text='{"full_text":"채용 공고","is_job_posting":true}',
            )
        ],
        stop_reason="end_turn",
    )
    module = types.ModuleType("anthropic")
    module.Anthropic = lambda **_kwargs: types.SimpleNamespace(
        messages=types.SimpleNamespace(create=lambda **_call: response)
    )
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv(posting_constants.ENV_ANTHROPIC_API_KEY, "가짜-시험키")

    ticket = paid_runtime._begin_paid_phase(
        run_id="ocr-real-gateway",
        phase=paid_runtime.SPEND_PHASE_OCR,
        share_key=_SHARE_KEY,
        cap_krw=9_000.0,
        requested_cost_krw=900.0,
    )
    assert ticket is not None

    result = paid_runtime._call_paid_provider(
        ticket, posting_image_logic.default_extract, [_png()]
    )

    with storage_db.connect() as conn:
        attempts = state_machine.list_attempts(
            conn, run_id=ticket.run_id, phase=ticket.phase
        )
        provider = health_store.get_state(
            conn, health_constants.PROVIDER_ANTHROPIC
        )
    assert result.text == "채용 공고"
    assert len(attempts) == 1
    assert attempts[0].provider == health_constants.PROVIDER_ANTHROPIC
    assert attempts[0].transport_state is state_machine.TransportState.RESPONSE_RECEIVED
    assert attempts[0].billing_state is state_machine.BillingState.KNOWN_COST
    assert provider.state is health_store.ProviderHealthState.HEALTHY
    paid_runtime._settle_paid_phase(
        ticket, amount_krw=result.cost_krw, billing_uncertain=False
    )


def test_건강상태_기록실패는_비용결과도_같이_rollback한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cutover()
    ticket = _ticket("health-write-rollback")
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    attempt_id = callbacks.begin_attempt("anthropic", "writer", 450.0)
    callbacks.heartbeat(attempt_id)
    callbacks.mark_dispatch_intent(attempt_id)

    def fail_health_write(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise health_store.ProviderHealthWriteConflict("강제 충돌")

    monkeypatch.setattr(health_store, "record_failure", fail_health_write)
    with pytest.raises(health_store.ProviderHealthWriteConflict):
        callbacks.record_observation(
            attempt_id,
            ProviderObservation(
                transport_state=TransportState.RESPONSE_RECEIVED,
                billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
                known_cost_krw=0.0,
                liability_krw=450.0,
                status_code=429,
                error_type="APIStatusError",
                request_id="request:rate-limit",
            ),
        )

    with storage_db.connect() as conn:
        attempt = state_machine.get_attempt(conn, attempt_id=attempt_id)
        state = health_store.get_state(conn, health_constants.PROVIDER_ANTHROPIC)
    assert attempt.transport_state is state_machine.TransportState.DISPATCH_INTENT_RECORDED
    assert attempt.billing_state is state_machine.BillingState.RESERVED
    assert state.state is health_store.ProviderHealthState.HEALTHY

    # 실제 전송 뒤 관측 저장이 끊긴 경우의 기존 보수부채 회수도 그대로 작동한다.
    paid_runtime._settle_paid_phase(
        ticket, amount_krw=0.0, billing_uncertain=True
    )


def test_차단기_권한확인실패는_attempt까지_원자적으로_rollback한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cutover()
    ticket = _ticket("health-preflight-rollback")
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)

    def fail_permission(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise health_store.ProviderHealthWriteConflict("강제 충돌")

    monkeypatch.setattr(health_store, "peek_permission", fail_permission)
    with pytest.raises(health_store.ProviderHealthWriteConflict):
        callbacks.begin_attempt("anthropic", "writer", 450.0)

    with storage_db.connect() as conn:
        attempts = state_machine.list_attempts(
            conn, run_id=ticket.run_id, phase=ticket.phase
        )
        phase = state_machine.get_phase(
            conn, run_id=ticket.run_id, phase=ticket.phase
        )
    assert attempts == ()
    assert phase.state is state_machine.PhaseState.ACTIVE
    paid_runtime._cancel_paid_phase(ticket)
