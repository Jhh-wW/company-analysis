"""모델의 명시 선택도 기존 미래·자기 인용 검사와 요청 신원을 유지한다."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest

from src.features.composer import verify
from src.features.composer.future_plan_constants import STATED_PLAN_SLOT
from src.features.composer.future_plan_guard import future_plan_prose_problem, _prose_plan_claims
from src.features.composer.future_proof_selection import (
    FutureProofCandidate, prepare_future_proof_options, restore_future_proof_selections,
)
from src.features.composer.future_proof_selection_constants import FUTURE_SELECTION_KEY
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA, DIAGRAM_REVIEW_SCHEMA

SOURCE = '회사는 신공장을 증설할 계획이다.'
TEXT = '회사는 신공장을 증설할 계획이다.'


def prepared(text=TEXT, source=SOURCE, slot=STATED_PLAN_SLOT, section='future_strategy'):
    fragment = CollectedFragment('1', '공시', source, source_url='https://example.com/a',
                                 document_identity='공식 자기 문서')
    row = FutureProofCandidate(1, section, slot, text, (fragment,))
    return prepare_future_proof_options({1: row}), {1: (text, {'1': source})}, {1: section}, {1: slot}, {'1': fragment}


def selected(value, **extra):
    return json.dumps({'판정': [{'번호': 1, '장': 'future_strategy', '근거': ['1'],
        '근거대조': '자기 계획을 대조했다.', '결과': '참',
        '검증근거': {FUTURE_SELECTION_KEY: value, **extra}}]}, ensure_ascii=False)


def test_selected_proof_keeps_original_bytes_and_passes_existing_guard():
    options, candidates, sections, slots, fragments = prepared()
    assert options[1]
    raw = selected(options[1][0].option_id)
    original = deepcopy(candidates)
    restored, failed = restore_future_proof_selections(raw, options, candidates, sections, slots, fragments)
    proof = json.loads(restored)['판정'][0]['검증근거']
    assert not failed and future_plan_prose_problem(TEXT, candidates[1][1], proof, claim_slot=STATED_PLAN_SLOT) == ''
    assert candidates == original and FUTURE_SELECTION_KEY in json.loads(raw)['판정'][0]['검증근거']
    assert proof['미래근거'][0]['원문'] in SOURCE
    problems = {}
    assert verify._apply_grounding(raw, {1: '참'}, candidates,
        future_options_by_number=options, source_fragments_by_id=fragments,
        diagnostic_contexts={1: ('future_strategy', '본문', TEXT)},
        claim_slots_by_number=slots, prose_numbers=frozenset({1}),
        grounding_problems=problems) == {1: '참'}
    assert not problems


@pytest.mark.parametrize('bad', ['unknown', '', True, 1, None, [], {}])
def test_bad_selection_is_closed(bad):
    options, candidates, sections, slots, fragments = prepared()
    assert restore_future_proof_selections(selected(bad), options, candidates, sections, slots, fragments)[1] == {1}


def test_stale_request_wrong_number_changed_metadata_or_source_is_closed():
    options, candidates, sections, slots, fragments = prepared()
    raw = selected(options[1][0].option_id)
    new_options, *_ = prepared()
    assert restore_future_proof_selections(raw, new_options, candidates, sections, slots, fragments)[1] == {1}
    assert restore_future_proof_selections(raw, options, candidates, {1: 'business_model'}, slots, fragments)[1] == {1}
    assert restore_future_proof_selections(raw, options, candidates, sections, {1: 'future_strategy:plan_status'}, fragments)[1] == {1}
    assert restore_future_proof_selections(raw, options, {1: (TEXT + ' 다른 계획', {'1': SOURCE})}, sections, slots, fragments)[1] == {1}
    changed = {'1': replace(fragments['1'], source_url='https://example.com/b')}
    assert restore_future_proof_selections(raw, options, candidates, sections, slots, changed)[1] == {1}
    assert restore_future_proof_selections(raw, options, {1: (TEXT, {'1': SOURCE + ' 변조'})}, sections, slots, fragments)[1] == {1}
    assert restore_future_proof_selections(selected(options[1][0].option_id, 미래근거=[]), options, candidates, sections, slots, fragments)[1] == {1}


def test_legacy_bytes_and_other_slots_have_no_new_selection_requirement():
    options, candidates, sections, slots, fragments = prepared()
    raw = '{"판정": [{"번호": 1, "결과": "거짓"}]}'
    assert restore_future_proof_selections(raw, options, candidates, sections, slots, fragments) == (raw, frozenset())
    assert prepared(slot='future_strategy:plan_status')[0] == {}
    assert prepared(section='business_model')[0] == {}
    assert FUTURE_SELECTION_KEY in FLAT_REVIEW_SCHEMA['$defs']['grounding']['properties']
    assert FUTURE_SELECTION_KEY not in DIAGRAM_REVIEW_SCHEMA['$defs']['grounding']['properties']


def test_all_own_source_metadata_and_duplicate_with_legacy_row_are_bound():
    options, candidates, sections, slots, fragments = prepared()
    second = CollectedFragment('2', '공시', '회사는 현재 창고를 운영한다.', source_url='https://example.com/b')
    fragments['2'] = second
    row = FutureProofCandidate(1, 'future_strategy', STATED_PLAN_SLOT, TEXT, tuple(fragments.values()))
    options = prepare_future_proof_options({1: row})
    candidates[1][1]['2'] = second.text
    raw = selected(options[1][0].option_id)
    changed = {**fragments, '2': replace(second, source_url='https://example.com/c')}
    assert restore_future_proof_selections(raw, options, candidates, sections, slots, changed)[1] == {1}
    payload = json.loads(raw)
    payload['판정'].append({'번호': 1, '결과': '참', '검증근거': {'미래근거': [json.loads(options[1][0].proof_json)]}})
    assert restore_future_proof_selections(json.dumps(payload, ensure_ascii=False), options, candidates, sections, slots, fragments)[1] == {1}


@pytest.mark.parametrize('source,text', [
    ('협력사는 신공장을 증설할 계획이다.', TEXT),
    ('회사는 신공장을 증설하고 있다.', TEXT),
    ('회사는 신공장을 증설할 계획이었으나 취소했다.', TEXT),
    (SOURCE, '회사는 신공장을 증설할 계획이며 창고를 매각할 예정이다.'),
])
def test_wrong_owner_current_cancelled_and_multiple_activity_never_get_single_proof(source, text):
    assert prepared(text, source)[0] == {}


def test_nominal_plan_introduction_is_one_claim_but_two_explicit_plans_stay_two():
    source = '향후 투자 계획은 시스템 고도화 및 품질 강화를 위해 투자 수행 예정입니다.'
    text = '시제품 부문은 향후 투자 계획으로 시스템 고도화 및 품질 강화를 위해 투자를 수행할 예정이다.'
    options, candidates, sections, slots, fragments = prepared(text, source)
    assert options[1]
    assert len(_prose_plan_claims(text, company_plan_slot=True)) == 2
    evidence = {'미래근거': [json.loads(options[1][0].proof_json)]}
    assert future_plan_prose_problem(text, candidates[1][1], evidence, claim_slot=STATED_PLAN_SLOT) == ''
    other = '회사는 신공장을 증설할 계획으로 창고를 매각할 예정이다.'
    assert len(_prose_plan_claims(other, company_plan_slot=True)) == 2
    assert prepared(other, SOURCE)[0] == {}


def test_production_hint_and_response_share_the_same_request_option():
    fragment = prepared()[4]['1']
    sentence = ComposedSentence(TEXT, ('1',), 'fact', planned_claim_slot=STATED_PLAN_SLOT)
    item = verify._ReviewItem(1, sentence, section_id='future_strategy')
    def ask(prompt):
        choices = prompt.split('미래증명 선택지(JSON): ', 1)[1].split('\n', 1)[0]
        return selected(json.loads(choices)[0]['ID'])
    assert verify._ask_verdicts(ask, (item,), {'1': fragment}, '') == {1: '참'}


def test_grouped_display_id_normalizes_before_selected_proof_restore():
    fragment = prepared()[4]['1']
    sentence = ComposedSentence(TEXT, ('1',), '확인', planned_claim_slot=STATED_PLAN_SLOT)
    item = verify._GroupedReviewItem(1, 'future_strategy', verify.REVIEW_KIND_SENTENCE,
                                   ('1',), sentence=sentence)

    def ask(prompt):
        choices = prompt.split('미래증명 선택지(JSON): ', 1)[1].split('\n', 1)[0]
        payload = json.loads(selected(json.loads(choices)[0]['ID']))
        payload['판정'][0]['근거'] = ['조각 1']
        return json.dumps(payload, ensure_ascii=False)

    assert verify._ask_grouped_verdicts(ask, (item,), {'1': fragment}, None) == {1: '참'}


@pytest.mark.parametrize('text,source', [
    ('회사는 고객의 편의성을 높이는 장치를 마련하겠다고 밝혔다.',
     '회사는 고객의 편의성을 높이는 장치를 마련하겠습니다.'),
    ('회사는 신공장을 증설할 것이라고 밝혔다.',
     '회사는 신공장을 증설할 것이다.'),
])
def test_reported_future_discovers_only_prevalidated_proofs(text, source):
    options, candidates, sections, slots, fragments = prepared(text, source)
    assert options[1]
    restored, failed = restore_future_proof_selections(
        selected(options[1][0].option_id), options, candidates, sections, slots, fragments)
    assert not failed
    assert not future_plan_prose_problem(text, candidates[1][1],
        json.loads(restored)['판정'][0]['검증근거'], claim_slot=STATED_PLAN_SLOT)


@pytest.mark.parametrize('source,text', [
    ('회사는 신공장을 증설했다.', '회사는 신공장을 증설하겠다고 밝혔다.'),
    ('회사는 신공장을 증설하고 있다.', '회사는 신공장을 증설하겠다고 밝혔다.'),
    ('협력사는 신공장을 증설할 것이다.', '회사는 신공장을 증설할 것이라고 밝혔다.'),
    ('회사는 신공장을 증설하지 않을 것이다.', '회사는 신공장을 증설할 것이라고 밝혔다.'),
    ('회사는 허가를 받으면 신공장을 증설할 것이다.', '회사는 신공장을 증설할 것이라고 밝혔다.'),
])
def test_reported_future_does_not_override_owner_state_or_condition(source, text):
    assert prepared(text, source)[0] == {}


def test_flat_review_auxiliary_table_preserves_selected_own_proof():
    fragment = prepared()[4]['1']
    item = verify._ReviewItem(1, ComposedSentence(TEXT, ('1',), '확인',
        planned_claim_slot=STATED_PLAN_SLOT), section_id='future_strategy')
    def ask(prompt):
        choices = prompt.split('미래증명 선택지(JSON): ', 1)[1].split('\n', 1)[0]
        return selected(json.loads(choices)[0]['ID'])
    assert verify._ask_verdicts(ask, (item,), {'1': fragment}, '', '별도 보조 실적표') == {1: '참'}


def test_auxiliary_table_never_replaces_or_adds_actual_cited_source():
    from src.features.composer.grounding_constants import TABLE_SOURCE_ID
    options, candidates, sections, slots, fragments = prepared()
    raw = selected(options[1][0].option_id)
    candidates[1][1][TABLE_SOURCE_ID] = '별도 실적표'
    assert not restore_future_proof_selections(raw, options, candidates, sections, slots, fragments)[1]
    changed = deepcopy(candidates)
    changed[1][1]['1'] = '변조 원문'
    assert restore_future_proof_selections(raw, options, changed, sections, slots, fragments)[1] == {1}
    added = deepcopy(candidates)
    added[1][1]['2'] = '회사는 신공장을 증설할 계획이다.'
    assert restore_future_proof_selections(raw, options, added, sections, slots, fragments)[1] == {1}
    removed = {1: (TEXT, {TABLE_SOURCE_ID: SOURCE})}
    assert restore_future_proof_selections(raw, options, removed, sections, slots, fragments)[1] == {1}
