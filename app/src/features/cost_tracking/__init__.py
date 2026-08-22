"""비용 추적 공개 편의 API. 실제 접근할 때만 store 의존성을 읽는다."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "AiCostEvent",
    "CustomerChargeDecision",
    "decide_customer_charge",
    "mark_automatic_release",
    "record_run_costs",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module("src.features.cost_tracking.store"), name)
    globals()[name] = value
    return value
