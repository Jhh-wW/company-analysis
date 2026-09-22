"""실제 계량 경계에서 세 공급자 호출의 예약·문맥·실패·취소를 재현한다."""

from __future__ import annotations

import contextvars
import threading
from contextlib import ExitStack
from dataclasses import replace
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from types import SimpleNamespace

import pytest

from src.core.pricing import usage_cost_krw
from src.core.provider_gateway import attempt_context, concurrency, gateway
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.pipeline import real


MODEL = "claude-haiku-4-5"
WAIT_SECONDS = 5
TOTAL_BUDGET = 10000
OUTPUT_CAP = 4000
INPUT_TOKENS = 100
CALL_TAG = contextvars.ContextVar("시험_호출_번호", default=-1)


class AttemptRecorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.starts = {}
        self.observations = {}
        self.first_recorded = threading.Event()

    def begin(self, provider, stage, reserved):
        with self.lock:
            token = len(self.starts) + 1
            self.starts[token] = (CALL_TAG.get(), provider, stage, reserved)
            return token

    def record(self, token, observation):
        with self.lock:
            self.observations[token] = observation
            if self.starts[token][0] == 1:
                self.first_recorded.set()

    def callbacks(self):
        return ProviderAttemptCallbacks(self.begin, lambda _: None, lambda _: None, self.record,
                                        max_parallel_calls=3)


def response(index=0):
    return SimpleNamespace(
        model=MODEL, stop_reason="end_turn", content=[SimpleNamespace(text=f"응답 {index}")],
        usage=SimpleNamespace(input_tokens=INPUT_TOKENS + index, output_tokens=10 + index,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )


def metered(messages):
    engine = real._MeteredEngine(SimpleNamespace(MODEL=MODEL))
    return engine, engine.meter_client(SimpleNamespace(messages=messages))


@pytest.fixture(autouse=True)
def isolated_slots(monkeypatch):
    monkeypatch.setenv(concurrency.PROVIDER_MAX_CONCURRENT_CALLS_ENV, "5")
    monkeypatch.delenv(real.WRITER_PARALLEL_CALLS_ENV, raising=False)
    monkeypatch.setattr(real, "PIPELINE_PROVIDER_LIMITER", concurrency.ProviderCallLimiter())


@pytest.mark.parametrize("failure", [None, "known", "unknown"])
def test_three_overlapping_calls_keep_stage_cache_reserve_usage_and_failure_isolated(failure):
    recorder = AttemptRecorder()
    count_barrier, send_barrier = threading.Barrier(3), threading.Barrier(3)
    captured, restored, requests = {}, {}, {}

    class Messages:
        def count_tokens(self, **kwargs):
            index = CALL_TAG.get()
            count_barrier.wait(WAIT_SECONDS)
            captured[index] = (engine.current_stage, engine.prompt_cache_enabled,
                               engine.reserved_calls, real.provider_budget.current())
            return SimpleNamespace(input_tokens=INPUT_TOKENS + index)

        def create(self, **kwargs):
            index = CALL_TAG.get()
            requests[index] = kwargs
            send_barrier.wait(WAIT_SECONDS)
            if index != 1:
                assert recorder.first_recorded.wait(WAIT_SECONDS)
            if index == 1 and failure:
                error = RuntimeError("시험용 공급자 실패")
                if failure == "known":
                    error.usage = response(index).usage
                raise error
            return response(index)

    engine, client = metered(Messages())
    engine.set_stage("부모 단계")

    def invoke(index):
        CALL_TAG.set(index)
        try:
            with engine.stage_context(f"단계 {index}", prompt_cache=index == 1, reserved_calls=index):
                return client.messages.create(model=MODEL, max_tokens=OUTPUT_CAP,
                                              messages=[{"role": "user", "content": f"입력 {index}"}])
        finally:
            restored[index] = (engine.current_stage, engine.prompt_cache_enabled, engine.reserved_calls)

    with real.provider_budget.activate(TOTAL_BUDGET) as budget, attempt_context.activate(recorder.callbacks()):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(contextvars.copy_context().run, invoke, index) for index in range(3)]
            for index, future in enumerate(futures):
                if index == 1 and failure:
                    with pytest.raises(gateway.ProviderCallFailed):
                        future.result(WAIT_SECONDS)
                else:
                    assert future.result(WAIT_SECONDS).content[0].text == f"응답 {index}"
        assert len(recorder.starts) == len(recorder.observations) == len(requests) == 3
        for index in range(3):
            assert captured[index] == (f"단계 {index}", index == 1, index, budget)
            assert restored[index] == ("부모 단계", False, 0)
            request = requests[index]
            assert request["model"] == MODEL and request["max_tokens"] == OUTPUT_CAP
            assert isinstance(request["messages"][0]["content"], list) is (index == 1)
        for index, provider, stage, reserved in recorder.starts.values():
            estimated_input = INPUT_TOKENS + index + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
            if index == 1:
                estimated_input = (estimated_input * 5 + 3) // 4
            assert reserved == usage_cost_krw(MODEL, estimated_input, OUTPUT_CAP)
            assert stage == f"단계 {index}" and provider == "anthropic"
        expected = sum(item.known_cost_krw + item.liability_krw for item in recorder.observations.values())
        assert budget.accounted_krw == pytest.approx(expected)
        assert len(budget._pending) == int(failure == "unknown")
    usages = {item["stage"]: item for item in engine.usages}
    assert len(usages) == (2 if failure == "unknown" else 3)
    for index in range(3):
        if index == 1 and failure == "unknown":
            continue
        usage = usages[f"단계 {index}"]
        assert usage["in"] == INPUT_TOKENS + index and usage["out"] == 10 + index
        assert usage["cost_krw"] == usage_cost_krw(MODEL, INPUT_TOKENS + index, 10 + index)
        assert usage.get("failed", False) is (index == 1 and failure == "known")
    assert engine.billing_uncertain is (failure == "unknown")
    assert engine.current_stage == "부모 단계"
    assert engine.available_provider_calls(reserved_calls=0) == 17


