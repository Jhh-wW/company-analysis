"""명시 작성 모델의 요청 격리·송신·예약·캐시·보존 결속을 확인한다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from threading import Barrier
from types import SimpleNamespace

import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.core.pricing import usage_cost_krw
from src.features.pipeline import real
from src.features.pipeline.tests.test_v2_prompt_cache_blocks import (
    _FakeRawEngine, _RecordingMessages,
)


@contextmanager
def budget_context():
    reservations = []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, operation, reserved: reservations.append((operation, reserved)) or object(),
        lambda token: None, lambda token: None, lambda token, observation: None,
    )
    with real.provider_budget.activate(100_000), attempt_context.activate(callbacks):
        yield reservations


@pytest.fixture(autouse=True)
def isolated_model_setting(monkeypatch):
    monkeypatch.delenv(real.V2_WRITER_MODEL_ENV, raising=False)


def engine_and_client():
    messages = _RecordingMessages(response_text='작성 응답')
    raw = _FakeRawEngine(messages)
    engine = real._MeteredEngine(raw)
    messages.attach(engine)
    return engine, real._metered_client(engine, raw._client()), messages, raw


@pytest.mark.parametrize('model', sorted(real.V2_WRITER_ALLOWED_MODELS))
def test_writer_sdk_reservation_usage_and_replay_share_selected_model(monkeypatch, model):
    from src.features.pipeline import private_replay

    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, model)
    engine, client, messages, raw = engine_and_client()
    replay = []
    monkeypatch.setattr(private_replay, 'local_provider_replay_enabled', lambda: True)
    monkeypatch.setattr(private_replay, 'record_local_provider_replay', lambda **kwargs: replay.append(kwargs) or True)
    ask = real._v2_ask_via_provider(engine, client, stage='v2_compose', max_tokens=6000)
    with budget_context() as reservations:
        assert ask('자기 원문') == '작성 응답'
    assert messages.requests[0]['model'] == engine.usages[0]['model'] == replay[0]['model'] == model
    assert reservations == [('v2_compose', usage_cost_krw(
        model, 1234 + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS, 6000))]
    assert engine.usages[0]['cost_krw'] == usage_cost_krw(model, 1000, 100)
    assert raw.MODEL == engine.MODEL == 'claude-haiku-4-5'
    assert replay[0]['stage'] == 'v2_compose' and replay[0]['output_limit'] == 6000
    assert replay[0]['prompt'] == '자기 원문'


def test_unset_writer_keeps_original_model(monkeypatch):
    engine, client, messages, raw = engine_and_client()
    with budget_context():
        with engine.stage_context('v2_compose'):
            client.messages.create(model='잘못된 호출자 모델', max_tokens=6000)
    assert messages.requests[0]['model'] == raw.MODEL
    assert real._configured_v2_writer_model(engine) == ''


@pytest.mark.parametrize('stage', ['news_grounding', 'collect', 'legacy', *sorted(real.V2_REVIEW_MODEL_STAGES)])
def test_writer_selection_does_not_change_other_stages(monkeypatch, stage):
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, 'claude-sonnet-4-6')
    engine, client, messages, raw = engine_and_client()
    with budget_context():
        with engine.stage_context(stage):
            client.messages.create(model='호출자', max_tokens=700)
    expected = real.V2_REVIEW_MODEL if stage in real.V2_REVIEW_MODEL_STAGES else raw.MODEL
    assert messages.requests[0]['model'] == expected
    assert engine.MODEL == raw.MODEL


def test_two_concurrent_requests_keep_captured_models(monkeypatch):
    pairs = []
    for model in sorted(real.V2_WRITER_ALLOWED_MODELS):
        monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, model)
        pairs.append((model, engine_and_client()))
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, '실행 중 변경된 환경')
    barrier = Barrier(2)

    def dispatch(pair):
        model, (engine, client, messages, raw) = pair
        with budget_context():
            barrier.wait(timeout=5)
            with engine.stage_context('v2_compose'):
                client.messages.create(model=raw.MODEL, max_tokens=6000)
        return messages.requests[0]['model'], real._configured_v2_writer_model(engine), model

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(sent == captured == expected for sent, captured, expected in pool.map(dispatch, pairs))


def test_writer_cache_selection_is_v2_only_and_unset_namespace_is_unchanged(monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'a' * 40)
    real.engine_build_identity.freeze_process_engine_build_identity()
    identity = real.engine_build_identity.process_engine_build_identity()
    engine, _, _, raw = engine_and_client()

    def namespace(target, mode):
        return real._generation_cache_namespace(target, identity, mode, release_mode=None)

    v1 = namespace(engine, real.engine_mode.EngineMode.V1)
    v2 = namespace(engine, real.engine_mode.EngineMode.V2)
    assert v2 == namespace(SimpleNamespace(MODEL=raw.MODEL), real.engine_mode.EngineMode.V2)
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, 'claude-sonnet-4-6')
    selected, _, _, _ = engine_and_client()
    assert namespace(selected, real.engine_mode.EngineMode.V1) == v1
    assert namespace(selected, real.engine_mode.EngineMode.V2) != v2
    assert namespace(engine, real.engine_mode.EngineMode.V2) == v2


@pytest.mark.parametrize('value', ['unknown', ' claude-sonnet-4-6', 'claude-sonnet-4-6 ', 'CLAUDE-SONNET-4-6'])
def test_invalid_writer_is_rejected_before_sdk_attempt_or_reservation(monkeypatch, value):
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, value)
    engine, client, messages, _ = engine_and_client()
    with budget_context() as reservations:
        with pytest.raises(real.provider_budget.ProviderBudgetUnavailable):
            with engine.stage_context('v2_compose'):
                client.messages.create(model='claude-haiku-4-5', max_tokens=6000)
        assert reservations == []
        assert engine._provider_call_count == 0
    assert messages.requests == []


def test_sonnet_writer_budget_cannot_use_haiku_reservation(monkeypatch):
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, 'claude-sonnet-4-6')
    engine, client, messages, _ = engine_and_client()
    bound = 1234 + real.provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
    cheap = usage_cost_krw('claude-haiku-4-5', bound, 6000)
    expensive = usage_cost_krw('claude-sonnet-4-6', bound, 6000)
    with budget_context() as reservations:
        with real.provider_budget.activate((cheap + expensive) / 2):
            with pytest.raises(real.provider_budget.ProviderBudgetExceeded):
                with engine.stage_context('v2_compose'):
                    client.messages.create(model='claude-haiku-4-5', max_tokens=6000)
    assert reservations == [] and messages.requests == []


@pytest.mark.parametrize('model', sorted(real.V2_WRITER_ALLOWED_MODELS))
def test_production_run_captures_writer_before_cache_and_sdk(monkeypatch, model):
    from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput

    snapshot = {'model_settings': {real.V2_WRITER_MODEL_ENV: model}}
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, snapshot['model_settings'][real.V2_WRITER_MODEL_ENV])
    monkeypatch.setenv('RENDER_GIT_COMMIT', 'a' * 40)
    real.engine_build_identity.freeze_process_engine_build_identity()
    monkeypatch.setattr(real.engine_mode, 'process_engine_mode', lambda: real.engine_mode.EngineMode.V2)
    monkeypatch.setattr(real.generation_coordination, 'frozen_engine_build_identity', lambda: None)
    messages = _RecordingMessages(response_text='작성 응답')
    raw = _FakeRawEngine(messages)
    monkeypatch.setattr(real, '_engine', lambda: raw)
    observations = []

    def inspect_request(self, user_input, card, on_step, *, engine, build_identity, generation_mode):
        assert isinstance(engine, real._MeteredEngine)
        messages.attach(engine)
        namespace = real._generation_cache_namespace(engine, build_identity, generation_mode, release_mode=None)
        # 요청 생성 뒤 환경이 바뀌어도 같은 요청의 캐시와 송신 모델은 바뀌지 않는다.
        monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, '다른 요청의 설정')
        assert real._generation_cache_namespace(engine, build_identity, generation_mode, release_mode=None) == namespace
        client = real._metered_client(engine, raw._client())
        ask = real._v2_ask_via_provider(engine, client, stage='v2_compose', max_tokens=6000)
        assert ask('자기 원문') == '작성 응답'
        observations.append((namespace, engine.usages[0]['model']))
        return RunResult(outcome=Outcome.GATE_STOPPED)

    monkeypatch.setattr(real.RealPipeline, '_run_metered', inspect_request)
    card = CompanyCard(legal_name='예시제조', typed_name='예시제조', address='', ceo='', founded='', ref='00000001')
    with budget_context() as reservations:
        result = real.RealPipeline().run(UserInput(company='예시제조', job='', region=''), card)
    assert len(observations) == 1
    namespace, actual_model = observations[0]
    assert actual_model == messages.requests[0]['model'] == result.model == model
    expected_models = {'pipeline': raw.MODEL, 'reviewer': real.V2_REVIEW_MODEL, 'writer': model}
    expected_digest = hashlib.sha256(json.dumps(expected_models, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert namespace.model_identity_sha256 == expected_digest
    assert reservations[0][0] == 'v2_compose'
