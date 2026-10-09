"""실제로 사업부 범위를 빠뜨린 자기 지원쌍만 보충 작성기에 돌려준다."""
from dataclasses import dataclass
import hashlib
import json
from collections.abc import Mapping, Sequence

from src.features.composer.business_population_scope import section_investment_plan_problem
from src.features.composer.evidence_pair_selection import build_evidence_pair_map
from src.features.composer.port import ComposedReport, SectionEvidencePacket, SectionEvidencePacketSet
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.plan_status_constants import SCOPE_FEEDBACK_BODY_KIND
from src.shared.report_evidence.section_context import parse_section_context, section_context_fingerprint


@dataclass(frozen=True)
class ScopeSupplementFailure:
    packet_sha256: str
    pair_id: str
    fragment_sha256: str
    section_context_sha256: str


def _context(fragment):
    return parse_section_context(
        fragment.section_context_json,
        document_id=fragment.source_document_id,
        document_sha256=fragment.document_content_sha256,
        fragment_location=fragment.location,
        fragment_sha256=hashlib.sha256(fragment.text.encode('utf-8')).hexdigest(),
    )


def collect_scope_supplement_failures(
    draft: ComposedReport, diagnostics: Sequence[Mapping], packets: SectionEvidencePacketSet,
) -> dict[str, tuple[ScopeSupplementFailure, ...]]:
    """일반 범위 실패를 전용 사업부 실패로 오해하지 않도록 원 선택쌍을 다시 검사한다."""
    rejected = {(item.get('section_id'), item.get('candidate_sha256'))
                for item in diagnostics if item.get('kind') == SCOPE_FEEDBACK_BODY_KIND
                and item.get('reason_code') == SCOPE_CONDITION_UNBOUND}
    result = {}
    for packet in packets.packets:
        section = next((item for item in draft.sections if item.section_id == packet.section_id), None)
        if section is None:
            continue
        by_id = {item.fragment_id: item for item in packet.fragments}
        pairs = build_evidence_pair_map(packet.section_id, packet.fragments)
        failures = set()
        for sentence in section.sentences:
            fingerprint = hashlib.sha256(sentence.text.encode('utf-8')).hexdigest()
            if (packet.section_id, fingerprint) not in rejected:
                continue
            own = {identifier: by_id[identifier] for identifier in sentence.citations if identifier in by_id}
            if len(own) != len(set(sentence.citations)):
                continue
            sources = {identifier: fragment.text for identifier, fragment in own.items()}
            contexts = {identifier: fragment.section_context_json for identifier, fragment in own.items()}
            if not section_investment_plan_problem(sentence.text, sources, contexts):
                continue
            for pair_id, (slot, identifier) in pairs.items():
                if slot != sentence.planned_claim_slot or identifier not in own:
                    continue
                fragment = own[identifier]
                if not fragment.section_context_json:
                    continue
                _context(fragment)
                # 같은 실패의 다른 인용을 범위 교정 대상으로 대여하지 않는다.
                if not section_investment_plan_problem(
                    sentence.text, {identifier: fragment.text}, {identifier: fragment.section_context_json},
                ):
                    continue
                failures.add(ScopeSupplementFailure(
                    packet.packet_sha256, pair_id,
                    hashlib.sha256(fragment.text.encode('utf-8')).hexdigest(),
                    section_context_fingerprint(fragment.section_context_json),
                ))
        if failures:
            result[packet.section_id] = tuple(sorted(failures, key=lambda item: item.pair_id))
    return result


def render_scope_supplement_feedback(packet: SectionEvidencePacket, failures: tuple[ScopeSupplementFailure, ...]) -> str:
    """메타 원문은 JSON 자료로만 표시한다. 실패 초안·모델 지시문은 전달하지 않는다."""
    if type(failures) is not tuple:
        raise ValueError('사업부 보충 안내는 봉인된 실패 tuple이어야 합니다')
    pairs = build_evidence_pair_map(packet.section_id, packet.fragments)
    by_id = {item.fragment_id: item for item in packet.fragments}
    rows = []
    for failure in failures:
        if type(failure) is not ScopeSupplementFailure or failure.packet_sha256 != packet.packet_sha256 or failure.pair_id not in pairs:
            raise ValueError('다른 packet 또는 미존재 지원쌍의 보충 안내입니다')
        slot, identifier = pairs[failure.pair_id]
        fragment = by_id[identifier]
        if (failure.fragment_sha256 != hashlib.sha256(fragment.text.encode('utf-8')).hexdigest()
                or failure.section_context_sha256 != section_context_fingerprint(fragment.section_context_json)):
            raise ValueError('사업부 보충 안내의 원문 또는 범위 지문이 다릅니다')
        context = _context(fragment)
        rows.append({'실패코드': 'source_section_scope_omitted', '지원쌍': failure.pair_id,
                     '주장범주': slot, '원문사업범위': context['text']})
    if not rows:
        return ''
    return ('아래는 이전 검수에서 빠진 사업부 범위의 자료 목록(JSON)이다. '
            '해당 지원쌍을 선택하면 원문 사업 범위를 보존한다. '
            '이 제목은 범위를 좁히는 문맥이며 별도 사실·현재 사업·수치 증명이 아니다.\n'
            + json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n')