def test_parallel_money_admission_allows_only_two_reservations():
    release = threading.Event()
    recorder = AttemptRecorder()
    sends = []

    class Messages:
        def count_tokens(self, **kwargs):
            return SimpleNamespace(input_tokens=INPUT_TOKENS)

        def create(self, **kwargs):
            sends.append(1)
            assert release.wait(WAIT_SECONDS)
            return response()

    engine, client = metered(Messages())
    estimate = usage_cost_krw(MODEL, INPUT_TOKENS + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, OUTPUT_CAP)
    with real.provider_budget.activate(estimate * 2) as budget, attempt_context.activate(recorder.callbacks()):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(contextvars.copy_context().run, client.messages.create,
                                   model=MODEL, max_tokens=OUTPUT_CAP, messages=[]) for _ in range(3)]
            try:
                done, _ = wait(futures, timeout=WAIT_SECONDS, return_when=FIRST_COMPLETED)
                assert len(done) == 1
                assert isinstance(next(iter(done)).exception(), real.provider_budget.ProviderBudgetExceeded)
                assert budget.accounted_krw == pytest.approx(estimate * 2)
            finally:
                release.set()
            assert sum(future.exception() is None for future in futures) == 2
        assert len(sends) == len(recorder.starts) == 2
        assert not budget._pending
        assert budget.accounted_krw == pytest.approx(2 * usage_cost_krw(MODEL, INPUT_TOKENS, 10))


