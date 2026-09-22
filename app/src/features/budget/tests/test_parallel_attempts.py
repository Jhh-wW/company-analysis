"""실제 SQLite 다중 시도의 원자 입장·정산·종료·lease 회수 계약."""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from src.features.budget import provider_budget, spend_store, state_machine as ledger
from src.features.budget.constants import SPEND_PHASE_PIPELINE


DAY = dt.date(2026, 9, 22)
START = "2026-09-22T10:00:00+09:00"
DISPATCH = "2026-09-22T10:00:01+09:00"
OUTCOME = "2026-09-22T10:00:02+09:00"
EXPIRY = "2026-09-22T10:05:00+09:00"
OWNER = "worker:parallel"
RUN = "parallel"
ESTIMATE = 100.0
WAIT_SECONDS = 5


@contextmanager
def connection(path):
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "동시정산.sqlite3"
    with connection(path) as conn:
        spend_store.ensure_schema(conn)
        ledger.prepare_cutover(conn, migrated_at=START)
        ledger.begin_phase(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE, day=DAY,
                           bucket="link:parallel", reservation_krw=ESTIMATE * 3,
                           bucket_limit_krw=1000, run_limit_krw=1000,
                           lease_owner_id=OWNER, lease_expires_at=EXPIRY, started_at=START)
    return path


def begin(conn, index, *, maximum=3, estimated=ESTIMATE):
    return ledger.begin_attempt(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE,
                                attempt_id=f"attempt:{index}", provider="anthropic", operation="v2_compose",
                                estimated_krw=estimated, lease_owner_id=OWNER, created_at=START,
                                max_inflight_attempts=maximum)


def dispatch(conn, index):
    ledger.mark_dispatch_intent(conn, attempt_id=f"attempt:{index}",
                                lease_owner_id=OWNER, recorded_at=DISPATCH)


def settle(conn, index, *, unknown=False, owner=OWNER, actual=10.0):
    return ledger.record_attempt_outcome(
        conn, attempt_id=f"attempt:{index}", transport_state=ledger.TransportState.RESPONSE_RECEIVED,
        billing_state=ledger.BillingState.CONSERVATIVE_LIABILITY if unknown else ledger.BillingState.KNOWN_COST,
        known_cost_krw=0.0 if unknown else actual, liability_krw=ESTIMATE if unknown else 0.0,
        close_phase=unknown, phase_succeeded=False, recorded_at=OUTCOME, lease_owner_id=owner,
    )


def test_three_connections_reserve_atomically_and_fourth_cannot_oversubscribe(database):
    start = threading.Barrier(4)

    def invoke(index):
        start.wait(WAIT_SECONDS)
        with connection(database) as conn:
            return begin(conn, index)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(invoke, index) for index in range(4)]
        errors = [future.exception(WAIT_SECONDS) for future in futures]
    assert sum(error is None for error in errors) == 3
    assert sum(isinstance(error, ledger.AttemptStateError) for error in errors) == 1
    with connection(database) as conn:
        attempts = ledger.list_attempts(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE)
        assert sorted(item.attempt_no for item in attempts) == [0, 1, 2]
        assert sum(item.reservation_krw for item in attempts) == 300
        assert ledger.load_run_exposure(conn, run_id=RUN).reservation_krw == 300


def test_parallel_reservations_share_original_money_ceiling(database):
    with connection(database) as conn:
        begin(conn, 0, estimated=160)
    with connection(database) as conn, pytest.raises(ledger.AdmissionLimitExceeded):
        begin(conn, 1, estimated=160)


@pytest.mark.parametrize("unknown_indices", [{0}, {0, 1, 2}])
def test_first_liability_blocks_new_work_but_all_dispatched_outcomes_settle(database, unknown_indices):
    with connection(database) as conn:
        for index in range(3):
            begin(conn, index)
            dispatch(conn, index)
    with connection(database) as conn:
        settle(conn, 0, unknown=True)
        phase = ledger.get_phase(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE)
        assert phase.state is ledger.PhaseState.ACTIVE and phase.lease_owner_id == OWNER
        assert ledger.load_run_exposure(conn, run_id=RUN).admission_exposure_krw == 300
    with connection(database) as conn, pytest.raises(ledger.AttemptStateError):
        begin(conn, 3, estimated=1)
    with connection(database) as conn, pytest.raises(ledger.LeaseOwnershipError):
        settle(conn, 1, owner="worker:stale")
    with connection(database) as conn:
        settle(conn, 2, unknown=2 in unknown_indices)
        settle(conn, 1, unknown=1 in unknown_indices)
        phase = ledger.get_phase(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE)
        assert phase.state is ledger.PhaseState.FAILED and phase.lease_owner_id is None
        exposure = ledger.load_run_exposure(conn, run_id=RUN)
        assert exposure.liability_krw == ESTIMATE * len(unknown_indices)
        assert exposure.known_cost_krw == 10 * (3 - len(unknown_indices))
        assert exposure.reservation_krw == 0
    with connection(database) as conn, pytest.raises(ledger.ActivePhaseError):
        settle(conn, 1)


