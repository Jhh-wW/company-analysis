"""실제15 완료 보도의 문장·원승인을 보존하고 시간 표시만 바로잡는다."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.completed_news_timing import completed_news_as_of
from src.features.composer.port import ComposedSentence
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.provenance.sources import Source, SourceKind
from src.shared.report_quality.fact_binding import fact_evidence_binding


SAVED = json.loads((Path(__file__).parent / 'fixtures/fifteenth_completed_news.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', SAVED['cases'])
def test_actual_approved_completion_preserves_claim_source_identity_and_unknown_event_day(case):
    old = case['fact']
    assert case['review_row']['결과'] == '참'
    assert case['reviewed_claim'] == old['claim']
    assert hashlib.sha256(case['quote'].encode()).hexdigest() in case['source']['exact_evidence_hashes']
    source = Source(**(case['source'] | {'kind': SourceKind.NEWS}))
    sentence = ComposedSentence(old['claim'], tuple(case['review_row']['근거']), '확인',
        verification_state='verified', planned_claim_slot=old['claim_slot'])
    result = evaluate_verified_prose_fact(sentence, section_id=old['section_owner'],
        company_name=old['legal_entity'], as_of_date=SAVED['baseline_date'],
        evidence=(ProseEvidence(str(source.number), source, case['quote']),),
        carry_official_exact_text=True)
    assert result.fact is not None and not result.reason_code
    old = asdict(type(result.fact)(**old))
    actual = asdict(result.fact)
    changed = {key for key in old.keys() | actual.keys() if old.get(key) != actual.get(key)}
    expected = {'as_of', 'evidence_binding'}
    if old['time_state'] != 'past':
        expected.add('time_state')
    assert changed == expected
    assert actual['time_state'] == 'past'
    assert actual['as_of'] == source.published_at
    assert actual['event_date'] == ''
    assert actual['evidence_binding'] == fact_evidence_binding(result.fact)


@pytest.mark.parametrize('quote', [
    '회사는 거래처와 업무협약을 체결할 예정이다.',
    '회사는 거래처와 업무협약을 체결하지 않았다.',
    '만약 회사는 거래처와 업무협약을 체결했다.',
    '회사는 거래처와 업무협약을 체결했다고 가정한다.',
    '회사는 현재 서비스를 운영하고 있으며 업무협약을 체결했다.',
    '회사는 서비스를 계속 운영하면서 업무협약을 체결했다.',
    '회사는 거래처와 업무협약을 체결했다. 현재 계약을 유지한다.',
    '회사는 사업자에게 필요한 마케팅 과제를 함께 풀어가는 자리를 이어가겠다고 말했다.',
    '회사는 결제 데이터를 사업자가 활용할 수 있는 인사이트로 바꾸는 데 힘쓰고 있다고 밝혔다.',
    '회사는 새로운 기술 도입을 검토했지만 기술을 도입했다.',
    '회사는 서비스를 운영 중이며 거래처와 업무협약을 체결했다.',
    '회사는 서비스를 제공 중이고 업무협약을 체결했다.',
    '회사는 서비스를 제공하는 한편 업무협약을 체결했다.',
    '회사는 서비스를 운영 중으로 거래처와 업무협약을 체결했다.',
    '회사는 서비스를 운영 중, 거래처와 업무협약을 체결했다.',
])
def test_future_conditional_denied_and_current_activity_do_not_become_completed_events(quote):
    assert completed_news_as_of(f'2026-06-18 매체 보도에 따르면, {quote}', quote=quote,
        publisher='매체', published_on='2026-06-18', baseline_date='2026-10-23') == ''


@pytest.mark.parametrize('publication,baseline', [
    ('', '2026-10-23'), ('2026-6-18', '2026-10-23'), ('2026-02-30', '2026-10-23'),
    ('2026-06-18', ''), ('2026-06-18', '2026-06-17'), ('2026-06-18', '2026-02-30'),
    (None, '2026-10-23'),
])
def test_missing_invalid_or_future_publication_does_not_supply_a_confirmation_date(publication, baseline):
    quote = '회사는 거래처와 업무협약을 체결했다.'
    assert completed_news_as_of(f'{publication} 매체 보도에 따르면, {quote}', quote=quote,
        publisher='매체', published_on=publication, baseline_date=baseline) == ''


@pytest.mark.parametrize('change', ['publisher', 'date', 'claim', 'quote'])
def test_only_its_exact_attributed_quote_supplies_the_confirmation_date(change):
    quote = '회사는 거래처와 업무협약을 체결했다.'
    values = dict(claim=f'2026-06-18 매체 보도에 따르면, {quote}', quote=quote,
        publisher='매체', published_on='2026-06-18', baseline_date='2026-10-23')
    key = {'publisher': 'publisher', 'date': 'published_on', 'claim': 'claim', 'quote': 'quote'}[change]
    values[key] = {'publisher': '다른매체', 'date': '2026-06-19',
        'claim': values['claim'] + ' 현재도 유지한다.', 'quote': '회사는 다른 거래처와 업무협약을 체결했다.'}[change]
    assert completed_news_as_of(**values) == ''


@pytest.mark.parametrize('prefix', [
    '다음은 가정이다. ', '현재 서비스를 운영 중이다. ', '조건이 충족될 경우다. ',
])
def test_selected_sentence_cannot_drop_conditional_or_current_context(prefix):
    body = '회사는 기술을 도입했다.'
    assert completed_news_as_of(f'2026-06-18 매체 보도에 따르면, {body}',
        quote=prefix + body, publisher='매체', published_on='2026-06-18',
        baseline_date='2026-10-23') == ''


@pytest.mark.parametrize('grade,verified', [('해석', 'verified'), ('확인', '')])
def test_time_label_never_creates_a_new_approval(grade, verified):
    case = SAVED['cases'][0]
    source = Source(**(case['source'] | {'kind': SourceKind.NEWS}))
    sentence = ComposedSentence(case['fact']['claim'], ('95',), grade,
        verification_state=verified, planned_claim_slot=case['fact']['claim_slot'])
    result = evaluate_verified_prose_fact(sentence, section_id='operations_partners',
        company_name=case['fact']['legal_entity'], as_of_date=SAVED['baseline_date'],
        evidence=(ProseEvidence('95', source, case['quote']),))
    if not verified:
        assert result.fact is None and result.reason_code == 'prose_not_verified'
    else:
        assert result.fact is not None and result.fact.as_of == SAVED['baseline_date']
