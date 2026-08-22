"""원문·내부 검증 문구 없이 파이프라인의 최종 게이트를 분류한다."""

from __future__ import annotations

from typing import Final


FINAL_GATE_REASON_COMPARISON_BLOCKED: Final[str] = "comparison_blocked"
FINAL_GATE_REASON_PUBLISH_BLOCKED: Final[str] = "publish_blocked"
FINAL_GATE_REASON_OTHER_GATE: Final[str] = "other_gate"
SAFE_FINAL_GATE_REASONS: Final[frozenset[str]] = frozenset(
    {
        FINAL_GATE_REASON_COMPARISON_BLOCKED,
        FINAL_GATE_REASON_PUBLISH_BLOCKED,
        FINAL_GATE_REASON_OTHER_GATE,
    }
)
