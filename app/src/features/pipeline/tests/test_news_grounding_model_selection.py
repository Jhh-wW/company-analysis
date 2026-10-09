"""뉴스 산업 검수의 송신·가격·캐시·보존 모델이 같은 선택을 사용한다."""
import pytest

from src.core.pricing import usage_cost_krw
from src.features.news_intake import analysis_result_cache as cache
from src.features.pipeline import real
from src.features.pipeline.tests.test_news_analysis_exact_cache import (
    CAP, INPUT, MODEL, NEWS_OUTPUT, attempts, collect, environment, setup_engine,
)


def test_뉴스검수_전송예약실비보존과_캐시신원은_같은검수모델이다(monkeypatch, attempts):
    records, namespaces = [], []
    original = real.analyze_with_cache
    monkeypatch.setattr(real, "_record_local_news_analysis_replay", lambda **kwargs: records.append(kwargs))

    def inspect(provider, *, namespace, reserve_hit):
        namespaces.append(namespace)
        return original(provider, namespace=namespace, reserve_hit=reserve_hit)

    monkeypatch.setattr(real, "analyze_with_cache", inspect)
    engine, client, messages = setup_engine()
    with real.provider_budget.activate(1000) as budget:
        result = collect(engine, client)
    selected = real.V2_REVIEW_MODEL
    assert result.fragments
    assert len(messages.requests) == len(messages.counts) == len(records) == len(attempts[0]) == 1
    assert messages.requests[0]["model"] == messages.counts[0]["model"] == records[0]["model"] == namespaces[0].model == engine.usages[0]["model"] == selected
    assert records[0]["max_tokens"] == messages.requests[0]["max_tokens"] == CAP
    assert attempts[0][0][1:] == ("news_grounding", usage_cost_krw(selected,
        INPUT + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, CAP))
    assert budget.accounted_krw == usage_cost_krw(selected, INPUT, NEWS_OUTPUT)
    assert engine.MODEL == MODEL


def test_옛뉴스모델의_분석캐시와_분리하고_새적중은_추가전송하지_않는다(monkeypatch, attempts):
    stages = real.V2_REVIEW_MODEL_STAGES
    runs = []
    for selected_stages in (stages - {"news_grounding"}, stages, stages):
        monkeypatch.setattr(real, "V2_REVIEW_MODEL_STAGES", selected_stages)
        engine, client, messages = setup_engine()
        with real.provider_budget.activate(1000) as budget:
            result = collect(engine, client)
        runs.append((engine, messages, result, budget.accounted_krw))
    assert [len(messages.requests) for _, messages, _, _ in runs] == [1, 1, 0]
    assert [run[2].diagnostics["분석캐시적중"] for run in runs] == [0, 0, 1]
    assert [run[2].diagnostics["분석논리호출"] for run in runs] == [1, 1, 1]
    assert runs[0][1].requests[0]["model"] == MODEL
    assert runs[1][1].requests[0]["model"] == real.V2_REVIEW_MODEL
    assert runs[0][2].fragments == runs[1][2].fragments == runs[2][2].fragments
    assert len(cache.PROCESS_ANALYSIS_CACHE._entries) == 2
    assert len(attempts[0]) == len(attempts[1]) == 2
    assert runs[2][0]._provider_dispatch_count == runs[2][0]._provider_call_count == 0
    assert runs[2][0]._cached_provider_call_slots == 1 and runs[2][3] == 0


@pytest.mark.parametrize("missing", ["model", "event"])
def test_뉴스응답_모델관측이_없어도_보존fallback은_예약모델이다(monkeypatch, attempts, missing):
    records = []
    monkeypatch.setattr(real, "_record_local_news_analysis_replay", lambda **kwargs: records.append(kwargs))
    engine, client, messages = setup_engine()
    ask = engine._ask

    def omit_observation(*args, **kwargs):
        payload, usage = ask(*args, **kwargs)
        if missing == "model":
            engine.usages[-1].pop(real.USAGE_MODEL_KEY, None)
        else:
            engine.usages.clear()
        return payload, usage

    engine._ask = omit_observation
    with real.provider_budget.activate(1000):
        collect(engine, client)
    assert len(messages.requests) == len(records) == len(attempts[0]) == 1
    assert records[0]["model"] == messages.requests[0]["model"] == real.V2_REVIEW_MODEL


@pytest.mark.parametrize("stage", ["collect", "search", "rank", "v2_compose", "v2_rewrite", "legacy"])
def test_검색작성후보선택의_기본모델은_유지한다(stage, attempts, monkeypatch):
    monkeypatch.delenv(real.V2_WRITER_MODEL_ENV, raising=False)
    engine, client, messages = setup_engine()
    with real.provider_budget.activate(1000):
        with engine.stage_context(stage):
            client.messages.create(model="잘못된 호출자 모델", max_tokens=CAP,
                                   messages=[{"role": "user", "content": "합성 입력"}])
    assert messages.requests[0]["model"] == messages.counts[0]["model"] == engine.MODEL == MODEL
