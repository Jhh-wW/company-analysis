from __future__ import annotations

from src.features.composer.constants import CLAIM_SLOTS_BY_SECTION as COMPOSER_SLOTS
from src.shared.report_claim_policy import (
    CLAIM_SECTION_IDS,
    CLAIM_SLOTS_BY_SECTION,
    claim_slots_for,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    REQUIRED_EVIDENCE_SLOTS_BY_SECTION,
)


def test_작가기와_평가기의_claim_범주는_한_정본을_쓴다() -> None:
    assert COMPOSER_SLOTS is CLAIM_SLOTS_BY_SECTION
    assert tuple(CLAIM_SLOTS_BY_SECTION) == CLAIM_SECTION_IDS


def test_근거_최소칸은_주장_범주의_부분집합이다() -> None:
    assert REQUIRED_EVIDENCE_SECTION_IDS == CLAIM_SECTION_IDS
    for section_id in CLAIM_SECTION_IDS:
        assert set(REQUIRED_EVIDENCE_SLOTS_BY_SECTION[section_id]) <= set(
            claim_slots_for(section_id)
        )


def test_claim_범주는_모두_자기_장_접두어를_가진다() -> None:
    for section_id, slots in CLAIM_SLOTS_BY_SECTION.items():
        assert len(slots) == len(set(slots))
        assert all(slot.startswith(f"{section_id}:") for slot in slots)
