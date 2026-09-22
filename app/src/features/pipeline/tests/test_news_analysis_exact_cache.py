"""실제 ask→계량→가짜 전송 경계에서 적중 비용·논리 몫·한계를 검증한다."""

from __future__ import annotations

import ast
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import anthropic
import pytest

from src.core import news_research_adapter, pricing
from src.core.constants import MAX_AI_CALLS_PER_REQUEST
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.composer.port import AskFatalError
from src.features.news_intake import analysis_cache_constants as cc
from src.features.news_intake import analysis_result_cache as cache
from src.features.news_intake.tests.test_collection import (
    AS_OF, BODY, COMPANY, POLICY, accepted, item, snapshot,
)
from src.features.pipeline import real
from src.shared import engine_build_identity


MODEL = "claude-haiku-4-5"
CAP = 128
INPUT = 100
NEWS_OUTPUT = 10
TEST_POLICY = replace(POLICY, analysis_max_tokens=CAP)


def raw_engine():
    # 원본 _ask를 실행한다. 파일 전체 import에 딸린 환경·외부 자원은 열지 않는다.
    path = real.paths.PROJECT_ROOT / "analysis_engine/tools/run_pilot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ask = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_ask")
    ask_source = "from __future__ import annotations\n" + ast.unparse(ask)
    diagnostic_path = real.paths.PROJECT_ROOT / "analysis_engine/src/features/provider_diagnostics/logic.py"
    diagnostic_tree = ast.parse(diagnostic_path.read_text(encoding="utf-8"))
    diagnostic = next(node for node in diagnostic_tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == "build_usage_diagnostic")
    namespace = dict(anthropic=anthropic, time=time, json=json, ai_pricing=pricing,
                     MODEL=MODEL, BUDGET_STOP_USD=8.0, _spent_usd=0.0,
                     safe_stop_reason=lambda value: value)
    exec(compile("from __future__ import annotations\n" + ast.unparse(diagnostic), str(diagnostic_path), "exec"), namespace)
    exec(compile(ask_source, str(path), "exec"), namespace)
    return SimpleNamespace(MODEL=MODEL, _ask=namespace["_ask"])


class Messages:
    def __init__(self, *, stop_reason="end_turn", failure=False, invalid=False, omit_usage=False):
        self.requests, self.counts = [], []
        self.stop_reason, self.failure, self.invalid, self.omit_usage = stop_reason, failure, invalid, omit_usage

    def count_tokens(self, **kwargs):
        self.counts.append(kwargs)
        return SimpleNamespace(input_tokens=INPUT)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.failure:
            raise TimeoutError("offline provider failure")
        prompt = kwargs["messages"][0]["content"]
        news = isinstance(prompt, str) and "자료 시작:\n" in prompt
        output = {"items": []}
        if news:
            articles = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"]
            output = {"items": [accepted(article) for article in articles]}
            if self.invalid:
                output["items"] = []
        return SimpleNamespace(
            model=kwargs["model"], stop_reason=self.stop_reason,
            content=[SimpleNamespace(text=json.dumps(output, ensure_ascii=False))],
            usage=None if self.omit_usage else SimpleNamespace(
                input_tokens=INPUT, output_tokens=NEWS_OUTPUT if news else CAP,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            ),
        )


def setup_engine(messages=None, *, raw=None):
    messages = messages or Messages()
    engine = real._MeteredEngine(raw or raw_engine())
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    return engine, client, messages


def collect(engine, client, *, policy=TEST_POLICY):
    snap, _ = snapshot([item()], policy=policy)
    session = news_research_adapter.NewsResearchSession(COMPANY, AS_OF, snap, policy)
    return session.collect(fetch_text=lambda _: BODY, analyze_grounded=real._news_grounded_analyzer(engine, client))


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv(cc.ANALYSIS_CACHE_ENV, "1")
    monkeypatch.setattr(cache, "PROCESS_ANALYSIS_CACHE", cache.AnalysisResultCache())
    engine_build_identity.freeze_process_engine_build_identity(
        engine_build_identity.EngineBuildIdentity("a" * 40, "deployment-commit-v1:" + "a" * 40),
    )


@pytest.fixture
def attempts():
    starts, observations = [], []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: starts.append((provider, stage, reserved)) or len(starts),
        lambda _: None, lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    with attempt_context.activate(callbacks):
        yield starts, observations


