"""provider 직전 예상비용 gateway의 로컬·무네트워크 계약."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from src.features.budget import provider_budget


_HAIKU = "claude-haiku-4-5"


def test_dated_model_snapshot_uses_its_exact_alias_price():
    assert provider_budget.usage_cost_krw(
        f"{_HAIKU}-20251001", 1_000_000, 1_000_000
    ) == provider_budget.usage_cost_krw(_HAIKU, 1_000_000, 1_000_000)


def test_vision_estimate는_공식_28px_grid와_이미지별_최대값을_쓴다():
    thin = provider_budget.estimate_image_tokens([(1, 10_000)])
    huge = provider_budget.estimate_image_tokens([(10_000, 10_000)] * 4)

    assert thin == 4096 + 358 + 32
    assert huge == 4096 + 4 * (1568 + 32)


def test_text_estimate는_utf8_payload와_고정_방어여유를_모두_센다():
    ascii_only = provider_budget.estimate_request_tokens({"text": "a"})
    korean = provider_budget.estimate_request_tokens({"text": "가"})

    assert ascii_only >= provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
    assert korean > ascii_only


def test_작은_호출은_허용하고_실제_usage만_남긴다():
    budget = provider_budget.ProviderBudget(10.0)
    reservation = budget.reserve_call(
        model=_HAIKU, input_tokens_upper=100, max_tokens=100
    )

    budget.settle_call(reservation, actual_krw=0.25)

    assert reservation.estimated_krw == 0.84
    assert budget.accounted_krw == pytest.approx(0.25)
    assert budget.estimate_overrun_krw == 0.0


def test_실제액이_예상액을_넘어도_전액과_차이를_숨기지_않는다():
    budget = provider_budget.ProviderBudget(10.0)
    reservation = budget.reserve_call(
        model=_HAIKU, input_tokens_upper=100, max_tokens=100
    )

    budget.settle_call(reservation, actual_krw=2.0)

    assert budget.accounted_krw == pytest.approx(2.0)
    assert budget.estimate_overrun_krw == pytest.approx(1.16)


def test_usage_불명은_호출전_예상액을_계속_보류한다():
    budget = provider_budget.ProviderBudget(10.0)
    reservation = budget.reserve_call(
        model=_HAIKU, input_tokens_upper=100, max_tokens=100
    )

    budget.mark_unknown(reservation)

    assert budget.accounted_krw == pytest.approx(reservation.estimated_krw)


def test_provider에_보내기전_callback실패는_호출예약을_반환한다():
    budget = provider_budget.ProviderBudget(10.0)
    reservation = budget.reserve_call(
        model=_HAIKU, input_tokens_upper=100, max_tokens=100
    )

    budget.cancel_before_dispatch(reservation)

    assert budget.accounted_krw == 0.0
    with pytest.raises(provider_budget.ProviderCostInvariantError):
        budget.cancel_before_dispatch(reservation)


def test_동시_호출도_단계_예상액을_원자적으로_예약한다():
    budget = provider_budget.ProviderBudget(1.0)
    barrier = Barrier(4)

    def reserve(_index: int) -> bool:
        barrier.wait(timeout=5)
        try:
            budget.reserve_call(
                model=_HAIKU, input_tokens_upper=100, max_tokens=100
            )
        except provider_budget.ProviderBudgetExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(reserve, range(4)))

    assert accepted.count(True) == 1
    assert accepted.count(False) == 3
    assert budget.accounted_krw == pytest.approx(0.84)


def test_예약문맥_밖에서는_provider_gateway를_열지_않는다():
    with pytest.raises(provider_budget.ProviderBudgetUnavailable):
        provider_budget.current()