@pytest.mark.parametrize("unknown", [False, True, "interrupt"])
def test_parallel_writer_waits_for_temporary_reservations_without_false_budget_failure(unknown):
    recorder = AttemptRecorder()
    counted, first_started, release = (threading.Event() for _ in range(3))
    lock = threading.Lock()
    count = 0
    sends = []

    class Messages:
        def count_tokens(self, **kwargs):
            nonlocal count
            with lock:
                count += 1
                if count == 3:
                    counted.set()
            return SimpleNamespace(input_tokens=INPUT_TOKENS)

        def create(self, **kwargs):
            sends.append(1)
            first_started.set()
            assert release.wait(WAIT_SECONDS)
            if unknown == "interrupt":
                raise KeyboardInterrupt("공급자 실행 중 취소")
            if unknown:
                raise RuntimeError("비용 미확정 시험")
            return response()

    engine, client = metered(Messages())
    estimate = usage_cost_krw(MODEL, INPUT_TOKENS + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, OUTPUT_CAP)
    actual = usage_cost_krw(MODEL, INPUT_TOKENS, 10)
    # 동시 2개 예약은 안 되지만 같은 모델·입력을 순차로 3회 쓰기에는 충분하다.
    with real.provider_budget.activate(estimate + actual * 3) as budget, attempt_context.activate(recorder.callbacks()):
        ask = real._v2_ask_via_provider(engine, client, stage="v2_compose", max_tokens=OUTPUT_CAP)
        assert ask.parallel_safe
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(contextvars.copy_context().run, ask, "같은 작성 입력") for _ in range(3)]
            try:
                assert counted.wait(WAIT_SECONDS) and first_started.wait(WAIT_SECONDS)
                assert sends == [1]
                assert budget.accounted_krw == pytest.approx(estimate)
            finally:
                release.set()
            errors = [future.exception(WAIT_SECONDS) for future in futures]
        assert len(sends) == (1 if unknown else 3)
        assert sum(error is not None for error in errors) == (3 if unknown else 0)
        assert budget.accounted_krw == pytest.approx(estimate if unknown else actual * 3)


def test_parallel_call_count_preserves_mandatory_tail():
    recorder = AttemptRecorder()
    sends = []
    messages = SimpleNamespace(create=lambda **_: sends.append(1) or response())
    engine, client = metered(messages)
    for _ in range(17):
        engine.reserve_provider_call()
    start = threading.Barrier(3)

    def invoke():
        start.wait(WAIT_SECONDS)
        with engine.stage_context("선택 단계", reserved_calls=2):
            return client.messages.create(model=MODEL, max_tokens=OUTPUT_CAP, messages=[])

    with real.provider_budget.activate(TOTAL_BUDGET), attempt_context.activate(recorder.callbacks()):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(contextvars.copy_context().run, invoke) for _ in range(3)]
            errors = [future.exception(WAIT_SECONDS) for future in futures]
    assert len(sends) == 1
    assert sum(isinstance(error, real.provider_budget.RequestCallLimitReached) for error in errors) == 2
    assert engine.available_provider_calls(reserved_calls=0) == 2


