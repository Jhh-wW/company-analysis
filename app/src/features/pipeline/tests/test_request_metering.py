"""P-129 — 겹쳐 도는 진짜 요청의 비용·모델이 서로 섞이지 않는가."""

from __future__ import annotations

import ast
import copy
import sys
import threading
from types import SimpleNamespace

import pytest

from src.core.constants import (
    MAX_AI_CALLS_PER_REQUEST,
    MODEL_PRICES_USD_PER_MTOK,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
)
from src.core.pricing import AI_COST_KRW_PER_USD
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.core.provider_gateway.types import BillingDisposition, ProviderObservation
from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, UserInput
from src.features.spanselect.canonical import answer_schema

_HAIKU = "claude-haiku-4-5"
_SONNET = "claude-sonnet-4-6"


class _AttemptRecorder:
    """DB 대신 callback 순서와 비민감 비용 관측만 담는 시험 원장."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.observations: list[ProviderObservation] = []

    def callbacks(self) -> ProviderAttemptCallbacks:
        def begin(provider: str, operation: str, reserved_krw: float) -> int:
            token = len(self.observations) + 1
            self.events.append(("begin", token, provider, operation, reserved_krw))
            return token

        def mark(token: int) -> None:
            self.events.append(("dispatch", token))

        def heartbeat(token: int) -> None:
            self.events.append(("heartbeat", token))

        def record(token: int, observation: ProviderObservation) -> None:
            self.events.append(("observation", token))
            self.observations.append(observation)

        return ProviderAttemptCallbacks(begin, heartbeat, mark, record)


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    """직접 engine 단위시험도 운영 경계와 같은 요청별 예약 문맥을 쓴다."""
    recorder = _AttemptRecorder()
    with provider_budget.activate(100_000.0), attempt_context.activate(
        recorder.callbacks()
    ):
        yield recorder


class FakeMessages:
    """네트워크 없이 provider의 messages.create 응답 모양만 흉내 낸다."""

    def __init__(
        self,
        *,
        response_model: str = "",
        fail_on_call: int = 0,
        omit_usage: bool = False,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ):
        self.response_model = response_model
        self.fail_on_call = fail_on_call
        self.omit_usage = omit_usage
        self.cache_creation_tokens = cache_creation_tokens
        self.cache_read_tokens = cache_read_tokens
        self.calls: list[str] = []
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs["model"])
        self.requests.append(kwargs)
        if self.fail_on_call == len(self.calls):
            raise TimeoutError("시험용 provider timeout")
        return SimpleNamespace(
            model=self.response_model or kwargs["model"],
            usage=(
                None
                if self.omit_usage
                else SimpleNamespace(
                    input_tokens=1_000_000,
                    output_tokens=100_000,
                    cache_creation_input_tokens=self.cache_creation_tokens,
                    cache_read_input_tokens=self.cache_read_tokens,
                )
            ),
        )


class FakeRawEngine:
    MODEL = _HAIKU

    def __init__(self, messages: FakeMessages):
        self.client = SimpleNamespace(messages=messages)

    def _client(self):
        return self.client


def _client(metered: real._MeteredEngine):
    return real._metered_client(metered, metered._client())


_ISOLATED_ENGINE_SOURCE = """
MODEL = "claude-haiku-4-5"
PRICE_IN, PRICE_OUT = 1.0, 5.0
BUDGET_STOP_USD = 8.0
_spent_usd = 0.0

def _ask(client, prompt, schema, max_tokens=700):
    global _spent_usd
    if _spent_usd > BUDGET_STOP_USD:
        raise RuntimeError(
            f"예산가드: 누적 ${_spent_usd:.2f} > ${BUDGET_STOP_USD}"
        )
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    cost = (
        response.usage.input_tokens * PRICE_IN
        + response.usage.output_tokens * PRICE_OUT
    ) / 1e6
    _spent_usd += cost
    return {"ok": True}, {
        "in": response.usage.input_tokens,
        "out": response.usage.output_tokens,
        "usd": round(cost, 6),
    }
