"""본문·도식 출력 상한이 계수·예약·전송·실제 비용에 같은 요청으로 전달된다."""

from types import SimpleNamespace

import pytest

from src.core.constants import MAX_AI_CALLS_PER_REQUEST
from src.core.pricing import usage_cost_krw
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.pipeline import real
from src.features.pipeline.v2_response_constants import V2_RESPONSE_STEP

# pipeline의 기존 조립 경계를 통해 검사하며 다른 기능의 직접 의존은 늘리지 않는다.
provider_budget = real.provider_budget
run_diagnostics = real.run_diagnostics


class RecordingMessages:
    def __init__(self, stop_reason=None, *, input_tokens=1234, output_tokens=1200):
        self.counts = []
        self.requests = []
        self.stop_reason = stop_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        return SimpleNamespace(input_tokens=self.input_tokens)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            model="claude-haiku-4-5-20251001",
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(text='{"판정": []}')],
            usage=SimpleNamespace(input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )


@pytest.fixture
def attempt_observations():
    observations = []
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _stage, _reserved: 1,
        lambda _: None,
        lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    with attempt_context.activate(callbacks):
        yield observations


@pytest.mark.parametrize("budget_amount,should_send", [(1000, True), (1, False)])
def test_diagram_cap_reaches_real_metered_boundary_without_changing_budget_or_model(budget_amount, should_send):
    messages = RecordingMessages()
    engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    reservations, observations = [], []

    def begin(provider, stage, reserved):
        reservations.append((provider, stage, reserved))
        return len(reservations)

    callbacks = ProviderAttemptCallbacks(begin, lambda _: None, lambda _: None,
                                        lambda _, observation: observations.append(observation))
    ask = real._v2_ask_via_provider(engine, client, stage="v2_diagram", max_tokens=real.V2_DIAGRAM_MAX_TOKENS)
    with provider_budget.activate(budget_amount) as budget, attempt_context.activate(callbacks):
        if should_send:
            assert ask("시험용 도식 입력") == '{"판정": []}'
            assert budget.accounted_krw == usage_cost_krw("claude-haiku-4-5", 1234, 1200)
        else:
            with pytest.raises(Exception) as error:
                ask("시험용 도식 입력")
            assert isinstance(error.value.cause, provider_budget.ProviderBudgetExceeded)
            assert error.value.request_budget and error.value.degradable
            assert not error.value.call_limit
            assert budget.accounted_krw == 0

    assert len(messages.requests) == len(reservations) == len(observations) == int(should_send)
    assert len(messages.counts) == 1
    assert engine.MODEL == "claude-haiku-4-5"
    assert engine.current_stage == "unspecified" and not engine.prompt_cache_enabled
    assert MAX_AI_CALLS_PER_REQUEST == 18
    assert real.V2_WRITER_MAX_TOKENS == 4000 and real.V2_REVIEWER_MAX_TOKENS == 16000
    if should_send:
        request, = messages.requests
        assert request["model"] == messages.counts[0]["model"] == "claude-haiku-4-5"
        assert request["max_tokens"] == 4096
        assert request["messages"] == messages.counts[0]["messages"]
        assert isinstance(request["messages"][0]["content"], str)
        # 예약액은 «실제 출력»이 아니라 상한으로 잡힌다 — 상한을 올리면 이 값이
        # 출력 token 단가만큼 확실히 커진다는 계약을 그대로 못 박는다.
        assert reservations == [("anthropic", "v2_diagram", usage_cost_krw(
            "claude-haiku-4-5", 1234 + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, 4096))]
        assert engine.usages[0]["stage"] == "v2_diagram"
        assert engine.usages[0]["out"] == 1200
        assert engine.usages[0]["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.parametrize("stop_reason,expected", [
    ("end_turn", "end_turn"), ("max_tokens", "max_tokens"),
    ("응답 내부의 비공개 문자열", "unknown"), (None, "unknown"),
    (["비정상 자료형"], "unknown"),
])
def test_success_response_records_only_allowlisted_metadata(
    stop_reason, expected, caplog, attempt_observations,
):
    messages = RecordingMessages(stop_reason)
    engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    collector = run_diagnostics.begin_run()
    try:
        ask = real._v2_ask_via_provider(engine, client, stage="v2_diagram", max_tokens=2048)
        with provider_budget.activate(1000) as budget:
            assert ask("저장하지 않는 시험 입력") == '{"판정": []}'
            assert budget.accounted_krw == usage_cost_krw("claude-haiku-4-5", 1234, 1200)
        assert collector.steps == [{"step": V2_RESPONSE_STEP, "단계": "v2_diagram",
                                    "출력상한": 2048, "종료사유": expected}]
        assert len(messages.requests) == len(engine.usages) == len(attempt_observations) == 1
    finally:
        collector.finish()
    assert "저장하지 않는 시험 입력" not in caplog.text
    assert "응답 내부의 비공개 문자열" not in caplog.text


