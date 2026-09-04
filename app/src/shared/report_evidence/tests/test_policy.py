from __future__ import annotations

import pytest

from src.shared.report_evidence.policy import (
    EVIDENCE_SLOT_POLICY_VERSION,
    REQUIRED_EVIDENCE_SECTION_IDS,
    REQUIRED_EVIDENCE_SLOTS_BY_SECTION,
    collector_slots_for,
    injected_slots_for,
    required_slots_for,
)


def test_근거정책은_아홉장을_정확히_한번씩_다룬다() -> None:
    assert EVIDENCE_SLOT_POLICY_VERSION == "section-evidence-slots-v1"
    assert tuple(REQUIRED_EVIDENCE_SLOTS_BY_SECTION) == REQUIRED_EVIDENCE_SECTION_IDS
    assert len(REQUIRED_EVIDENCE_SECTION_IDS) == 9
    assert len(set(REQUIRED_EVIDENCE_SECTION_IDS)) == 9


@pytest.mark.parametrize("section_id", REQUIRED_EVIDENCE_SECTION_IDS)
def test_수집칸과_주입칸은_겹치지_않고_전체필수칸을_정확히_나눈다(
    section_id: str,
) -> None:
    required = required_slots_for(section_id)
    collected = collector_slots_for(section_id)
    injected = injected_slots_for(section_id)

    assert required
    assert collected
    assert set(collected).isdisjoint(injected)
    assert set(collected) | set(injected) == set(required)
    assert len(required) == len(set(required))
    assert all(slot_id.startswith(f"{section_id}:") for slot_id in required)


def test_실적과_동일조건비교는_수집기_자기판정으로_채우지_않는다() -> None:
    assert injected_slots_for("past_changes") == (
        "past_changes:historical_performance",
    )
    assert set(injected_slots_for("competitive_position")) == {
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    }
    assert collector_slots_for("competitive_position") == (
        "competitive_position:self_context",
    )


def test_알수없는_장_이름을_빈정책으로_조용히_바꾸지_않는다() -> None:
    with pytest.raises(ValueError, match="알 수 없는"):
        required_slots_for("unknown-section")
