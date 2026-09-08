"""보완조사 출고 guard와 캐시·single-flight 순서의 독립 회귀 시험.

실제 provider·서버·운영 DB를 사용하지 않는다. 공식 근거가 부족해 보완조사를
연 요청만 최종 Report를 다시 검사하고, 그 검사는 모든 재사용 반환과 신규 캐시
저장보다 앞서야 한다.
"""

from __future__ import annotations

from dataclasses import replace
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.core import deployment_identity, news_intake_switch
from src.features.cost_tracking.store import AiCostEvent
from src.features.pipeline import real
from src.features.pipeline.port import Grade, Outcome, Report, RunResult
from src.features.pipeline.supplementary_research_runtime import (
    enforce_supplementary_research_release,
)
from src.features.pipeline.supplementary_research_runtime_constants import (
    SUPPLEMENTARY_RESEARCH_RELEASE_STEP,
)
from src.features.pipeline.tests.test_news_intake_wiring import (
    GROUNDED_RUNTIME_BODY,
    _grounded_runtime_analysis,
    _grounded_runtime_item,
    _grounded_runtime_result,
)
from src.features.pipeline.tests.test_official_evidence_runtime import (
    CORP_ID,
    FakeEngine,
    _Collector,
    _freeze_runtime,
    _official_result,
    _request,
    _wire_runtime,
)
from src.features.pipeline.tests.test_real_v2_switch import (
    _branch_ingredients,
)
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
)
from src.shared.report_evidence.constants import (
    GenerationGateStatus,
    ReleaseMode,
    ReportExecutionOutcome,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_generation.models import GenerationRunMetrics


_REJECT_CODE = "supplementary_release_insufficient_body_sections"
_ACTUAL_COST_KRW = 17.25


@pytest.fixture(autouse=True)
def _isolated_process_contract(monkeypatch: pytest.MonkeyPatch):
    """공유 process 스위치와 build 영수증을 시험마다 새로 고정한다."""

    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    yield
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _report(*, cache_claim: bool = False) -> Report:
    """캐시·보고서 자기 선언만으로는 출고할 수 없는 최소 저장본."""

    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        citations=[],
        schema_version=real.ENGINE_V2_SCHEMA_VERSION,
        release_mode=ReleaseMode.SHADOW.value,
        safety_decision=("supplementary_release_allowed" if cache_claim else ""),
        publication_policy=("supplementary_release_allowed" if cache_claim else ""),
        generation_metrics=GenerationRunMetrics(0, 0, 0, 0),
    )


