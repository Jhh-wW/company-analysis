"""P-125 — OCR 비용이 성공·파싱 실패 모두에서 장부까지 갈 수 있는가."""

from __future__ import annotations

import sys
import types
from io import BytesIO

import pytest
from PIL import Image

from src.core.constants import AI_COST_KRW_PER_USD
from src.features.budget import provider_budget
from src.features.posting_image import constants, logic


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    with provider_budget.activate(10_000.0):
        yield


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output, "PNG")
    return output.getvalue()


def test_OCR_성공결과가_비용과_모델을_그대로_전달한다():
    result = logic.extract_posting_text(
        [_png()],
        extract=lambda _images: logic.ExtractResult(
            text="채용 공고",
            cost_krw=12.34,
            model=constants.DEFAULT_EXTRACT_MODEL,
        ),
    )

    assert result.ok is True
    assert result.cost_krw == 12.34
    assert result.model == constants.DEFAULT_EXTRACT_MODEL


def test_OCR_빈응답도_이미_쓴_비용을_버리지_않는다():
    result = logic.extract_posting_text(
        [_png()],
        extract=lambda _images: logic.ExtractResult(
            text="",
            cost_krw=7.89,
            model=constants.DEFAULT_EXTRACT_MODEL,
        ),
    )

    assert result.ok is False
    assert result.cost_krw == 7.89
    assert result.model == constants.DEFAULT_EXTRACT_MODEL


def test_haiku_사용량을_공통단가와_환율로_계산한다():
    # 입력·출력 각 100만 토큰이라는 단순 경계값이면 1달러+5달러다.
    assert logic._usage_cost_krw(  # noqa: SLF001 — 비용 계산 회귀를 직접 고정한다
        constants.DEFAULT_EXTRACT_MODEL, 1_000_000, 1_000_000
    ) == 6 * AI_COST_KRW_PER_USD


def test_dated_model_snapshot_uses_its_exact_alias_price():
    assert logic._usage_cost_krw(  # noqa: SLF001 - 가격 경계를 직접 고정한다
        f"{constants.DEFAULT_EXTRACT_MODEL}-20251001", 1_000_000, 1_000_000
    ) == logic._usage_cost_krw(  # noqa: SLF001 - 같은 별칭 단가여야 한다
        constants.DEFAULT_EXTRACT_MODEL, 1_000_000, 1_000_000
    )


def _fake_anthropic(
    monkeypatch, *, content, stop_reason="end_turn", include_usage=True
):
    usage = types.SimpleNamespace(input_tokens=1_000, output_tokens=100)
    usage.constructor_calls = []
    response = types.SimpleNamespace(
        model=constants.DEFAULT_EXTRACT_MODEL,
        usage=usage if include_usage else None,
        content=content,
        stop_reason=stop_reason,
    )
    messages = types.SimpleNamespace(create=lambda **_kwargs: response)
    client = types.SimpleNamespace(messages=messages)
    module = types.ModuleType("anthropic")
    def anthropic_client(**kwargs):
        usage.constructor_calls.append(kwargs)
        return client

    module.Anthropic = anthropic_client
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv(constants.ENV_ANTHROPIC_API_KEY, "시험용-가짜키")
    return usage


def test_응답_JSON_파싱실패여도_usage_비용을_보존한다(monkeypatch):
    usage = _fake_anthropic(
        monkeypatch,
        content=[types.SimpleNamespace(type="text", text="{깨진 JSON")],
    )

    result = logic.default_extract([_png()])

    assert result.text == ""
    assert result.cost_krw == logic._usage_cost_krw(
        constants.DEFAULT_EXTRACT_MODEL,
        usage.input_tokens,
        usage.output_tokens,
    )
    assert usage.constructor_calls == [{"max_retries": 0}]


def test_응답_JSON이_객체가_아니어도_usage_비용을_보존한다(monkeypatch):
    usage = _fake_anthropic(
        monkeypatch,
        content=[types.SimpleNamespace(type="text", text='["객체 아님"]')],
    )

    result = logic.default_extract([_png()])

    assert result.text == ""
    assert result.cost_krw == logic._usage_cost_krw(
        constants.DEFAULT_EXTRACT_MODEL,
        usage.input_tokens,
        usage.output_tokens,
    )


def test_extractor_예외는_0원으로_단정하지_않는다():
    def broken(_images):
        raise TimeoutError("provider 응답 불명")

    result = logic.extract_posting_text([_png()], extract=broken)

    assert result.ok is False
    assert result.billing_uncertain is True
    assert result.failure_kind == "technical"


def test_응답_text_블록이_없어도_usage_비용을_보존한다(monkeypatch):
    usage = _fake_anthropic(monkeypatch, content=[])

    result = logic.default_extract([_png()])

    assert result.text == ""
    assert result.cost_krw == logic._usage_cost_krw(
        constants.DEFAULT_EXTRACT_MODEL,
        usage.input_tokens,
        usage.output_tokens,
    )


def test_응답_refusal이어도_usage_비용을_보존한다(monkeypatch):
    usage = _fake_anthropic(
        monkeypatch,
        content=[
            types.SimpleNamespace(
                type="text",
                text='{"full_text":"쓰면 안 됨","is_job_posting":false}',
            )
        ],
        stop_reason="refusal",
    )

    result = logic.default_extract([_png()])

    assert result.text == ""
    assert result.cost_krw == logic._usage_cost_krw(
        constants.DEFAULT_EXTRACT_MODEL,
        usage.input_tokens,
        usage.output_tokens,
    )


def test_응답은_왔지만_usage가_없으면_0원으로_확정하지_않는다(monkeypatch):
    _fake_anthropic(
        monkeypatch,
        content=[
            types.SimpleNamespace(
                type="text",
                text='{"full_text":"채용 공고","is_job_posting":true}',
            )
        ],
        include_usage=False,
    )

    result = logic.default_extract([_png()])

    assert result.text == ""
    assert result.billing_uncertain is True


def test_OCR_예상예약이_부족하면_client도_만들지_않고_provider_0회다(monkeypatch):
    usage = _fake_anthropic(
        monkeypatch,
        content=[
            types.SimpleNamespace(
                type="text",
                text='{"full_text":"채용 공고","is_job_posting":true}',
            )
        ],
    )

    with provider_budget.activate(0.01):
        with pytest.raises(provider_budget.ProviderBudgetExceeded):
            logic.default_extract([_png()])

    assert usage.constructor_calls == []