def test_waiting_call_cancels_without_reservation_and_inflight_call_still_settles(monkeypatch):
    monkeypatch.setenv(concurrency.PROVIDER_MAX_CONCURRENT_CALLS_ENV, "1")
    cancel, started, release, queued = (threading.Event() for _ in range(4))
    recorder = AttemptRecorder()
    sends = []

    def check():
        if CALL_TAG.get() == 1:
            queued.set()
        if cancel.is_set():
            raise real.generation_coordination.GenerationWaitCancelled("시험 요청 취소")

    class Messages:
        def create(self, **kwargs):
            sends.append(1)
            started.set()
            assert release.wait(WAIT_SECONDS)
            return response()

    engine, client = metered(Messages())
    coordination_token = real.generation_coordination._CURRENT.set(SimpleNamespace(ensure_paid_phase=check))

    def invoke(index):
        CALL_TAG.set(index)
        return client.messages.create(model=MODEL, max_tokens=OUTPUT_CAP, messages=[])

    try:
        with real.provider_budget.activate(TOTAL_BUDGET) as budget, attempt_context.activate(recorder.callbacks()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(contextvars.copy_context().run, invoke, 0)
                assert started.wait(WAIT_SECONDS)
                second = pool.submit(contextvars.copy_context().run, invoke, 1)
                try:
                    assert queued.wait(WAIT_SECONDS)
                    cancel.set()
                    with pytest.raises(real.generation_coordination.GenerationWaitCancelled):
                        second.result(WAIT_SECONDS)
                finally:
                    release.set()
                first.result(WAIT_SECONDS)
            assert budget.accounted_krw == pytest.approx(usage_cost_krw(MODEL, INPUT_TOKENS, 10))
            assert len(sends) == len(recorder.starts) == len(recorder.observations) == 1
            assert engine.available_provider_calls(reserved_calls=0) == 19
    finally:
        real.generation_coordination._CURRENT.reset(coordination_token)


def test_writer_capability_requires_parent_budget_attempt_and_resolved_metered_client():
    recorder = AttemptRecorder()
    engine, client = metered(SimpleNamespace(create=lambda **_: response()))
    make_ask = lambda value: real._v2_ask_via_provider(engine, value, stage="v2_compose", max_tokens=OUTPUT_CAP)
    assert make_ask(client).parallel_safe is False
    with real.provider_budget.activate(TOTAL_BUDGET), attempt_context.activate(recorder.callbacks()):
        ask = make_ask(client)
        assert ask.parallel_safe is True and ask.max_parallel_calls == 3
        deferred = real._DeferredMeteredClient(lambda: client)
        assert make_ask(deferred).parallel_safe is False
        assert deferred.messages is client.messages
        assert make_ask(deferred).parallel_safe is True
        assert make_ask(SimpleNamespace(messages=client.messages)).parallel_safe is False


@pytest.mark.parametrize("value,expected", [("1", 1), ("2", 2), ("3", 3), ("0", 1), ("4", 1), ("오류", 1)])
def test_writer_can_roll_back_to_serial_without_disabling_metering(monkeypatch, value, expected):
    monkeypatch.setenv(real.WRITER_PARALLEL_CALLS_ENV, value)
    engine, client = metered(SimpleNamespace(create=lambda **_: response()))
    with real.provider_budget.activate(TOTAL_BUDGET), attempt_context.activate(AttemptRecorder().callbacks()):
        ask = real._v2_ask_via_provider(engine, client, stage="v2_compose", max_tokens=OUTPUT_CAP)
        assert ask.parallel_safe and ask.max_parallel_calls == expected
        ask.prepare_parallel()
        assert ask.max_parallel_calls == expected


def test_prepare_parallel_opens_budget_and_deferred_client_in_parent_context():
    recorder = AttemptRecorder()
    parent_thread = threading.get_ident()
    prepared = []
    stack = ExitStack()

    def prepare():
        assert threading.get_ident() == parent_thread
        if not prepared:
            stack.enter_context(real.provider_budget.activate(TOTAL_BUDGET))
            stack.enter_context(attempt_context.activate(recorder.callbacks()))
            prepared.append(True)

    engine, client = metered(SimpleNamespace(create=lambda **_: response()))
    deferred = real._DeferredMeteredClient(lambda: client)
    token = real.generation_coordination._CURRENT.set(SimpleNamespace(ensure_paid_phase=prepare))
    try:
        ask = real._v2_ask_via_provider(engine, deferred, stage="v2_compose", max_tokens=OUTPUT_CAP)
        assert not ask.parallel_safe
        ask.prepare_parallel()
        assert ask.parallel_safe and ask.max_parallel_calls == 3
        assert deferred._resolved is client
        assert not recorder.starts
    finally:
        stack.close()
        real.generation_coordination._CURRENT.reset(token)


def test_global_slots_bound_separate_report_engines_without_serializing_network(monkeypatch):
    monkeypatch.setenv(concurrency.PROVIDER_MAX_CONCURRENT_CALLS_ENV, "3")
    first_wave, all_waiting, release = (threading.Event() for _ in range(3))
    lock = threading.Lock()
    checked, sends = set(), []
    active = 0
    maximum = 0

    def check():
        with lock:
            checked.add(CALL_TAG.get())
            if len(checked) == 6:
                all_waiting.set()

    class Messages:
        def create(self, **kwargs):
            nonlocal active, maximum
            with lock:
                sends.append(CALL_TAG.get())
                active += 1
                maximum = max(maximum, active)
                if active == 3:
                    first_wave.set()
            try:
                assert release.wait(WAIT_SECONDS)
                return response()
            finally:
                with lock:
                    active -= 1

    def invoke(index):
        CALL_TAG.set(index)
        recorder = AttemptRecorder()
        engine, client = metered(Messages())
        with real.provider_budget.activate(TOTAL_BUDGET), attempt_context.activate(recorder.callbacks()):
            return client.messages.create(model=MODEL, max_tokens=OUTPUT_CAP, messages=[])

    token = real.generation_coordination._CURRENT.set(SimpleNamespace(ensure_paid_phase=check))
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(contextvars.copy_context().run, invoke, index) for index in range(6)]
            try:
                assert first_wave.wait(WAIT_SECONDS) and all_waiting.wait(WAIT_SECONDS)
                assert len(sends) == maximum == 3
            finally:
                release.set()
            assert all(future.result(WAIT_SECONDS) is not None for future in futures)
        assert len(sends) == 6 and maximum == 3
    finally:
        real.generation_coordination._CURRENT.reset(token)


