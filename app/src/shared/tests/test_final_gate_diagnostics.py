from __future__ import annotations

from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_OTHER_GATE,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    SAFE_FINAL_GATE_REASONS,
)


def test_최종게이트_사유는_원문없는_세코드로_닫혀있다() -> None:
    assert SAFE_FINAL_GATE_REASONS == {
        FINAL_GATE_REASON_COMPARISON_BLOCKED,
        FINAL_GATE_REASON_PUBLISH_BLOCKED,
        FINAL_GATE_REASON_OTHER_GATE,
    }
    assert SAFE_FINAL_GATE_REASONS == {
        "comparison_blocked",
        "publish_blocked",
        "other_gate",
    }
