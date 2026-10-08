"""실제 run 조립이 첫 수집보다 먼저 SDK와 같은 frozen writer를 전달한다."""
from types import SimpleNamespace

import pytest

from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.pipeline.tests.test_v2_writer_model_selection import engine_and_client
from src.shared import generation_coordination


@pytest.mark.parametrize("model,mode,expected_model,is_v2", [
    ("claude-sonnet-4-6", real.engine_mode.EngineMode.V2, "claude-sonnet-4-6", True),
    ("claude-haiku-4-5", real.engine_mode.EngineMode.V2, "claude-haiku-4-5", True),
    ("", real.engine_mode.EngineMode.V2, "", True),
    ("claude-sonnet-4-6", real.engine_mode.EngineMode.V1, "", False),
])
def test_run_binds_before_collect_preparation_and_does_not_reread_environment(monkeypatch, model, mode, expected_model, is_v2):
    monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, model)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    real.engine_build_identity.freeze_process_engine_build_identity()
    identity = real.engine_build_identity.process_engine_build_identity()
    monkeypatch.setattr(real.engine_mode, "process_engine_mode", lambda: mode)
    engine, _, _, raw = engine_and_client()
    monkeypatch.setattr(real, "_engine", lambda: raw)
    seen = []
    callbacks = generation_coordination.GenerationCallbacks(coordinate=lambda *args: None,
        ensure_paid_phase=lambda: seen.append("paid"), engine_build_identity=identity,
        bind_writer_model=lambda selected, selected_v2: seen.append((selected, selected_v2)))
    def inspect_request(self, user_input, card, on_step, *, engine, build_identity, generation_mode):
        assert seen == [(expected_model, is_v2)]
        monkeypatch.setenv(real.V2_WRITER_MODEL_ENV, "실행 중 다른 요청 환경")
        if is_v2:
            assert real._configured_v2_writer_model(engine) == expected_model
        # 실제 collect/preparation 경로가 이후 공유하는 첫 paid callback이다.
        generation_coordination.ensure_paid_phase()
        return RunResult(outcome=Outcome.GATE_STOPPED)
    monkeypatch.setattr(real.RealPipeline, "_run_metered", inspect_request)
    with generation_coordination.activate(callbacks):
        result = real.RealPipeline().run(UserInput(company="예시제조", job="", region=""),
            CompanyCard(legal_name="예시제조", typed_name="예시제조", address="", ceo="", founded="", ref="00000001"))
    assert result.outcome is Outcome.GATE_STOPPED
    assert seen == [(expected_model, is_v2), "paid"]
