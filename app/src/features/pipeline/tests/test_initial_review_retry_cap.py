"""1차 본문 검수 «재요청»의 출력 상한을 첫 답의 실제 출력에서 구한다.

★ 2026-09-13 실측(키움증권·claude-haiku-4-5·환율 1,400원/USD) — 본조사 예약액
  1,000원 중 713원을 쓴 뒤 1차 검수 답이 정상 종료했는데 JSON 문법 오류로
  68문장 중 0행만 읽혔다. 재요청을 걸었지만 호출 «전» 예약액이 출력 상한
  24,000토큰으로 잡혀 남은 287원을 넘겼고, 1차 검수는 강등 대상이 아니라 조사
  전체가 「AI 예산 소진」으로 멈췄다. 첫 답의 실제 출력은 약 7,000토큰이었다.

여기서 지키는 것:
  ① 재요청 상한 = 첫 답 출력 × 1.5, 하한 12,000 · 상한 24,000 (리터럴 오라클).
  ② 같은 잔액에서 24,000 상한은 막히고 줄인 상한은 «실제로» 전송된다.

실행기가 composer 에 넘기는 «그 객체»의 배선은
``test_v2_ask_reserved_calls_wiring.py`` 가 본다 (그쪽 예약·배포 fixture 안에서
`_run_v2_composer` 를 실제로 돌린다).
"""

from types import SimpleNamespace
from typing import Any

import pytest

from src.core.constants import MAX_AI_CALLS_PER_REQUEST
from src.core.pricing import usage_cost_krw
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.pipeline import real
from src.features.pipeline.tests.test_diagram_budget_metering import (
    RecordingMessages,
)
from src.features.pipeline.v2_response_constants import V2_RESPONSE_STEP

provider_budget = real.provider_budget
run_diagnostics = real.run_diagnostics

# ★ 리터럴 오라클 — 생산 상수를 import해 비교하면 값이 내려가도 초록이다.
_상한 = 24000  # V2_INITIAL_REVIEWER_MAX_TOKENS
_하한 = 12000  # V2_INITIAL_REVIEW_RETRY_MIN_TOKENS (= 상한의 절반)

# 실측 사고의 수치. 본문 검수 프롬프트는 조각 전체를 실어 입력이 크다.
_모델 = "claude-haiku-4-5"
_입력토큰 = 119943
_첫답출력 = 7000
_사고시점_누적 = 713.0
_본조사_예약액 = 1000.0


def _엔진과_사용량(*usages: dict[str, Any]) -> real._MeteredEngine:
    """계량 엔진에 «이미 성공한» 응답 사용량을 심는다 (시험 전용 주입)."""
    engine = real._MeteredEngine(SimpleNamespace(MODEL=_모델))
    engine._usages.extend(usages)
    return engine


def _사용량(stage: str, out: Any) -> dict[str, Any]:
    return {"in": _입력토큰, "out": out, "stage": stage, "cost_krw": 0.0,
            "failed": False}


@pytest.mark.parametrize("usages,expected", [
    # 쓸 만한 사용량이 없으면 예전 그대로 큰 상한을 쓴다 (모르는 값을 지어내지 않는다).
    ((), 24000),
    # 7,000 × 1.5 = 10,500 → 하한 12,000 으로 올린다 (실측 사고의 첫 답).
    ((_사용량("v2_review", 7000),), 12000),
    ((_사용량("v2_review", 10000),), 15000),
    # 첫 답이 상한에서 잘린 경우 — 1.5배가 상한에 걸려 예전과 같은 값이 된다.
    ((_사용량("v2_review", 24000),), 24000),
    ((_사용량("v2_review", 20000),), 24000),
    # 검수가 아닌 단계의 사용량은 재요청 상한의 근거가 아니다.
    ((_사용량("v2_compose", 3000),), 24000),
    # 검수가 여러 번이면 «마지막» 답이 재요청의 기준이다.
    ((_사용량("v2_review", 3000), _사용량("v2_review", 10000)), 15000),
    # 출력이 없거나 비정상 자료형이면 근거로 쓰지 않는다.
    ((_사용량("v2_review", 0),), 24000),
    ((_사용량("v2_review", None),), 24000),
    ((_사용량("v2_review", "7000"),), 24000),
    ((_사용량("v2_review", 8000), _사용량("v2_compose", 3000)), 12000),
])
def test_재요청_상한은_첫_답_실제_출력에서_나온다(usages, expected) -> None:
    engine = _엔진과_사용량(*usages)
    assert real._initial_review_retry_max_tokens(engine) == expected


def test_하한과_상한은_설계값_그대로다() -> None:
    """상한을 건드리지 않았고 하한은 그 절반이다 — 값이 바뀌면 여기서 걸린다."""
    assert real.V2_INITIAL_REVIEWER_MAX_TOKENS == _상한
    assert real.V2_INITIAL_REVIEW_RETRY_MIN_TOKENS == _하한
    assert real.V2_INITIAL_REVIEW_RETRY_OUTPUT_HEADROOM == 1.5


@pytest.fixture
def 검수_진단():
    collector = run_diagnostics.begin_run()
    try:
        yield collector
    finally:
        collector.finish()


