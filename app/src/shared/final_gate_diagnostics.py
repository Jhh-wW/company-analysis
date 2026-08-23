"""원문·내부 검증 문구 없이 파이프라인의 최종 게이트를 분류한다."""

from __future__ import annotations

from typing import Final


FINAL_GATE_REASON_COMPARISON_BLOCKED: Final[str] = "comparison_blocked"
FINAL_GATE_REASON_PUBLISH_BLOCKED: Final[str] = "publish_blocked"
FINAL_GATE_REASON_MISSING_IDENTITY: Final[str] = "publish_missing_identity"
FINAL_GATE_REASON_MISSING_REVENUE: Final[str] = "publish_missing_revenue"
FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE: Final[str] = (
    "publish_missing_identity_revenue"
)
FINAL_GATE_REASON_OTHER_GATE: Final[str] = "other_gate"
FINAL_GATE_DIAGNOSTIC_TABLE: Final[str] = "pipeline_final_gate_diagnostics"
FINAL_GATE_DIAGNOSTIC_SCHEMA_VERSION: Final[int] = 1
FINAL_GATE_DIAGNOSTIC_COLUMNS: Final[frozenset[str]] = frozenset(
    {"run_id", "schema_version", "reason_code", "recorded_at"}
)
SAFE_FINAL_GATE_REASONS: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_REASON_COMPARISON_BLOCKED,
        FINAL_GATE_REASON_MISSING_IDENTITY,
        FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
        FINAL_GATE_REASON_MISSING_REVENUE,
        FINAL_GATE_REASON_PUBLISH_BLOCKED,
        FINAL_GATE_REASON_OTHER_GATE,
    }
)
