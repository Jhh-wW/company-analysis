from __future__ import annotations

import pytest

from src.core.constants import (
    GENERATION_MODEL,
    MODEL_PRICES_USD_PER_MTOK,
    SAMPLING_OK_MODELS,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
)
from src.core.pricing import AI_COST_KRW_PER_USD, model_price, usage_cost_krw


@pytest.mark.parametrize("model_id", MODEL_PRICES_USD_PER_MTOK)
def test_only_explicit_model_ids_and_aliases_use_known_prices(model_id: str):
    assert model_price(model_id) == MODEL_PRICES_USD_PER_MTOK[model_id]


def test_pre_46_haiku_alias_and_official_dated_snapshot_share_price():
    assert model_price("claude-haiku-4-5") == model_price(
        "claude-haiku-4-5-20251001"
    )


def test_fable_5_official_dateless_snapshot_price_and_won_conversion():
    model_id = "claude-fable-5"

    assert MODEL_PRICES_USD_PER_MTOK[model_id] == (10.0, 50.0)
    assert model_price(model_id) == (10.0, 50.0)
    assert usage_cost_krw(model_id, 1_000_000, 1_000_000) == (
        60 * AI_COST_KRW_PER_USD
    )
    assert GENERATION_MODEL != model_id
    assert model_id not in SAMPLING_OK_MODELS


def test_fable_5_does_not_invent_a_dated_snapshot_id():
    assert model_price("claude-fable-5-20260609") == (
        UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    )


@pytest.mark.parametrize(
    "model",
    [
        "claude-haiku-4-5-2025100",
        "claude-haiku-4-5-202510011",
        "claude-haiku-4-5-20251001-extra",
        "claude-sonnet-4-6-20251001",
        "claude-opus-5-20260609",
        "claude-haiku-4-50-20251001",
        "claude-haiku-4-5 -20251001",
        "claude-haiku-4-5-20251301",
        "claude-haiku-4-5-20250229",
        "claude-haiku-4-5-２０２５１００１",
        "claude-haiku-4-5-20251001 ",
        "CLAUDE-HAIKU-4-5-20251001",
        None,
    ],
)
def test_near_prefix_and_malformed_snapshots_keep_unknown_price(model: object):
    assert model_price(model) == UNKNOWN_MODEL_PRICE_USD_PER_MTOK
