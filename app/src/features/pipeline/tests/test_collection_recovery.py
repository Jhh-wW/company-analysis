"""부분 수집을 완성으로 위장하지 않고 마지막 정상 근거를 전달한다."""

from types import SimpleNamespace

import pytest

from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.features.pipeline.collection_recovery import (
    record_collection_failure, report_evidence_availability, retain_official_collection,
)
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _Collector, _freeze_runtime, _official_result, _run, _wire_runtime,
)
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine
from src.shared.report_evidence.constants import CollectionState, ReleaseMode
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.generation_coordination import GenerationWaitCancelled


def _fail(*_args, **_kwargs):
    raise RuntimeError("시험용 자료원 조회 실패")


@pytest.mark.parametrize("boundary", ("profile", "audit", "financials", "filing", "registry"))
def test_초기조회_실패가_확보한_공식자료를_폐기하지_않는다(monkeypatch, boundary):
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    engine = FakeEngine()
    original_get_json = engine.get_json
    if boundary in {"profile", "audit"}:
        endpoint_to_fail = "company.json" if boundary == "profile" else "list.json"

        def get_json(endpoint, params, counter):
            if endpoint == endpoint_to_fail:
                return _fail()
            return original_get_json(endpoint, params, counter)

        monkeypatch.setattr(engine, "get_json", get_json)
    else:
        method = {"financials": "fetch_financials", "filing": "latest_report_rcept", "registry": "load_public_org_registry"}[boundary]
        monkeypatch.setattr(engine, method, _fail)
    calls = _wire_runtime(monkeypatch, engine=engine)
    official = _official_result()
    with run_diagnostics.capture() as captured:
        result = _run(_Collector([official]))
    assert result.outcome is Outcome.REPORT
    assert result.generation_cache_eligible is False
    assert calls.composers[0]["release_mode_override"] is ReleaseMode.SHADOW
    assert sum(len(candidate.fragments) for candidate in official.candidates) <= len(calls.composers[0]["frags"])
    assert any(step.get("상태") == "FAILED" for step in captured.steps)
    if boundary == "profile":
        assert engine.decide_calls[0]["corp_cls"] == ""


def test_재분류_예외는_마지막_정상_문서와_원문을_보존한다(monkeypatch):
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    calls = _wire_runtime(monkeypatch, engine=FakeEngine())
    monkeypatch.setattr(real, "reclassify_official_evidence", _fail)
    official = _official_result()
    result = _run(_Collector([official]))
    assert result.outcome is Outcome.REPORT
    projected = calls.legacy_collects[0]["formal_official_evidence"]
    for original, retained in zip(official.candidates, projected.candidates):
        assert set(original.documents) <= set(retained.documents)
        # 자기 선언 재분류는 의미 칸을 정리하므로 원문·문서·해시 보존을 대조한다.
        assert {(item.document_id, item.text, item.text_sha256) for item in original.fragments} <= {
            (item.document_id, item.text, item.text_sha256) for item in retained.fragments
        }
        assert retained.attempts[-1].state is CollectionState.FAILED
    assert projected.source_snapshot_sha256 != official.source_snapshot_sha256


def test_대상자료_0건도_빈_근거를_정직하게_작성기로_넘긴다(monkeypatch):
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    engine = FakeEngine()
    monkeypatch.setattr(engine, "fetch_financials", lambda *_args, **_kwargs: (None, []))
    monkeypatch.setattr(engine, "latest_report_rcept", lambda *_args, **_kwargs: None)
    calls = _wire_runtime(monkeypatch, engine=engine, legacy_fragments={})
    official = _official_result(first_state=CollectionState.MISSING, failed_section_count=len(REQUIRED_EVIDENCE_SECTION_IDS))
    result = _run(_Collector([official]))
    assert result.outcome is Outcome.REPORT
    assert calls.composers[0]["frags"] == {}
    assert calls.composers[0]["release_mode_override"] is ReleaseMode.SHADOW
    assert real.assess_official_evidence(official).decision.ready_section_ids == ()


