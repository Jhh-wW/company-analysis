"""요청 선택과 웹의 실제 provider 예산 문맥을 같은 값으로 묶는다."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import datetime as dt
from threading import Barrier
from types import SimpleNamespace

import pytest

from src.core import deployment_identity
from src.features.budget import provider_budget
from src.shared import engine_build_identity, generation_coordination
from src.web import generation_singleflight, job_runtime, paid_runtime


@pytest.fixture
def session_factory(monkeypatch):
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    engine_build_identity.freeze_process_engine_build_identity()
    identity = engine_build_identity.process_engine_build_identity()
    def create(run_id="writer-session"):
        session = generation_singleflight.GenerationSession(run_id=run_id, share_key="writer-policy",
            billing_bucket_id="writer-policy", cap_krw=15000, on_paid_phase=lambda ticket: None,
            build_identity=identity)
        session._state = "bypass"
        return session
    return create


def install_phase_stub(monkeypatch):
    records = []
    def begin(**kwargs):
        records.append(kwargs)
        return SimpleNamespace(reserved_krw=kwargs.get("requested_cost_krw", 2000), lease_owner_id="")
    monkeypatch.setattr(paid_runtime, "_begin_paid_phase", begin)
    return records


@pytest.mark.parametrize("model,is_v2,expected", [
    ("claude-sonnet-4-6", True, 4000), ("claude-haiku-4-5", True, 2000),
    ("", True, 2000), ("claude-sonnet-4-6", False, 2000),
])
def test_frozen_choice_reaches_existing_phase_and_provider_context(session_factory, monkeypatch, model, is_v2, expected):
    session = session_factory()
    records = install_phase_stub(monkeypatch)
    with generation_coordination.activate(session.callbacks):
        generation_coordination.bind_writer_model(model, is_v2=is_v2)
        assert records == []
        monkeypatch.setenv("REPORT_V2_WRITER_MODEL", "다른 요청 값")
        generation_coordination.ensure_paid_phase()
        assert provider_budget.current().total_krw == expected
        session.close_provider_context()
    assert records[0]["requested_cost_krw"] == expected
    assert records[0]["cap_krw"] == 15000


def test_guarded_job_callback_clone_preserves_writer_binding(session_factory, monkeypatch):
    session = session_factory()
    records = install_phase_stub(monkeypatch)
    job = SimpleNamespace(share_link_hash="bound-link")
    checks = []
    monkeypatch.setattr(job_runtime, "_require_open_share_link", lambda job: checks.append("checked"))
    guarded = job_runtime._link_guarded_callbacks(job, session.callbacks)
    assert guarded.bind_writer_model == session.bind_writer_model
    with generation_coordination.activate(guarded):
        generation_coordination.bind_writer_model("claude-sonnet-4-6", is_v2=True)
        generation_coordination.ensure_paid_phase()
        assert provider_budget.current().total_krw == 4000
        session.close_provider_context()
    assert checks == ["checked"] and records[0]["requested_cost_krw"] == 4000


def test_changed_or_late_selection_cannot_reuse_cheaper_phase(session_factory, monkeypatch):
    session = session_factory()
    install_phase_stub(monkeypatch)
    session.bind_writer_model("claude-haiku-4-5", True)
    with pytest.raises(generation_singleflight.GenerationSingleflightUnavailable):
        session.bind_writer_model("claude-sonnet-4-6", True)
    session.ensure_paid_phase()
    session.close_provider_context()
    with pytest.raises(generation_singleflight.GenerationSingleflightUnavailable):
        session.bind_writer_model("claude-sonnet-4-6", True)
    unbound = session_factory("unbound")
    unbound.ensure_paid_phase()
    unbound.close_provider_context()
    with pytest.raises(generation_singleflight.GenerationSingleflightUnavailable):
        unbound.bind_writer_model("claude-sonnet-4-6", True)


def test_explicit_model_requires_capable_active_callbacks(session_factory):
    callbacks = replace(session_factory().callbacks, bind_writer_model=None)
    with generation_coordination.activate(callbacks):
        generation_coordination.bind_writer_model("", is_v2=True)
        generation_coordination.bind_writer_model("", is_v2=False)
        with pytest.raises(generation_coordination.GenerationCoordinationError):
            generation_coordination.bind_writer_model("claude-sonnet-4-6", is_v2=True)


def test_concurrent_requests_keep_different_phase_contexts(session_factory, monkeypatch):
    records = install_phase_stub(monkeypatch)
    sessions = [session_factory("sonnet"), session_factory("haiku")]
    barrier = Barrier(2)
    def execute(pair):
        session, model = pair
        with generation_coordination.activate(session.callbacks):
            generation_coordination.bind_writer_model(model, is_v2=True)
            barrier.wait(timeout=5)
            generation_coordination.ensure_paid_phase()
            try:
                limit = provider_budget.current().total_krw
            finally:
                session.close_provider_context()
            return limit
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(execute, zip(sessions, ("claude-sonnet-4-6", "claude-haiku-4-5")))) == [4000, 2000]
    assert {record["run_id"]: record["requested_cost_krw"] for record in records} == {"sonnet": 4000, "haiku": 2000}


def test_same_production_request_uses_sonnet_in_phase_sdk_usage_and_cache(session_factory, monkeypatch):
    import hashlib
    import json
    from src.features.pipeline import real
    from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
    from src.features.pipeline.tests.test_v2_writer_model_selection import engine_and_client
    from src.core.provider_gateway import attempt_context
    from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks

    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, "claude-sonnet-4-6")
    _, _, messages, raw = engine_and_client()
    monkeypatch.setattr(real, "_engine", lambda: raw)
    monkeypatch.setattr(real.engine_mode, "process_engine_mode", lambda: real.engine_mode.EngineMode.V2)
    session = session_factory()
    records = install_phase_stub(monkeypatch)
    observed = []
    attempts = []
    attempt_callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: attempts.append((stage, reserved)) or object(),
        lambda token: None, lambda token: None, lambda token, observation: None)
    def inspect_request(self, user_input, card, on_step, *, engine, build_identity, generation_mode):
        assert session._writer_selection == ("claude-sonnet-4-6", True) and records == []
        namespace = real._generation_cache_namespace(engine, build_identity, generation_mode, release_mode=None)
        expected_models = {"pipeline": raw.MODEL, "reviewer": real.V2_REVIEW_MODEL, "writer": "claude-sonnet-4-6"}
        expected_sha = hashlib.sha256(json.dumps(expected_models, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert namespace.model_identity_sha256 == expected_sha
        monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, "다른 요청의 환경")
        messages.attach(engine)
        ask = real._v2_ask_via_provider(engine, real._metered_client(engine, raw._client()), stage="v2_compose", max_tokens=6000)
        assert ask("검증용 원문") == "작성 응답"
        assert provider_budget.current().total_krw == 4000
        assert messages.requests[0]["model"] == engine.usages[0]["model"] == "claude-sonnet-4-6"
        observed.append(namespace.model_identity_sha256)
        return RunResult(outcome=Outcome.GATE_STOPPED)
    monkeypatch.setattr(real.RealPipeline, "_run_metered", inspect_request)
    try:
        with generation_coordination.activate(session.callbacks), attempt_context.activate(attempt_callbacks):
            result = real.RealPipeline().run(UserInput(company="예시제조", job="", region=""),
                CompanyCard(legal_name="예시제조", typed_name="예시제조", address="", ceo="", founded="", ref="00000001"))
    finally:
        session.close_provider_context()
    assert len(records) == len(observed) == 1 and records[0]["requested_cost_krw"] == 4000
    assert len(attempts) == 1 and attempts[0][0] == "v2_compose"
    assert result.outcome is Outcome.GATE_STOPPED
