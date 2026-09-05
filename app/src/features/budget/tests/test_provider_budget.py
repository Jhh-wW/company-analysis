"""provider 직전 예상비용 gateway의 로컬·무네트워크 계약."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import pytest

from src.features.budget import provider_budget


_HAIKU = "claude-haiku-4-5"


class _RecordingCounter:
    """SDK ``client.messages`` 대역 — 받은 kwargs를 그대로 보관한다."""

    def __init__(self, input_tokens: object = 1234) -> None:
        self.calls: list[dict[str, Any]] = []
        self._input_tokens = input_tokens

    def count_tokens(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(input_tokens=self._input_tokens)


class _RaisingCounter:
    """네트워크·인증 실패처럼 계수 도중 예외가 나는 경우."""

    def count_tokens(self, **_kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("provider 계수 실패 SECRET_PROMPT_MARKER")


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


def test_정확계수가_있으면_한글_payload의_바이트추정보다_작다():
    payload = {"text": "가" * 2_000}

    byte_estimate = provider_budget.estimate_request_tokens(payload)
    exact_estimate = provider_budget.estimate_request_tokens_exact(
        payload, exact_input_tokens=800
    )

    # 방어여유 4096을 상수로 참조하면 상수가 0이 되어도 시험이 통과해 회귀를 못
    # 잡는다. 관측 가능한 값을 리터럴로 못 박는다.
    assert exact_estimate == 800 + 4096
    assert exact_estimate < byte_estimate
    # 한글 1글자 = UTF-8 3바이트라 바이트 추정은 실제 token보다 부풀려진다.
    assert byte_estimate > 2_000 * 3


def test_정확계수가_없으면_기존_바이트추정과_같다():
    payload = {"text": "가"}

    fallback = provider_budget.estimate_request_tokens_exact(
        payload, exact_input_tokens=None
    )

    assert fallback == provider_budget.estimate_request_tokens(payload)
    # compact UTF-8 14바이트 + 고정 방어여유.
    assert fallback == 14 + 4096


def test_count_tokens가_예외를_내면_None으로_복귀한다():
    messages = [{"role": "user", "content": "안녕"}]
    payload = {"text": "가"}

    assert (
        provider_budget.count_input_tokens(
            _RecordingCounter(), model=_HAIKU, messages=messages
        )
        == 1234
    )
    assert (
        provider_budget.count_input_tokens(
            _RaisingCounter(), model=_HAIKU, messages=messages
        )
        is None
    )
    # SDK 자체가 없으면 count_tokens 속성도 없다(AttributeError).
    assert (
        provider_budget.count_input_tokens(
            object(), model=_HAIKU, messages=messages
        )
        is None
    )
    assert (
        provider_budget.count_input_tokens(
            _RecordingCounter(input_tokens="1234"), model=_HAIKU, messages=messages
        )
        is None
    )
    assert (
        provider_budget.count_input_tokens(
            _RecordingCounter(input_tokens=-1), model=_HAIKU, messages=messages
        )
        is None
    )
    # 계수가 없으면 예약액은 예전 바이트 추정 그대로여야 한다(복귀 계약).
    assert provider_budget.estimate_request_tokens_exact(
        payload,
        exact_input_tokens=provider_budget.count_input_tokens(
            _RaisingCounter(), model=_HAIKU, messages=messages
        ),
    ) == provider_budget.estimate_request_tokens(payload)


def test_계수_실패_로그에는_프롬프트도_예외문도_남지_않는다(caplog):
    messages = [{"role": "user", "content": "대외비 회사 자료 SECRET_PROMPT_MARKER"}]

    with caplog.at_level(logging.WARNING, logger=provider_budget.__name__):
        provider_budget.count_input_tokens(
            _RaisingCounter(), model=_HAIKU, messages=messages
        )

    warnings = [
        record for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        "provider 입력 토큰 계수를 얻지 못해 바이트 추정으로 대체합니다"
    )
    assert "SECRET_PROMPT_MARKER" not in caplog.text
    assert warnings[0].exc_info is None


def test_count_tokens는_system이_있을때만_넘긴다():
    resource = _RecordingCounter()
    messages = [{"role": "user", "content": "안녕"}]

    provider_budget.count_input_tokens(resource, model=_HAIKU, messages=messages)
    provider_budget.count_input_tokens(
        resource, model=_HAIKU, messages=messages, system="너는 분석가다"
    )
    provider_budget.count_input_tokens(
        resource, model=_HAIKU, messages=messages, system=""
    )

    assert "system" not in resource.calls[0]
    assert resource.calls[1]["system"] == "너는 분석가다"
    # 빈 문자열은 «system 없음»과 같게 다뤄 SDK에 빈 값을 넘기지 않는다.
    assert "system" not in resource.calls[2]
    assert resource.calls[0]["model"] == _HAIKU
    assert resource.calls[0]["messages"] == messages


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
