"""선택한 사건 금액도 기존 수치·자기 인용·시점 검사를 통과해야 한다."""
from copy import deepcopy
import hashlib
import json
import re

import pytest

from src.features.composer.grounding import grounding_problem
from src.features.composer.direct_support import support_entries_by_number
from src.features.composer.numeric_proof_selection import (
    NumericProofCandidate, prepare_numeric_proof_options, restore_numeric_proof_selections,
)
from src.features.composer.numeric_proof_selection_constants import NUMERIC_SELECTION_KEY
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer import verify
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA

SOURCE = ('제재조치일 | 조치대상자 | 처벌 또는 조치내용 | 이행 및 재발방지대책\n'
          '2023.04.10 | 가온제조㈜ | 과태료(750만원) | 납부 완료, 설비 점검')
TEXT = '가온제조㈜는 2023.04.10 과태료 750만원을 받았고 설비 점검을 대응 항목에 기재했다.'


def fragment(text=SOURCE, fid='1'):
    return CollectedFragment(fid, '공시', text, document_identity='dart:test:1',
        document_content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        formal_source_kind='dart_business_report', source_document_id='test:1',
        identity_binding='공식 문서 법인 확인', supported_claim_slots=('current_challenges:issue',))


def prepared(text=TEXT, fragments=None, section='current_challenges', number=1):
    frags = (fragment(),) if fragments is None else fragments
    options = prepare_numeric_proof_options({number: NumericProofCandidate(number, section, text, frags)})
    own = {item.fragment_id: item.text for item in frags}
    return options, {number: (text, own)}, {number: section}


def selected(option_id, *, number=1, extra=None, result='참'):
    return json.dumps({'판정': [{'번호': number, '장': 'current_challenges', '근거': ['1'],
        '근거대조': '같은 행을 대조했다.', '결과': result,
        '검증근거': {NUMERIC_SELECTION_KEY: option_id, **(extra or {})}}]}, ensure_ascii=False)


def test_same_row_selection_restores_literal_fields_without_mutating_raw_or_sources():
    options, candidates, sections = prepared()
    original = deepcopy(candidates)
    raw = selected(options[1][0].option_id)
    restored, failed = restore_numeric_proof_selections(raw, options, candidates, sections)
    proof = support_entries_by_number(restored)[1]['수치'][0]
    assert not failed and grounding_problem(TEXT, candidates[1][1], json.loads(restored)['판정'][0]) == ''
    assert proof['항목'] == proof['원문항목'] == '과태료'
    assert proof['표현'] == '과태료 750만원'
    assert proof['원문'] in SOURCE and proof['원문값'] == proof['후보값'] == '750만원'
    assert candidates == original and json.loads(raw)['판정'][0]['검증근거'] == {NUMERIC_SELECTION_KEY: options[1][0].option_id}


@pytest.mark.parametrize('change', [
    lambda: prepared(TEXT.replace('2023.04.10', '2023년 4월')),
    lambda: prepared(TEXT.replace('가온제조㈜', '회사는')),
    lambda: prepared(fragments=(fragment(SOURCE.replace('2023.04.10', '2024.04.10')),)),
    lambda: prepared(fragments=(fragment(), fragment(fid='2'))),
    lambda: prepared(fragments=(fragment(SOURCE.replace('750만원)', '750만원) 미부과')),)),
    lambda: prepared(TEXT + ' 과태료 100만원도 받았다.'),
    lambda: prepared(TEXT.replace('750만원', '750만원 이하')),
    lambda: prepared(section='business_model'),
    lambda: prepared(fragments=(CollectedFragment('1', '공시', SOURCE),)),
    lambda: prepared(TEXT.replace('과태료 750만원', '사망자 1명'), fragments=(fragment(SOURCE.replace('과태료(750만원)', '사망자 1')),)),
])
def test_unsupported_or_ambiguous_claims_keep_the_existing_free_proof_path(change):
    assert change()[0] == {}


