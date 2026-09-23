"""검수 재요청의 네이티브 스키마와 별도 상한이 같은 계량 경계를 통과한다.

제공자는 로컬 모형이며 네트워크를 쓰지 않는다. 스키마 컴파일 성공 여부는
이 시험의 범위가 아니고, 응답의 파싱·근거 판정은 기존 검증기를 그대로 쓴다.
"""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import anthropic
import pytest

from src.core.pricing import usage_cost_krw
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.composer import verify
from src.features.composer.constants import MISSING_VERDICTS_REMINDER, RETRY_REMINDER
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA
from src.features.pipeline import real
from src.features.pipeline.tests import test_v2_native_schema_metering as native
from src.features.pipeline.v2_response_constants import V2_RESPONSE_STEP


TEXT = "회사는 제품을 판매한다."
SENTENCE = ComposedSentence(TEXT, ("1",), "확인")
FRAGMENTS = {"1": CollectedFragment("1", "공시", TEXT)}
FLAT_ITEMS = (verify._ReviewItem(1, SENTENCE, "identity"),)
GROUPED_ITEMS = (verify._GroupedReviewItem(
    1, "identity", "문장", ("1",), sentence=SENTENCE,
),)
GOOD = json.dumps({"판정": [{
    "번호": 1, "장": "identity", "근거": ["1"],
    "근거대조": "공시 원문과 제품 판매가 일치한다.", "결과": "참",
}]}, ensure_ascii=False)
BROKEN = '{"판정": [{"번호": 1, "결과": "참"'
REPLY_OUTPUT = 50
BUDGET_KRW = 1000


class ScriptedMessages(native.RecordingMessages):
    """전송 순서와 사용량을 기록하며 준비한 응답만 돌려주는 제공자 모형."""

    def __init__(self):
        super().__init__()
        self.replies = []

    def create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        raw, output_tokens = self.replies.pop(0)
        body = native.response_body(text=raw)
        body["usage"]["output_tokens"] = output_tokens
        return anthropic.types.Message(**body)


@pytest.fixture
def review_calls():
    original_schema = deepcopy(FLAT_REVIEW_SCHEMA)
    messages = ScriptedMessages()
    engine = real._MeteredEngine(SimpleNamespace(MODEL=native.MODEL))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    # 첫 응답이 오기 전에 세 호출자를 모두 만든다. 재요청만 전송 시점에 상한을 푼다.
    regular = real._v2_ask_via_provider(
        engine, client, stage="v2_review", max_tokens=real.V2_REVIEWER_MAX_TOKENS,
    )
    initial = real._v2_ask_via_provider(
        engine, client, stage="v2_review",
        max_tokens=real.V2_INITIAL_REVIEWER_MAX_TOKENS,
    )
    retry = real._v2_ask_via_provider(
        engine, client, stage="v2_review",
        max_tokens=lambda: real._initial_review_retry_max_tokens(engine),
    )
    reservations, observations = [], []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: reservations.append(
            (provider, stage, reserved)
        ) or len(reservations),
        lambda _: None, lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    collector = real.run_diagnostics.begin_run()
    try:
        with real.provider_budget.activate(BUDGET_KRW) as budget, attempt_context.activate(callbacks):
            yield SimpleNamespace(
                messages=messages, engine=engine, regular=regular, initial=initial,
                retry=retry, reservations=reservations, observations=observations,
                budget=budget, collector=collector,
            )
    finally:
        collector.finish()
        assert FLAT_REVIEW_SCHEMA == original_schema


