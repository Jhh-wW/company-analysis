"""provider attempt callback은 요청 문맥마다 격리되고 종료 뒤 사라진다."""

from __future__ import annotations

import pytest

from src.core.provider_gateway import attempt_context


def _callbacks(label: str) -> attempt_context.ProviderAttemptCallbacks:
    return attempt_context.ProviderAttemptCallbacks(
        begin_attempt=lambda _provider, _operation, _reserved: label,
        heartbeat=lambda _token: None,
        mark_dispatch_intent=lambda _token: None,
        record_observation=lambda _token, _observation: None,
    )


def test_문맥밖_provider_attempt는_fail_closed한다() -> None:
    with pytest.raises(attempt_context.ProviderAttemptContextUnavailable):
        attempt_context.current()


def test_중첩문맥은_안쪽종료뒤_바깥callback으로_복원된다() -> None:
    outer = _callbacks("outer")
    inner = _callbacks("inner")

    with attempt_context.activate(outer):
        assert attempt_context.current() is outer
        with attempt_context.activate(inner):
            assert attempt_context.current() is inner
        assert attempt_context.current() is outer

    with pytest.raises(attempt_context.ProviderAttemptContextUnavailable):
        attempt_context.current()