"""


@pytest.fixture
def isolated_engine_path(tmp_path):
    """외부 의존·네트워크 없이 1판의 module 전역 예산가드만 재현한다."""
    path = tmp_path / "run_pilot_contract.py"
    path.write_text(_ISOLATED_ENGINE_SOURCE, encoding="utf-8")
    return path


def _isolated_request(engine_path, messages):
    raw = real._load_isolated_engine_module(engine_path)
    metered = real._MeteredEngine(raw)
    client = real._metered_client(metered, SimpleNamespace(messages=messages))
    return raw, metered, client


def test_모듈함수가_client를_직접_불러도_식별과_알맹이_usage를_모두_센다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)

    # 1판 identify/substance_check처럼 wrapper._ask가 아니라 모듈 함수가 받은
    # client에서 messages.create를 직접 부르는 경로다.
    def identify(module_client):
        module_client.messages.create(model="1판-전역값", max_tokens=700)

    def substance_check(module_client):
        module_client.messages.create(model="1판-전역값", max_tokens=700)

    identify(client)
    substance_check(client)

    one_call_usd = 1.0 + 0.1 * 5.0
    assert real._request_spent_krw(metered) == pytest.approx(
        2 * one_call_usd * AI_COST_KRW_PER_USD
    )
    assert real._request_model_label(metered) == _HAIKU


def test_요청당_AI호출_15회상한은_실제전송경계에서_강제된다(
    _paid_provider_budget_context,
):
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)

    for _ in range(MAX_AI_CALLS_PER_REQUEST):
        client.messages.create(model=_HAIKU, max_tokens=700)

    with pytest.raises(
        provider_budget.ProviderBudgetExceeded,
        match="AI 호출 횟수 상한",
    ):
        client.messages.create(model=_HAIKU, max_tokens=700)

    assert len(messages.calls) == MAX_AI_CALLS_PER_REQUEST
    assert sum(
        event[0] == "begin" for event in _paid_provider_budget_context.events
    ) == MAX_AI_CALLS_PER_REQUEST


def test_usage가_있는_실패를_반복해도_16번째는_전송전에_막힌다(
    _paid_provider_budget_context,
):
    class FailedWithUsage(RuntimeError):
        def __init__(self):
            super().__init__("provider rejected after usage")
            self.model = _HAIKU
            self.usage = SimpleNamespace(
                input_tokens=10,
                output_tokens=1,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )

    class FailedMessages:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            raise FailedWithUsage()

    messages = FailedMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)

    for _ in range(MAX_AI_CALLS_PER_REQUEST):
        with pytest.raises(FailedWithUsage):
            client.messages.create(model=_HAIKU, max_tokens=100)
    with pytest.raises(
        provider_budget.ProviderBudgetExceeded,
        match="AI 호출 횟수 상한",
    ):
        client.messages.create(model=_HAIKU, max_tokens=100)

    assert messages.calls == MAX_AI_CALLS_PER_REQUEST
    assert sum(
        event[0] == "begin" for event in _paid_provider_budget_context.events
    ) == MAX_AI_CALLS_PER_REQUEST


def test_Sonnet_선택3회용_prompt_cache와_단계별비용을_기록한다():
    messages = FakeMessages(cache_creation_tokens=1000, cache_read_tokens=500)
    metered = real._MeteredEngine(FakeRawEngine(messages))
    metered.MODEL = _SONNET
    client = _client(metered)

    with metered.stage_context("span_selection", prompt_cache=True):
        client.messages.create(
            model="ignored",
            max_tokens=700,
            messages=[{"role": "user", "content": "same selection prompt"}],
        )

    content = messages.requests[0]["messages"][0]["content"]
    assert content == [
        {
            "type": "text",
            "text": "same selection prompt",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    event = real._request_cost_events(metered)[0]
    assert event.stage == "span_selection"
    assert event.model_id == _SONNET
    assert event.cache_creation_tokens == 1000
    assert event.cache_read_tokens == 500
    assert event.cache_hit is True


def test_raw_structured_output_schema는_provider_전송전에_공식형식으로_바꾼다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)
    source_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }

    client.messages.create(
        model=_SONNET,
        max_tokens=700,
        messages=[{"role": "user", "content": "schema normalization"}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": source_schema,
            }
        },
    )

    sent_schema = messages.requests[0]["output_config"]["format"]["schema"]

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert "uniqueItems" not in keys(sent_schema)
    assert source_schema["properties"]["items"]["uniqueItems"] is True
    assert "uniqueItems" in sent_schema["properties"]["items"]["description"]


def test_structured_output_schema가_아니면_설정을_그대로_보낸다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)
    output_config = {"effort": "low"}

    client.messages.create(
        model=_SONNET,
        max_tokens=700,
        messages=[{"role": "user", "content": "plain output"}],
        output_config=output_config,
    )

    assert messages.requests[0]["output_config"] is output_config


def test_정본_문장선택_schema도_provider_호환형식으로_정규화된다():
    source_schema = answer_schema()
    original_schema = copy.deepcopy(source_schema)
    normalized = real._provider_output_config(
        {"format": {"type": "json_schema", "schema": source_schema}}
    )
    sent_schema = normalized["format"]["schema"]
    official_schema = real.importlib.import_module("anthropic").transform_schema(
        copy.deepcopy(source_schema)
    )

    def key_count(value, target):
        if isinstance(value, dict):
            return int(target in value) + sum(
                key_count(item, target) for item in value.values()
            )
        if isinstance(value, list):
            return sum(key_count(item, target) for item in value)
        return 0

    assert key_count(source_schema, "uniqueItems") == 2
    assert key_count(sent_schema, "uniqueItems") == 0
    assert source_schema == original_schema
    assert sent_schema == official_schema


def test_schema_정규화가_실패하면_예약과_provider호출은_모두_0회다(monkeypatch):
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)

    def unavailable(_name):
        raise ImportError("시험용 anthropic SDK 없음")

    monkeypatch.setattr(real.importlib, "import_module", unavailable)
    with provider_budget.activate(10_000.0) as budget:
        with pytest.raises(provider_budget.ProviderBudgetUnavailable):
            client.messages.create(
                model=_SONNET,
                max_tokens=700,
                messages=[{"role": "user", "content": "must stay local"}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": answer_schema(),
                    }
                },
            )

        assert budget.accounted_krw == 0

    assert messages.calls == []
    assert metered.usages == []


def test_usage가_있는_실패호출도_실제원가이벤트로_보존한다(
    _paid_provider_budget_context,
):
    class FailedWithUsage(RuntimeError):
        def __init__(self):
            super().__init__("provider rejected after usage")
            self.model = _SONNET
            self.usage = SimpleNamespace(
                input_tokens=2000,
                output_tokens=10,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            )

    class FailedMessages:
        def create(self, **_kwargs):
            raise FailedWithUsage()

    metered = real._MeteredEngine(
        FakeRawEngine(FailedMessages())
    )
    metered.MODEL = _SONNET

    with pytest.raises(FailedWithUsage):
        _client(metered).messages.create(model=_SONNET, max_tokens=100)

    event = real._request_cost_events(metered)[0]
    assert event.failed_call is True
    assert event.input_tokens == 2000
    assert event.cost_krw > 0
    assert real._request_spent_krw(metered) == event.cost_krw
    observation = _paid_provider_budget_context.observations[-1]
    assert observation.billing_disposition is BillingDisposition.KNOWN_COST
    assert observation.known_cost_krw == event.cost_krw


def test_두_요청의_모델과_usage는_겹쳐불러도_각자에게만_쌓인다():
    messages = FakeMessages()
    raw = FakeRawEngine(messages)
    first = real._MeteredEngine(raw)
    second = real._MeteredEngine(raw)
    first.MODEL = _SONNET
    first_client = _client(first)
    second_client = _client(second)

    first_client.messages.create(model="1판-전역값", max_tokens=700)
    second_client.messages.create(model="1판-전역값", max_tokens=700)
    first_client.messages.create(model="1판-전역값", max_tokens=700)

    assert messages.calls == [_SONNET, _HAIKU, _SONNET]
    assert raw.MODEL == _HAIKU, "한 요청의 모델 교체가 1판 모듈 전역을 바꾸면 안 된다"
    assert len(first.usages) == 2
    assert len(second.usages) == 1
    assert real._request_model_label(first) == _SONNET
    assert real._request_model_label(second) == _HAIKU


def test_옛_요청이_8달러를_넘어도_새_요청은_0달러에서_정상_시작한다(
    isolated_engine_path,
):
    old_raw, old, old_client = _isolated_request(
        isolated_engine_path, FakeMessages()
    )
    old_raw._spent_usd = old_raw.BUDGET_STOP_USD + 0.01

    with pytest.raises(RuntimeError, match="예산가드"):
        old._ask(old_client, "old", {})

    new_raw, new, new_client = _isolated_request(
        isolated_engine_path, FakeMessages()
    )
    payload, _usage = new._ask(new_client, "new", {})

    assert payload == {"ok": True}
    assert new_raw._spent_usd == pytest.approx(1.5)
    assert old_raw._spent_usd == pytest.approx(8.01)
    assert old_raw is not new_raw
    assert old_raw.__name__ != new_raw.__name__
    assert old_raw.__name__ not in sys.modules
    assert new_raw.__name__ not in sys.modules


def test_두_thread가_교차해도_8달러_가드는_각_요청에만_작동한다(
    isolated_engine_path,
):
    barrier = threading.Barrier(2)

    class FirstCallBarrierMessages(FakeMessages):
        def create(self, **kwargs):
            if not self.calls:
                barrier.wait(timeout=3)
            return super().create(**kwargs)

    first_raw, first, first_client = _isolated_request(
        isolated_engine_path, FirstCallBarrierMessages()
    )
    second_raw, second, second_client = _isolated_request(
        isolated_engine_path, FirstCallBarrierMessages()
    )
    # 첫 호출은 통과하고 1.5달러가 더해져 8달러를 넘는다. 다음 호출만 막혀야 한다.
    first_raw._spent_usd = 7.0
    outcomes: dict[str, list[str]] = {"first": [], "second": []}
    unexpected: list[BaseException] = []

    def run_two(key, engine, client):
        try:
            with provider_budget.activate(100_000.0), attempt_context.activate(
                _AttemptRecorder().callbacks()
            ):
                for turn in range(2):
                    try:
                        engine._ask(client, f"{key}-{turn}", {})
                        outcomes[key].append("ok")
                    except RuntimeError as exc:
                        if "예산가드" not in str(exc):
                            raise
                        outcomes[key].append("blocked")
        except BaseException as exc:  # thread 예외를 주 thread의 시험 실패로 되돌린다
            unexpected.append(exc)

    threads = [
        threading.Thread(
            target=run_two, args=("first", first, first_client), daemon=True
        ),
        threading.Thread(
            target=run_two, args=("second", second, second_client), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads), "교차 호출 시험이 멈췄습니다"
    assert unexpected == []
    assert outcomes == {
        "first": ["ok", "blocked"],
        "second": ["ok", "ok"],
    }
    assert first_raw._spent_usd == pytest.approx(8.5)
    assert second_raw._spent_usd == pytest.approx(3.0)
    assert len(first.usages) == 1
    assert len(second.usages) == 2


def test_five_interleaved_requests_keep_budget_usage_and_model_isolated(
    isolated_engine_path,
):
    first_round = threading.Barrier(5)
    second_round = threading.Barrier(4)

    class TwoRoundBarrierMessages(FakeMessages):
        def __init__(self, *, joins_second_round: bool):
            super().__init__()
            self.joins_second_round = joins_second_round

        def create(self, **kwargs):
            call_number = len(self.calls) + 1
            if call_number == 1:
                first_round.wait(timeout=3)
            elif call_number == 2 and self.joins_second_round:
                second_round.wait(timeout=3)
            return super().create(**kwargs)

    requests = []
    for index in range(5):
        messages = TwoRoundBarrierMessages(joins_second_round=index != 0)
        raw, metered, client = _isolated_request(
            isolated_engine_path, messages
        )
        metered.MODEL = f"request-model-{index}"
        requests.append((raw, metered, client, messages))

    initial_spends = [7.0, 0.0, 0.25, 0.5, 0.75]
    for (raw, _metered, _client, _messages), spent in zip(
        requests, initial_spends, strict=True
    ):
        raw._spent_usd = spent

    outcomes: list[list[str]] = [[] for _ in requests]
    unexpected: list[BaseException] = []

    def run_two(index, engine, client):
        try:
            with provider_budget.activate(100_000.0), attempt_context.activate(
                _AttemptRecorder().callbacks()
            ):
                for turn in range(2):
                    try:
                        engine._ask(client, f"request-{index}-{turn}", {})
                        outcomes[index].append("ok")
                    except RuntimeError:
                        outcomes[index].append("blocked")
        except BaseException as exc:
            unexpected.append(exc)

    threads = [
        threading.Thread(
            target=run_two,
            args=(index, metered, client),
            daemon=True,
        )
        for index, (_raw, metered, client, _messages) in enumerate(requests)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert unexpected == []
    assert outcomes == [
        ["ok", "blocked"],
        ["ok", "ok"],
        ["ok", "ok"],
        ["ok", "ok"],
        ["ok", "ok"],
    ]

    expected_spends = [8.5, 3.0, 3.25, 3.5, 3.75]
    expected_usage_counts = [1, 2, 2, 2, 2]
    assert len({id(raw) for raw, *_rest in requests}) == 5
    for index, (raw, metered, _client, messages) in enumerate(requests):
        expected_model = f"request-model-{index}"
        assert raw._spent_usd == pytest.approx(expected_spends[index])
        assert len(metered.usages) == expected_usage_counts[index]
        assert real._request_model_label(metered) == expected_model
        assert messages.calls == [expected_model] * expected_usage_counts[index]
        assert raw.__name__ not in sys.modules


def test_1판_원본의_8달러_예산가드_계약은_바꾸지_않는다():
    engine_path = (
        real.paths.PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py"
    )
    tree = ast.parse(engine_path.read_text(encoding="utf-8"))

    def assigned(name):
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            ):
                return ast.literal_eval(node.value)
        raise AssertionError(f"1판 원본에 {name}이 없습니다")

    ask = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_ask"
    )
    client_factory = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_client"
    )
    client_return = next(
        node for node in ast.walk(client_factory) if isinstance(node, ast.Return)
    )
    assert isinstance(client_return.value, ast.Call)
    client_options = {
        keyword.arg: keyword.value for keyword in client_return.value.keywords
    }
    positional = [arg.arg for arg in ask.args.args]
    globals_in_ask = {
        name
        for node in ast.walk(ask)
        if isinstance(node, ast.Global)
        for name in node.names
    }

    assert assigned("BUDGET_STOP_USD") == 8.0
    assert assigned("_spent_usd") == 0.0
    assert positional == ["client", "prompt", "schema", "max_tokens"]
    assert ast.literal_eval(ask.args.defaults[-1]) == 700
    assert "_spent_usd" in globals_in_ask
    assert ast.literal_eval(client_options["max_retries"]) == 0
    assert isinstance(client_options["timeout"], ast.Name)
    assert client_options["timeout"].id == "ANTHROPIC_TIMEOUT_SEC"
    assert assigned("ANTHROPIC_TIMEOUT_SEC") == 180.0


def test_요청모델이_아니라_실제_응답모델_단가와_이름을_쓴다():
    messages = FakeMessages(response_model=_SONNET)
    metered = real._MeteredEngine(FakeRawEngine(messages))

    _client(metered).messages.create(model=_HAIKU, max_tokens=700)

    price_in, price_out = MODEL_PRICES_USD_PER_MTOK[_SONNET]
    expected = (price_in + price_out * 0.1) * AI_COST_KRW_PER_USD
    assert real._request_spent_krw(metered) == pytest.approx(expected)
    assert real._request_model_label(metered) == _SONNET


def test_dated_response_model_snapshot_uses_its_alias_price():
    snapshot = f"{_HAIKU}-20251001"
    messages = FakeMessages(response_model=snapshot)
    metered = real._MeteredEngine(FakeRawEngine(messages))

    _client(metered).messages.create(model=_HAIKU, max_tokens=700)

    price_in, price_out = MODEL_PRICES_USD_PER_MTOK[_HAIKU]
    expected = (price_in + price_out * 0.1) * AI_COST_KRW_PER_USD
    assert real._request_spent_krw(metered) == pytest.approx(expected)
    assert real._request_model_label(metered) == snapshot


def test_near_prefix_response_model_keeps_conservative_unknown_price():
    messages = FakeMessages(response_model=f"{_HAIKU}-20251001-extra")
    metered = real._MeteredEngine(FakeRawEngine(messages))

    _client(metered).messages.create(model=_HAIKU, max_tokens=700)

    price_in, price_out = UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    expected = (price_in + price_out * 0.1) * AI_COST_KRW_PER_USD
    assert real._request_spent_krw(metered) == pytest.approx(expected)


def test_모르는_응답모델은_보수적_공통값을_쓴다():
    messages = FakeMessages(response_model="future-model")
    metered = real._MeteredEngine(FakeRawEngine(messages))

    _client(metered).messages.create(model=_HAIKU, max_tokens=700)

    price_in, price_out = UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    expected = (price_in + price_out * 0.1) * AI_COST_KRW_PER_USD
    assert real._request_spent_krw(metered) == pytest.approx(expected)


def test_응답은_왔지만_usage가_없으면_0원으로_확정하지_않는다(
    _paid_provider_budget_context,
):
    metered = real._MeteredEngine(FakeRawEngine(FakeMessages(omit_usage=True)))

    _client(metered).messages.create(model=_HAIKU, max_tokens=700)

    assert real._request_spent_krw(metered) == 0.0
    assert real._request_billing_uncertain(metered) is True
    observation = _paid_provider_budget_context.observations[-1]
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.known_cost_krw == 0.0
    assert observation.liability_krw > 0


def test_응답뒤_후속코드가_터져도_그_요청비용으로_FAILED를_돌려준다(monkeypatch):
    messages = FakeMessages(response_model=_SONNET)
    raw = FakeRawEngine(messages)
    monkeypatch.setattr(real, "_engine", lambda: raw)

    def broken(_self, _user_input, _card, _on_step, *, engine):
        _client(engine).messages.create(model=_HAIKU, max_tokens=700)
        raise RuntimeError("응답 뒤 후속 코드 실패")

    monkeypatch.setattr(real.RealPipeline, "_run_metered", broken)

    result = real.RealPipeline().run(
        UserInput(company="회사", job="직무", region="서울"),
        CompanyCard(
            legal_name="회사", typed_name="회사", address="서울",
            ceo="대표", founded="20200101", ref="corp",
        ),
    )

    assert result.outcome is Outcome.FAILED
    assert result.cost_krw > 0
    assert result.model == _SONNET
    assert result.billing_uncertain is False


def test_provider_예외는_앞선_확정비용을_남기고_과금불확실을_알린다(monkeypatch):
    messages = FakeMessages(fail_on_call=2)
    raw = FakeRawEngine(messages)
    monkeypatch.setattr(real, "_engine", lambda: raw)

    def timeout(_self, _user_input, _card, _on_step, *, engine):
        client = _client(engine)
        client.messages.create(model=_HAIKU, max_tokens=700)
        client.messages.create(model=_HAIKU, max_tokens=700)

    monkeypatch.setattr(real.RealPipeline, "_run_metered", timeout)

    result = real.RealPipeline().run(
        UserInput(company="회사", job="직무", region="서울"),
        CompanyCard(
            legal_name="회사", typed_name="회사", address="서울",
            ceo="대표", founded="20200101", ref="corp",
        ),
    )

    assert result.outcome is Outcome.FAILED
    assert result.cost_krw > 0
    assert result.billing_uncertain is True


def test_usage없는_provider_예외뒤에는_같은요청의_추가호출을_막는다(
    _paid_provider_budget_context,
):
    messages = FakeMessages(fail_on_call=1)
    metered = real._MeteredEngine(FakeRawEngine(messages))
    client = _client(metered)

    with pytest.raises(TimeoutError):
        client.messages.create(
            model=_HAIKU,
            max_tokens=700,
            messages=[{"role": "user", "content": "first unknown call"}],
        )
    with pytest.raises(provider_budget.ProviderBudgetUnavailable):
        client.messages.create(
            model=_HAIKU,
            max_tokens=700,
            messages=[{"role": "user", "content": "must stay local"}],
        )

    assert messages.calls == [_HAIKU]
    assert metered.billing_uncertain is True
    observation = _paid_provider_budget_context.observations[-1]
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.liability_krw > 0


def test_DART_회사응답_실패는_회사없음이_아니라_기술실패다(monkeypatch):
    class LookupEngine(FakeRawEngine):
        class UsageCounter:
            pass

        def load_env(self):
            return None

        def identify(self, *_args):
            return "corp-code"

        def get_json(self, *_args):
            return {"status": "999", "message": "DART 오류"}

    raw = LookupEngine(FakeMessages())
    monkeypatch.setattr(real, "_engine", lambda: raw)
    monkeypatch.setattr(real, "_company_index", lambda: [])

    result = real.RealPipeline().find_company_metered(
        UserInput(company="회사", job="직무", region="서울")
    )

    assert result.card is None
    assert result.failed is True
    assert result.model == "", "AI 응답이 없는데 기본 모델을 썼다고 적으면 안 된다"


def test_작은_입력은_gateway를_거쳐_provider를_한번_부른다(
    _paid_provider_budget_context,
):
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))

    with provider_budget.activate(10_000.0):
        _client(metered).messages.create(
            model=_HAIKU,
            max_tokens=32,
            messages=[{"role": "user", "content": "작은 입력"}],
        )

    assert messages.calls == [_HAIKU]
    assert len(metered.usages) == 1
    assert [event[0] for event in _paid_provider_budget_context.events] == [
        "begin",
        "heartbeat",
        "dispatch",
        "observation",
    ]
    assert (
        _paid_provider_budget_context.observations[-1].billing_disposition
        is BillingDisposition.KNOWN_COST
    )


def test_예상예약_잔액이_부족하면_provider는_0회다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))

    with provider_budget.activate(0.01):
        with pytest.raises(provider_budget.ProviderBudgetExceeded):
            _client(metered).messages.create(
                model=_HAIKU,
                max_tokens=700,
                messages=[{"role": "user", "content": "호출하면 안 됨"}],
            )

    assert messages.calls == []
    assert metered.usages == []


def test_예약문맥이_없으면_provider는_0회다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    token = provider_budget._CURRENT.set(None)
    try:
        with pytest.raises(provider_budget.ProviderBudgetUnavailable):
            _client(metered).messages.create(
                model=_HAIKU,
                max_tokens=700,
                messages=[{"role": "user", "content": "호출하면 안 됨"}],
            )
    finally:
        provider_budget._CURRENT.reset(token)

    assert messages.calls == []


def test_attempt_문맥이_없으면_로컬예약을_돌려주고_provider는_0회다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    token = attempt_context._CURRENT.set(None)
    try:
        with provider_budget.activate(10_000.0) as budget:
            with pytest.raises(provider_budget.ProviderBudgetUnavailable):
                _client(metered).messages.create(
                    model=_HAIKU,
                    max_tokens=700,
                    messages=[{"role": "user", "content": "호출하면 안 됨"}],
                )
            assert budget.accounted_krw == 0.0
    finally:
        attempt_context._CURRENT.reset(token)

    assert messages.calls == []


def test_heartbeat_callback이_실패하면_로컬예약을_돌려주고_provider는_0회다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))
    dispatches = []

    def fail_heartbeat(_token):
        raise RuntimeError("시험용 lease 연장 실패")

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: "attempt-1",
        fail_heartbeat,
        lambda token: dispatches.append(token),
        lambda _token, _observation: None,
    )
    with provider_budget.activate(10_000.0) as budget, attempt_context.activate(
        callbacks
    ):
        with pytest.raises(provider_budget.ProviderBudgetUnavailable):
            _client(metered).messages.create(
                model=_HAIKU,
                max_tokens=700,
                messages=[{"role": "user", "content": "호출하면 안 됨"}],
            )
        assert budget.accounted_krw == 0.0

    assert dispatches == []
    assert messages.calls == []


def test_전송의도_callback이_실패하면_로컬예약을_돌려주고_provider는_0회다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))

    def fail_dispatch(_token):
        raise RuntimeError("시험용 DB 실패")

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: "attempt-1",
        lambda _token: None,
        fail_dispatch,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(10_000.0) as budget, attempt_context.activate(
        callbacks
    ):
        with pytest.raises(provider_budget.ProviderBudgetUnavailable):
            _client(metered).messages.create(
                model=_HAIKU,
                max_tokens=700,
                messages=[{"role": "user", "content": "호출하면 안 됨"}],
            )
        assert budget.accounted_krw == 0.0

    assert messages.calls == []


def test_결과_callback이_실패하면_provider는_1회이고_예약을_부채후보로_남긴다():
    messages = FakeMessages()
    metered = real._MeteredEngine(FakeRawEngine(messages))

    def fail_record(_token, _observation):
        raise RuntimeError("시험용 결과 DB 실패")

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: "attempt-1",
        lambda _token: None,
        lambda _token: None,
        fail_record,
    )
    with provider_budget.activate(10_000.0) as budget, attempt_context.activate(
        callbacks
    ):
        with pytest.raises(provider_budget.ProviderBudgetUnavailable):
            _client(metered).messages.create(
                model=_HAIKU,
                max_tokens=700,
                messages=[{"role": "user", "content": "한 번만 호출"}],
            )
        assert budget.accounted_krw > 0.0

    assert messages.calls == [_HAIKU]
    assert metered.billing_uncertain is True


def test_sdk_내부_retry를_provider경계에서_0으로_고정한다():
    messages = FakeMessages()

    class RetryAwareClient:
        def __init__(self):
            self.messages = messages
            self.options = []

        def with_options(self, **kwargs):
            self.options.append(kwargs)
            return self

    raw = FakeRawEngine(messages)
    metered = real._MeteredEngine(raw)
    client = RetryAwareClient()

    wrapped = real._metered_client(metered, client)

    assert client.options == [
        {"max_retries": 0, "timeout": real.ANTHROPIC_TIMEOUT_SEC}
    ]
    assert wrapped.messages is not messages


def test_Naver_인증·한도오류는_같은요청에서_반복호출하지_않는다(
    monkeypatch,
):
    calls: list[str] = []

    class PermanentProviderFailure(RuntimeError):
        stop_further_requests = True

    class Engine:
        def search_news(self, query, **_kwargs):
            calls.append(query)
            raise PermanentProviderFailure("인증 거부")

    monkeypatch.setattr(
        real.newspick_logic,
        "search_terms",
        lambda *_args, **_kwargs: [
            ("첫 검색", "date", 10),
            ("둘째 검색", "date", 10),
            ("셋째 검색", "sim", 10),
        ],
    )
    steps: list[dict] = []

    result = real._collect_news(Engine(), None, "회사", {}, steps)

    assert result == []
    assert calls == ["첫 검색"]
    assert steps[0]["검색별"] == {"첫 검색": 0}
