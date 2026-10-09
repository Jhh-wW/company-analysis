"""재분류에서도 교육 예시·안내 문맥을 보존하고 실행 완료 승격을 닫는다."""
from dataclasses import replace
import hashlib
import json

import pytest

from src.features.evidence_reclassify.constants import REJECT_PRACTICE_COMPLETION
from src.features.evidence_reclassify.logic import build_reclassify_request, parse_and_verify, to_typed_fragments
from src.features.evidence_reclassify.tests.test_logic import _assignment, _candidate, _response
from src.shared.report_evidence.practice_context import build_practice_context


TEXT = '2024년 검수 시스템을 구현했습니다.'


def _with_context(mode='example'):
    marker = '프롬프트의 예시:' if mode == 'example' else 'QA 에이전트에게 검수 기준을 확인하게 합니다.'
    ranges = (marker, TEXT)
    candidate = _candidate(text=TEXT)
    candidate['content_sha256'] = hashlib.sha256('\n'.join(ranges).encode()).hexdigest()
    start = len(marker) + 1
    candidate['location'] = f'{start}-{start + len(TEXT)}'
    candidate['practice_context_json'] = build_practice_context(
        ranges=ranges, document_id=candidate['document_id'],
        document_sha256=candidate['content_sha256'], fragment_index=1,
        fragment_location=candidate['location'],
    )
    assert candidate['practice_context_json']
    return candidate


@pytest.mark.parametrize('mode', ['example', 'instruction'])
@pytest.mark.parametrize('source_kind', ['candidate', 'document', 'document_id_alias', 'none'])
def test_정확문맥_원문_지문_위치를_프롬프트와_typed까지_보존한다(mode, source_kind):
    candidate = _with_context(mode)
    before = dict(candidate)
    request = build_reclassify_request(['portfolio'], [candidate])
    assert candidate['practice_context_json'] in request.prompt
    result = parse_and_verify(_response(_assignment(quote=TEXT)), [candidate])
    if source_kind == 'candidate':
        source = dict(candidate)
    elif source_kind in ('document', 'document_id_alias'):
        source = {key: candidate[key] for key in ('company_id', 'document_id', 'content_sha256')}
        if source_kind == 'document_id_alias':
            source['id'] = candidate['document_id']
    else:
        source = {}
    fragment, = to_typed_fragments(result, source)
    for key in ('text', 'location', 'text_sha256', 'practice_context_json'):
        assert fragment[key] == candidate[key]
    assert candidate == before


@pytest.mark.parametrize('mode', ['example', 'instruction'])
def test_예시의_완료어미와_연도도_회사실행배정이_되지_않는다(mode):
    candidate = _with_context(mode)
    item = _assignment(section_id='past_changes', slot_id='past_changes:completed_execution', quote=TEXT)
    result = parse_and_verify(_response(item), [candidate])
    assert not result.assignments
    assert result.rejected[0].reason_code == REJECT_PRACTICE_COMPLETION
    assert to_typed_fragments(result, candidate) == []
    assert result.candidate_paragraphs[0].text == candidate['text']


@pytest.mark.parametrize('mode', ['example', 'instruction'])
def test_배정자료를_직접변조해도_typed에서_완료실행으로_승격하지_못한다(mode):
    candidate = _with_context(mode)
    result = parse_and_verify(_response(_assignment(quote=TEXT)), [candidate])
    assignment, = result.assignments
    forged = replace(result, assignments=(replace(assignment, section_id='past_changes', slot_id='past_changes:completed_execution'),))
    with pytest.raises(ValueError, match='완료된 실행'):
        to_typed_fragments(forged, candidate)


@pytest.mark.parametrize('change', ['erase', 'missing', 'replace', 'document_hash', 'fragment_hash'])
def test_조각_source레코드의_문맥삭제_교환_지문변조는_거절한다(change):
    candidate = _with_context()
    result = parse_and_verify(_response(_assignment(quote=TEXT)), [candidate])
    source = dict(candidate)
    if change == 'erase':
        source['practice_context_json'] = ''
    elif change == 'missing':
        source.pop('practice_context_json')
    elif change == 'document_hash':
        source['content_sha256'] = 'b' * 64
    elif change == 'fragment_hash':
        source['text_sha256'] = 'b' * 64
    else:
        source['practice_context_json'] = _with_context('instruction')['practice_context_json']
    with pytest.raises(ValueError):
        to_typed_fragments(result, source)


def test_source만의_문맥을_후보가_삭제했을_때도_거절한다():
    source = _with_context()
    candidate = dict(source)
    candidate.pop('practice_context_json')
    result = parse_and_verify(_response(_assignment(quote=TEXT)), [candidate])
    with pytest.raises(ValueError, match='원래 후보'):
        to_typed_fragments(result, source)


def test_원문과_위치의_기존한글별칭에도_문맥을_결속한다():
    candidate = _with_context()
    candidate['원문'] = candidate.pop('text')
    candidate['원문위치'] = candidate.pop('location')
    result = parse_and_verify(_response(_assignment(quote=TEXT)), [candidate])
    fragment, = to_typed_fragments(result, {})
    assert fragment['text'] == candidate['원문']
    assert fragment['location'] == candidate['원문위치']
    assert fragment['practice_context_json'] == candidate['practice_context_json']


@pytest.mark.parametrize('change', ['not_string', 'noncanonical', 'cross_document', 'cross_text', 'missing_document', 'missing_location'])
def test_후보의_문맥은_모델요청과_배정전에_자기원문에_검증한다(change):
    candidate = _with_context()
    context = json.loads(candidate['practice_context_json'])
    if change == 'not_string':
        candidate['practice_context_json'] = None
    elif change == 'noncanonical':
        candidate['practice_context_json'] = json.dumps(context, ensure_ascii=False, indent=2)
    elif change == 'cross_document':
        candidate['document_id'] = 'other-document'
    elif change == 'cross_text':
        candidate['text'] += ' 변경'
    elif change == 'missing_document':
        candidate.pop('document_id')
    else:
        candidate.pop('location')
    with pytest.raises(ValueError):
        build_reclassify_request(['portfolio'], [candidate])
    with pytest.raises(ValueError):
        parse_and_verify(_response(_assignment(quote=TEXT)), [candidate])


def test_명시예시없는_회사완료실행과_현재고객교육은_기존계약대로_보존한다():
    for text, section, slot in (
        ('회사는 2024년 검수 시스템을 도입했다.', 'past_changes', 'past_changes:completed_execution'),
        ('회사는 고객에게 교육서비스를 제공한다.', 'portfolio', 'portfolio:product_role'),
    ):
        candidate = _candidate(text=text)
        result = parse_and_verify(_response(_assignment(section_id=section, slot_id=slot, quote=text)), [candidate])
        fragment, = to_typed_fragments(result, {})
        assert fragment['text'] == text
        assert 'practice_context_json' not in fragment
