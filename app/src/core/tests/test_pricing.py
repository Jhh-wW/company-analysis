from __future__ import annotations

import pytest

from src.core.constants import (
    MODEL_PRICES_USD_PER_MTOK,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
)
from src.core.pricing import model_price


@pytest.mark.parametrize("alias", MODEL_PRICES_USD_PER_MTOK)
def test_exact_alias_and_strict_dated_snapshot_share_the_known_price(alias: str):
    assert model_price(alias) == MODEL_PRICES_USD_PER_MTOK[alias]
    assert model_price(f"{alias}-20251001") == MODEL_PRICES_USD_PER_MTOK[alias]


@pytest.mark.parametrize(
    "model",
    [
        "claude-haiku-4-5-2025100",
        "claude-haiku-4-5-202510011",
        "claude-haiku-4-5-20251001-extra",
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