def test_cold_warm_actual_ask_no_fake_usage_or_attempts_and_news_cost_decreases(attempts):
    engines, results, spend = [], [], []
    for _ in range(2):
        engine, client, messages = setup_engine()
        with real.provider_budget.activate(1000) as budget:
            results.append(collect(engine, client))
            spend.append(budget.accounted_krw)
        engines.append((engine, messages))
    cold, warm = results
    assert cold == replace(warm, diagnostics={**warm.diagnostics, "분석캐시적중": 0, "분석캐시보존호출": 0,
        "분석provider호출": 1})
    assert cold.diagnostics["분석provider호출"] == 1 and warm.diagnostics["분석provider호출"] == 0
    assert cold.diagnostics["분석논리호출"] == warm.diagnostics["분석논리호출"] == 1
    assert cold.diagnostics["분석provider미관측"] == warm.diagnostics["분석provider미관측"] == 0
    assert cold.fragments and warm.diagnostics["분석캐시적중"] == 1
    assert len(attempts[0]) == len(attempts[1]) == 1
    assert len(engines[0][1].requests) == 1 and not engines[1][1].requests
    assert len(engines[0][0].usages) == 1 and not engines[1][0].usages
    assert spend[0] > spend[1] == 0
    assert engines[0][0].available_provider_calls(reserved_calls=0) == engines[1][0].available_provider_calls(reserved_calls=0)
    assert engines[1][0]._provider_call_count == 0 and engines[1][0]._cached_provider_call_slots == 1


@pytest.mark.parametrize("reserved", [0, 2, 5])
def test_real_bounded_ask_cannot_reuse_saved_logical_slot_for_optional_call(attempts, reserved):
    runs = []
    for _ in range(2):
        engine, client, messages = setup_engine()
        with real.provider_budget.activate(1000):
            collect(engine, client)
            optional = real._v2_ask_via_provider(engine, client, stage="v2_rewrite", max_tokens=CAP,
                                                reserved_calls=reserved)
            successful = 0
            while True:
                try:
                    optional("선택적 후속 요청")
                except AskFatalError as error:
                    assert error.call_limit is True and error.request_budget is False
                    break
                successful += 1
            before = len(messages.requests)
            with pytest.raises(AskFatalError):
                optional("상한 후 추가 호출도 금지")
            assert len(messages.requests) == before
        runs.append((engine, messages, successful))
    cold, warm = runs
    assert cold[2] == warm[2] == MAX_AI_CALLS_PER_REQUEST - reserved - 1
    assert len(warm[1].requests) == len(cold[1].requests) - 1
    assert real._request_spent_krw(warm[0]) < real._request_spent_krw(cold[0])


def test_monetary_headroom_can_change_optional_work_so_default_remains_off(monkeypatch, attempts):
    # 기존 금액 admission의 반례를 숨기지 않는다. 논리 상한보다 금액이 먼저 닿는 경우다.
    optional_reserve = pricing.usage_cost_krw(
        MODEL, INPUT + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, CAP,
    )
    spends, sent = [], []
    for _ in range(2):
        engine, client, messages = setup_engine()
        optional = real._v2_ask_via_provider(engine, client, stage="v2_rewrite", max_tokens=CAP)
        with real.provider_budget.activate(optional_reserve) as budget:
            collect(engine, client)
            try:
                optional("더 비싼 선택적 후속 호출")
                sent.append(True)
            except AskFatalError as error:
                assert error.request_budget and not error.call_limit
                sent.append(False)
            spends.append(budget.accounted_krw)
            assert budget.total_krw == optional_reserve
            assert budget.accounted_krw <= budget.total_krw
    assert sent == [False, True] and spends[1] > spends[0]
    monkeypatch.delenv(cc.ANALYSIS_CACHE_ENV)
    assert cache.cache_enabled() is False


@pytest.mark.parametrize("kind", ["cutoff", "refusal", "invalid", "failure", "missing_usage", "retry"])
def test_incomplete_failed_or_retry_response_cannot_seed_cache(attempts, kind):
    options = {
        "cutoff": {"stop_reason": "max_tokens"}, "refusal": {"stop_reason": "refusal"},
        "invalid": {"invalid": True}, "failure": {"failure": True},
        "missing_usage": {"omit_usage": True}, "retry": {},
    }[kind]
    raw = raw_engine()
    if kind == "retry":
        ask = raw._ask
        def twice(*args, **kwargs):
            ask(*args, **kwargs)
            return ask(*args, **kwargs)
        raw._ask = twice
    engine, client, _ = setup_engine(Messages(**options), raw=raw)
    with real.provider_budget.activate(1000):
        if kind == "failure":
            with pytest.raises(Exception):
                collect(engine, client)
        else:
            result = collect(engine, client)
            if kind == "missing_usage":
                assert not result.fragments and engine.billing_uncertain
    assert not cache.PROCESS_ANALYSIS_CACHE._entries
    fresh, client, messages = setup_engine()
    with real.provider_budget.activate(1000):
        collect(fresh, client)
    assert len(messages.requests) == 1


