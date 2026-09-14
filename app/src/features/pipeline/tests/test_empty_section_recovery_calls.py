"""실제 복구 호출자는 정상 계량하고 도식·요약 두 호출을 남긴다."""
from types import SimpleNamespace

import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer import pipeline as composer_pipeline
from src.features.composer.port import AskFatalError
from src.features.pipeline import real
from src.features.pipeline.port import Grade, Report
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine
from src.features.pipeline.tests.test_real_v2_switch import _build_identity, _frozen_v2_mode
from src.features.pipeline.tests.test_mandatory_tail_call_reserve import _응답기록
from src.shared.report_evidence.constants import ReleaseMode


@pytest.mark.parametrize("already_called", [14, 15])
def test_actual_recovery_closures_meter_calls_and_reserve_two_tail_calls(monkeypatch, already_called):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **kwargs: None)
    engine = real._MeteredEngine(FakeEngine())
    messages = _응답기록()
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    observations = []
    callbacks = ProviderAttemptCallbacks(
        lambda *args: object(), lambda _: None, lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    for _ in range(already_called):
        engine.reserve_provider_call()

    def run_v2(*args, **kwargs):
        can_start = kwargs["empty_recovery_can_start"]
        writer = kwargs["empty_recovery_writer_ask"]
        reviewer = kwargs["empty_recovery_reviewer_ask"]
        assert can_start() is (already_called == 14)
        if already_called == 14:
            writer("새로운 짧은 사실 작성")
            assert not can_start()
            reviewer("동일 근거 검수")
        else:
            with pytest.raises(AskFatalError) as caught:
                writer("예약 경계 확인")
            assert caught.value.call_limit
            assert not messages.requests
        # 본문 복구가 끝나도 필수 후속 두 호출은 정상 전송된다.
        kwargs["diagram_ask"]("도식 검수")
        kwargs["writer_ask"]("검증 본문 요약 고르기")
        if already_called == 14:
            with pytest.raises(AskFatalError) as caught:
                reviewer("상한 초과 확인")
            assert caught.value.call_limit
        return composer_pipeline.V2RunOutput(
            report=Report(company="가나다전자", job="", corp_type="상장사",
                          grade=Grade.PARTIAL, sections=[], citations=[]),
            composed_sentences=0, verified_sentences=0,
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", run_v2)
    with provider_budget.activate(100_000), attempt_context.activate(callbacks):
        real._run_v2_composer(
            engine=engine, client=client, company_name="가나다전자", corp_type="상장사",
            frags={}, financials=None, filing=None, revenue_tables=[], sources=[],
            business_date=real.today_kst(), model="가짜모델", steps=[],
            corp_id=CORP_ID,
            build_identity=_build_identity(), generation_mode=_frozen_v2_mode(),
            release_mode_override=ReleaseMode.SHADOW,
        )
    expected = 4 if already_called == 14 else 2
    assert len(messages.requests) == len(observations) == expected
    assert engine.available_provider_calls(reserved_calls=0) == 18 - already_called - expected
