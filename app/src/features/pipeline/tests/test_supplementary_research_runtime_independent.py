"""희소 공식근거 보완조사의 runtime 진입을 독립적으로 고정한다.

실제 네트워크·AI·DB를 열지 않고, 정식 collector 결과와 뉴스 경계만 메모리
대역으로 주입한다. 이 파일은 production과 기존 시험을 변경하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core import news_intake_switch
from src.features.news_intake.models import NewsBodyFetchResult
from src.features.pipeline import real
from src.features.pipeline.official_evidence_preflight import assess_official_evidence
from src.features.pipeline.port import Outcome
from src.features.pipeline.supplementary_research_runtime_constants import (
    SUPPLEMENTARY_RESEARCH_CONTINUE_STEP,
)
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _Collector,
    _freeze_runtime,
    _official_result,
    _request,
    _wire_runtime,
    CORP_ID,
    FakeEngine,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    ReleaseMode,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SourceRequirement,
)
from src.shared.report_evidence.models import CollectionAttempt
from src.shared.report_evidence.policy import collector_slots_for


@pytest.fixture(autouse=True)
def _reset_news_switch(monkeypatch: pytest.MonkeyPatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _dart_ready_result(
    *, ready_count: int = 2, failure: bool = False, contract_error: bool = False,
):
    """실제 DART 원문 N장과 확인 완료 MISSING 나머지 장을 만든다."""

    base = _official_result()
    candidates = []
    for index, candidate in enumerate(base.candidates, start=1):
        if index <= ready_count:
            receipt = f"20260315{index:06d}"
            document_id = f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}"
            document = replace(
                candidate.documents[0],
                document_id=document_id,
                canonical_url=(
                    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
                ),
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                identity_binding=f"corp_code={CORP_ID};rcept_no={receipt}",
            )
            candidates.append(
                replace(
                    candidate,
                    documents=(document,),
                    fragments=(
                        replace(candidate.fragments[0], document_id=document_id),
                    ),
                    reason_codes=(
                        ("fragment_document_missing:fixture",)
                        if contract_error and index == 1
                        else ()
                    ),
                )
            )
            continue

        state = (
            CollectionState.FAILED
            if failure and index == ready_count + 1
            else CollectionState.MISSING
        )
        candidates.append(
            replace(
                candidate,
                documents=(),
                fragments=(),
                attempts=(
                    CollectionAttempt(
                        company_id=CORP_ID,
                        attempt_id=f"dart:{candidate.section_id}:{state.value.lower()}",
                        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                        requirement=SourceRequirement.REQUIRED,
                        state=state,
                        slot_ids=collector_slots_for(candidate.section_id),
                        reason_code=(
                            "document_fetch_failed"
                            if state is CollectionState.FAILED
                            else "document_fetch_missing"
                        ),
                    ),
                ),
                candidate_readiness=(
                    EvidenceReadiness.UNKNOWN
                    if state is CollectionState.FAILED
                    else EvidenceReadiness.INSUFFICIENT
                ),
            )
        )
    return replace(base, candidates=tuple(candidates))


def _news_item() -> SimpleNamespace:
    return SimpleNamespace(
        title="가나다전자 새 제품군 공급",
        originallink="https://www.newsis.com/view/company-product",
        link="",
        description="가나다전자가 새 제품군을 공급했다.",
        pubDate="2026-09-01",
    )


def _search_result(items: list[object]) -> SimpleNamespace:
    return SimpleNamespace(
        state="success",
        reason_code="news_search_ok",
        items=items,
        elapsed_ms=1,
        transport_attempts=1,
        retry_recovered=False,
        attempt_reason_codes=("news_search_ok",),
    )


def _grounded_analysis(prompt: str, _schema: dict, _max_tokens: int) -> dict:
    payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
    return {
        "items": [
            {
                "id": article["id"],
                "same_company": True,
                "material": True,
                "entity_evidence": article["body"],
                "source_type": "news_report",
                "excerpts": [
                    {
                        "text": article["body"],
                        "section_id": "portfolio",
                        "claim_slot": "portfolio:product_role",
                        "claim_kind": "reported_fact",
                        "temporal_status": "completed",
                        "topic": "products",
                        "event_key": "새 제품군 공급",
                        "event_on": "",
                        "time_evidence": "",
                        "subject": "",
                        "subject_evidence": "",
                    }
                ],
            }
            for article in payload["articles"]
        ]
    }


def _run(monkeypatch: pytest.MonkeyPatch, official, *, news_on: bool, search_news, fetch_text):
    if news_on:
        monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([official])
    calls = _wire_runtime(monkeypatch, engine=engine)
    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=search_news,
        news_analyze=_grounded_analysis,
        news_fetch_text=fetch_text,
    ).run(user_input, card)
    return result, calls


def test_news_off_preserves_ready_two_section_prejudgment_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    official = _dart_ready_result()
    preflight = assess_official_evidence(official)
    called = 0

    def unexpected(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called += 1
        raise AssertionError("NEWS_INTAKE OFF에서 보완 뉴스 경계를 호출하면 안 됩니다")

    result, calls = _run(
        monkeypatch, official, news_on=False, search_news=unexpected, fetch_text=unexpected
    )

    assert preflight.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert preflight.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    assert preflight.can_call_ai is False
    assert preflight.supplementary_research_allowed is True
    assert result.outcome is Outcome.GATE_STOPPED
    assert called == 0
    assert calls.composers == []


def test_news_on_and_bound_dart_ready_two_sections_connect_news_and_shadow_composer_after_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    official = _dart_ready_result()
    searches = 0

    def search_news(_query: str, **_kwargs: object) -> SimpleNamespace:
        nonlocal searches
        searches += 1
        return _search_result([_news_item()] if searches == 1 else [])

    result, calls = _run(
        monkeypatch,
        official,
        news_on=True,
        search_news=search_news,
        fetch_text=(
            lambda _url: "가나다전자는 구체적인 사업으로 고객 업무를 잇는 "
            "새 제품군을 운영하며 자동화 설비 120대를 공급했다."
        ),
    )

    assert result.outcome is Outcome.REPORT
    assert searches > 0
    assert len(calls.coordinates) == 1
    assert len(calls.composers) == 1
    composer = calls.composers[0]
    assert composer["release_mode_override"] is ReleaseMode.SHADOW
    assert composer["supplementary_research_required"] is True
    transported_official = composer["supplementary_official_evidence"]
    assert transported_official is not None
    assert transported_official.source_snapshot_sha256 == official.source_snapshot_sha256
    assert transported_official.candidates == official.candidates
    continuation = next(
        step
        for step in composer["steps"]
        if step.get("step") == SUPPLEMENTARY_RESEARCH_CONTINUE_STEP
    )
    assert continuation["사전판정"] == GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE.value
    assert continuation["사유코드"] == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    news_step = next(
        step for step in composer["steps"] if step.get("step") == "5b_뉴스_수집"
    )
    assert news_step["본문읽기"] == 1


@pytest.mark.parametrize("failure,contract_error", [(True, False), (False, True)])
def test_required_dart_failure_and_internal_contract_error_do_not_open_news_or_composer_when_news_on(
    monkeypatch: pytest.MonkeyPatch, failure: bool, contract_error: bool,
) -> None:
    official = _dart_ready_result(failure=failure, contract_error=contract_error)
    calls_to_news = 0

    def unexpected(*_args: object, **_kwargs: object) -> None:
        nonlocal calls_to_news
        calls_to_news += 1
        raise AssertionError("hard/transient 공식 오류는 보완 뉴스로 우회하면 안 됩니다")

    result, calls = _run(
        monkeypatch, official, news_on=True, search_news=unexpected, fetch_text=unexpected
    )

    assert result.outcome is Outcome.GATE_STOPPED
    assert calls_to_news == 0
    assert calls.composers == []


@pytest.mark.parametrize("ready_count", [3, 9])
def test_existing_shadow_and_ready_nine_path_has_false_supplementary_flag(
    monkeypatch: pytest.MonkeyPatch, ready_count: int,
) -> None:
    official = (
        _dart_ready_result(ready_count=3)
        if ready_count == 3
        else _official_result()
    )

    result, calls = _run(
        monkeypatch,
        official,
        news_on=False,
        search_news=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("OFF 뉴스 호출")),
        fetch_text=lambda _url: "",
    )

    assert result.outcome is Outcome.REPORT
    assert len(calls.composers) == 1
    assert calls.composers[0]["supplementary_research_required"] is False
    assert calls.composers[0]["supplementary_official_evidence"] is None


@pytest.mark.parametrize(
    ("items", "fetch_text", "expected_failure"),
    [
        ([], lambda _url: "", None),
        (
            [_news_item()],
            lambda _url: NewsBodyFetchResult(reason_code="fetch_robots_blocked"),
            "fetch_robots_blocked",
        ),
    ],
)
def test_supplementary_research_preserves_no_news_and_body_failure_diagnostics(
    monkeypatch: pytest.MonkeyPatch, items: list[object], fetch_text, expected_failure: str | None,
) -> None:
    searches = 0

    def search_news(_query: str, **_kwargs: object) -> SimpleNamespace:
        nonlocal searches
        searches += 1
        return _search_result(items if searches == 1 else [])

    result, calls = _run(
        monkeypatch,
        _dart_ready_result(),
        news_on=True,
        search_news=search_news,
        fetch_text=fetch_text,
    )

    assert result.outcome is Outcome.REPORT
    step = next(
        item
        for item in calls.composers[0]["steps"]
        if item.get("step") == "5b_뉴스_수집"
    )
    assert step["조각"] == 0
    assert step["실패"] == expected_failure
