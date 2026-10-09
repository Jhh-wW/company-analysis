"""사업부 실패 안내가 같은 봉인 지원쌍에만 붙는지 검사한다."""
from dataclasses import replace
import hashlib
import json

import pytest

from src.features.composer.logic import compose_selected_sections
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.scope_supplement_feedback import (
    collect_scope_supplement_failures, render_scope_supplement_feedback,
)
from src.features.composer.tests.test_evidence_pair_selection import _packets
from src.features.composer.tests.test_business_population_scope import _context

SOURCE = '향후 투자 계획은 정보 시스템 고도화 및 생산성 향상을 위해 투자 수행 예정입니다.'
CLAIM = '회사는 정보 시스템 고도화를 위해 투자를 수행할 예정이다.'
TITLE = '[기타부문-시제품]'
SLOT = 'future_strategy:stated_plan'


def inputs():
    packets = _packets()
    original = next(item for item in packets.packets if item.section_id == 'future_strategy')
    raw = _context(SOURCE, TITLE)
    metadata = json.loads(raw)
    first = replace(original.fragments[0], text=SOURCE,
                    document_content_sha256=metadata['document_sha256'],
                    source_document_id=metadata['document_id'],
                    location=metadata['fragment_location'], section_context_json=raw,
                    supported_claim_slots=(SLOT,))
    packet = replace(original, fragments=(first, *original.fragments[1:]))
    packets = replace(packets, packets=tuple(packet if item.section_id == packet.section_id else item
                                            for item in packets.packets))
    sentence = ComposedSentence(CLAIM, (first.fragment_id,), '확인', planned_claim_slot=SLOT)
    draft = ComposedReport((ComposedSection('future_strategy', (sentence,)),))
    diagnostic = {'section_id': 'future_strategy', 'kind': '본문',
                  'reason_code': 'scope_condition_unbound',
                  'candidate_sha256': hashlib.sha256(CLAIM.encode()).hexdigest()}
    return packets, packet, draft, diagnostic


def test_actual_scope_failure_passes_only_own_pair_and_original_scope():
    packets, packet, draft, diagnostic = inputs()
    failures = collect_scope_supplement_failures(draft, [diagnostic], packets)
    rows = failures['future_strategy']
    assert len(rows) == 1 and rows[0].packet_sha256 == packet.packet_sha256
    prompt = render_scope_supplement_feedback(packet, rows)
    assert TITLE in prompt and 'source_section_scope_omitted' in prompt
    assert CLAIM not in prompt and SOURCE not in prompt
    seen = []
    def ask(prompt):
        seen.append(prompt)
        return json.dumps({'문장들': []})
    compose_selected_sections('합성회사', None, ask, section_evidence_packets=packets,
                              section_ids=('future_strategy',), scope_failures_by_section=failures)
    assert len(seen) == 1 and prompt in seen[0]
    assert seen[0].response_schema and seen[0].cache_prefix_chars == 0


@pytest.mark.parametrize('change', [
    {'reason_code': 'future_plan_evidence_missing'}, {'kind': '도식'},
    {'section_id': 'identity'}, {'candidate_sha256': '0' * 64},
])
def test_other_failure_draft_or_section_cannot_borrow_scope_feedback(change):
    packets, _, draft, diagnostic = inputs()
    assert collect_scope_supplement_failures(draft, [{**diagnostic, **change}], packets) == {}


def test_generic_scope_failure_or_missing_citation_is_not_section_omission():
    packets, _, draft, diagnostic = inputs()
    sentence = draft.sections[0].sentences[0]
    other = replace(sentence, text='회사는 고객 지원 업무를 수행한다.')
    other_draft = replace(draft, sections=(replace(draft.sections[0], sentences=(other,)),))
    diagnostic['candidate_sha256'] = hashlib.sha256(other.text.encode()).hexdigest()
    assert collect_scope_supplement_failures(other_draft, [diagnostic], packets) == {}
    foreign = replace(sentence, citations=('999999',))
    foreign_draft = replace(draft, sections=(replace(draft.sections[0], sentences=(foreign,)),))
    diagnostic['candidate_sha256'] = hashlib.sha256(sentence.text.encode()).hexdigest()
    assert collect_scope_supplement_failures(foreign_draft, [diagnostic], packets) == {}


@pytest.mark.parametrize('change', [
    {'pair_id': 'p6-999'}, {'packet_sha256': '0' * 64},
    {'fragment_sha256': '0' * 64}, {'section_context_sha256': '0' * 64},
])
def test_tampered_feedback_is_rejected_against_sealed_sources(change):
    packets, packet, draft, diagnostic = inputs()
    row = collect_scope_supplement_failures(draft, [diagnostic], packets)['future_strategy'][0]
    with pytest.raises(ValueError):
        render_scope_supplement_feedback(packet, (replace(row, **change),))


def test_no_failure_keeps_existing_supplement_prompt():
    _, packet, _, _ = inputs()
    assert render_scope_supplement_feedback(packet, ()) == ''
