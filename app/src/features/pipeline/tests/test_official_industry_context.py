"""선택 공식 원문·단회 호출·예산과 불명 실패의 실제 계량 경계를 검증한다."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.composer.port import CollectedFragment, AskFatalError
from src.features.pipeline import real
from src.features.pipeline.official_industry_context import prepare_official_industry_fallback
from src.features.pipeline.official_industry_context_constants import OFFICIAL_INDUSTRY_STAGE
from src.features.pipeline.tests.test_business_activity_anchors import _evidence, PROFILE, RECEIPT
from src.features.pipeline.tests import test_v2_native_schema_metering as native
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT


def bound_input():
    evidence = _evidence(("당사는 정밀부품을 제조·판매합니다.",), identity_binding=(
        f"corp_code={PROFILE['corp_code']};rcept_no={RECEIPT};"
        f"source_kind={SOURCE_KIND_DART_BUSINESS_REPORT};identity_check=verified_match"
    ))
    group = next(value for value in evidence.candidates if value.fragments)
    fragment, document = group.fragments[0], group.documents[0]
    selected = CollectedFragment(
        "9", "공시", fragment.text, source_url=document.canonical_url,
        document_title=document.title, location=fragment.location,
        document_date=document.published_on, source_document_id=document.document_id,
        formal_source_kind=document.source_kind, source_publisher=document.publisher,
        document_content_sha256=document.content_sha256, identity_binding=document.identity_binding,
        source_collected_on=document.collected_at,
    )
    return evidence, selected


def prepare(*, evidence=None, collect=None, analyze=None, available=1, profile=PROFILE):
    default, selected = bound_input()
    calls, diagnostics = [], []

    def producer(**kwargs):
        calls.append(kwargs)
        kwargs["analyze"]("정확 산업 검수", {"type": "object"}, 3000)
        return ()

    anchors, fallback = prepare_official_industry_fallback(
        collection=evidence or default, profile=profile, company_id=PROFILE["corp_code"],
        reference_date="2026-10-08", anchors=(), analyze=analyze or (lambda *args: {}),
        available_calls=lambda: available, diagnostics=diagnostics,
        budget_exceptions=(real.provider_budget.ProviderBudgetExceeded,), collect=collect or producer,
    )
    return anchors, fallback, selected, calls, diagnostics


def test_news_off_builds_same_run_anchors_and_attempts_once():
    anchors, fallback, selected, calls, _ = prepare()
    assert len(anchors) == 1 and anchors[0].exact_text == selected.text
    assert fallback((selected,)) == fallback((selected,)) == ()
    assert len(calls) == 1
    original, = calls[0]["candidates"]
    assert original[0].text == selected.text and original[0].fragment_id != selected.fragment_id


@pytest.mark.parametrize("field,value", (
    ("text", "다른 원문"), ("location", "1-20"), ("source_url", "https://example.test/other"),
    ("document_title", "다른 자료"), ("document_date", "2025-01-01"),
    ("source_document_id", "dart:20260331000002"),
    ("identity_binding", "다른 법인"), ("source_collected_on", "2025-01-01"),
    ("item_title", "하위 항목"), ("domain_attestation_source_id", "다른 증명"),
    ("section_context_json", "변조"),
    ("source_context_json", "변조"), ("source_publisher", "다른 발행자"),
    ("formal_source_kind", "official_web_page"), ("document_content_sha256", "f" * 64),
))
def test_selected_mutation_never_borrows_original(field, value):
    _, fallback, selected, calls, _ = prepare()
    # 생성 단계 검증까지 우회한 입력도 실제 호출 경계에서 exact 비교한다.
    altered = SimpleNamespace(**{name: getattr(selected, name) for name in selected.__dataclass_fields__})
    setattr(altered, field, value)
    assert fallback((altered,)) == () and not calls


def test_missing_current_profile_and_company_mismatch_have_no_callback():
    assert prepare(profile=None)[1] is None
    assert prepare(profile={**PROFILE, "corp_code": "00999999"})[1] is None


def test_no_call_room_and_request_budget_keep_verified_body_without_retry():
    _, fallback, selected, calls, diagnostics = prepare(available=0)
    assert fallback((selected,)) == () and calls == []
    assert diagnostics[-1]["상태"] == "호출여유없음"

    def exhausted(*args):
        raise real.provider_budget.ProviderBudgetExceeded("요청 예산 소진")

    _, fallback, selected, calls, diagnostics = prepare(analyze=exhausted)
    assert fallback((selected,)) == fallback((selected,)) == ()
    assert len(calls) == 1 and diagnostics[-1]["상태"] == "요청예산부족"


def test_unavailable_ledger_is_not_optional_budget_shortfall():
    def uncertain(*args):
        raise real.provider_budget.ProviderBudgetUnavailable("미확정 원장")
    _, fallback, selected, _, _ = prepare(analyze=uncertain)
    with pytest.raises(real.provider_budget.ProviderBudgetUnavailable):
        fallback((selected,))


def test_producer_cannot_dispatch_twice():
    dispatches = []
    def producer(**kwargs):
        kwargs["analyze"]("첫 요청", {}, 3000)
        kwargs["analyze"]("두 번째 요청", {}, 3000)
    _, fallback, selected, _, _ = prepare(collect=producer, analyze=lambda *args: dispatches.append(args))
    with pytest.raises(ValueError, match="한 번"):
        fallback((selected,))
    assert len(dispatches) == 1


def test_official_call_uses_sonnet_180_seconds_native_schema_and_ledger(monkeypatch):
    requests, reservations, replay = [], [], []
    def handler(request):
        requests.append((request.url.path, json.loads(request.content), request.extensions["timeout"]))
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": native.COUNTED_INPUT})
        return httpx.Response(200, json=native.response_body(text='{"검수": []}'))

    def ask(client, prompt, schema, max_tokens):
        response = client.messages.create(model="claude-haiku-4-5", max_tokens=max_tokens,
            temperature=0, messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}})
        return json.loads(response.content[0].text), {"stop_reason": response.stop_reason}

    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: reservations.append((stage, reserved)) or len(reservations),
        lambda _: None, lambda _: None, lambda *_: None,
    )
    monkeypatch.setattr(real, "_record_local_news_analysis_replay", lambda **kwargs: replay.append(kwargs))
    with anthropic.Anthropic(api_key="offline-test", max_retries=0, timeout=180.0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler))) as sdk:
        engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5", _ask=ask))
        client = real._metered_client(engine, sdk)
        with real.provider_budget.activate(2000), attempt_context.activate(callbacks):
            result = real._official_industry_analyzer(engine, client, fatal_error_type=AskFatalError)(
                "공식 검수", {"type": "object"}, 3000)
    assert result == {"검수": []}
    assert len(reservations) == len(engine.usages) == engine._provider_dispatch_count == 1
    assert reservations[0][0] == OFFICIAL_INDUSTRY_STAGE
    assert engine.usages[0]["model"] == real.V2_REVIEW_MODEL
    sent = requests[-1]
    assert sent[1]["model"] == real.V2_REVIEW_MODEL and sent[1]["max_tokens"] == 3000
    assert sent[2]["read"] == 180.0
    assert replay[0]["stage"] == OFFICIAL_INDUSTRY_STAGE
    assert replay[0]["payload"] == result
    assert replay[0]["model"] == real.V2_REVIEW_MODEL


@pytest.mark.parametrize("factory_override", [False, True])
def test_metered_guard_is_lazy_and_rejects_before_actual_analysis(monkeypatch, factory_override):
    sends = []
    low_level = SimpleNamespace(_ask=lambda *args, **kwargs: sends.append(args))
    if factory_override:
        monkeypatch.setattr(real, "_MeteredEngine", lambda engine: engine)
    analyze = real._official_industry_analyzer(low_level, None, fatal_error_type=AskFatalError)
    anchors, fallback = prepare_official_industry_fallback(
        collection=None, profile=None, company_id="00000001", reference_date="2026-10-08",
        anchors=(), analyze=analyze, available_calls=lambda: 1, diagnostics=[],
    )
    assert anchors == () and fallback is None and sends == []
    with pytest.raises(TypeError, match="요청별 계량 래퍼가 필요합니다"):
        analyze("호출 전 검사", {"type": "object"}, 3000)
    assert sends == []


def test_official_timeout_preserves_unknown_cost_failure_replay_and_never_retries(monkeypatch):
    sends, failures, reservations = [], [], []
    def handler(request):
        if request.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": native.COUNTED_INPUT})
        sends.append(request)
        raise httpx.ReadTimeout("로컬 모형 시간 초과", request=request)
    def ask(client, prompt, schema, max_tokens):
        return client.messages.create(model="claude-haiku-4-5", max_tokens=max_tokens,
            temperature=0, messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}})
    from src.features.pipeline import private_replay
    monkeypatch.setattr(private_replay, "local_provider_replay_enabled", lambda: True)
    monkeypatch.setattr(real, "_record_local_news_analysis_failure", lambda **kwargs: failures.append(kwargs))
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: reservations.append((stage, reserved)) or len(reservations),
        lambda _: None, lambda _: None, lambda *_: None,
    )
    with anthropic.Anthropic(api_key="offline-test", max_retries=0, timeout=180.0,
            http_client=httpx.Client(transport=httpx.MockTransport(handler))) as sdk:
        engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5", _ask=ask))
        analyze = real._official_industry_analyzer(engine, real._metered_client(engine, sdk),
            fatal_error_type=AskFatalError)
        with real.provider_budget.activate(2000) as budget, attempt_context.activate(callbacks):
            with pytest.raises(AskFatalError) as caught:
                analyze("보존할 원요청", {"type": "object"}, 3000)
            assert engine.billing_uncertain and budget.accounted_krw > 0
            assert caught.value.provider_failure
            with pytest.raises(AskFatalError):
                analyze("다음 전송 금지", {"type": "object"}, 3000)
    assert len(sends) == len(reservations) == len(failures) == 1
    assert sends[0].extensions["timeout"]["read"] == 180.0
    assert failures[0]["stage"] == OFFICIAL_INDUSTRY_STAGE
    assert failures[0]["request"]["model"] == real.V2_REVIEW_MODEL
    assert failures[0]["request"]["prompt"] == "보존할 원요청"