def test_observation_failure_does_not_change_success_or_metering(monkeypatch, attempt_observations):
    messages = RecordingMessages("max_tokens")
    engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))

    def unavailable():
        raise RuntimeError("진단 저장소를 사용할 수 없다")

    monkeypatch.setattr(run_diagnostics, "current_steps", unavailable)
    ask = real._v2_ask_via_provider(engine, client, stage="v2_diagram", max_tokens=2048)
    with provider_budget.activate(1000) as budget:
        assert ask("시험 입력") == '{"판정": []}'
        assert budget.accounted_krw == usage_cost_krw("claude-haiku-4-5", 1234, 1200)
    assert len(messages.requests) == len(engine.usages) == len(attempt_observations) == 1


@pytest.mark.parametrize("output_tokens,prior_state", [
    (8357, "none"), (10680, "none"), (16000, "none"),
    (8357, "settled"), (8357, "held"),
])
def test_body_cap_reaches_reservation_and_request_with_actual_only_settlement(
    output_tokens, prior_state, monkeypatch,
):
    # 입력은 과거 사용량을 사용한 시험값이며 현재 프롬프트를 실제 계수하지 않는다.
    model, input_tokens = "claude-haiku-4-5", 119943
    messages = RecordingMessages("end_turn", input_tokens=input_tokens, output_tokens=output_tokens)
    engine = real._MeteredEngine(SimpleNamespace(MODEL=model))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    reservations, observations, reserve_inputs = [], [], []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: reservations.append((provider, stage, reserved)) or len(reservations),
        lambda _: None, lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    ask = real._v2_ask_via_provider(engine, client, stage="v2_review", max_tokens=real.V2_REVIEWER_MAX_TOKENS)
    with provider_budget.activate(1000) as budget, attempt_context.activate(callbacks):
        if prior_state != "none":
            # 앞선 확정 사용액과 아직 보유한 예약액 모두 누적 한도에 포함한다.
            for index in range(3):
                previous = budget.reserve_call(
                    model=model, input_tokens_upper=input_tokens + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS,
                    max_tokens=12000,
                )
                if prior_state == "settled" or index < 2:
                    budget.settle_call(previous, actual_krw=usage_cost_krw(model, input_tokens, 12000))
        before = budget.accounted_krw
        reserve_call = budget.reserve_call

        def observe_reservation(**kwargs):
            reserve_inputs.append(kwargs)
            return reserve_call(**kwargs)

        monkeypatch.setattr(budget, "reserve_call", observe_reservation)
        if prior_state == "none":
            assert ask("본문 계량 시험 입력") == '{"판정": []}'
            actual_cost = usage_cost_krw(model, input_tokens, output_tokens)
            assert budget.accounted_krw == actual_cost
            assert engine.usages[0]["cost_krw"] == actual_cost
            assert engine.usages[0]["out"] == output_tokens
            request, = messages.requests
            assert request["max_tokens"] == 16000
            assert request["model"] == messages.counts[0]["model"] == model
            assert request["messages"] == messages.counts[0]["messages"]
            expected_reserve = usage_cost_krw(
                model, input_tokens + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, 16000,
            )
            assert reservations == [("anthropic", "v2_review", expected_reserve)]
            assert budget.accounted_krw < expected_reserve
            assert observations[0].known_cost_krw == actual_cost
        else:
            with pytest.raises(Exception) as error:
                ask("본문 계량 시험 입력")
            assert isinstance(error.value.cause, provider_budget.ProviderBudgetExceeded)
            assert error.value.request_budget and error.value.degradable and not error.value.call_limit
            assert budget.accounted_krw == before
            assert messages.requests == reservations == observations == engine.usages == []
        assert reserve_inputs == [{"model": model,
                                   "input_tokens_upper": input_tokens + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS,
                                   "max_tokens": 16000}]
        assert len(messages.counts) == 1
    assert engine.MODEL == model and engine.current_stage == "unspecified"
    assert not engine.prompt_cache_enabled
    assert real.V2_WRITER_MAX_TOKENS == 4000 and real.V2_DIAGRAM_MAX_TOKENS == 4096
    assert MAX_AI_CALLS_PER_REQUEST == 18
