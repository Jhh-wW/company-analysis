"""공용 provider gateway는 호출을 한 번만 보내고 결과를 먼저 기록한다."""

from __future__ import annotations

import pytest

from src.core.provider_gateway import gateway
from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)


class _Adapter:
    def success(self, response: object, *, reserved_krw: float) -> ProviderObservation:
        return ProviderObservation(
            transport_state=TransportState.RESPONSE_RECEIVED,
            billing_disposition=BillingDisposition.KNOWN_COST,
            known_cost_krw=2.0,
            liability_krw=0.0,
            status_code=200,
            error_type="",
            request_id="request:ok",
        )

    def failure(self, error: Exception, *, reserved_krw: float) -> ProviderObservation:
        return ProviderObservation(
            transport_state=TransportState.TRANSPORT_AMBIGUOUS,
            billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
            known_cost_krw=0.0,
            liability_krw=reserved_krw,
            status_code=None,
            error_type=type(error).__name__,
            request_id="",
        )


def test_성공은_dispatch표식_전에는_보내지않고_관측을_기록한다() -> None:
    order: list[str] = []

    result = gateway.call_once(
        adapter=_Adapter(),
        reserved_krw=10.0,
        before_dispatch=lambda: order.append("dispatch-intent"),
        send=lambda: order.append("send") or {"ok": True},
        record_observation=lambda _observation: order.append("record"),
    )

    assert order == ["dispatch-intent", "send", "record"]
    assert result == {"ok": True}
    assert gateway.MAX_RETRIES == 0


def test_retry가능오류여도_send는_정확히_한번이고_관측뒤_예외가난다() -> None:
    calls = 0
    recorded: list[ProviderObservation] = []

    def send() -> object:
        nonlocal calls
        calls += 1
        raise TimeoutError("secret response body must not escape")

    with pytest.raises(gateway.ProviderCallFailed) as captured:
        gateway.call_once(
            adapter=_Adapter(),
            reserved_krw=10.0,
            before_dispatch=lambda: None,
            send=send,
            record_observation=recorded.append,
        )

    assert calls == 1
    assert len(recorded) == 1
    assert recorded[0].liability_krw == 10.0
    assert "secret response body" not in str(captured.value)


def test_dispatch표식_기록이_실패하면_provider를_보내지않는다() -> None:
    sent = False

    def fail_before_dispatch() -> None:
        raise RuntimeError("DB write failed")

    def send() -> object:
        nonlocal sent
        sent = True
        return object()

    with pytest.raises(gateway.ProviderDispatchNotStarted) as captured:
        gateway.call_once(
            adapter=_Adapter(),
            reserved_krw=10.0,
            before_dispatch=fail_before_dispatch,
            send=send,
            record_observation=lambda _observation: None,
        )

    assert sent is False
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_전송뒤_관측기록실패는_재시도하지않고_별도오류로_알린다() -> None:
    sent = 0

    def send() -> object:
        nonlocal sent
        sent += 1
        return object()

    def fail_record(_observation: ProviderObservation) -> None:
        raise RuntimeError("DB unavailable")

    with pytest.raises(gateway.ProviderObservationRecordFailed):
        gateway.call_once(
            adapter=_Adapter(),
            reserved_krw=10.0,
            before_dispatch=lambda: None,
            send=send,
            record_observation=fail_record,
        )

    assert sent == 1


@pytest.mark.parametrize("method", ("success", "failure"))
def test_adapter_관측변환이_깨져도_전송비용을_보수부채로_기록한다(method: str) -> None:
    class BrokenAdapter(_Adapter):
        def success(self, response: object, *, reserved_krw: float):
            if method == "success":
                raise ValueError("usage 변환 실패")
            return super().success(response, reserved_krw=reserved_krw)

        def failure(self, error: Exception, *, reserved_krw: float):
            if method == "failure":
                raise ValueError("예외 status 변환 실패")
            return super().failure(error, reserved_krw=reserved_krw)

    sent = 0
    recorded: list[ProviderObservation] = []

    def send() -> object:
        nonlocal sent
        sent += 1
        if method == "failure":
            raise TimeoutError("provider timeout")
        return object()

    with pytest.raises(gateway.ProviderCallFailed):
        gateway.call_once(
            adapter=BrokenAdapter(),
            reserved_krw=17.0,
            before_dispatch=lambda: None,
            send=send,
            record_observation=recorded.append,
        )

    assert sent == 1
    assert len(recorded) == 1
    assert recorded[0].billing_disposition is BillingDisposition.CONSERVATIVE_LIABILITY
    assert recorded[0].liability_krw == 17.0
    assert recorded[0].transport_state is (
        TransportState.RESPONSE_RECEIVED
        if method == "success"
        else TransportState.TRANSPORT_AMBIGUOUS
    )