@pytest.mark.parametrize("outcome", ["success", "known_failure", "unknown_failure", "cancel"])
def test_real_sqlite_runtime_records_three_overlapping_requests_and_drains_failure(outcome):
    # 네트워크만 가짜이며 실제 paid runtime·차단기·SQLite 정산 callback을 쓴다.
    from src.web import paid_runtime

    ledger = paid_runtime.state_machine
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()
    ticket = paid_runtime._begin_paid_phase(
        run_id="parallel-provider", phase=paid_runtime.SPEND_PHASE_PIPELINE,
        share_key="link:parallel-provider", cap_krw=9000, requested_cost_krw=900,
    )
    assert ticket is not None
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    first_recorded, cancelled = threading.Event(), threading.Event()
    sends = threading.Barrier(3)
    observations = {}

    def record(token, observation):
        callbacks.record_observation(token, observation)
        observations[CALL_TAG.get()] = observation
        if CALL_TAG.get() == 1:
            first_recorded.set()

    def check():
        if cancelled.is_set():
            raise real.generation_coordination.GenerationWaitCancelled("진행 중 요청 취소")

    class Messages:
        def count_tokens(self, **kwargs):
            return SimpleNamespace(input_tokens=INPUT_TOKENS)

        def create(self, **kwargs):
            index = CALL_TAG.get()
            sends.wait(WAIT_SECONDS)
            if index == 1:
                if outcome == "cancel":
                    cancelled.set()
                if outcome.endswith("failure"):
                    error = RuntimeError("가짜 공급자 실패")
                    if outcome == "known_failure":
                        error.usage = response(index).usage
                    raise error
            else:
                assert first_recorded.wait(WAIT_SECONDS)
            return response(index)

    engine, client = metered(Messages())
    coordination_token = real.generation_coordination._CURRENT.set(SimpleNamespace(ensure_paid_phase=check))
    try:
        with real.provider_budget.activate(ticket.reserved_krw) as budget, attempt_context.activate(
            replace(callbacks, record_observation=record),
        ):
            ask = real._v2_ask_via_provider(engine, client, stage="v2_compose", max_tokens=OUTPUT_CAP)
            assert ask.parallel_safe and ask.max_parallel_calls == 3

            def invoke(index):
                CALL_TAG.set(index)
                return ask(f"시험 입력 {index}")

            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(contextvars.copy_context().run, invoke, index) for index in range(3)]
                errors = [future.exception(WAIT_SECONDS) for future in futures]
            assert sum(error is not None for error in errors) == int(outcome.endswith("failure"))
            assert len(observations) == 3
            assert budget.accounted_krw == pytest.approx(sum(
                item.known_cost_krw + item.liability_krw for item in observations.values()
            ))
            if outcome == "cancel":
                with pytest.raises(Exception) as captured:
                    ask("취소 뒤 추가 요청")
                assert isinstance(captured.value.cause, real.generation_coordination.GenerationWaitCancelled)
        paid_runtime._settle_paid_phase(
            ticket, amount_krw=sum(item["cost_krw"] for item in engine.usages),
            billing_uncertain=engine.billing_uncertain,
        )
        with paid_runtime.storage_db.connect() as conn:
            attempts = ledger.list_attempts(conn, run_id=ticket.run_id, phase=ticket.phase)
            phase = ledger.get_phase(conn, run_id=ticket.run_id, phase=ticket.phase)
            exposure = ledger.load_run_exposure(conn, run_id=ticket.run_id)
        assert sorted(item.attempt_no for item in attempts) == [0, 1, 2]
        assert all(item.billing_state is not ledger.BillingState.RESERVED for item in attempts)
        assert phase.state is (ledger.PhaseState.FAILED if outcome == "unknown_failure" else ledger.PhaseState.SUCCEEDED)
        assert exposure.known_cost_krw == pytest.approx(sum(item["cost_krw"] for item in engine.usages))
        assert exposure.reservation_krw == 0
        assert paid_runtime._BUDGET_STORE_HEALTHY
    finally:
        real.generation_coordination._CURRENT.reset(coordination_token)


