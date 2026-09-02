"""요청별 provider 호출의 보수적 예상비용 admission.

SQLite가 단계 전체 예상액을 먼저 예약하고, 이 모듈은 그 예약 안에서 각 provider
호출의 방어적 예상비용을 호출 *전에* 잡는다. 성공 응답의 실제 usage가 확인되면 차액을
다음 호출에 돌려주고, 예외·usage 누락은 예상비용을 계속 점유한다.

provider의 실제 token 집계는 사전 추정과 다를 수 있으므로 이 값은 청구액의 수학적
hard ceiling이 아니다. 동시 요청이 모두 과거 지출만 보고 들어가는 경쟁을 막는 운영
중단 기준이며, 실제 usage는 추정 초과 여부와 관계없이 원장에 전액 기록한다.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterator

from src.core.pricing import usage_cost_krw


# JSON 필드명·role·content block·structured-output schema처럼 사람이 넘긴 본문
# 밖에서 provider tokenizer에 들어가는 방어적 고정 여유. provider의 실제 token
# 집계와 완전히 같다는 계약은 아니므로 hard-cap 증명에 사용하지 않는다.
REQUEST_ESTIMATE_MARGIN_TOKENS = 4096

# Anthropic vision 계약: resize 뒤 ceil(width/28)*ceil(height/28), 표준 모델은
# 이미지당 최대 1,568 visual tokens. 경계·content block 여유를 32 tokens 더 잡는다.
IMAGE_GRID_PIXELS = 28
IMAGE_MAX_BILLED_TOKENS = 1568
IMAGE_ENVELOPE_TOKENS = 32


class ProviderBudgetUnavailable(RuntimeError):
    """유료 provider 호출에 요청별 예약 문맥이 없음."""


class ProviderBudgetExceeded(RuntimeError):
    """다음 호출의 방어적 예상비용이 단계 예약 잔액을 넘음."""


class RequestCallLimitReached(ProviderBudgetExceeded):
    """돈이 아니라 «한 요청에 허락된 AI 호출 «횟수»»를 다 썼다.

    ★ 왜 따로 두나 (실측) — 이 둘은 뜻이 다르다.
      · 예산 초과 : 더 부르면 «돈»이 넘는다 → 요청 전체를 멈추는 게 맞다.
      · 횟수 상한 : 이미 만들어 둔 장·문장이 손에 있는데 «선택적 다듬기»를
        한 번 더 못 부를 뿐이다. 그때까지 만든 보고서를 버릴 이유가 없다.
      한 타입으로 뭉쳐 두었더니, 다듬기 한 번을 못 불렀다고 완성된 9개 장이
      통째로 버려졌다(현대카드·우리은행 실측).
    ★ 부모를 그대로 상속하므로 기존에 ProviderBudgetExceeded 를 잡던 곳은
      «하나도» 동작이 바뀌지 않는다. 구분이 필요한 곳만 이 타입을 본다.
    """


class ProviderCostInvariantError(RuntimeError):
    """provider 비용 예약 생명주기 자체가 깨졌음."""


def estimate_request_tokens(payload: Any) -> int:
    """텍스트 provider payload의 입력 token 수를 방어적으로 추정한다.

    JSON을 compact UTF-8로 직렬화해 schema와 role 필드를 빠뜨리지 않고 고정
    envelope를 더한다. provider 집계와의 차이는 남으므로 운영 guard일 뿐 청구액
    hard ceiling은 아니다.
    """
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 예상 요청 크기를 못 구하면 호출하지 않는다
        raise ProviderBudgetUnavailable(
            "provider 요청 크기를 추정할 수 없습니다"
        ) from exc
    return len(encoded) + REQUEST_ESTIMATE_MARGIN_TOKENS


def estimate_image_tokens(dimensions: list[tuple[int, int]]) -> int:
    """검증된 이미지 크기 목록을 공식 vision grid 식으로 추정한다.

    이미지당 문서상 visual-token 최대값은 적용하지만 provider의 최종 input usage와
    동일하다는 청구 계약은 아니므로 결과는 운영 admission에만 사용한다.
    """
    total = REQUEST_ESTIMATE_MARGIN_TOKENS
    for width, height in dimensions:
        if width <= 0 or height <= 0:
            raise ProviderBudgetUnavailable("이미지 token 수를 추정할 수 없습니다")
        estimated = math.ceil(width / IMAGE_GRID_PIXELS) * math.ceil(
            height / IMAGE_GRID_PIXELS
        )
        total += min(estimated, IMAGE_MAX_BILLED_TOKENS) + IMAGE_ENVELOPE_TOKENS
    return total


@dataclass(frozen=True)
class CallReservation:
    call_id: int
    estimated_krw: float


class ProviderBudget:
    """한 paid phase의 provider 호출들이 공유하는 요청 로컬 예약."""

    def __init__(self, total_krw: float):
        total = float(total_krw)
        if not math.isfinite(total) or total <= 0:
            raise ValueError("provider 단계 예약액은 0보다 큰 유한한 수여야 합니다")
        self.total_krw = total
        self._known_actual_krw = 0.0
        self._held_estimated_krw = 0.0
        self._estimate_overrun_krw = 0.0
        self._next_id = 1
        self._pending: dict[int, float] = {}
        self._lock = threading.Lock()

    @property
    def accounted_krw(self) -> float:
        with self._lock:
            return self._known_actual_krw + self._held_estimated_krw

    @property
    def estimate_overrun_krw(self) -> float:
        with self._lock:
            return self._estimate_overrun_krw

    def reserve_call(
        self, *, model: str, input_tokens_upper: int, max_tokens: int
    ) -> CallReservation:
        """호출 전 방어적 예상비용을 원자적으로 잡는다."""
        clean_in = int(input_tokens_upper)
        clean_out = int(max_tokens)
        if clean_in < 0 or clean_out <= 0:
            raise ProviderBudgetUnavailable("provider token 상한이 올바르지 않습니다")
        estimate = usage_cost_krw(model, clean_in, clean_out)
        with self._lock:
            if (
                self._known_actual_krw
                + self._held_estimated_krw
                + estimate
                > self.total_krw
            ):
                raise ProviderBudgetExceeded(
                    "다음 provider 호출의 예상비용이 단계 예약 잔액을 넘습니다"
                )
            call_id = self._next_id
            self._next_id += 1
            self._pending[call_id] = estimate
            self._held_estimated_krw += estimate
        return CallReservation(call_id=call_id, estimated_krw=estimate)

    def settle_call(self, reservation: CallReservation, *, actual_krw: float) -> None:
        """확정 usage를 전액 반영하고 예상비용과의 차액을 반환한다."""
        actual = float(actual_krw)
        if not math.isfinite(actual) or actual < 0:
            raise ProviderCostInvariantError("provider 실제 비용이 유효하지 않습니다")
        with self._lock:
            estimate = self._pending.pop(reservation.call_id, None)
            if estimate is None:
                raise ProviderCostInvariantError("provider 호출 예약이 없거나 이미 마감됐습니다")
            self._held_estimated_krw -= estimate
            self._known_actual_krw += actual
            if actual > estimate:
                # 사전값은 운영 guard이지 청구 hard ceiling이 아니다. 이미 발생한
                # 비용은 숨기지 않고 전액 반영하며 차이를 관측값으로 남긴다.
                self._estimate_overrun_krw += actual - estimate

    def cancel_before_dispatch(self, reservation: CallReservation) -> None:
        """provider에 보내지 않았음이 확실할 때만 호출 예약을 전액 반환한다."""
        with self._lock:
            estimate = self._pending.pop(reservation.call_id, None)
            if estimate is None:
                raise ProviderCostInvariantError(
                    "취소할 provider 호출 예약이 없거나 이미 마감됐습니다"
                )
            self._held_estimated_krw -= estimate

    def mark_unknown(self, reservation: CallReservation) -> None:
        """예외·usage 누락은 호출 전 예상비용을 반환하지 않는다."""
        with self._lock:
            if reservation.call_id not in self._pending:
                raise ProviderCostInvariantError("미확정 처리할 provider 호출 예약이 없습니다")


_CURRENT: contextvars.ContextVar[ProviderBudget | None] = contextvars.ContextVar(
    "paid_provider_budget", default=None
)


@contextlib.contextmanager
def activate(total_krw: float) -> Iterator[ProviderBudget]:
    """현재 실행 문맥에 paid phase 예산을 설치한다."""
    budget = ProviderBudget(total_krw)
    token = _CURRENT.set(budget)
    try:
        yield budget
    finally:
        _CURRENT.reset(token)


def current() -> ProviderBudget:
    budget = _CURRENT.get()
    if budget is None:
        raise ProviderBudgetUnavailable(
            "유료 provider 호출 전에 원자 예약 문맥이 설치되지 않았습니다"
        )
    return budget