def test_zero_budget_and_request_call_limit_block_even_warm_cache(attempts):
    seed, client, _ = setup_engine()
    with real.provider_budget.activate(1000):
        collect(seed, client)
    engine, client, messages = setup_engine()
    with real.provider_budget.activate(1000):
        zero = collect(engine, client, policy=replace(TEST_POLICY, max_analysis_calls=0))
        assert zero.diagnostics["분석AI호출"] == 0
        for _ in range(MAX_AI_CALLS_PER_REQUEST):
            engine.reserve_provider_call()
        exhausted = collect(engine, client)
        assert not exhausted.fragments and exhausted.diagnostics["분석캐시적중"] == 0
    assert not messages.requests and not engine.usages and engine._cached_provider_call_slots == 0


def test_hit_still_requires_paid_context_and_no_uncertain_billing(attempts):
    seed, client, _ = setup_engine()
    with real.provider_budget.activate(1000):
        collect(seed, client)
    engine, client, messages = setup_engine()
    assert not collect(engine, client).fragments
    with real.provider_budget.activate(1000):
        engine.mark_billing_uncertain()
        assert not collect(engine, client).fragments
    assert not messages.requests and not engine.usages and engine._cached_provider_call_slots == 0


def test_request_logical_slot_reservation_is_atomic_and_request_local(attempts):
    engine, _, _ = setup_engine()
    def reserve(_):
        with real.provider_budget.activate(1000):
            try:
                engine.reserve_cached_provider_call()
                return True
            except real.provider_budget.RequestCallLimitReached:
                return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted_slots = list(pool.map(reserve, range(MAX_AI_CALLS_PER_REQUEST * 2)))
    assert sum(accepted_slots) == MAX_AI_CALLS_PER_REQUEST
    assert engine.available_provider_calls(reserved_calls=0) == 0
    other, _, _ = setup_engine()
    assert other.available_provider_calls(reserved_calls=0) == MAX_AI_CALLS_PER_REQUEST
    assert engine._provider_call_count == 0 and not engine.usages and not attempts[0]


def test_runtime_wrapper_keeps_current_body_fetch_on_hit(attempts):
    fetches, outputs = [], []
    for _ in range(2):
        snap, _ = snapshot([item()], policy=TEST_POLICY)
        session = news_research_adapter.NewsResearchSession(COMPANY, AS_OF, snap, TEST_POLICY)
        engine, client, _ = setup_engine()
        steps = []
        def fetch(url):
            fetches.append(url)
            return BODY
        with real.provider_budget.activate(1000):
            outputs.append(real._collect_grounded_news(
                session=session, analyze=real._news_grounded_analyzer(engine, client), fetch_text=fetch,
                corp_id="00123456", official_web_documents=0, collected_on=AS_OF.isoformat(), steps=steps,
            ))
        assert any(step.get("분석AI호출") == 1 for step in steps)
    assert outputs[0] == outputs[1] and len(fetches) == 2
    assert len(attempts[0]) == len(attempts[1]) == 1


@pytest.mark.parametrize("kind", ["default_off", "unknown_build", "unknown_model"])
def test_runtime_unclear_namespace_or_default_off_always_uses_provider(monkeypatch, attempts, kind):
    if kind == "default_off":
        monkeypatch.delenv(cc.ANALYSIS_CACHE_ENV)
    elif kind == "unknown_build":
        monkeypatch.setattr(engine_build_identity, "process_engine_build_identity",
                            lambda: engine_build_identity.EngineBuildIdentity("", "unknown"))
    for _ in range(2):
        engine, client, messages = setup_engine()
        if kind == "unknown_model":
            engine.MODEL = "unknown"
        with real.provider_budget.activate(1000):
            result = collect(engine, client)
        assert result.fragments and result.diagnostics["분석provider호출"] == 1
        assert len(messages.requests) == 1
    assert not cache.PROCESS_ANALYSIS_CACHE._entries


def test_admission_denial_is_not_reported_as_a_provider_dispatch(attempts):
    engine, client, messages = setup_engine()
    with real.provider_budget.activate(0.01):
        result = collect(engine, client)
    assert not result.fragments and result.diagnostics["분석provider호출"] == 0
    assert result.diagnostics["분석논리호출"] == 1
    assert not messages.requests and not engine.usages and not attempts[0]
