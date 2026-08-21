"""유료 단계 context가 비용 표식을 정확히 한 번만 닫는지 본다."""

from __future__ import annotations

import datetime as dt

import pytest

from src.web import paid_runtime


def _ticket() -> paid_runtime.PaidPhase:
    return paid_runtime.PaidPhase(
        run_id="run-1",
        phase="identify",
        day=dt.date(2026, 8, 21),
        share_key="bucket",
        bucket_id="bucket-hash",
    )


def _patch_lifecycle(monkeypatch, ticket):
    settled: list[tuple[float, bool]] = []
    cancelled: list[object] = []
    monkeypatch.setattr(paid_runtime, "_begin_paid_phase", lambda **_kwargs: ticket)
    monkeypatch.setattr(
        paid_runtime,
        "_settle_paid_phase",
        lambda _ticket, *, amount_krw, billing_uncertain: settled.append(
            (amount_krw, billing_uncertain)
        ),
    )
    monkeypatch.setattr(
        paid_runtime, "_cancel_paid_phase", lambda value: cancelled.append(value)
    )
    return settled, cancelled


def test_provider_전_정상_이탈은_취소만_한번한다(monkeypatch):
    settled, cancelled = _patch_lifecycle(monkeypatch, _ticket())

    with paid_runtime.paid_phase(
        run_id="run-1", phase="identify", share_key="bucket", cap_krw=100
    ) as phase:
        assert phase.ticket is not None

    assert settled == []
    assert len(cancelled) == 1


def test_provider_후_예외는_미확정으로_한번_정산한다(monkeypatch):
    settled, cancelled = _patch_lifecycle(monkeypatch, _ticket())

    with pytest.raises(RuntimeError, match="provider failure"):
        with paid_runtime.paid_phase(
            run_id="run-1", phase="identify", share_key="bucket", cap_krw=100
        ) as phase:
            phase.mark_provider_started()
            raise RuntimeError("provider failure")

    assert settled == [(0.0, True)]
    assert cancelled == []


def test_정상_정산뒤_finally가_다시_취소하지_않는다(monkeypatch):
    settled, cancelled = _patch_lifecycle(monkeypatch, _ticket())

    with paid_runtime.paid_phase(
        run_id="run-1", phase="identify", share_key="bucket", cap_krw=100
    ) as phase:
        phase.mark_provider_started()
        phase.settle(amount_krw=12.5, billing_uncertain=False)

    assert settled == [(12.5, False)]
    assert cancelled == []


def test_표식을_열지못하면_정산과_취소가_없다(monkeypatch):
    settled, cancelled = _patch_lifecycle(monkeypatch, None)

    with paid_runtime.paid_phase(
        run_id="run-1", phase="identify", share_key="bucket", cap_krw=100
    ) as phase:
        assert phase.ticket is None

    assert settled == []
    assert cancelled == []