def _assert_payloads(calls, caps, prompts, schemas):
    """무료 계수·실제 전송·예약·정산이 같은 요청 수와 구조화 출력 설정을 쓴다."""
    expected_requests = []
    for cap, prompt, schema in zip(caps, prompts, schemas, strict=True):
        request = {
            "model": native.MODEL, "max_tokens": cap, "temperature": 0,
            "messages": [{"role": "user", "content": str(prompt)}],
        }
        if schema is not None:
            request["output_config"] = {"format": {
                "type": "json_schema", "schema": anthropic.transform_schema(deepcopy(schema)),
            }}
        expected_requests.append(request)
    # 전체 요청 비교로 숨은 일반 출력 재호출·캐시 블록 추가도 잡는다.
    assert calls.messages.requests == expected_requests
    assert calls.messages.counts == [
        {key: value for key, value in request.items() if key not in {"max_tokens", "temperature"}}
        for request in expected_requests
    ]
    input_upper = native.COUNTED_INPUT + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
    assert calls.reservations == [
        ("anthropic", "v2_review", usage_cost_krw(native.MODEL, input_upper, cap))
        for cap in caps
    ]
    assert len(calls.observations) == len(calls.engine.usages) == len(caps)
    assert calls.budget.accounted_krw == pytest.approx(sum(
        usage_cost_krw(native.MODEL, native.COUNTED_INPUT, usage["out"])
        for usage in calls.engine.usages
    ))
    assert [step["출력상한"] for step in calls.collector.steps
            if step["step"] == V2_RESPONSE_STEP] == caps
    assert calls.engine.current_stage == "unspecified"
    assert not calls.engine.prompt_cache_enabled


@pytest.mark.parametrize("first_raw,first_output,retry_raw,caps,expected", (
    (GOOD, 7000, GOOD, [24000], {1: "참"}),
    (BROKEN, 7000, GOOD, [24000, 12000], {1: "참"}),
    (BROKEN, 10000, GOOD, [24000, 15000], {1: "참"}),
    (BROKEN, 10001, GOOD, [24000, 15002], {1: "참"}),
    (BROKEN, 7000, BROKEN, [24000, 12000], None),
), ids=("first", "retry-floor", "retry-scaled", "retry-ceil", "retry-exhausted"))
def test_only_initial_parse_retry_sends_schema_with_separate_cap(
    review_calls, first_raw, first_output, retry_raw, caps, expected,
):
    calls = review_calls
    calls.messages.replies = [(first_raw, first_output), (retry_raw, REPLY_OUTPUT)]
    prompt = verify._build_review_prompt(FLAT_ITEMS, FRAGMENTS, "")
    assert type(prompt) is str
    assert verify._ask_verdicts(
        calls.regular, FLAT_ITEMS, FRAGMENTS, "",
        initial_ask=calls.initial, initial_retry_ask=calls.retry,
    ) == expected
    prompts = [prompt] if len(caps) == 1 else [prompt, str(prompt) + RETRY_REMINDER]
    # 2026-09-22: 최초 본문 검수(initial_ask)는 첫 요청부터 스키마를 싣는다.
    #   재요청의 별도 상한·같은 계량 경계는 그대로다(test_initial_review_schema.py 참조).
    schemas = [FLAT_REVIEW_SCHEMA] * len(caps)
    _assert_payloads(calls, caps, prompts, schemas)
    assert calls.engine.usages[0]["out"] == first_output


def test_followup_parse_retry_keeps_regular_caller_with_native_schema(review_calls):
    calls = review_calls
    calls.messages.replies = [(BROKEN, 7000), (GOOD, REPLY_OUTPUT)]
    prompt = verify._build_review_prompt(FLAT_ITEMS, FRAGMENTS, "")
    assert verify._ask_verdicts(
        calls.regular, FLAT_ITEMS, FRAGMENTS, "", initial_retry_ask=calls.retry,
    ) == {1: "참"}
    _assert_payloads(
        calls, [16000, 16000], [prompt, str(prompt) + RETRY_REMINDER],
        [None, FLAT_REVIEW_SCHEMA],
    )