def test_planned_sibling_cannot_dispatch_after_failure_and_can_cancel_to_zero(database):
    with connection(database) as conn:
        for index in range(3):
            begin(conn, index)
        dispatch(conn, 0)
        settle(conn, 0, unknown=True)
    with connection(database) as conn, pytest.raises(ledger.AttemptStateError):
        dispatch(conn, 1)
    with connection(database) as conn:
        for index in (1, 2):
            ledger.record_pre_dispatch_failure(conn, attempt_id=f"attempt:{index}",
                                               lease_owner_id=OWNER, error_type="Cancelled",
                                               close_phase=False, recorded_at=OUTCOME)
        assert ledger.get_phase(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE).state is ledger.PhaseState.FAILED
        assert ledger.load_run_exposure(conn, run_id=RUN).admission_exposure_krw == ESTIMATE


@pytest.mark.parametrize("first_unknown", [False, True])
def test_expired_lease_recovers_every_dispatched_and_planned_sibling(database, first_unknown):
    with connection(database) as conn:
        for index in range(3):
            begin(conn, index)
        dispatch(conn, 0)
        dispatch(conn, 1)
        if first_unknown:
            settle(conn, 0, unknown=True)
    with connection(database) as conn:
        phase = ledger.expire_phase_lease(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE, observed_at=EXPIRY)
        assert phase.state is ledger.PhaseState.FAILED
        attempts = ledger.list_attempts(conn, run_id=RUN, phase=SPEND_PHASE_PIPELINE)
        assert [item.billing_state for item in attempts] == [ledger.BillingState.CONSERVATIVE_LIABILITY,
                                                           ledger.BillingState.CONSERVATIVE_LIABILITY,
                                                           ledger.BillingState.KNOWN_ZERO]
        assert ledger.load_run_exposure(conn, run_id=RUN).admission_exposure_krw == ESTIMATE * 2
    with connection(database) as conn, pytest.raises(ledger.ActivePhaseError):
        settle(conn, 1)


def test_actual_overrun_does_not_release_sibling_reservations(database):
    with connection(database) as conn:
        for index in range(3):
            begin(conn, index)
            dispatch(conn, index)
        settle(conn, 0, actual=400)
        exposure = ledger.load_run_exposure(conn, run_id=RUN)
        assert exposure.known_cost_krw == 400 and exposure.reservation_krw == 200
    with connection(database) as conn, pytest.raises(ledger.AdmissionLimitExceeded):
        begin(conn, 3, estimated=1)
    with connection(database) as conn:
        settle(conn, 1, actual=10)
        settle(conn, 2, actual=10)
        exposure = ledger.load_run_exposure(conn, run_id=RUN)
        assert exposure.known_cost_krw == 420 and exposure.reservation_krw == 0
    with connection(database) as conn, pytest.raises(ledger.AdmissionLimitExceeded):
        begin(conn, 4, estimated=1)


def test_waiting_money_reservation_checks_cancellation_without_releasing_inflight_cost():
    entered, cancel = threading.Event(), threading.Event()
    budget = provider_budget.ProviderBudget(100)
    reservation = budget.reserve_call(model="claude-haiku-4-5", input_tokens_upper=0, max_tokens=10000)

    def check():
        entered.set()
        if cancel.is_set():
            raise RuntimeError("대기 요청 취소")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(budget.reserve_call, model="claude-haiku-4-5", input_tokens_upper=0,
                             max_tokens=10000, wait_for_pending=True, check=check)
        assert entered.wait(WAIT_SECONDS)
        cancel.set()
        with pytest.raises(RuntimeError, match="대기 요청 취소"):
            future.result(WAIT_SECONDS)
    assert budget.accounted_krw == reservation.estimated_krw
    budget.settle_call(reservation, actual_krw=10)
    assert budget.accounted_krw == 10


def test_actual_budget_exhaustion_is_not_waited_away():
    budget = provider_budget.ProviderBudget(100)
    reservation = budget.reserve_call(model="claude-haiku-4-5", input_tokens_upper=0, max_tokens=10000)
    budget.settle_call(reservation, actual_krw=50)
    with pytest.raises(provider_budget.ProviderBudgetExceeded):
        budget.reserve_call(model="claude-haiku-4-5", input_tokens_upper=0,
                            max_tokens=10000, wait_for_pending=True)
