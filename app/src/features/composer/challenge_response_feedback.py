"""실제로 탈락한 현재 대응의 후보·지원쌍·자기 원문을 보충 작성에 돌려준다."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json

from src.features.composer import challenge_response_feedback_constants as c
from src.features.composer.challenge_event_constants import TIME_BINDING_PROBLEM
from src.features.composer.challenge_event_scope import response_current_activity_problem
from src.features.composer.evidence_pair_selection import build_evidence_pair_map
from src.features.composer.port import ComposedReport, SectionEvidencePacket, SectionEvidencePacketSet


def _fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResponseSupplementFailure:
    packet_sha256: str
    pair_id: str
    candidate: str
    candidate_sha256: str
    own_source_sha256: tuple[tuple[str, str], ...]


def _sources(packet: SectionEvidencePacket, failure: ResponseSupplementFailure) -> dict[str, str]:
    by_id = {fragment.fragment_id: fragment for fragment in packet.fragments}
    identifiers = [identifier for identifier, _ in failure.own_source_sha256]
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("현재 대응 안내의 자기 인용은 중복 없는 원문이어야 합니다")
    sources = {}
    for identifier, fingerprint in failure.own_source_sha256:
        fragment = by_id.get(identifier)
        if (fragment is None or not fragment.text.strip()
                or fingerprint != _fingerprint(fragment.text)):
            raise ValueError("현재 대응 안내의 자기 원문 지문이 다릅니다")
        sources[identifier] = fragment.text
    return sources


def collect_response_supplement_failures(
    draft: ComposedReport, diagnostics: Sequence[Mapping], packets: SectionEvidencePacketSet,
) -> dict[str, tuple[ResponseSupplementFailure, ...]]:
    """시점 실패의 번호 대신 실제 후보 지문과 자기 지원쌍을 다시 대조한다."""
    rejected = {item.get("candidate_sha256") for item in diagnostics
                if item.get("section_id") == c.RESPONSE_FEEDBACK_SECTION
                and item.get("kind") == c.RESPONSE_FEEDBACK_KIND
                and item.get("reason_code") == TIME_BINDING_PROBLEM}
    section = next((item for item in draft.sections
                    if item.section_id == c.RESPONSE_FEEDBACK_SECTION), None)
    packet = next((item for item in packets.packets
                   if item.section_id == c.RESPONSE_FEEDBACK_SECTION), None)
    if section is None or packet is None or not rejected:
        return {}
    by_id = {fragment.fragment_id: fragment for fragment in packet.fragments}
    pairs = build_evidence_pair_map(packet.section_id, packet.fragments)
    failures = set()
    for sentence in section.sentences:
        if (sentence.planned_claim_slot != c.RESPONSE_FEEDBACK_SLOT
                or not sentence.text.strip()
                or len(sentence.text) > c.RESPONSE_FEEDBACK_MAX_CANDIDATE_CHARS
                or _fingerprint(sentence.text) not in rejected):
            continue
        identifiers = tuple(sorted(set(sentence.citations)))
        if not identifiers or any(identifier not in by_id for identifier in identifiers):
            continue
        sources = {identifier: by_id[identifier].text for identifier in identifiers}
        if (any(not text.strip() for text in sources.values())
                or sum(map(len, sources.values())) > c.RESPONSE_FEEDBACK_MAX_SOURCE_CHARS
                or response_current_activity_problem(sentence.text, sources) != TIME_BINDING_PROBLEM):
            continue
        for pair_id, (slot, identifier) in pairs.items():
            if (slot != c.RESPONSE_FEEDBACK_SLOT or identifier not in sources
                    or response_current_activity_problem(
                        sentence.text, {identifier: sources[identifier]}) != TIME_BINDING_PROBLEM):
                continue
            failures.add(ResponseSupplementFailure(
                packet.packet_sha256, pair_id, sentence.text, _fingerprint(sentence.text),
                tuple((key, _fingerprint(value)) for key, value in sources.items()),
            ))
    if not failures:
        return {}
    return {packet.section_id: tuple(sorted(failures, key=lambda item: (item.pair_id, item.candidate_sha256)))}


def render_response_supplement_feedback(
    packet: SectionEvidencePacket, failures: tuple[ResponseSupplementFailure, ...],
    missing_slots: tuple[str, ...] = (),
) -> str:
    """동일 요청의 누락 대응 칸에만 자료 JSON을 붙이고 원문·후보는 수정하지 않는다."""
    if type(failures) is not tuple:
        raise ValueError("현재 대응 보충 안내는 봉인된 실패 tuple이어야 합니다")
    if not failures or c.RESPONSE_FEEDBACK_SLOT not in missing_slots:
        return ""
    if packet.section_id != c.RESPONSE_FEEDBACK_SECTION:
        raise ValueError("현재 대응 보충 안내를 다른 장에 사용할 수 없습니다")
    pairs = build_evidence_pair_map(packet.section_id, packet.fragments)
    rows = []
    available = c.RESPONSE_FEEDBACK_MAX_CHARS - len(c.RESPONSE_FEEDBACK_GUIDE)
    for failure in failures:
        if (type(failure) is not ResponseSupplementFailure
                or failure.packet_sha256 != packet.packet_sha256
                or failure.pair_id not in pairs
                or pairs[failure.pair_id][0] != c.RESPONSE_FEEDBACK_SLOT
                or not failure.candidate.strip()
                or len(failure.candidate) > c.RESPONSE_FEEDBACK_MAX_CANDIDATE_CHARS
                or _fingerprint(failure.candidate) != failure.candidate_sha256):
            raise ValueError("현재 대응 안내의 후보 또는 지원쌍 결속이 다릅니다")
        sources = _sources(packet, failure)
        identifier = pairs[failure.pair_id][1]
        if (identifier not in sources
                or sum(map(len, sources.values())) > c.RESPONSE_FEEDBACK_MAX_SOURCE_CHARS
                or response_current_activity_problem(failure.candidate, sources) != TIME_BINDING_PROBLEM
                or response_current_activity_problem(
                    failure.candidate, {identifier: sources[identifier]}) != TIME_BINDING_PROBLEM):
            raise ValueError("현재 대응 안내에 실제 자기 원문의 시점 실패가 없습니다")
        row = json.dumps({
            "실패코드": TIME_BINDING_PROBLEM, "지원쌍": failure.pair_id,
            "주장범주": c.RESPONSE_FEEDBACK_SLOT, "실패후보": failure.candidate,
            "자기원문": sources,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        # 과한 원문은 자르거나 다른 원문으로 대체하지 않고 해당 안내만 생략한다.
        if len(rows) < c.RESPONSE_FEEDBACK_MAX_ROWS and len(row) <= available:
            rows.append(row)
            available -= len(row)
    return c.RESPONSE_FEEDBACK_GUIDE + "".join(rows) if rows else ""