# ★ 2026-09-23 — packet(묶음) 검수도 평문과 같은 재요청 계약이다. 예전에는 «1회
#   고정»이라 JSON 한 글자 오류로 판정 42행이 통째로 사라졌다. 이제 못 읽으면 형식
#   재요청을, 일부 번호가 빠지면 빠진 항목만 누락 후속을 «재요청 전용 상한» 호출자로
#   1회 보낸다. 같은 계량 경계(예약·정산·출력상한 기록)를 지나는지를 여기서 본다.
# ⚠️ 네이티브 스키마(output_config)는 스위치 PACKET_REVIEW_SCHEMA_ENABLED(기본 꺼짐)를
#   따른다 — 실제 공급자로 검증된 적이 없어 꺼 두었다(review_schema.py 주석). 기본
#   갈래는 세 호출 모두 output_config 가 없고, 켠 갈래는 모두 FLAT_REVIEW_SCHEMA 다.
PACKET_SCHEMA_SWITCH = "src.features.composer.verify.PACKET_REVIEW_SCHEMA_ENABLED"
SECOND_TEXT = "회사는 고객에게 서비스를 제공한다."
TWO_FRAGMENTS = {**FRAGMENTS, "2": CollectedFragment("2", "공시", SECOND_TEXT)}
TWO_GROUPED_ITEMS = GROUPED_ITEMS + (verify._GroupedReviewItem(
    2, "identity", "문장", ("2",), sentence=ComposedSentence(SECOND_TEXT, ("2",), "확인"),
),)
SECOND_GOOD = json.dumps({"판정": [{
    "번호": 2, "장": "identity", "근거": ["2"],
    "근거대조": "공시 원문과 서비스 제공이 일치한다.", "결과": "참",
}]}, ensure_ascii=False)


@pytest.mark.parametrize("schema_enabled", (False, True), ids=("schema-off", "schema-on"))
@pytest.mark.parametrize("first_raw,retry_raw,caps,expected", (
    (GOOD, GOOD, [24000], {1: "참"}),
    (BROKEN, GOOD, [24000, 12000], {1: "참"}),
    ('{"판정": []}', GOOD, [24000, 12000], {1: "참"}),
    (BROKEN, BROKEN, [24000, 12000], None),
), ids=("valid", "malformed", "empty-rows", "retry-exhausted"))
def test_grouped_review_parse_retry_uses_the_separate_cap_with_schema_per_switch(
    review_calls, monkeypatch, first_raw, retry_raw, caps, expected, schema_enabled,
):
    if schema_enabled:
        monkeypatch.setattr(PACKET_SCHEMA_SWITCH, True)
    calls = review_calls
    calls.messages.replies = [(first_raw, 7000), (retry_raw, REPLY_OUTPUT)]
    prompt = verify._build_grouped_review_prompt(GROUPED_ITEMS, FRAGMENTS, None)
    assert type(prompt) is str
    assert verify._ask_grouped_verdicts(
        calls.regular, GROUPED_ITEMS, FRAGMENTS, None,
        initial_ask=calls.initial, initial_retry_ask=calls.retry,
    ) == expected
    prompts = [prompt] if len(caps) == 1 else [prompt, str(prompt) + RETRY_REMINDER]
    schema = FLAT_REVIEW_SCHEMA if schema_enabled else None
    _assert_payloads(calls, caps, prompts, [schema] * len(caps))
    assert calls.engine.usages[0]["out"] == 7000


@pytest.mark.parametrize("schema_enabled", (False, True), ids=("schema-off", "schema-on"))
def test_grouped_missing_followup_asks_only_the_missing_item_through_the_retry_cap(
    review_calls, monkeypatch, schema_enabled,
):
    if schema_enabled:
        monkeypatch.setattr(PACKET_SCHEMA_SWITCH, True)
    calls = review_calls
    # 첫 답은 1번만 온전히 답하고 2번을 빠뜨린다 — 형식은 정상이다.
    calls.messages.replies = [(GOOD, 7000), (SECOND_GOOD, REPLY_OUTPUT)]
    first_prompt = verify._build_grouped_review_prompt(
        TWO_GROUPED_ITEMS, TWO_FRAGMENTS, None,
    )
    followup_prompt = verify._build_grouped_review_prompt(
        TWO_GROUPED_ITEMS[1:], TWO_FRAGMENTS, None,
    ) + MISSING_VERDICTS_REMINDER
    assert verify._ask_grouped_verdicts(
        calls.regular, TWO_GROUPED_ITEMS, TWO_FRAGMENTS, None,
        initial_ask=calls.initial, initial_retry_ask=calls.retry,
    ) == {1: "참", 2: "참"}
    schema = FLAT_REVIEW_SCHEMA if schema_enabled else None
    _assert_payloads(
        calls, [24000, 12000], [first_prompt, followup_prompt], [schema, schema],
    )
