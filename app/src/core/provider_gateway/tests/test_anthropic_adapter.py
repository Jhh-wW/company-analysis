"""Anthropic SDK 예외의 전송 상태와 비용 상태를 별개로 분류한다."""

from __future__ import annotations

import anthropic
import httpx

from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter
from src.core.provider_gateway.types import BillingDisposition, TransportState


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_timeout예외는_usage가_없어도_보수부채로_보존한다() -> None:
    error = anthropic.APITimeoutError(request=_request())

    observation = AnthropicAdapter(lambda _response: 1.0).failure(
        error,
        reserved_krw=12.5,
    )

    assert not hasattr(error, "usage")
    assert observation.transport_state is TransportState.TRANSPORT_AMBIGUOUS
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.liability_krw == 12.5
    assert observation.error_type == "APITimeoutError"
    assert observation.status_code is None


def test_429응답은_응답수신과_청구불명을_동시에_기록한다() -> None:
    response = httpx.Response(
        429,
        request=_request(),
        headers={"request-id": "request:rate"},
    )
    error = anthropic.RateLimitError(
        "rate limited",
        response=response,
        body={"type": "error"},
    )

    observation = AnthropicAdapter(lambda _response: 1.0).failure(
        error,
        reserved_krw=8.0,
    )

    assert observation.transport_state is TransportState.RESPONSE_RECEIVED
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.status_code == 429
    assert observation.request_id == "request:rate"
    assert observation.liability_krw == 8.0


def test_정상응답도_usage비용을_확인못하면_0원으로_만들지않는다() -> None:
    class Response:
        id = "request:missing-usage"

    observation = AnthropicAdapter(lambda _response: None).success(
        Response(),
        reserved_krw=7.0,
    )

    assert observation.transport_state is TransportState.RESPONSE_RECEIVED
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.known_cost_krw == 0.0
    assert observation.liability_krw == 7.0


def test_정상응답의_확정usage비용은_확정액으로_분류한다() -> None:
    class Response:
        id = "request:known"

    observation = AnthropicAdapter(lambda _response: 3.25).success(
        Response(),
        reserved_krw=7.0,
    )

    assert observation.billing_disposition is BillingDisposition.KNOWN_COST
    assert observation.known_cost_krw == 3.25
    assert observation.liability_krw == 0.0


def test_예외에_권위있는_usage가_실리면_기존처럼_확정비용으로_남긴다() -> None:
    error = RuntimeError("synthetic usage-bearing failure")
    adapter = AnthropicAdapter(
        lambda _response: None,
        failure_cost_resolver=lambda _error: 1.75,
    )

    observation = adapter.failure(error, reserved_krw=7.0)

    assert observation.billing_disposition is BillingDisposition.KNOWN_COST
    assert observation.known_cost_krw == 1.75
    assert observation.liability_krw == 0.0
