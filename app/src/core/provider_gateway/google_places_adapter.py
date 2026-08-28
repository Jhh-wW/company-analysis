"""Google Places 응답·예외를 고정 회계비용과 보수부채로 분류한다."""

from __future__ import annotations

import math
import urllib.error
from typing import Any

from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)


def _header_request_id(error: Exception) -> str:
    headers = getattr(error, "headers", None) or getattr(error, "hdrs", None)
    if headers is None:
        return ""
    for name in ("x-request-id", "request-id"):
        candidate = headers.get(name)
        if isinstance(candidate, str) and candidate:
            return candidate[:128]
    return ""


class GooglePlacesAdapter:
    """Text Search 성공은 승인된 고정 회계값, 실패는 별도 부채로 둔다."""

    def __init__(self, *, accounting_cost_krw: float) -> None:
        cost = float(accounting_cost_krw)
        if not math.isfinite(cost) or cost <= 0:
            raise ValueError("Google Places 회계비용은 0보다 커야 합니다")
        self._accounting_cost_krw = cost

    def success(self, response: Any, *, reserved_krw: float) -> ProviderObservation:
        request_id = str(getattr(response, "request_id", "") or "")[:128]
        return ProviderObservation(
            transport_state=TransportState.RESPONSE_RECEIVED,
            billing_disposition=BillingDisposition.KNOWN_COST,
            known_cost_krw=self._accounting_cost_krw,
            liability_krw=0.0,
            status_code=200,
            error_type="",
            request_id=request_id,
        )

    def failure(
        self, error: Exception, *, reserved_krw: float
    ) -> ProviderObservation:
        raw_status = getattr(error, "status_code", None)
        if raw_status is None:
            raw_status = getattr(error, "code", None)
        status = int(raw_status) if isinstance(raw_status, int) else None
        transport = (
            TransportState.RESPONSE_RECEIVED
            if isinstance(error, urllib.error.HTTPError) or status is not None
            else TransportState.TRANSPORT_AMBIGUOUS
        )
        # Text Search는 승인된 고정 회계값을 쓴다. 2xx 응답을 받은 뒤 JSON 해석이나
        # 크기 검사가 실패한 경우에도 provider 전송·응답은 확정됐으므로 0원이나
        # 미확정 부채로 돌리지 않고 같은 고정 비용을 남긴다.
        if status is not None and 200 <= status < 300:
            return ProviderObservation(
                transport_state=transport,
                billing_disposition=BillingDisposition.KNOWN_COST,
                known_cost_krw=self._accounting_cost_krw,
                liability_krw=0.0,
                status_code=status,
                error_type=type(error).__name__[:128],
                request_id=_header_request_id(error),
            )
        return ProviderObservation(
            transport_state=transport,
            billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
            known_cost_krw=0.0,
            liability_krw=float(reserved_krw),
            status_code=status,
            error_type=type(error).__name__[:128],
            request_id=_header_request_id(error),
        )
