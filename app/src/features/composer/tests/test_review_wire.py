"""키 축약은 전송 표현만 바꾸며 기존 판정·검증·구제 계약은 유지한다."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.features.composer import verify
from src.features.composer.constants import RETRY_REMINDER
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.prompt_metadata import PromptMetadata
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA, ReviewPrompt
from src.features.composer.review_wire import restore_review_wire, review_wire_prompt
from src.features.composer.review_wire_constants import REVIEW_WIRE_GUIDE, REVIEW_WIRE_KEYS
from src.features.composer.tests import test_numeric_quote_refs as numeric_fixture


def _compact(value):
    if isinstance(value, dict):
        return {REVIEW_WIRE_KEYS.get(key, key): _compact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact(item) for item in value]
    return value


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def test_모든_검증필드의_값과_유형과_행을_그대로_복원한다():
    proof = {
        '수치': numeric_fixture._three_year_ref_entries(),
        '추세': [{'표현': '비교', '항목': '매출액', '방향': '증가',
                 '관측': [{'근거': '공시', '원문': '그대로\n보존', '항목': '매출액',
                         '기간': '2025', '원문값': '120억원'}]}],
        '관계': [{'근거': '공시', '원문': '원문', '유형': '역할', '대상': '대상', '역할값': '역할'}],
        '시점': [{'표현': '표현', '근거': '공시', '원문': '원문', '기간': '2025'}],
        '인식기준': [{'표현': '표현', '근거': '공시', '원문': '원문'}],
        '미래근거': [{'근거': '공시', '원문': '원문', '대상': '대상', '활동': '활동', '양태': '계획'}],
        '계획진행근거': [{'근거': '공시', '대상': '대상', '활동': '활동', '계획원문': '계획', '진행원문': '진행'}],
        '번역': {'요청SHA': '요청', '문장SHA': '문장', '원문SHA': '원문', '근거': '공시',
                 '대응': [{'원문': 'q: "r"와 {기호}', '주장': '주장'}]},
        '수치선택': '선택ID', '미래증명선택': '다른선택ID',
    }
    canonical = {'판정': [{'번호': 1, '장': 'identity', '근거': ['공시'],
                        '근거대조': '키 r와 문자열 "원문"은 그대로', '결과': '참', '검증근거': proof}]}
    before = deepcopy(canonical)

    assert json.loads(restore_review_wire(_dump(_compact(canonical)))) == canonical == before
    legacy = _dump(canonical)
    assert restore_review_wire(legacy) == legacy


@pytest.mark.parametrize('row', [
    '"r":"참","결과":"거짓"', '"결과":"참","r":"거짓"',
    '"r":"참","r":"거짓"', '"g":{"n":[],"수치":[]},"r":"참"',
    '"r":"참","\\u0072":"참"',
])
def test_별칭_충돌은_판정을_만들지_않고_닫는다(row):
    assert json.loads(restore_review_wire('{"판정":[{"번호":1,' + row + '}]}')) == {'판정': [None]}


def test_충돌한_행_밖의_판정과_배열_행수는_유지한다():
    raw = ('{"판정":[{"번호":1,"r":"참"},'
           '{"번호":2,"r":"참","g":{"n":[],"수치":[]}},'
           '{"번호":3,"r":"거짓"}]}')
    restored = restore_review_wire(raw)

    assert len(json.loads(restored)['판정']) == 3
    assert verify._parse_verdicts(restored) == {1: '참', 3: '거짓'}


def test_판정번호가_없는_충돌은_봉투를_닫는다():
    assert restore_review_wire('{"판정":[{"r":"참","결과":"거짓"}]}') == '{}'


@pytest.mark.parametrize('metadata', [False, True, 'native'])
@pytest.mark.parametrize('retry', [False, True])
def test_실제전송_안내가_캐시와_스키마와_재요청_경계를_유지한다(metadata, retry):
    text = '고정\n개별원문'
    prompt = (ReviewPrompt(text, FLAT_REVIEW_SCHEMA, cache_prefix_chars=2) if metadata == 'native'
              else PromptMetadata(text, cache_prefix_chars=2) if metadata else text)
    if retry:
        prompt += RETRY_REMINDER
    sent = review_wire_prompt(prompt)

    assert str(sent) == text + REVIEW_WIRE_GUIDE + (RETRY_REMINDER if retry else '')
    if metadata:
        assert sent.cache_prefix_chars == 2
    if metadata == 'native':
        assert sent.response_schema is FLAT_REVIEW_SCHEMA


@pytest.mark.parametrize('grouped', [False, True])
@pytest.mark.parametrize('followup', ['none', 'missing', 'format'])
def test_실제_최초와_형식재요청과_누락후속_호출에서_복원한다(grouped, followup):
    text = '회사의 이름은 가나다전자이다.'
    fragments = {'1': CollectedFragment('1', '공시', text)}
    sentences = [ComposedSentence(text, ('1',), '확인') for _ in range(2)]
    calls = []

    def ask(prompt):
        calls.append(prompt)
        assert REVIEW_WIRE_GUIDE in prompt
        if followup == 'format' and len(calls) == 1:
            return '형식 오류'
        numbers = ([1] if len(calls) == 1 else [2]) if followup == 'missing' else [1, 2]
        rows = [{'번호': number, '장': 'identity', '근거': ['1'], '근거대조': '같은 원문',
                 '결과': '참'} for number in numbers]
        return _dump(_compact({'판정': rows}))

    if grouped:
        items = tuple(verify._GroupedReviewItem(i, 'identity', '문장', ('1',), sentence=sentence)
                      for i, sentence in enumerate(sentences, 1))
        result = verify._ask_grouped_verdicts(ask, items, fragments, None, initial_ask=ask)
    else:
        items = tuple(verify._ReviewItem(i, sentence, 'identity')
                      for i, sentence in enumerate(sentences, 1))
        result = verify._ask_verdicts(ask, items, fragments, '', initial_ask=ask)
    assert result == {1: '참', 2: '참'}
    assert len(calls) == (1 if followup == 'none' else 2)


@pytest.mark.parametrize('grouped', [False, True])
def test_재검수의_수치_원문참조도_기존_결속기를_통과한다(grouped):
    text = numeric_fixture._THREE_YEAR_TEXT
    fragments = {'공시': CollectedFragment('공시', '공시', text)}
    sentence = ComposedSentence(text, ('공시',), '확인')
    row = {'번호': 1, '장': 'past_changes', '근거': ['공시'], '근거대조': '같은 지표·기간·값',
           '결과': '참', '검증근거': {'수치': numeric_fixture._three_year_ref_entries()}}

    def ask(prompt):
        assert REVIEW_WIRE_GUIDE in prompt
        return _dump(_compact({'판정': [row]}))

    if grouped:
        item = verify._GroupedReviewItem(1, 'past_changes', '문장', ('공시',), sentence=sentence)
        result = verify._ask_grouped_verdicts(ask, (item,), fragments, None)
    else:
        result = verify._ask_verdicts(ask, (verify._ReviewItem(1, sentence, 'past_changes'),), fragments, '')
    assert result == {1: '참'}


def test_별칭을_복원한_구문오류도_기존_행구제를_유지한다():
    raw = '{"판정":[{"번호":1,"r":"참"},{"번호":2,"r":oops},{"번호":3,"r":"거짓"}]}'
    assert verify._parse_verdicts(restore_review_wire(raw)) == {1: '참', 3: '거짓'}


@pytest.mark.parametrize('change', ['값', '타입', '참조', '근거'])
def test_축약이_기존_수치의_변조와_참조공격을_되살리지_않는다(change):
    text = numeric_fixture._THREE_YEAR_TEXT
    proof = numeric_fixture._three_year_ref_entries()
    if change == '값':
        proof[-1]['원문값'] = '999억원'
    elif change == '타입':
        proof[-1]['원문참조'] = True
    elif change == '참조':
        proof[-1]['원문참조'] = 99
    else:
        proof[-1]['근거'] = '다른근거'
    row = {'번호': 1, '결과': '참', '검증근거': {'수치': proof}}
    from src.features.composer.grounding import grounding_problem

    restored = json.loads(restore_review_wire(_dump(_compact({'판정': [row]}))))['판정'][0]
    expected = grounding_problem(text, numeric_fixture._THREE_YEAR_SOURCE, row)
    assert expected
    assert grounding_problem(text, numeric_fixture._THREE_YEAR_SOURCE, restored) == expected


def test_실제_호출의_길이진단은_복원전_전송량을_기록한다():
    text = '회사의 이름은 가나다전자이다.'
    fragment = CollectedFragment('1', '공시', text)
    wire = _dump(_compact({'판정': [{'번호': 1, '결과': '참'}]}))
    sent = []
    observations = []

    def ask(prompt):
        sent.append(prompt)
        return wire

    verify._ask_verdicts(
        ask, (verify._ReviewItem(1, ComposedSentence(text, ('1',), '확인'), 'identity'),),
        {'1': fragment}, '', protocol_diagnostics=observations,
    )

    assert observations[0]['입력문자'] == len(sent[0])
    assert observations[0]['응답문자'] == len(wire)
