"""검수 schema의 전송·무료 계수·예약·실패 정산을 실제 계량 경계에서 검증한다."""

from __future__ import annotations

import copy
import json
from types import MappingProxyType, SimpleNamespace

import anthropic
import httpx
import pytest

from src.core.pricing import usage_cost_krw
from src.core.provider_gateway import attempt_context, gateway
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.core.provider_gateway.types import BillingDisposition
from src.features.composer.logic import CacheablePrompt
from src.features.composer.port import AskFatalError
from src.features.pipeline import real
from src.features.pipeline.v2_response_constants import V2_RESPONSE_STEP

provider_budget = real.provider_budget
MODEL = "claude-haiku-4-5"
CAP = 700
COUNTED_INPUT = 1350


class SchemaPrompt(str):
    """composer 구현과 결합하지 않고 문자열의 선택적 속성 계약만 흉내 낸다."""

    def __new__(cls, text, schema):
        prompt = super().__new__(cls, text)
        prompt.response_schema = schema
        return prompt


def schema():
    return {
        "type": "object",
        "properties": {
            "판정": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "minItems": 2,
                "description": "검수 결과",
            }
        },
        "required": ["판정"],
        "additionalProperties": False,
    }


def response_body(*, stop_reason="end_turn", text='{"판정": []}'):
    return {
        "id": "msg_offline",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": COUNTED_INPUT, "output_tokens": 50},
    }


class RecordingMessages:
    def __init__(self, *, count_failure=False, error=None, response=None):
        self.counts = []
        self.requests = []
        self.count_failure = count_failure
        self.error = error
        self.response = response or anthropic.types.Message(**response_body())

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        if self.count_failure:
            raise TypeError("시험용 무료 계수 실패")
        return SimpleNamespace(input_tokens=COUNTED_INPUT)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def make_ask(messages, *, stage="v2_review"):
    engine = real._MeteredEngine(SimpleNamespace(MODEL=MODEL))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    ask = real._v2_ask_via_provider(engine, client, stage=stage, max_tokens=CAP)
    return engine, ask


@pytest.fixture
def attempts():
    reservations, observations = [], []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: reservations.append(
            (provider, stage, reserved)
        ) or len(reservations),
        lambda _: None,
        lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    with attempt_context.activate(callbacks):
        yield reservations, observations


