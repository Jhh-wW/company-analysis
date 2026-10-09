"""1차 공개 후보의 빠진 질문을 보충 작성기에 전달한다. 출고 판정은 바꾸지 않는다."""

from collections.abc import Mapping
import json

from src.features.composer.writer_schema_constants import (
    FULL_SUPPLEMENT_GUIDE, FULL_SUPPLEMENT_MISSING_HEAD, FULL_SUPPLEMENT_PAIRS_GUIDE,
)
from src.features.composer.evidence_pair_selection import build_evidence_pair_map
from src.features.composer.port import SectionEvidencePacket
from src.shared.report_evidence.policy import injected_slots_for, required_slots_for
from src.shared.report_quality.constants import STRICT_PUBLIC_CLAIM_TYPES
from src.shared.report_quality.dto import ReportCandidate


def missing_writer_slots(candidate: ReportCandidate, section_ids: tuple[str, ...]):
    """실제 공개 결속과 v3의 사실 종류로 누락을 관측한다. 원문·기존 문장은 넘기지 않는다."""
    facts = {fact.fact_id: fact for fact in candidate.facts}
    sections = {section.section_id: section for section in candidate.sections}
    result = {}
    for section_id in section_ids:
        section = sections.get(section_id)
        visible_ids = section.fact_ids if section is not None else ()
        covered = {
            facts[fact_id].claim_slot.strip() for fact_id in visible_ids
            if fact_id in facts and facts[fact_id].claim_type.strip() in STRICT_PUBLIC_CLAIM_TYPES
        }
        injected = set(injected_slots_for(section_id))
        result[section_id] = tuple(slot for slot in required_slots_for(section_id)
                                   if slot not in covered and slot not in injected)
    return result


def supplement_feedback(
    section_id: str, missing_by_section: Mapping[str, tuple[str, ...]] | None,
    *, packet: SectionEvidencePacket | None = None,
) -> str:
    """외부 문자열을 지시문에 넣지 않고 이 장의 정책상 질문만 안내한다."""
    supplied = () if missing_by_section is None else missing_by_section.get(section_id, ())
    allowed = set(required_slots_for(section_id)) - set(injected_slots_for(section_id))
    if type(supplied) is not tuple or any(type(slot) is not str or slot not in allowed for slot in supplied):
        raise ValueError("보충 안내에는 해당 장의 필수 의미칸만 넣을 수 있습니다")
    missing = tuple(slot for slot in required_slots_for(section_id) if slot in supplied)
    feedback = FULL_SUPPLEMENT_GUIDE + (
        FULL_SUPPLEMENT_MISSING_HEAD + "".join(f"- {slot}\n" for slot in missing) if missing else ""
    )
    if packet is None:
        return feedback
    if type(packet) is not SectionEvidencePacket or packet.section_id != section_id:
        raise ValueError('보충 허용쌍은 해당 장의 봉인된 packet이어야 합니다')
    pairs = build_evidence_pair_map(section_id, packet.fragments)
    rows = [
        {'주장범주': slot, '허용지원쌍': [identifier for identifier, (pair_slot, _) in pairs.items()
                                  if pair_slot == slot]}
        for slot in missing
    ]
    if not rows:
        return feedback
    return feedback + FULL_SUPPLEMENT_PAIRS_GUIDE + json.dumps(
        {'packet_sha256': packet.packet_sha256, '누락의미칸': rows},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ) + '\n'
