"""Central fail-safe model pricing lookup."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
from typing import Final

from src.core.constants import (
    MODEL_PRICES_USD_PER_MTOK,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
)

#: AI 사용량을 원화로 기록할 때 쓰는 비용 환율 정본.
#: provider admission·실제 장부·OCR·파일럿이 모두 아래 ``usage_cost_krw``를
#: 호출하므로, 환율을 바꾸면 네 경로가 한 번에 이동한다.
AI_COST_KRW_PER_USD: Final[float] = 1400.0

_TOKENS_PER_MTOK = Decimal(1_000_000)
_WON_CENT = Decimal("0.01")
_CACHE_WRITE_MULTIPLIER: Final[dict[str, Decimal]] = {
    "5m": Decimal("1.25"),
    "1h": Decimal("2"),
}
_CACHE_READ_MULTIPLIER = Decimal("0.1")
_BATCH_MULTIPLIER = Decimal("0.5")


def model_price(model: object) -> tuple[float, float]:
    """Return a price only for an exact official model ID or alias.

    Dated IDs are not constructed from aliases: 4.6+ dateless IDs are already
    pinned snapshots, while earlier dated snapshots must be listed explicitly.
    Everything else deliberately keeps the conservative unknown-model price.
    """
    if not isinstance(model, str):
        return UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    return MODEL_PRICES_USD_PER_MTOK.get(
        model, UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    )


def usage_cost_krw(
    model: object, tokens_in: int | float, tokens_out: int | float
) -> float:
    """Provider usage를 공통 단가·환율로 원화 소수 둘째 자리까지 올림한다.

    호출 전 예상비용과 호출 뒤 실제비용이 같은 함수를 써야 admission과 장부가
    서로 다른 돈을 말하지 않는다. 올림은 아주 작은 유료 호출을 0원으로 기록하지
    않게 하는 보수적 운영 규칙이다.
    """
    return detailed_usage_cost_krw(
        model,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )


def detailed_usage_cost_krw(
    model: object,
    *,
    input_tokens: int | float,
    output_tokens: int | float,
    cache_creation_tokens: int | float = 0,
    cache_read_tokens: int | float = 0,
    batch: bool = False,
    cache_ttl: str = "5m",
) -> float:
    """Calculate normal, prompt-cache, and Batch usage from one price source.

    Unknown model IDs keep the existing conservative fallback price.  Cache
    creation/read and Batch modifiers are applied explicitly so stage logs do
    not need to duplicate exchange rates or model prices.
    """

    values = tuple(
        Decimal(str(value))
        for value in (
            input_tokens,
            output_tokens,
            cache_creation_tokens,
            cache_read_tokens,
        )
    )
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("provider token 수는 0 이상의 유한한 수여야 합니다")
    if type(batch) is not bool:
        raise ValueError("batch 적용 여부는 bool이어야 합니다")
    if cache_ttl not in _CACHE_WRITE_MULTIPLIER:
        raise ValueError("cache TTL은 5m 또는 1h여야 합니다")

    normal_in, output, cache_create, cache_read = values
    price_in, price_out = model_price(model)
    input_price = Decimal(str(price_in))
    value = (
        normal_in * input_price
        + cache_create * input_price * _CACHE_WRITE_MULTIPLIER[cache_ttl]
        + cache_read * input_price * _CACHE_READ_MULTIPLIER
        + output * Decimal(str(price_out))
    )
    if batch:
        value *= _BATCH_MULTIPLIER
    value = value * Decimal(str(AI_COST_KRW_PER_USD)) / _TOKENS_PER_MTOK
    return float(value.quantize(_WON_CENT, rounding=ROUND_CEILING))