def test_real_sqlite_cancellation_after_reservation_closes_planned_attempt_once():
    from src.web import paid_runtime

    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()
    ticket = paid_runtime._begin_paid_phase(
        run_id="cancel-planned", phase=paid_runtime.SPEND_PHASE_PIPELINE,
        share_key="link:cancel-planned", cap_krw=9000, requested_cost_krw=900,
    )
    callbacks = paid_runtime._provider_attempt_callbacks(ticket)
    cancelled = threading.Event()
    attempts = []

    def begin(*args):
        token = callbacks.begin_attempt(*args)
        attempts.append(token)
        cancelled.set()
        return token

    def check():
        if cancelled.is_set():
            raise real.generation_coordination.GenerationWaitCancelled("전송 전 취소")

    engine, client = metered(SimpleNamespace(create=lambda **_: pytest.fail("취소 뒤 전송됨")))
    token = real.generation_coordination._CURRENT.set(SimpleNamespace(ensure_paid_phase=check))
    try:
        with real.provider_budget.activate(ticket.reserved_krw) as budget, attempt_context.activate(
            replace(callbacks, begin_attempt=begin),
        ):
            ask = real._v2_ask_via_provider(engine, client, stage="v2_compose", max_tokens=OUTPUT_CAP)
            with pytest.raises(Exception) as captured:
                ask("전송 전에 취소할 입력")
            assert isinstance(captured.value.cause, real.generation_coordination.GenerationWaitCancelled)
            assert budget.accounted_krw == 0 and not budget._pending
        assert len(attempts) == 1 and not engine.billing_uncertain
        callbacks.cancel_before_dispatch(attempts[0])
        paid_runtime._cancel_paid_phase(ticket)
        with paid_runtime.storage_db.connect() as conn:
            attempt = paid_runtime.state_machine.get_attempt(conn, attempt_id=attempts[0])
            assert attempt.billing_state is paid_runtime.state_machine.BillingState.KNOWN_ZERO
            assert paid_runtime.state_machine.load_run_exposure(conn, run_id=ticket.run_id).admission_exposure_krw == 0
        assert paid_runtime._BUDGET_STORE_HEALTHY
    finally:
        real.generation_coordination._CURRENT.reset(token)


@pytest.mark.parametrize("value,expected", [(None, 5), ("1", 1), ("3", 3), ("5", 5),
                                            ("0", 1), ("6", 1), ("오류", 1)])
def test_process_limit_has_bounded_safe_fallback(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(concurrency.PROVIDER_MAX_CONCURRENT_CALLS_ENV, raising=False)
    else:
        monkeypatch.setenv(concurrency.PROVIDER_MAX_CONCURRENT_CALLS_ENV, value)
    assert concurrency.configured_limit() == expected
