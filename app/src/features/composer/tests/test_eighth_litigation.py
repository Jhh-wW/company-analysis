"""실제 8차 건수 이름표를 같은 소송 원문에 결속하며 승인 범위는 늘리지 않는다."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.grounding import grounding_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, filing_meta_from_raw
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_seventh_litigation import _final_legacy_filter
from src.features.composer.verify import _apply_grounding, verify_report
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches


SAVED = json.loads((Path(__file__).parent / 'fixtures/eighth_litigation.json').read_text(encoding='utf-8'))
BODY, SOURCE = SAVED['body']['글'], SAVED['source']
PERIOD, COMPANY = SAVED['reporting_period'], SAVED['source_publisher']
SLOT = 'current_challenges:issue'


def fragment(**changes):
    original = CollectedFragment('6', '소송·분쟁', SOURCE,
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        source_document_id='20260331000296', reporting_period=PERIOD, source_publisher=COMPANY)
    return replace(original, **changes)


def problem(body=BODY, source=SOURCE, row=None, *, period=PERIOD, publisher=COMPANY):
    return grounding_problem(body, {'6': source}, row or SAVED['review_body'],
        litigation_sources=((source, period, publisher),))


def test_actual_entire_review_response_is_consumed_unchanged_for_original_39():
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == SAVED['source_sha256']
    assert hashlib.sha256(SAVED['full_review_response'].encode()).hexdigest() == SAVED['full_review_response_sha256']
    before = deepcopy(SAVED)
    issues = {}
    verdicts = _apply_grounding(SAVED['full_review_response'], {39: '참'}, {39: (BODY, {'6': SOURCE})},
        diagnostic_contexts={39: ('current_challenges', '본문', BODY)},
        claim_slots_by_number={39: SLOT}, source_fragments_by_id={'6': fragment()},
        grounding_problems=issues, baseline_date='2026-10-23')
    assert verdicts[39] == '참' and not issues
    assert SAVED == before


def test_same_claim_review_render_fact_and_final_filter_keep_2025_snapshot():
    sentence = ComposedSentence(BODY, ('6',), '확인', planned_claim_slot=SLOT)
    report = ComposedReport((ComposedSection('current_challenges', (sentence,)),))
    calls = []
    before = deepcopy(SAVED['review_body'])

    def saved_review(prompt):
        # 실제 전체 응답의 번호39 검증은 위 시험. 이 부분은 같은 주장만 요청 번호에 대응한다.
        assert BODY in prompt and SOURCE in prompt and PERIOD in prompt and COMPANY in prompt
        items = review_items(prompt)
        assert len(items) == 1 and items[0].text == BODY
        calls.append(prompt)
        return json.dumps({'판정': [dict(SAVED['review_body'], 번호=items[0].number)]}, ensure_ascii=False)

    checked = verify_report(report, (fragment(),), None, saved_review, baseline_date='2026-10-23',
        allowed_fragment_ids_by_section={'current_challenges': frozenset({'6'})}, allow_sentence_rewrite=False)
    assert len(calls) == 1 and [s.text for s in checked.sections[0].sentences] == [BODY]
    public = render_report(COMPANY, checked, (fragment(),), None, as_of_date='2026-10-23',
        filing_meta=filing_meta_from_raw({'rcept_no': '20260331000296', 'rcept_dt': '20260331', 'report_nm': '감사보고서 (2025.12)'}))
    fact, = public.fact_records
    source, = public.citations
    assert (fact.claim, fact.time_state, fact.as_of) == (BODY, 'past', '2025-12-31')
    assert source.reporting_period == PERIOD and source.publisher == COMPANY
    assert fact_primary_source_metadata_mismatches(fact, source) == ()
    final = _final_legacy_filter(public)
    assert final.fact_records == public.fact_records
    assert final.sections[0].prose_lines == public.sections[0].prose_lines
    assert SAVED['review_body'] == before


@pytest.mark.parametrize('change', [
    'other_metric', 'wrong_count', 'wrong_unit', 'wrong_money', 'other_role',
    'other_company', 'other_period', 'missing_date', 'persistent_claim',
    'foreign_source_subject', 'source_count', 'quote_role', 'quote_punctuation',
    'other_expression', 'duplicate_count', 'duplicate_money', 'missing_money',
    'bad_metric_type', 'bare_count', 'source_id',
])
def test_count_label_never_releases_other_metric_role_period_number_or_proof(change):
    row = deepcopy(SAVED['review_body'])
    count, money = row['검증근거']['수치']
    body, source, period = BODY, SOURCE, PERIOD
    if change == 'other_metric':
        count['항목'] = count['원문항목'] = '상담사건 건수'
    elif change == 'wrong_count':
        body = body.replace('9건', '8건')
        count['후보값'] = count['원문값'] = '8건'
    elif change == 'wrong_unit':
        body = body.replace('9건', '9명')
        count['후보값'] = '9명'
    elif change == 'wrong_money':
        body = body.replace('약 4억원', '약 7억원')
        money['후보값'] = '약 7억원'
    elif change == 'other_role':
        body = body.replace('피고', '원고')
    elif change == 'other_company':
        body = body.replace('토스페이먼츠', '다른회사')
    elif change == 'other_period':
        period = '2024-01-01/2024-12-31'
    elif change == 'missing_date':
        body = body.replace('2025년 12월 31일 기준', '당기말 현재')
    elif change == 'persistent_claim':
        body += ' 이후에도 계류 중이다.'
    elif change == 'foreign_source_subject':
        source = source.replace('현재 회사가', '현재 경쟁회사가')
    elif change == 'source_count':
        source = source.replace('9건', '8건')
    elif change == 'quote_role':
        count['원문'] = count['원문'].replace('피고', '원고')
    elif change == 'quote_punctuation':
        count['원문'] = count['원문'].replace('9건으로', '9건;')
    elif change == 'other_expression':
        body += ' 보험사건은 9건이다.'
        count['표현'] = '보험사건은 9건'
    elif change == 'duplicate_count':
        row['검증근거']['수치'].append(deepcopy(count))
    elif change == 'duplicate_money':
        row['검증근거']['수치'].append(deepcopy(money))
    elif change == 'missing_money':
        row['검증근거']['수치'].pop()
    elif change == 'bad_metric_type':
        count['항목'] = ['소송사건 건수']
    elif change == 'bare_count':
        count['후보값'] = count['원문값'] = '9'
    else:
        count['근거'] = money['근거'] = '조각 6'
    assert problem(body, source, row, period=period)


def test_other_metric_period_and_same_value_duplicate_are_not_waived():
    body = BODY + ' 보험료는 약 4억원이다.'
    source = SOURCE + ' 2024년 보험료는 405,535천원입니다.'
    row = deepcopy(SAVED['review_body'])
    row['검증근거']['수치'].append({'표현': '보험료는 약 4억원', '항목': '보험료', '근거': '6',
        '원문': '2024년 보험료는 405,535천원입니다.', '원문항목': '보험료',
        '원문값': '405,535천원', '후보값': '약 4억원'})
    assert problem(body, source, row)
    source = source.replace('2024년 보험료', '2025년 보험료')
    row['검증근거']['수치'][-1]['원문'] = '2025년 보험료는 405,535천원입니다.'
    assert problem(body, source, row) == ''
    row['검증근거']['수치'][-1] = deepcopy(row['검증근거']['수치'][1])
    assert problem(body, source, row)


@pytest.mark.parametrize('bound', ['이하', '이상', '미만', '초과'])
def test_count_bounds_are_not_normalized_as_exact_cases(bound):
    row = deepcopy(SAVED['review_body'])
    source = SOURCE.replace('9건으로', f'9건 {bound}로')
    row['검증근거']['수치'][0]['원문'] = row['검증근거']['수치'][0]['원문'].replace('9건으로', f'9건 {bound}로')
    assert problem(source=source, row=row)
    row = deepcopy(SAVED['review_body'])
    body = BODY.replace('9건이며', f'9건 {bound}이며')
    row['검증근거']['수치'][0]['표현'] = row['검증근거']['수치'][0]['표현'].replace('9건이며', f'9건 {bound}이며')
    assert problem(body=body, row=row)


def test_false_verdict_remains_false_after_same_source_numeric_binding():
    row = deepcopy(SAVED['review_body'])
    row['결과'] = '거짓'
    verdicts = _apply_grounding(json.dumps({'판정': [row]}, ensure_ascii=False), {39: '거짓'},
        {39: (BODY, {'6': SOURCE})}, diagnostic_contexts={39: ('current_challenges', '본문', BODY)},
        source_fragments_by_id={'6': fragment()}, baseline_date='2026-10-23')
    assert verdicts[39] == '거짓'
