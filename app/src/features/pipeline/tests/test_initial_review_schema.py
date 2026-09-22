"""최초 본문 검수의 «첫 요청»이 계량 경계에서 구조화 출력·상한·예약·정산과 결속되는지 확인한다.

제공자는 로컬 모형이며 네트워크를 쓰지 않는다. 스키마 문법 컴파일 성공·지연·
절감액은 이 시험의 범위가 아니고, 응답 파싱·근거 판정은 기존 검증기를 그대로 쓴다.
같은 계량 모형(`test_native_review_retry_integration`)을 그대로 빌려 쓴다.
"""

from __future__ import annotations

from copy import deepcopy

import anthropic
import pytest

from src.core.pricing import usage_cost_krw
from src.features.composer import verify
from src.features.composer.constants import RETRY_REMINDER
from src.features.composer.prompt_cache_constants import REVIEW_PROMPT_CACHE_ENV
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA
from src.features.pipeline import real
from src.features.pipeline.tests import test_v2_native_schema_metering as native
from src.features.pipeline.tests.test_native_review_retry_integration import (  # noqa: F401
    BROKEN,
    FLAT_ITEMS,
    FRAGMENTS,
    GOOD,
    REPLY_OUTPUT,
    _assert_payloads,
    review_calls,
)


@pytest.mark.parametrize("first_raw,first_output,retry_raw,caps,expected", (
    (GOOD, 7000, GOOD, [24000], {1: "참"}),
    (BROKEN, 7000, GOOD, [24000, 12000], {1: "참"}),
    (BROKEN, 10000, GOOD, [24000, 15000], {1: "참"}),
    (BROKEN, 10001, GOOD, [24000, 15002], {1: "참"}),
    (BROKEN, 7000, BROKEN, [24000, 12000], None),
), ids=("first", "retry-floor", "retry-scaled", "retry-ceil", "retry-exhausted"))
def test_initial_review_sends_schema_from_first_request_with_same_caps(
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
    # 첫 요청부터 스키마가 실리고, 재요청은 예전처럼 축소 상한 + 스키마다.
    _assert_payloads(calls, caps, prompts, [FLAT_REVIEW_SCHEMA] * len(caps))
    assert calls.engine.usages[0]["out"] == first_output


def test_followup_review_keeps_plain_first_request_on_regular_caller(review_calls):
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


def test_initial_review_with_prompt_cache_sends_two_blocks_and_schema(review_calls, monkeypatch):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1")
    calls = review_calls
    calls.messages.replies = [(GOOD, 7000)]
    prompt = verify._build_review_prompt(FLAT_ITEMS, FRAGMENTS, "")
    boundary = prompt.cache_prefix_chars
    assert boundary > 0
    assert verify._ask_verdicts(
        calls.regular, FLAT_ITEMS, FRAGMENTS, "",
        initial_ask=calls.initial, initial_retry_ask=calls.retry,
    ) == {1: "참"}
    [request] = calls.messages.requests
    text = str(prompt)
    assert request["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": text[:boundary], "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": text[boundary:]},
    ]}]
    assert request["output_config"] == {"format": {
        "type": "json_schema",
        "schema": anthropic.transform_schema(deepcopy(FLAT_REVIEW_SCHEMA)),
    }}
    assert request["max_tokens"] == real.V2_INITIAL_REVIEWER_MAX_TOKENS
    assert calls.messages.counts == [
        {key: value for key, value in request.items() if key not in {"max_tokens", "temperature"}}
    ]
    assert len(calls.reservations) == len(calls.observations) == len(calls.engine.usages) == 1
    input_upper = native.COUNTED_INPUT + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
    # 캐시 쓰기 예약은 입력 단가 1.25배 — 스키마 유무와 무관한 기존 규칙이다.
    assert calls.reservations == [(
        "anthropic", "v2_review",
        usage_cost_krw(native.MODEL, (input_upper * 5 + 3) // 4, real.V2_INITIAL_REVIEWER_MAX_TOKENS),
    )]
    assert calls.budget.accounted_krw == pytest.approx(
        usage_cost_krw(native.MODEL, native.COUNTED_INPUT, 7000)
    )
