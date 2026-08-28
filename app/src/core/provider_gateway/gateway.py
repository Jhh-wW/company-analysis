"""자동 재시도 없이 유료 provider를 한 번만 부르는 실행 경계."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TypeVar

from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderAdapter,
    ProviderObservation,
    TransportState,
)


MAX_RETRIES = 0
ResponseT = TypeVar("ResponseT")


class ProviderCallFailed(RuntimeError):
    """비민감 관측을 저장한 뒤 원래 provider 실패를 상위로 전달함."""

    def __init__(self, observation: ProviderObservation) -> None:
        super().__init__("유료 provider 호출이 실패했습니다")
        self.observation = observation


class ProviderDispatchNotStarted(RuntimeError):
    """전송 의도 callback 실패로 provider를 부르지 않음."""

    def __init__(self) -> None:
        super().__init__("provider 전송 의도를 기록하지 못해 호출하지 않았습니다")


class ProviderObservationRecordFailed(RuntimeError):
    """provider 전송 뒤 관측 callback이 실패해 lease 회수가 필요함."""

    def __init__(self, observation: ProviderObservation) -> None:
        super().__init__("provider 호출 결과를 비용 원장에 기록하지 못했습니다")
        self.observation = observation


def _fallback_observation(
    *, reserved_krw: float, response_received: bool, error_type: str
) -> ProviderObservation:
    """adapter 자체가 깨져도 전송 뒤 비용 사건을 빈칸으로 남기지 않는다."""

    return ProviderObservation(
        transport_state=(
            TransportState.RESPONSE_RECEIVED
            if response_received
            else TransportState.TRANSPORT_AMBIGUOUS
        ),
        billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
        known_cost_krw=0.0,
        liability_krw=reserved_krw,
        status_code=None,
        error_type=error_type,
        request_id="",
    )


def _record_or_raise(
    observation: ProviderObservation,
    record_observation: Callable[[ProviderObservation], None],
) -> None:
    try:
        record_observation(observation)
    except Exception as error:
        raise ProviderObservationRecordFailed(observation) from error


def call_once(
    *,
    adapter: ProviderAdapter[ResponseT],
    reserved_krw: float,
    before_dispatch: Callable[[], None],
    send: Callable[[], ResponseT],
    record_observation: Callable[[ProviderObservation], None],
) -> ResponseT:
    """전송 의도를 먼저 commit하고, 정확히 한 번 전송하고, 결과를 먼저 기록한다.

    ``before_dispatch``가 실패하면 네트워크를 부르지 않는다. 전송 뒤 결과 기록이
    실패하면 호출자는 DB lease를 그대로 남겨 만료 시 보수부채로 바꾼다. 이 함수에는
    반복문과 SDK 재시도 옵션이 없으며 실제 client도 ``max_retries=0``으로 만들어야
    한다.
    """
    reservation = float(reserved_krw)
    if not math.isfinite(reservation) or reservation <= 0:
        raise ValueError("provider 호출 예약액은 0보다 큰 유한한 수여야 합니다")
    try:
        before_dispatch()
    except Exception as error:
        raise ProviderDispatchNotStarted() from error
    try:
        response = send()
    except Exception as error:
        try:
            observation = adapter.failure(error, reserved_krw=reservation)
            if not isinstance(observation, ProviderObservation):
                raise TypeError("provider adapter 실패 관측 형식이 올바르지 않습니다")
        except Exception:
            # 네트워크 전송은 이미 시작됐다. adapter의 usage/status 해석 결함을
            # 0원이나 미기록으로 바꾸지 않고 예약 전액을 보수부채로 남긴다.
            observation = _fallback_observation(
                reserved_krw=reservation,
                response_received=False,
                error_type="AdapterFailureFailed",
            )
        _record_or_raise(observation, record_observation)
        raise ProviderCallFailed(observation) from error
    try:
        observation = adapter.success(response, reserved_krw=reservation)
        if not isinstance(observation, ProviderObservation):
            raise TypeError("provider adapter 성공 관측 형식이 올바르지 않습니다")
    except Exception as error:
        # 응답을 받았는데 usage 변환기가 깨진 경우도 dispatch-intent를 열린 채
        # 두지 않는다. response 본문은 싣지 않고 예약액만 보수적으로 기록한다.
        observation = _fallback_observation(
            reserved_krw=reservation,
            response_received=True,
            error_type="AdapterSuccessFailed",
        )
        _record_or_raise(observation, record_observation)
        raise ProviderCallFailed(observation) from error
    _record_or_raise(observation, record_observation)
    return response