@pytest.mark.parametrize('bad', ['unknown', True, 1, None, [], {}])
def test_bad_selection_never_grants_a_verdict(bad):
    options, candidates, sections = prepared()
    raw = selected(bad)
    restored, failed = restore_numeric_proof_selections(raw, options, candidates, sections)
    assert failed == {1}
    assert grounding_problem(TEXT, candidates[1][1], json.loads(restored)['판정'][0]) != ''


def test_stale_request_wrong_number_changed_source_and_conflicting_proof_are_closed():
    options, candidates, sections = prepared()
    new_options, _, _ = prepared()
    raw = selected(options[1][0].option_id)
    assert restore_numeric_proof_selections(raw, new_options, candidates, sections)[1] == {1}
    other = {2: candidates[1]}
    assert restore_numeric_proof_selections(selected(options[1][0].option_id, number=2), {2: options[1]}, other, {2: 'current_challenges'})[1] == {2}
    changed = {1: (TEXT, {'1': SOURCE + '\n다른 원문'})}
    assert restore_numeric_proof_selections(raw, options, changed, sections)[1] == {1}
    assert restore_numeric_proof_selections(selected(options[1][0].option_id, extra={'수치': []}), options, candidates, sections)[1] == {1}
    assert restore_numeric_proof_selections(selected('', extra={'수치': []}), options, candidates, sections)[1] == {1}
    assert restore_numeric_proof_selections(raw, options, candidates, {1: 'business_model'})[1] == {1}


def test_legacy_binding_bytes_and_false_verdict_are_preserved():
    options, candidates, sections = prepared()
    raw = '{"판정": [{"번호": 1, "결과": "거짓"}]}'
    assert restore_numeric_proof_selections(raw, options, candidates, sections) == (raw, frozenset())
    assert verify._apply_grounding(selected(options[1][0].option_id, result='거짓'), {1: '거짓'}, candidates,
        numeric_options_by_number=options, diagnostic_contexts={1: ('current_challenges', '문장', TEXT)}) == {1: '거짓'}


def test_apply_grounding_all_consumers_read_the_same_expanded_input(monkeypatch):
    options, candidates, sections = prepared()
    observed = []
    original_constrain = verify.constrain_verdicts
    original_support = verify.support_entries_by_number
    def constrain(raw, *args, **kwargs):
        observed.append(raw)
        return original_constrain(raw, *args, **kwargs)
    def support(raw):
        observed.append(raw)
        return original_support(raw)
    monkeypatch.setattr(verify, 'constrain_verdicts', constrain)
    monkeypatch.setattr(verify, 'support_entries_by_number', support)
    raw = selected(options[1][0].option_id)
    result = verify._apply_grounding(raw, {1: '참'}, candidates, numeric_options_by_number=options,
        diagnostic_contexts={1: ('current_challenges', '문장', TEXT)})
    assert result == {1: '참'} and len(observed) >= 2 and len(set(observed)) == 1
    assert NUMERIC_SELECTION_KEY not in support_entries_by_number(observed[0])[1]
    assert '수치' in support_entries_by_number(observed[0])[1]
    assert NUMERIC_SELECTION_KEY in json.loads(raw)['판정'][0]['검증근거']


def test_grouped_and_flat_requests_use_one_local_option_without_extra_calls():
    calls = []
    def ask(prompt):
        calls.append(prompt)
        option_id = re.search(r'np1_[0-9a-f]{64}', prompt).group()
        return selected(option_id)
    sentence = ComposedSentence(TEXT, ('1',), '확인', planned_claim_slot='current_challenges:issue')
    grouped = verify._GroupedReviewItem(1, 'current_challenges', '문장', ('1',), sentence=sentence)
    result = verify._ask_grouped_verdicts(ask, [grouped], {'1': fragment()}, None)
    assert result == {1: '참'} and len(calls) == 1 and not hasattr(calls[0], 'response_schema')
    calls.clear()
    item = verify._ReviewItem(1, sentence, section_id='current_challenges', kind='문장')
    assert verify._ask_verdicts(ask, [item], {'1': fragment()}, '') == {1: '참'}
    assert len(calls) == 1
    assert FLAT_REVIEW_SCHEMA['$defs']['grounding']['properties'][NUMERIC_SELECTION_KEY] == {'type': 'string'}


