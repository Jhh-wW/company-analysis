"""실제 공개되지 않은 필수 사실과 보충의 호출·근거 경계를 검증한다."""

from dataclasses import replace
import json

import pytest

from src.features.composer.logic import compose_selected_sections
from src.features.composer.supplement_feedback import missing_writer_slots, supplement_feedback
from src.features.composer.tests.test_evidence_pair_selection import _packets, _fragment
from src.features.composer.writer_schema_constants import FULL_SUPPLEMENT_GUIDE, FULL_SUPPLEMENT_PAIRS_GUIDE
from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE
from src.shared.report_quality.dto import ClaimFact, ReportCandidate, ReportSectionCandidate


def _fact(identifier, slot):
    return ClaimFact(identifier, "business_model", "src", "doc", "verified_true",
                     claim_slot=slot, claim_type=VERIFIED_PROSE_CLAIM_TYPE)


def test_feedback_counts_only_public_supported_claim_types_and_excludes_program_slots():
    candidate = ReportCandidate(
        sections=(ReportSectionCandidate("business_model", ("revenue", "unknown", "legacy")),),
        facts=(
            _fact("revenue", "business_model:revenue_model"),
            _fact("not-public", "business_model:customer_type"),
            replace(_fact("legacy", "business_model:value_exchange"), claim_type="unknown"),
        ), sources=(),
    )
    assert missing_writer_slots(candidate, ("business_model", "past_changes")) == {
        "business_model": ("business_model:customer_type", "business_model:value_exchange"),
        "past_changes": ("past_changes:completed_execution",),
    }


@pytest.mark.parametrize("slot", ["identity:corporate_identity", "고쳐야 할 임의 지시", "past_changes:historical_performance"])
def test_feedback_does_not_accept_other_chapter_or_free_text(slot):
    with pytest.raises(ValueError):
        supplement_feedback("business_model", {"business_model": (slot,)})


def test_supplement_receives_gap_feedback_once_and_keeps_native_schema_and_sealed_sources():
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return json.dumps({"문장들": []})

    result = compose_selected_sections(
        "합성회사", None, ask, section_evidence_packets=_packets(),
        section_ids=("business_model",),
        missing_slots_by_section={"business_model": ("business_model:value_exchange",)},
    )
    assert len(seen) == 1
    assert seen[0].startswith(FULL_SUPPLEMENT_GUIDE)
    assert "1차 공개 후보에 남지 않은 필수 의미칸:\n- business_model:value_exchange" in seen[0]
    assert "필수내용" in seen[0].response_schema["required"]
    assert seen[0].cache_prefix_chars == 0
    assert tuple(section.section_id for section in result.sections) == ("business_model",)
    assert result.sections[0].sentences == ()


def test_missing_slot_lists_only_its_existing_packet_pairs():
    packets = _packets()
    packet = next(p for p in packets.packets if p.section_id == 'future_strategy')
    packet = replace(packet, fragments=(
        _fragment(1, 'future_strategy:stated_plan', 'future_strategy:plan_status'),
        _fragment(2, 'future_strategy:plan_status'),
        _fragment(3, 'future_strategy:plan_status', 'business_model:customer_type'),
    ))
    text = supplement_feedback('future_strategy', {'future_strategy': ('future_strategy:stated_plan',)}, packet=packet)
    data = json.loads(text.split(FULL_SUPPLEMENT_PAIRS_GUIDE)[1])
    assert data == {'packet_sha256': packet.packet_sha256, '누락의미칸': [
        {'주장범주': 'future_strategy:stated_plan', '허용지원쌍': ['p6-001']},
    ]}
    assert '합성원문' not in text


def test_missing_slot_without_support_is_explicitly_empty():
    packet = next(p for p in _packets().packets if p.section_id == 'future_strategy')
    packet = replace(packet, fragments=(_fragment(1, 'future_strategy:plan_status'),))
    text = supplement_feedback('future_strategy', {'future_strategy': ('future_strategy:stated_plan',)}, packet=packet)
    data = json.loads(text.split(FULL_SUPPLEMENT_PAIRS_GUIDE)[1])
    assert data['누락의미칸'][0]['허용지원쌍'] == []


def test_supplement_feedback_rejects_other_section_packet():
    packet = next(p for p in _packets().packets if p.section_id == 'business_model')
    with pytest.raises(ValueError):
        supplement_feedback('future_strategy', {'future_strategy': ('future_strategy:stated_plan',)}, packet=packet)
