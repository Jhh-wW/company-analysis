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


# ══════════════════════════════════════════════════════════
# 뉴스 단계가 빈 장 복구 2회를 «미리» 남긴다
#
# 실측(2026-09-16): 뉴스 5 + 장 작성 9 + 본문 검수 1 + 필수 후속 2 = 17회로
# 상한 18을 거의 채운 실행에서 복구에 쓸 여유가 1회뿐이라 시작조차 못 했다.
# 빈 장이 생길지는 본문 검수가 끝나야 알 수 있고 그때는 이미 뉴스가 몫을 다 쓴
# 뒤이므로, 뉴스 «전»에 남겨 두는 것 말고는 방법이 없다.
# ══════════════════════════════════════════════════════════


def _news_branch_budget(monkeypatch, *, already_called: int) -> int:
    """실제 뉴스 갈래를 돌려 «검색에 넘어간» 분석 호출 상한을 그대로 받아 온다."""
    engine = real._MeteredEngine(SimpleNamespace())
    for _ in range(already_called):
        engine.reserve_provider_call()
    captured: list[int] = []

    def fake_prepare(**kwargs):
        captured.append(kwargs["max_analysis_calls"])
        return SimpleNamespace(
            snapshot=SimpleNamespace(
                digest="d" * 64, status="ok", reason_codes=(),
                cache_eligible=False, transport_diagnostics={},
            ),
            policy=SimpleNamespace(max_analysis_calls=kwargs["max_analysis_calls"]),
        )

    monkeypatch.setattr(real.news_research_adapter, "prepare_news_research", fake_prepare)
    real._run_news_search_branch(
        engine=engine, profile={}, official_evidence=None,
        company_name="가나다전자", business_date=real.today_kst(),
        pipeline_news_search=lambda *args, **kwargs: None,
    )
    assert len(captured) == 1, "뉴스 준비가 한 번 불리지 않았습니다"
    return captured[0]


def test_뉴스_분석_상한이_빈장_복구_2회를_남긴다(monkeypatch) -> None:
    """리터럴 오라클 — 18 - (작성 9 + 검수 1 + 필수 후속 2) - 복구 2 = 4."""
    assert _news_branch_budget(monkeypatch, already_called=0) == 4
    # 앞 단계가 이미 쓴 만큼 줄어들되 복구 몫은 계속 남는다.
    assert _news_branch_budget(monkeypatch, already_called=2) == 2


def test_뉴스가_상한까지_써도_복구_2회와_필수후속_2회가_남는다(monkeypatch) -> None:
    """실행 순서를 그대로 재생한다 — 뉴스 → 작성 9 → 검수 1 → 복구 2 → 도식·요약 2."""
    engine = real._MeteredEngine(SimpleNamespace())
    captured: list[int] = []

    def fake_prepare(**kwargs):
        captured.append(kwargs["max_analysis_calls"])
        return SimpleNamespace(
            snapshot=SimpleNamespace(
                digest="d" * 64, status="ok", reason_codes=(),
                cache_eligible=False, transport_diagnostics={},
            ),
            policy=SimpleNamespace(max_analysis_calls=kwargs["max_analysis_calls"]),
        )

    monkeypatch.setattr(real.news_research_adapter, "prepare_news_research", fake_prepare)
    real._run_news_search_branch(
        engine=engine, profile={}, official_evidence=None,
        company_name="가나다전자", business_date=real.today_kst(),
        pipeline_news_search=lambda *args, **kwargs: None,
    )
    for _ in range(captured[0]):          # 뉴스 분석
        engine.reserve_provider_call()
    for _ in range(9 + 1):                # 장 작성 9 + 본문 검수 1
        engine.reserve_provider_call()
    # 복구 작가는 재검수 1 + 필수 후속 2를 남기고도 나갈 수 있어야 한다.
    assert engine.available_provider_calls(reserved_calls=2 + 1) > 0
    engine.reserve_provider_call(reserved_calls=2 + 1)   # 복구 작성
    engine.reserve_provider_call(reserved_calls=2)       # 복구 검수
    assert engine.reserve_provider_call() == 17          # 도식 검수
    assert engine.reserve_provider_call() == 18          # 요약 고르기
    with pytest.raises(provider_budget.RequestCallLimitReached):
        engine.reserve_provider_call()