def test_invalid_selection_is_diagnosed_without_overriding_false():
    options, candidates, _ = prepared()
    diagnostics = []
    result = verify._apply_grounding(selected('unknown'), {1: '참'}, candidates,
        numeric_options_by_number=options, diagnostic_contexts={1: ('current_challenges', '문장', TEXT)}, diagnostics=diagnostics)
    assert result[1] != '참'
    assert diagnostics[0]['grounding_detail']['stage'] == 'numeric_selection_unbound'


@pytest.mark.parametrize('grouped', [False, True])
def test_missing_verdict_followup_keeps_the_original_request_choices(grouped):
    calls, first_ids = [], {}
    def ask(prompt):
        calls.append(prompt)
        choices = [json.loads(line.split('수치선택 후보(JSON): ', 1)[1])[0]
                   for line in prompt.splitlines() if '수치선택 후보(JSON): ' in line]
        choices_by_number = {choice['번호']: choice['ID'] for choice in choices}
        if len(calls) == 1:
            first_ids.update(choices_by_number)
            return selected(first_ids[1])
        assert choices_by_number == {2: first_ids[2]}
        return selected(choices_by_number[2], number=2)
    sentence = ComposedSentence(TEXT, ('1',), '확인', planned_claim_slot='current_challenges:issue')
    if grouped:
        items = [verify._GroupedReviewItem(number, 'current_challenges', '문장', ('1',), sentence=sentence)
                 for number in (1, 2)]
        result = verify._ask_grouped_verdicts(ask, items, {'1': fragment()}, None, initial_ask=ask)
    else:
        items = [verify._ReviewItem(number, sentence, section_id='current_challenges') for number in (1, 2)]
        result = verify._ask_verdicts(ask, items, {'1': fragment()}, '', initial_ask=ask)
    assert result == {1:'참', 2:'참'} and len(calls) == 2


@pytest.mark.parametrize('response,candidate,allowed', [
    ('안전시설 보완 진행 중', '안전시설 보완을 완료했다고 기재했다', False),
    ('안전시설 보완 진행 중', '안전시설 보완을 진행 중이라고 기재했다', True),
    ('납부 완료, 안전시설 보완 진행 중', '납부를 완료했고 안전시설 보완을 진행 중이라고 기재했다', True),
    ('납부 완료, 안전시설 보완 진행 중', '납부와 안전시설 보완을 모두 완료했다고 기재했다', False),
    ('납부 완료, 안전시설 보완 진행 중', '납부 완료를 기재했다', True),
])
def test_payment_and_facility_supplement_states_stay_with_their_own_action(response, candidate, allowed):
    source = SOURCE.replace('납부 완료, 설비 점검', response)
    text = '가온제조㈜는 2023.04.10 과태료 750만원을 받았고 ' + candidate + '.'
    assert bool(prepared(text, fragments=(fragment(source),))[0]) is allowed


def test_flat_legacy_item_without_explicit_owner_does_not_advertise_new_options():
    calls = []
    def ask(prompt):
        calls.append(prompt)
        assert '수치선택 후보(JSON):' not in prompt
        return '{"판정":[{"번호":1,"결과":"거짓"}]}'
    sentence = ComposedSentence(TEXT, ('1',), '확인', planned_claim_slot='current_challenges:issue')
    assert verify._ask_verdicts(ask, [verify._ReviewItem(1, sentence)], {'1':fragment()}, '') == {1:'거짓'}
    assert len(calls) == 1


@pytest.mark.parametrize('actor', ['-', '2023', '1', '미확인', '미상', 'N/A', 'n/a', '확인 불가'])
def test_missing_actor_never_binds_to_a_date_or_an_unknown_label(actor):
    source = SOURCE.replace('가온제조㈜', actor)
    text = TEXT.replace('가온제조㈜', '회사는' if actor in ('-', '2023', '1') else actor)
    assert prepared(text, fragments=(fragment(source),))[0] == {}


def test_company_name_with_digits_remains_an_explicit_actor():
    source = SOURCE.replace('가온제조㈜', '제1산업㈜')
    text = TEXT.replace('가온제조㈜', '제1산업㈜')
    assert prepared(text, fragments=(fragment(source),))[0]