@pytest.mark.parametrize("status, expected", (("거부A_공공기관", Outcome.REJECT_PUBLIC), ("거부B_외감아님", Outcome.REPORT)))
def test_자료없음과_입증된_대상외를_구분한다(monkeypatch, status, expected):
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    engine = FakeEngine()
    monkeypatch.setattr(engine, "decide", lambda *_args, **_kwargs: SimpleNamespace(status=status, corp_type=None))
    calls = _wire_runtime(monkeypatch, engine=engine)
    with run_diagnostics.capture() as captured:
        result = _run(_Collector([_official_result()]))
    assert result.outcome is expected
    if expected is Outcome.REPORT:
        assert calls.composers[0]["corp_type"] == ""
        assert next(step["판정"] for step in captured.steps if step.get("step") == "5_대상판정") == "미확인"
    else:
        assert calls.composers == []


def test_실패시도만_있는_결과는_완료증명이나_원문을_만들지_않는다():
    result = retain_official_collection(CORP_ID, None, reason_code="collector_failed")
    assert result.independent_document_count == 0
    assert all(not candidate.documents and not candidate.fragments for candidate in result.candidates)
    assert all(candidate.attempts[0].document_scan is None for candidate in result.candidates)
    preflight = real.assess_official_evidence(result)
    assert preflight.can_call_ai
    assert preflight.collection_incomplete
    assert not preflight.decision.can_call_ai


def test_재무_부분실패_성공_payload를_작성기에_전달한다(monkeypatch):
    exception_type = real._engine().PartialFinancialCollectionError
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    engine = FakeEngine()
    financials, years = engine.fetch_financials(CORP_ID, engine.UsageCounter())
    monkeypatch.setattr(engine, "PartialFinancialCollectionError", exception_type, raising=False)

    def fetch(*_args, **_kwargs):
        raise exception_type(financials, years[:1], years[1:])

    monkeypatch.setattr(engine, "fetch_financials", fetch)
    calls = _wire_runtime(monkeypatch, engine=engine)
    result = _run(_Collector([_official_result()]))
    assert result.outcome is Outcome.REPORT
    assert calls.composers[0]["financials"] is financials
    assert calls.legacy_collects[0]["fin_years"] == years[:1]


@pytest.mark.parametrize("boundary", ("profile", "financials", "collector", "reclassify", "comparison"))
def test_요청_취소는_부분보고서로_우회하지_않는다(monkeypatch, boundary):
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    engine = FakeEngine()
    calls = _wire_runtime(monkeypatch, engine=engine)
    stopped = GenerationWaitCancelled("시험용 요청 취소")

    def stop(*_args, **_kwargs):
        raise stopped

    if boundary == "profile":
        monkeypatch.setattr(engine, "get_json", stop)
    elif boundary == "financials":
        monkeypatch.setattr(engine, "fetch_financials", stop)
    elif boundary == "reclassify":
        monkeypatch.setattr(real, "reclassify_official_evidence", stop)
    elif boundary == "comparison":
        monkeypatch.setattr(real, "_prepare_v2_comparison_result", stop)
    collector = _Collector([stopped if boundary == "collector" else _official_result()])
    with pytest.raises(GenerationWaitCancelled) as caught:
        _run(collector)
    assert caught.value is stopped
    assert calls.composers == []


def test_공식_및_초반수집_실패범위를_안내로만_전달한다():
    official = retain_official_collection(CORP_ID, _official_result(), reason_code="collector_failed")
    steps = []
    record_collection_failure(steps, source="재무 자료", reason="financial_collection_failed")
    availability = report_evidence_availability(official, has_fragments=True, failure_steps=steps)
    assert availability.collection_state == "partial"
    assert any("사업보고서" in scope.label for scope in availability.unverified_scopes)
    assert any("재무 자료" in scope.label for scope in availability.unverified_scopes)
    assert all(candidate.attempts[-1].state is CollectionState.FAILED for candidate in official.candidates)


def test_재무_부분수집_운반자에_담긴_취소도_다시_전파한다(monkeypatch):
    exception_type = real._engine().PartialFinancialCollectionError
    _freeze_runtime(monkeypatch, mode=real.engine_mode.EngineMode.V2, release_mode=ReleaseMode.FULL)
    engine = FakeEngine()
    monkeypatch.setattr(engine, "PartialFinancialCollectionError", exception_type, raising=False)
    stopped = GenerationWaitCancelled("시험용 재무 요청 취소")

    def fetch(*_args, **_kwargs):
        raise exception_type(None, [], [2025], errors=(stopped,))

    monkeypatch.setattr(engine, "fetch_financials", fetch)
    calls = _wire_runtime(monkeypatch, engine=engine)
    with pytest.raises(GenerationWaitCancelled) as caught:
        _run(_Collector([_official_result()]))
    assert caught.value is stopped
    assert calls.composers == []