def test_줄인_상한이_사고와_같은_잔액에서_재요청을_통과시킨다(검수_진단) -> None:
    """713원을 쓴 뒤: 24,000 상한은 막히고 12,000 상한은 실제로 전송된다."""
    messages = RecordingMessages(
        "end_turn", input_tokens=_입력토큰, output_tokens=_첫답출력,
    )
    engine = real._MeteredEngine(SimpleNamespace(MODEL=_모델))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    reservations: list[tuple[str, str, float]] = []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: (
            reservations.append((provider, stage, reserved)) or len(reservations)
        ),
        lambda _: None, lambda _: None, lambda _token, _observation: None,
    )
    첫검수 = real._v2_ask_via_provider(
        engine, client, stage="v2_review",
        max_tokens=real.V2_INITIAL_REVIEWER_MAX_TOKENS,
    )
    재요청 = real._v2_ask_via_provider(
        engine, client, stage="v2_review",
        max_tokens=lambda: real._initial_review_retry_max_tokens(engine),
    )
    첫답_실제 = usage_cost_krw(_모델, _입력토큰, _첫답출력)
    with provider_budget.activate(_본조사_예약액) as budget, attempt_context.activate(
        callbacks
    ):
        # 뉴스·장 작성으로 이미 나간 몫을 앞선 확정 지출로 심는다.
        seed = budget.reserve_call(model=_모델, input_tokens_upper=1, max_tokens=1)
        budget.settle_call(seed, actual_krw=_사고시점_누적 - 첫답_실제)
        # ① 1차 검수는 큰 상한 그대로 나가고 실제 사용량으로 정산된다.
        assert 첫검수("1차 본문 검수 입력") == '{"판정": []}'
        assert budget.accounted_krw == pytest.approx(_사고시점_누적, abs=0.01)
        assert engine.usages[-1]["stage"] == "v2_review"
        assert engine.usages[-1]["out"] == _첫답출력

        남은예약액 = _본조사_예약액 - budget.accounted_krw
        보낸호출수 = len(messages.requests)
        # ② 같은 큰 상한으로 재요청하면 사고 그대로 예약 단계에서 막힌다.
        with pytest.raises(Exception) as error:
            첫검수("재요청 입력 — 큰 상한")
        assert isinstance(error.value.cause, provider_budget.ProviderBudgetExceeded)
        assert error.value.request_budget and not error.value.call_limit
        assert len(messages.requests) == 보낸호출수, "막혀야 할 호출이 나갔습니다"
        assert budget.accounted_krw == pytest.approx(_사고시점_누적, abs=0.01)

        # ③ 첫 답에 맞춰 줄인 상한이면 같은 잔액에서 전송된다.
        assert 재요청("재요청 입력 — 줄인 상한") == '{"판정": []}'
        assert len(messages.requests) == 보낸호출수 + 1
        assert messages.requests[-1]["max_tokens"] == _하한

    입력상한 = _입력토큰 + provider_budget.REQUEST_ESTIMATE_MARGIN_TOKENS
    큰상한_예약 = usage_cost_krw(_모델, 입력상한, _상한)
    줄인상한_예약 = usage_cost_krw(_모델, 입력상한, _하한)
    # 사고 재현의 전제 — 큰 상한만 잔액을 넘고 줄인 상한은 들어간다.
    assert 큰상한_예약 > 남은예약액 >= 줄인상한_예약
    # 막힌 호출은 예약 기록조차 남기지 않는다 (전송 전에 닫힌다).
    assert [stage for _provider, stage, _reserved in reservations] == [
        "v2_review", "v2_review",
    ]
    assert reservations[-1][2] == pytest.approx(줄인상한_예약, abs=0.01)
    # 진단에 남은 «출력상한»이 이 동작의 관측 수단이다 (새 단계를 만들지 않는다).
    assert [단계["출력상한"] for 단계 in 검수_진단.steps
            if 단계["step"] == V2_RESPONSE_STEP] == [_상한, _하한]
    assert MAX_AI_CALLS_PER_REQUEST == 18


def test_callable_상한은_클로저를_만들_때가_아니라_보낼_때_풀린다() -> None:
    """첫 답이 아직 없을 때 만들어진 호출자도 보낼 때의 사용량을 본다."""
    messages = RecordingMessages("end_turn", input_tokens=100, output_tokens=10000)
    engine = real._MeteredEngine(SimpleNamespace(MODEL=_모델))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    # 사용량이 하나도 없는 시점에 만든다 — 이때 상한을 굳히면 24,000이 된다.
    재요청 = real._v2_ask_via_provider(
        engine, client, stage="v2_review",
        max_tokens=lambda: real._initial_review_retry_max_tokens(engine),
    )
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _stage, _reserved: 1,
        lambda _: None, lambda _: None, lambda _token, _observation: None,
    )
    with provider_budget.activate(_본조사_예약액), attempt_context.activate(callbacks):
        engine._usages.append(_사용량("v2_review", 10000))
        assert 재요청("보낼 때 풀린다") == '{"판정": []}'
    assert messages.requests[-1]["max_tokens"] == 15000
