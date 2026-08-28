"""Anthropic SDK 응답·예외를 비용과 전송의 두 축으로 바꾸는 adapter."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)


CostResolver = Callable[[Any], float | None]


def _request_id(value: object) -> str:
    for attribute in ("request_id", "_request_id", "id"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and candidate:
            return candidate[:128]
    return ""


def _error_request_id(error: Exception) -> str:
    direct = _request_id(error)
    if direct:
        return direct
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in ("request-id", "x-request-id"):
            candidate = headers.get(name)
            if isinstance(candidate, str) and candidate:
                return candidate[:128]
    return ""


class AnthropicAdapter:
    """SDK 예외에 usage가 없다는 실제 계약을 보수적으로 처리한다."""

    def __init__(
        self,
        cost_resolver: CostResolver,
        *,
        failure_cost_resolver: CostResolver | None = None,
    ) -> None:
        self._cost_resolver = cost_resolver
        self._failure_cost_resolver = failure_cost_resolver

    def success(self, response: Any, *, reserved_krw: float) -> ProviderObservation:
        reserved = float(reserved_krw)
        actual = self._cost_resolver(response)
        if actual is None:
            return ProviderObservation(
                transport_state=TransportState.RESPONSE_RECEIVED,
                billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
                known_cost_krw=0.0,
                liability_krw=reserved,
                status_code=200,
                error_type="MissingUsage",
                request_id=_request_id(response),
            )
        known = float(actual)
        if not math.isfinite(known) or known < 0:
            raise ValueError("Anthropic 확정 usage 비용이 올바르지 않습니다")
        return ProviderObservation(
            transport_state=TransportState.RESPONSE_RECEIVED,
            billing_disposition=BillingDisposition.KNOWN_COST,
            known_cost_krw=known,
            liability_krw=0.0,
            status_code=200,
            error_type="",
            request_id=_request_id(response),
        )

    def failure(
        self, error: Exception, *, reserved_krw: float
    ) -> ProviderObservation:
        response = getattr(error, "response", None)
        raw_status = getattr(error, "status_code", None)
        if raw_status is None and response is not None:
            raw_status = getattr(response, "status_code", None)
        status = int(raw_status) if isinstance(raw_status, int) else None
        transport = (
            TransportState.RESPONSE_RECEIVED
            if response is not None or status is not None
            else TransportState.TRANSPORT_AMBIGUOUS
        )
        if self._failure_cost_resolver is not None:
            actual = self._failure_cost_resolver(error)
            if actual is not None:
                known = float(actual)
                if not math.isfinite(known) or known < 0:
                    raise ValueError("Anthropic 실패 usage 비용이 올바르지 않습니다")
                return ProviderObservation(
                    transport_state=transport,
                    billing_disposition=BillingDisposition.KNOWN_COST,
                    known_cost_krw=known,
                    liability_krw=0.0,
                    status_code=status,
                    error_type=type(error).__name__[:128],
                    request_id=_error_request_id(error),
                )
        # 400은 형식 오류와 조직 지출한도를, 429는 속도 제한과 월 지출한도를
        # 함께 나타낼 수 있다. status만으로 0원을 단정하지 않는다.
        return ProviderObservation(
            transport_state=transport,
            billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
            known_cost_krw=0.0,
            liability_krw=float(reserved_krw),
            status_code=status,
            error_type=type(error).__name__[:128],
            request_id=_error_request_id(error),
        )
