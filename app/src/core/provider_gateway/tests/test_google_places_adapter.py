"""Google Places의 고정 회계비용과 실패 부채 분류 계약."""

from __future__ import annotations

import socket
import urllib.error

from src.core.provider_gateway.google_places_adapter import GooglePlacesAdapter
from src.core.provider_gateway.types import BillingDisposition, TransportState


def test_성공응답은_고정_회계비용으로_확정한다() -> None:
    observation = GooglePlacesAdapter(accounting_cost_krw=49.0).success(
        object(),
        reserved_krw=50.0,
    )

    assert observation.transport_state is TransportState.RESPONSE_RECEIVED
    assert observation.billing_disposition is BillingDisposition.KNOWN_COST
    assert observation.known_cost_krw == 49.0
    assert observation.liability_krw == 0.0


def test_HTTP오류는_status만으로_0원이라_단정하지않는다() -> None:
    error = urllib.error.HTTPError(
        "https://places.googleapis.com/v1/places:searchText",
        400,
        "bad request body must not persist",
        hdrs={"x-request-id": "places:bad"},
        fp=None,
    )

    observation = GooglePlacesAdapter(accounting_cost_krw=49.0).failure(
        error,
        reserved_krw=50.0,
    )

    assert observation.transport_state is TransportState.RESPONSE_RECEIVED
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.status_code == 400
    assert observation.liability_krw == 50.0
    assert observation.error_type == "HTTPError"


def test_socket_timeout은_전송모호와_보수부채다() -> None:
    observation = GooglePlacesAdapter(accounting_cost_krw=49.0).failure(
        socket.timeout("timed out"),
        reserved_krw=50.0,
    )

    assert observation.transport_state is TransportState.TRANSPORT_AMBIGUOUS
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.status_code is None
    assert observation.liability_krw == 50.0


def test_2xx뒤_로컬해석실패는_고정비용으로_확정한다() -> None:
    error = RuntimeError("깨진 JSON")
    error.status_code = 200

    observation = GooglePlacesAdapter(accounting_cost_krw=49.0).failure(
        error,
        reserved_krw=50.0,
    )

    assert observation.transport_state is TransportState.RESPONSE_RECEIVED
    assert observation.billing_disposition is BillingDisposition.KNOWN_COST
    assert observation.known_cost_krw == 49.0
    assert observation.liability_krw == 0.0