def test_pinned_sdk_counts_and_sends_the_same_normalized_schema(attempts):
    """실제 SDK 직렬화까지 지나며 긴 한글 입력을 전체 바이트 예약으로 되돌리지 않는다."""
    source = schema()
    original = copy.deepcopy(source)
    prompt = SchemaPrompt("한글 검수 근거 " * 3000, MappingProxyType(source))
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        if request.url.path == "/v1/messages/count_tokens":
            # schema의 추가 지시가 없으면 더 작은 계수가 돌아오는 provider 모형이다.
            counted = COUNTED_INPUT if "output_config" in body else 1200
            return httpx.Response(200, json={"input_tokens": counted})
        assert request.url.path == "/v1/messages"
        return httpx.Response(200, json=response_body())

    estimated = usage_cost_krw(
        MODEL, COUNTED_INPUT + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, CAP
    )
    # 모든 HTTP 요청은 모형 transport가 받는다. 환경의 키·실제 네트워크는 쓰지 않는다.
    with anthropic.Anthropic(
        api_key="offline-test", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as sdk, provider_budget.activate(estimated + 0.01) as budget:
        engine, ask = make_ask(sdk.messages)
        assert ask(prompt) == '{"판정": []}'
        assert budget.accounted_krw == usage_cost_krw(MODEL, COUNTED_INPUT, 50)

    assert [path for path, _ in requests] == [
        "/v1/messages/count_tokens", "/v1/messages"
    ]
    counted, sent = [body for _, body in requests]
    expected_schema = anthropic.transform_schema(copy.deepcopy(source))
    assert counted["output_config"] == sent["output_config"] == {
        "format": {"type": "json_schema", "schema": expected_schema}
    }
    normalized_array = expected_schema["properties"]["판정"]
    assert "uniqueItems" not in normalized_array and "minItems" not in normalized_array
    assert "uniqueItems: True" in normalized_array["description"]
    assert "minItems: 2" in normalized_array["description"]
    assert counted["messages"] == sent["messages"] == [
        {"role": "user", "content": str(prompt)}
    ]
    assert counted["model"] == sent["model"] == engine.MODEL == MODEL
    assert sent["max_tokens"] == CAP and sent["temperature"] == 0
    assert source == original
    assert attempts[0] == [("anthropic", "v2_review", estimated)]
    assert len(attempts[1]) == len(engine.usages) == 1


@pytest.mark.parametrize("cache", [False, True])
def test_unmarked_plain_and_writer_cache_payloads_remain_unchanged(cache, attempts):
    text = "공유 근거.장별 지시."
    prompt = CacheablePrompt(text, cache_prefix_chars=6) if cache else text
    content = [
        {"type": "text", "text": text[:6], "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": text[6:]},
    ] if cache else text
    messages = RecordingMessages()
    engine, ask = make_ask(messages, stage="v2_compose")
    with provider_budget.activate(1000):
        ask(prompt)
    assert messages.requests == [{
        "model": MODEL, "max_tokens": CAP, "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    }]
    assert messages.counts == [{
        "model": MODEL, "messages": messages.requests[0]["messages"],
    }]
    estimated_input = COUNTED_INPUT + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
    if cache:
        estimated_input = (estimated_input * 5 + 3) // 4
    assert attempts[0][0][2] == usage_cost_krw(MODEL, estimated_input, CAP)
    assert engine.current_stage == "unspecified" and not engine.prompt_cache_enabled


@pytest.mark.parametrize("allow", [False, True])
def test_failed_free_count_reserves_normalized_full_payload_before_send(allow, attempts):
    source = schema()
    prompt = SchemaPrompt("보수 계측 입력", source)
    expected = {
        "model": MODEL, "max_tokens": CAP, "temperature": 0,
        "messages": [{"role": "user", "content": str(prompt)}],
        "output_config": {"format": {
            "type": "json_schema", "schema": anthropic.transform_schema(source),
        }},
    }
    estimated_input = provider_budget.estimate_request_tokens({"args": (), "kwargs": expected})
    estimated = usage_cost_krw(MODEL, estimated_input, CAP)
    messages = RecordingMessages(count_failure=True)
    engine, ask = make_ask(messages)
    with provider_budget.activate(estimated + (0.01 if allow else -0.01)) as budget:
        if allow:
            assert ask(prompt) == '{"판정": []}'
            assert messages.requests == [expected]
            assert attempts[0] == [("anthropic", "v2_review", estimated)]
            assert budget.accounted_krw == usage_cost_krw(MODEL, COUNTED_INPUT, 50)
        else:
            with pytest.raises(AskFatalError) as caught:
                ask(prompt)
            assert isinstance(caught.value.cause, provider_budget.ProviderBudgetExceeded)
            assert caught.value.request_budget and caught.value.degradable
            assert budget.accounted_krw == 0
            assert messages.requests == attempts[0] == attempts[1] == engine.usages == []
    assert len(messages.counts) == 1
    assert messages.counts[0]["output_config"] == expected["output_config"]


@pytest.mark.parametrize("stop_reason,text", [
    ("end_turn", '{"판정": []}'),
    ("max_tokens", '{"판정": ['),
    ("refusal", "요청을 수행할 수 없습니다."),
])
def test_native_stop_reasons_preserve_text_diagnostics_and_actual_charge(
    stop_reason, text, attempts,
):
    response = anthropic.types.Message(**response_body(stop_reason=stop_reason, text=text))
    messages = RecordingMessages(response=response)
    engine, ask = make_ask(messages)
    collector = real.run_diagnostics.begin_run()
    try:
        with provider_budget.activate(1000) as budget:
            assert ask(SchemaPrompt("검수 입력", schema())) == text
            assert budget.accounted_krw == usage_cost_krw(MODEL, COUNTED_INPUT, 50)
        assert collector.steps == [{
            "step": V2_RESPONSE_STEP, "단계": "v2_review",
            "출력상한": CAP, "종료사유": stop_reason,
        }]
    finally:
        collector.finish()
    assert len(messages.requests) == len(engine.usages) == len(attempts[1]) == 1
    assert attempts[1][0].billing_disposition is BillingDisposition.KNOWN_COST


@pytest.mark.parametrize("with_usage", [False, True])
def test_native_provider_error_never_retries_as_plain_output(with_usage, attempts):
    """스키마 거절도 다른 제공자 장애와 같은 요청 전역 실패로 다룬다(main c20a2101의 계약).

    그 회차만 넘기는 대안은 채택하지 않았다.
    """
    error = anthropic.BadRequestError(
        "시험용 schema 거절",
        response=httpx.Response(400, request=httpx.Request("POST", "https://offline.invalid")),
        body={"type": "error", "error": {"type": "invalid_request_error"}},
    )
    if with_usage:
        error.usage = SimpleNamespace(input_tokens=COUNTED_INPUT, output_tokens=50)
    messages = RecordingMessages(error=error)
    engine, ask = make_ask(messages)
    with provider_budget.activate(1000) as budget:
        with pytest.raises(AskFatalError) as caught:
            ask(SchemaPrompt("검수 입력", schema()))
        assert isinstance(caught.value.cause, gateway.ProviderCallFailed)
        assert caught.value.cause.__cause__ is error
        if with_usage:
            assert budget.accounted_krw == usage_cost_krw(MODEL, COUNTED_INPUT, 50)
            assert engine.usages[0]["failed"] is True
            assert attempts[1][0].billing_disposition is BillingDisposition.KNOWN_COST
        else:
            assert budget.accounted_krw == attempts[0][0][2]
            assert engine.billing_uncertain and engine.usages == []
            assert attempts[1][0].billing_disposition is BillingDisposition.CONSERVATIVE_LIABILITY
            with pytest.raises(AskFatalError) as blocked:
                ask(SchemaPrompt("명시적 후속 호출도 차단", schema()))
            assert isinstance(blocked.value.cause, provider_budget.ProviderBudgetUnavailable)
    assert len(messages.requests) == len(messages.counts) == len(attempts[0]) == len(attempts[1]) == 1
    assert messages.requests[0]["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize("invalid_schema", [[], "json", {}])
def test_invalid_marker_or_untransformable_schema_stops_before_count_or_payment(
    invalid_schema, attempts,
):
    messages = RecordingMessages()
    engine, ask = make_ask(messages)
    with provider_budget.activate(1000) as budget:
        with pytest.raises(AskFatalError) as caught:
            ask(SchemaPrompt("검수 입력", invalid_schema))
        assert isinstance(caught.value.cause, provider_budget.ProviderBudgetUnavailable)
        assert not caught.value.degradable
        assert budget.accounted_krw == 0
    assert messages.counts == messages.requests == attempts[0] == attempts[1] == engine.usages == []
