"""Central fail-safe model pricing lookup."""

from __future__ import annotations

import datetime as dt
import re

from src.core.constants import (
    MODEL_PRICES_USD_PER_MTOK,
    UNKNOWN_MODEL_PRICE_USD_PER_MTOK,
)

_SNAPSHOT_DATE_RE = re.compile(r"[0-9]{8}")


def model_price(model: object) -> tuple[float, float]:
    """Return a known alias price, including only its strict dated snapshots.

    A provider snapshot is accepted only as ``<known-alias>-YYYYMMDD`` with a
    real calendar date. Everything else deliberately keeps the conservative
    unknown-model price.
    """
    if not isinstance(model, str):
        return UNKNOWN_MODEL_PRICE_USD_PER_MTOK

    exact = MODEL_PRICES_USD_PER_MTOK.get(model)
    if exact is not None:
        return exact

    alias, separator, snapshot_date = model.rpartition("-")
    if (
        separator != "-"
        or alias not in MODEL_PRICES_USD_PER_MTOK
        or _SNAPSHOT_DATE_RE.fullmatch(snapshot_date) is None
    ):
        return UNKNOWN_MODEL_PRICE_USD_PER_MTOK

    try:
        dt.date(
            int(snapshot_date[:4]),
            int(snapshot_date[4:6]),
            int(snapshot_date[6:]),
        )
    except ValueError:
        return UNKNOWN_MODEL_PRICE_USD_PER_MTOK
    return MODEL_PRICES_USD_PER_MTOK[alias]
