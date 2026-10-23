"""실제 7차의 종결형 활동을 검수부터 공개 Fact까지 무료 재생한다."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.core import news_intake_switch
from src.features.composer.future_plan_guard import future_plan_prose_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import render_report
from src.features.composer.verify import _ReviewItem, _recheck_rewritten
from src.features.provenance.sources import has_valid_provenance_seal
from src.features.report_standard.publish import _evidence_support_problems
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import document_identity_from_parts


SAVED = json.loads((Path(__file__).parent / 'fixtures/seventh_direct_future_plan.json').read_text(encoding='utf-8'))
SLOT = 'future_strategy:stated_plan'


def _fragment():
    article = SAVED['article']
    url = article['url']
    return CollectedFragment(
        '93', '뉴스', SAVED['source_text'], source_url=url,
        document_title=article['title'], document_date=article['indexed_published_on'],
        location='기사 본문 · news-fragment-3439525891bdeeb74df6',
        source_collected_on=SAVED['baseline_date'], source_document_id=url,
        document_identity=document_identity_from_parts(document_id=url, host='www.etnews.com', url=url),
        document_content_sha256=SAVED['body_sha256'], counts_toward_document_floor=False,
        supported_claim_slots=(SLOT,), formal_source_kind='news', source_publisher=article['publisher'],
        news_claim_kind='company_plan', news_temporal_status='planned',
        news_source_category='news_report', news_grounded=True,
    )


def _rewritten_item(number):
    entry = next(x for x in SAVED['rewrite_entries'] if x['번호'] == number)
    return _ReviewItem(number, ComposedSentence(entry['글'], ('93',), '확인',
        planned_claim_slot=SLOT), 'future_strategy')


def _response(entries):
    return json.dumps({'판정': entries}, ensure_ascii=False)


@pytest.fixture
def _news_intake_on(monkeypatch):
    # 앞 시험의 OFF 동결값은 환경변수 변경만으로 바뀌지 않는다.
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, '1')
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


@pytest.mark.parametrize('number', [43, 44])
def test_실제_재작성과_참응답_불변으로_최종_fact와_출처까지_복구된다(_news_intake_on, number):
    original = deepcopy(SAVED)
    fragment = _fragment()
    item = _rewritten_item(number)
    calls, diagnostics, final = [], [], {}

    def saved_review(prompt):
        calls.append(prompt)
        return _response(SAVED['review_entries'])

    verdicts = _recheck_rewritten(saved_review, [item], {'93': fragment}, '', '', final,
        diagnostics=diagnostics, baseline_date=SAVED['baseline_date'])
    assert len(calls) == 1 and not diagnostics and verdicts[number] == '참'
    sentence = final[number]
    assert sentence.text == item.sentence.text
    assert sentence.citations == ('93',) and sentence.verification_state == 'verified'
    report = render_report(SAVED['company_name'],
        ComposedReport((ComposedSection('future_strategy', (sentence,)),)), (fragment,), None,
        generated_at=SAVED['baseline_date'], as_of_date=SAVED['baseline_date'], company_id='01492697')
    section = next(s for s in report.sections if s.cell == 'future_strategy')
    assert section.prose_lines == [(sentence.text + ' [93]', '')]
    assert len(report.fact_records) == 1
    fact = report.fact_records[0]
    assert fact.claim == sentence.text and fact.claim_slot == SLOT
    assert fact.time_state == 'future' and fact.verification_status == 'verified'
    assert fact.evidence_binding == fact_evidence_binding(fact)
    assert not _evidence_support_problems(fact)
    assert fact.fact_id in section.fact_ids
    source = report.citations[0]
    assert has_valid_provenance_seal(source)
    assert source.document_content_sha256 == SAVED['body_sha256']
    assert source.exact_evidence_hashes == [SAVED['quote_sha256']]
    assert source.url == SAVED['article']['url'] and source.published_at == '2026-10-21'
    assert hashlib.sha256(fragment.text.encode()).hexdigest() == SAVED['quote_sha256']
    assert len(fragment.text) == SAVED['quote']['end'] - SAVED['quote']['start']
    assert SAVED == original


def test_최초_인용형은_복구하되_대상을_바꾼_첫_초안은_계속_거절한다():
    results = []
    for index, number in enumerate((43, 44)):
        claim = SAVED['writer_items'][index]['글']
        entry = next(x for x in SAVED['initial_review_entries'] if x['번호'] == number)
        results.append(future_plan_prose_problem(claim, {'93': SAVED['source_text']},
            entry['검증근거'], claim_slot=SLOT))
    assert results == ['future_plan_target_not_in_candidate', '']


@pytest.mark.parametrize('change', ['source', 'target', 'activity', 'verdict', 'proof'])
def test_실제_증명이나_자기원문이_달라지면_참을_재사용할수없다(change):
    fragment = _fragment()
    entries = deepcopy(SAVED['review_entries'])
    entry = next(x for x in entries if x['번호'] == 44)
    if change == 'source':
        fragment = replace(fragment, text=fragment.text.replace('이어가겠다', '중단했다'))
    elif change == 'target':
        entry['검증근거']['미래근거'][0]['대상'] = '새로운 해외 공장'
    elif change == 'activity':
        entry['검증근거']['미래근거'][0]['활동'] = '설립하겠다'
    elif change == 'verdict':
        entry['결과'] = '거짓'
    else:
        entry['검증근거'] = {}
    final = {}
    _recheck_rewritten(lambda _: _response(entries), [_rewritten_item(44)], {'93': fragment},
        '', '', final, baseline_date=SAVED['baseline_date'])
    assert final[44] is None


def _problem(source, candidate, activity='확대하겠다', target='상담 서비스를'):
    evidence = {'미래근거': [{'근거': 'a', '대상': target, '활동': activity,
        '원문': source, '양태': '계획'}]}
    return future_plan_prose_problem(candidate, {'a': source}, evidence, claim_slot=SLOT)


@pytest.mark.parametrize('activity', ['확대하겠다', '확대하겠습니다', '확대해 나가겠다', '이어 가겠다'])
def test_같은_활동의_직접_의지_종결형만_인식한다(activity):
    source = f'회사는 상담 서비스를 {activity}고 밝혔다.'
    assert not _problem(source, source, activity)


@pytest.mark.parametrize('suffix', [
    '는 계획을 철회했다', '던 계획을 완료했다', '고 말하지 않았다',
    '고 말했다는 사실은 없다', '고 경쟁사가 말했다', '는 기자의 전망이다',
    '고 말했지만 현재는 계획이 없다',
])
def test_철회_부인_과거_타사_전망을_직접_계획으로_구제하지않는다(suffix):
    assert _problem(f'회사는 상담 서비스를 확대하겠다{suffix}.',
        '회사는 상담 서비스를 확대하겠다고 밝혔다.')


def test_활동_앞_조건과_주체를_제거한_후보는_계속_거절한다():
    assert _problem('회사는 허가를 받으면 상담 서비스를 확대하겠다고 밝혔다.',
        '회사는 상담 서비스를 확대하겠다고 밝혔다.')
    assert _problem('경쟁사는 상담 서비스를 확대하겠다고 밝혔다.',
        '회사는 상담 서비스를 확대하겠다고 밝혔다.')


@pytest.mark.parametrize('followup, denied', [
    ('하지만 이 계획은 철회했다.', True),
    ('그러나 해당 계획을 취소했다.', True),
    ('이후 그 계획은 폐기되었다.', True),
    ('하지만 다른 계획은 철회했다.', False),
    ('다른 회사는 계획을 철회했다.', False),
    ('회사는 별도 개발을 완료했다.', False),
    ('다른 사업을 설명했다. 이 계획은 철회했다.', False),
])
def test_바로_다음_문장의_명시적_동일계획_철회만_결속한다(followup, denied):
    candidate = '회사는 상담 서비스를 확대하겠다고 밝혔다.'
    problem = _problem(candidate + ' ' + followup, candidate)
    assert bool(problem) is denied
    if denied:
        assert problem == 'future_plan_denied_in_source'


def test_표에도_같은_원문의_명시적_철회_경계를_적용한다():
    from src.features.composer.future_plan_guard import future_plan_problem
    candidate = '회사는 상담 서비스를 확대하겠다고 밝혔다.'
    source = candidate + ' 하지만 이 계획은 철회했다.'
    evidence = {'미래근거': [{'근거': 'a', '대상': '상담 서비스를', '활동': '확대하겠다',
        '원문': candidate, '양태': '계획'}]}
    assert future_plan_problem((candidate,), {'a': source}, evidence) == 'future_plan_denied_in_source'


@pytest.mark.parametrize('activity', ['확대', '운영'])
def test_현재_활동_뒤_별도_미래계획은_빌려오지못한다(activity):
    assert _problem(f'회사는 상담 서비스를 {activity}하고 있으며 해외 지점을 개설하겠다고 밝혔다.',
        f'회사는 상담 서비스를 {activity}할 계획이다.', activity)