def _sparse_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 preflight 값에서 READY 두 장·보완조사 전용 관측만 만든다."""

    original = real.assess_official_evidence

    def assess(result: object):
        base = original(result)
        ready = tuple(REQUIRED_EVIDENCE_SECTION_IDS[:2])
        insufficient = tuple(
            section_id
            for section_id in REQUIRED_EVIDENCE_SECTION_IDS
            if section_id not in ready
        )
        return replace(
            base,
            decision=replace(
                base.decision,
                status=GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE,
                outcome=ReportExecutionOutcome.INSUFFICIENT_EVIDENCE,
                ready_section_ids=ready,
                insufficient_section_ids=insufficient,
                unknown_section_ids=(),
                reason_codes=("fixture_sparse_official_evidence",),
            ),
            detail_code=FINAL_GATE_DETAIL_PREFLIGHT_CLASSIFIER_COVERAGE_GAP,
            dart_partial_fallback=False,
            dart_partial_reason="",
            supplementary_research_allowed=True,
        )

    monkeypatch.setattr(real, "assess_official_evidence", assess)


def _reject_assessment(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    """순수 guard의 판단만 닫힌 실패로 고정하고 호출 순서를 관측한다."""

    release_guard = importlib.import_module(
        "src.features.pipeline.supplementary_research_release"
    )

    def reject(
        _report: object,
        *,
        official_evidence: object = None,
        source_verifier: object = None,
    ) -> object:
        assert official_evidence is not None
        assert callable(source_verifier)
        events.append("guard")
        return SimpleNamespace(
            allowed=False,
            code=_REJECT_CODE,
            qualified_section_ids=("identity", "business_model"),
        )

    monkeypatch.setattr(release_guard, "assess_supplementary_research_release", reject)


def test_guard_rejection_removes_report_and_cache_authority_but_preserves_actual_cost_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _reject_assessment(monkeypatch, events)
    cost_event = AiCostEvent(
        stage="news_grounding",
        model_id="fixture-model",
        input_tokens=10,
        output_tokens=5,
        cost_krw=_ACTUAL_COST_KRW,
    )
    original = RunResult(
        outcome=Outcome.REPORT,
        report=_report(cache_claim=True),
        charged=True,
        cost_krw=_ACTUAL_COST_KRW,
        ai_cost_events=(cost_event,),
        generation_cache_eligible=True,
        reused_content_snapshot_id="c" * 32,
        reused_artifact_id="d" * 32,
    )
    steps: list[dict[str, object]] = []

    result = enforce_supplementary_research_release(
        original,
        official_evidence=_official_result(),
        steps=steps,
    )

    assert events == ["guard"]
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.charged is False
    assert result.cost_krw == _ACTUAL_COST_KRW
    assert result.ai_cost_events == (cost_event,)
    assert result.generation_cache_eligible is False
    assert result.reused_content_snapshot_id == ""
    assert result.reused_artifact_id == ""
    assert result.final_gate_reason == FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    assert steps == [
        {
            "step": SUPPLEMENTARY_RESEARCH_RELEASE_STEP,
            "허용": False,
            "사유코드": _REJECT_CODE,
            "검증본문장": ["identity", "business_model"],
        }
    ]


def test_new_report_guard_runs_before_cache_eligibility_and_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeEngine()
    engine, client, frags, financials, filing = _branch_ingredients(fake)
    official = _official_result()
    order: list[str] = []

    def fake_run_v2(*_args: object, **_kwargs: object):
        return composer_pipeline.V2RunOutput(
            report=_report(),
            composed_sentences=3,
            verified_sentences=3,
            generation_metrics=GenerationRunMetrics(len(frags), 0, 3, 3),
        )

    def guard(
        result: RunResult,
        *,
        official_evidence: object,
        steps: list[dict],
    ) -> RunResult:
        assert official_evidence is official
        assert result.report is not None
        order.append("guard")
        return result

    def eligibility(*_args: object, **_kwargs: object):
        order.append("eligibility")
        return True, set(), set()

    def save(**_kwargs: object) -> None:
        order.append("save")

    monkeypatch.setattr(composer_pipeline, "run_v2", fake_run_v2)
    monkeypatch.setattr(real, "enforce_supplementary_research_release", guard)
    monkeypatch.setattr(real, "_generation_cache_eligibility", eligibility)
    monkeypatch.setattr(real, "_v2_cache_save", save)
    monkeypatch.setattr(real.generation_coordination, "is_active", lambda: False)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.SHADOW.value)
    mode = real.engine_mode.freeze_process_engine_mode(real.engine_mode.EngineMode.V2)
    build_identity = build_identity_contract.freeze_process_engine_build_identity()

    result = real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[],
        business_date=real.today_kst(),
        model="가짜모델",
        steps=[],
        corp_id=CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="a" * 64,
        build_identity=build_identity,
        generation_mode=mode,
        release_mode_override=ReleaseMode.SHADOW,
        supplementary_research_required=True,
        supplementary_official_evidence=official,
    )

    assert result.outcome is Outcome.REPORT
    assert result.generation_cache_eligible is True
    assert order == ["guard", "eligibility", "save"]


def test_coordination_reuse_guards_against_self_declaration_and_does_not_reanalyze_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    _sparse_preflight(monkeypatch)
    fake = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=fake)
    events: list[str] = []
    _reject_assessment(monkeypatch, events)
    stored = _report(cache_claim=True)

    monkeypatch.setattr(real.generation_coordination, "is_active", lambda: True)

    def coordinate(**_kwargs: object) -> generation_coordination.ReusedGeneration:
        events.append("coordinate")
        return generation_coordination.ReusedGeneration(
            content_snapshot_id="c" * 32,
            artifact_id="d" * 32,
            report=stored,
            actual_models=("fixture-model",),
            generation_cache_eligible=True,
        )

    monkeypatch.setattr(real.generation_coordination, "coordinate", coordinate)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cache/waiter 결과는 뉴스 본문을 재분석하면 안 됩니다")

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=lambda *_args, **_kwargs: _grounded_runtime_result([]),
        news_analyze=forbidden,
        news_fetch_text=forbidden,
    ).run(user_input, card)

    assert events == ["coordinate", "guard"]
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.charged is False
    assert result.cache_hit == ""
    assert result.reused_content_snapshot_id == ""
    assert result.reused_artifact_id == ""
    assert calls.composers == []
    assert calls.legacy_collects == []
    assert calls.paid_phase_count == 0


def test_news_body_is_analyzed_once_only_after_owner_and_context_passed_to_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    _sparse_preflight(monkeypatch)
    fake = FakeEngine()
    official = _official_result()
    collector = _Collector([official])
    calls = _wire_runtime(monkeypatch, engine=fake)
    events: list[str] = []
    search_calls = 0
    analyze_calls = 0

    monkeypatch.setattr(real.generation_coordination, "is_active", lambda: True)

    def coordinate(**kwargs: object) -> None:
        calls.coordinates.append(dict(kwargs))
        events.append("owner")
        return None

    monkeypatch.setattr(real.generation_coordination, "coordinate", coordinate)

    def search(_query: str, **_kwargs: object) -> object:
        nonlocal search_calls
        search_calls += 1
        return _grounded_runtime_result(
            [_grounded_runtime_item()] if search_calls == 1 else []
        )

    def analyze(prompt: str, schema: dict[str, Any], max_tokens: int) -> object:
        nonlocal analyze_calls
        analyze_calls += 1
        events.append("analyze")
        return _grounded_runtime_analysis(prompt, schema, max_tokens)

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=search,
        news_analyze=analyze,
        news_fetch_text=lambda _url: GROUNDED_RUNTIME_BODY,
    ).run(user_input, card)

    assert result.outcome is Outcome.REPORT
    assert search_calls > 0
    assert analyze_calls == 1
    assert events == ["owner", "analyze"]
    assert len(calls.composers) == 1
    composer_call = calls.composers[0]
    assert composer_call["supplementary_research_required"] is True
    supplied = composer_call["supplementary_official_evidence"]
    assert supplied is not None and supplied.company_id == CORP_ID


def test_legacy_first_tier_cache_boundary_also_guards_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """스위치는 process에서 고정되지만 경계 자체가 빠지는 회귀를 별도로 잡는다."""

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    _sparse_preflight(monkeypatch)
    fake = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=fake)
    events: list[str] = []
    _reject_assessment(monkeypatch, events)
    stored = _report(cache_claim=True)

    # 정상 process에서는 NEWS_ON 보완조사가 옛 1층 캐시를 읽지 않는다. 다만
    # 호출 중 스위치 일관성이 깨져도 저장본이 guard 없이 나가지 않는 방어선을
    # 실제 분기로 실행한다: 진입·검색·namespace는 ON, 옛 cache 조회만 OFF다.
    switch_values = iter((True, True, True, False))
    monkeypatch.setattr(
        real.news_intake_switch,
        "news_intake_enabled",
        lambda: next(switch_values),
    )
    monkeypatch.setattr(real.generation_coordination, "is_active", lambda: False)
    monkeypatch.setattr(
        real.generation_coordination,
        "coordinate",
        lambda **_kwargs: events.append("coordinate"),
    )

    def cache_lookup(**kwargs: object) -> Report:
        calls.cache_lookups.append(dict(kwargs))
        events.append("cache")
        return stored

    monkeypatch.setattr(real, "_v2_cache_lookup", cache_lookup)
    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=lambda *_args, **_kwargs: _grounded_runtime_result([]),
        news_analyze=lambda *_args, **_kwargs: pytest.fail("cache hit을 재분석했습니다"),
        news_fetch_text=lambda *_args, **_kwargs: pytest.fail("cache hit을 다시 읽었습니다"),
    ).run(user_input, card)

    assert events == ["coordinate", "cache", "guard"]
    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.charged is False
    assert result.cache_hit == ""
    assert calls.composers == []
    assert calls.legacy_collects == []
