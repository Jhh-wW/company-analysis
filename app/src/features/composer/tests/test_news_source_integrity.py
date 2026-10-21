"""실제 저장 응답의 제3자 번역과 보상 주체 결속을 무료로 재생한다."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.features.composer.news_compensation_scope import news_compensation_scope_problem
from src.features.composer.news_usage import attribution_prefix, retain_verified_news
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.verify import _apply_grounding, _machine_check, _numeric_disposal
from src.shared.report_quality.supplementary_prose import news_number_tokens


SAVED = json.loads((Path(__file__).parent / 'fixtures/news_source_integrity.json').read_text(encoding='utf-8'))


def _fragment(key, fid, date):
    source = SAVED[key]
    return CollectedFragment(
        fragment_id=fid, kind='news', text=source['text'], source_url=source['url'],
        document_date=date, source_publisher='it.chosun.com', news_grounded=True,
        formal_source_kind='news', supported_claim_slots=(source['slot'],),
    )


ISSUE = _fragment('issue_source', '84', '2026-09-11')
RESPONSE = _fragment('response_source', '80', '2026-10-06')


def _sentence(key):
    item = SAVED[key]
    return ComposedSentence(item['글'], tuple(item['인용']), item['등급'],
                            planned_claim_slot=item['주장슬롯'])


def test_실제_제3자_번역은_자기_영어원문으로_확인등급을_유지한다():
    sentence = _sentence('issue_candidate')
    assert _numeric_disposal(sentence, {'84': ISSUE}, ()) == '통과'
    checked, = _machine_check((sentence,), {'84': ISSUE}, ())
    assert checked.grade == '확인' and checked.text == sentence.text
    assert _apply_grounding(
        json.dumps(SAVED['saved_reviewer_response'], ensure_ascii=False), {35: '참'},
        {35: (sentence.text[len(attribution_prefix(ISSUE)):], {'84': ISSUE.text})},
        diagnostic_contexts={35: ('current_challenges', '문장', '')},
        claim_slots_by_number={35: 'current_challenges:issue'},
        source_fragments_by_id={'84': ISSUE},
    )[35] == '참'
    reviewed = replace(checked, verification_state='verified')
    report = ComposedReport((ComposedSection('current_challenges', (reviewed,)),))
    assert retain_verified_news(report, (ISSUE,)).sections[0].sentences == (reviewed,)


@pytest.mark.parametrize('text,expected', [
    ('제3자가 조회했다.', set()), ('제3자에게 넘어갔다.', set()),
    ('제3자 3명이 조회했다.', {'3명'}), ('3개 업체가 조회했다.', {'3개'}),
    ('제3분기에 조회했다.', {'3'}), ('3차 조사했다.', {'3'}),
    ('제13자가 조회했다.', {'13'}), ('제3자동화 공정이다.', {'3'}),
])
def test_고정어만_비수치이고_실제수량과_다른표현은_검사한다(text, expected):
    assert news_number_tokens(text, source_text=ISSUE.text) == expected


def test_자기인용에_third_party가_없으면_제3자_예외도_없다():
    sentence = _sentence('issue_candidate')
    unrelated = replace(ISSUE, text='Toss Payments confirmed a payment incident.')
    assert _numeric_disposal(sentence, {'84': unrelated}, ()) == '강등'
    assert news_number_tokens('제3자가 조회했다.', source_text=unrelated.text) == {'3'}


def test_한국어_원문_제3자의_숫자를_실제_인원수_근거로_쓰지않는다():
    source = replace(ISSUE, text='토스페이먼츠는 제3자가 결제 내역을 조회했다고 밝혔다.')
    sentence = ComposedSentence('제3자 3명이 조회했다.', ('84',), '확인')
    assert _numeric_disposal(sentence, {'84': source}, ()) != '통과'


def test_실제_별도_참판정_정확원문_대안은_왜곡문장_거절후_출고대상으로_보존한다():
    bad = _sentence('response_candidate')
    exact = ComposedSentence(attribution_prefix(RESPONSE) + RESPONSE.text, ('80',), '확인',
        planned_claim_slot='current_challenges:response')
    verdicts = _apply_grounding(
        json.dumps(SAVED['saved_reviewer_response'], ensure_ascii=False), {36: '참', 40: '참'},
        {36: (bad.text[len(attribution_prefix(RESPONSE)):], {'80': RESPONSE.text}),
         40: (RESPONSE.text, {'80': RESPONSE.text})},
        diagnostic_contexts={36: ('current_challenges', '문장', ''), 40: ('current_challenges', '문장', '')},
        claim_slots_by_number={36: 'current_challenges:response', 40: 'current_challenges:response'},
        source_fragments_by_id={'80': RESPONSE})
    assert verdicts[36] != '참' and verdicts[40] == '참'
    survivors = tuple(replace(sentence, verification_state='verified')
                      for number, sentence in ((36, bad), (40, exact)) if verdicts[number] == '참')
    report = ComposedReport((ComposedSection('current_challenges', survivors),))
    assert retain_verified_news(report, (RESPONSE,)).sections[0].sentences == survivors


def test_실제_보상주체를_수령자로_바꾼_참_판정도_결속검사에서_거절한다():
    sentence = _sentence('response_candidate')
    sources = {'80': RESPONSE.text}
    assert news_compensation_scope_problem(sentence.text, sources, {'80': RESPONSE}) == 'scope_condition_unbound'
    results = _apply_grounding(
        json.dumps(SAVED['saved_reviewer_response'], ensure_ascii=False), {36: '참'},
        {36: (sentence.text[len(attribution_prefix(RESPONSE)):], sources)},
        diagnostic_contexts={36: ('current_challenges', '문장', '')},
        source_fragments_by_id={'80': RESPONSE},
    )
    assert results[36] != '참'


@pytest.mark.parametrize('claim', [
    '가맹점은 쿠폰으로, 토스페이먼츠는 현금성 상품권으로 보상하고 있다.',
    SAVED['response_source']['text'],
])
def test_실제_각_보상주체와_정확_원문은_보존한다(claim):
    assert not news_compensation_scope_problem(claim, {'80': RESPONSE.text}, {'80': RESPONSE})


def test_명시_수령자와_다른_보상수단은_역할반전으로_판정하지않는다():
    source = '제조사는 쿠폰으로 보상하며 판매점에는 현금으로 보상한다.'
    fragment = replace(RESPONSE, text=source)
    assert not news_compensation_scope_problem('판매점에는 현금으로 보상한다.', {'80': source}, {'80': fragment})
    assert not news_compensation_scope_problem('제조사에는 현금으로 보상한다.', {'80': source}, {'80': fragment})
    assert news_compensation_scope_problem('제조사에는 쿠폰으로 보상한다.', {'80': source}, {'80': fragment})
