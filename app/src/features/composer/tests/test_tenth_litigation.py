"""실제10 동일 원문·판정을 소비하며 소송 건수 범주와 절 경계를 검증한다."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.direct_support_constants import FLOW_CELL_JOIN
from src.features.composer.grounding import grounding_problem
from src.features.composer.litigation_timing import litigation_numeric_text
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
    filing_meta_from_raw,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_seventh_litigation import _final_legacy_filter
from src.features.composer.verify import _apply_grounding, verify_report
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches


SAVED = json.loads((Path(__file__).parent / 'fixtures/tenth_litigation.json').read_text(encoding='utf-8'))
SOURCE, PERIOD, COMPANY = SAVED['source'], SAVED['reporting_period'], SAVED['source_publisher']
BODY, CELLS = SAVED['body']['글'], tuple(SAVED['flow']['칸'])
SECTION, SLOT = 'current_challenges', 'current_challenges:issue'


def fragment(**changes):
    return replace(CollectedFragment('6', '소송·분쟁', SOURCE,
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        source_document_id=SAVED['document_id'], reporting_period=PERIOD,
        source_publisher=COMPANY), **changes)


def problem(text=BODY, row=None, source=SOURCE, period=PERIOD, publisher=COMPANY):
    return grounding_problem(text, {'6': source}, row or SAVED['review_body'],
        litigation_sources=((source, period, publisher),))


def test_original_full_response_keeps_both_original_numbers_without_proof_changes():
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == SAVED['source_sha256']
    assert hashlib.sha256(SAVED['full_review_response'].encode()).hexdigest() == SAVED['full_review_response_sha256']
    assert hashlib.sha256(SAVED['writer_response'].encode()).hexdigest() == SAVED['writer_response_sha256']
    before = deepcopy(SAVED)
    flow = FLOW_CELL_JOIN.join(CELLS)
    issues = {}
    verdicts = _apply_grounding(SAVED['full_review_response'], {34: '참', 64: '참'},
        {34: (BODY, {'6': SOURCE}), 64: (flow, {'6': SOURCE})},
        diagnostic_contexts={34: (SECTION, '본문', BODY), 64: (SECTION, '도식', flow)},
        claim_slots_by_number={34: SLOT}, flow_cells_by_number={64: CELLS},
        source_fragments_by_id={'6': fragment()}, grounding_problems=issues,
        baseline_date='2026-10-23')
    assert {number: verdicts[number] for number in (34, 64)} == {34: '참', 64: '참'}
    assert not any(number in issues for number in (34, 64))
    # 전체 응답 안의 다른 뉴스 receipt는 이 소송 단위 요청으로 승인하지 않는다.
    assert verdicts.get(36) != '참'
    assert SAVED == before


def test_same_claim_review_render_fact_and_final_filter_preserve_snapshot():
    sentence = ComposedSentence(BODY, ('6',), '확인', planned_claim_slot=SLOT)
    report = ComposedReport((ComposedSection(SECTION, (sentence,), flow_rows=(FlowRow(CELLS, ('6',)),)),))
    requests = []

    def captured_review(prompt):
        # 번호만 이번 단위 요청에 대응한다. 동일 원문·주장·기간 외의 후보는 받지 않는다.
        assert SOURCE in prompt and PERIOD in prompt and COMPANY in prompt
        requests.append(prompt)
        rows = []
        for item in review_items(prompt):
            assert item.section == SECTION and item.citations == ('6',)
            if item.kind == '문장':
                assert item.text == BODY
                proof = SAVED['review_body']
            else:
                assert all(cell in item.text for cell in CELLS)
                proof = SAVED['review_flow']
            rows.append(dict(proof, 번호=item.number))
        assert len(rows) == 2
        return json.dumps({'판정': rows}, ensure_ascii=False)

    checked = verify_report(report, (fragment(),), None, captured_review,
        baseline_date='2026-10-23', allowed_fragment_ids_by_section={SECTION: frozenset({'6'})},
        allow_sentence_rewrite=False)
    assert len(requests) == 1
    assert [s.text for s in checked.sections[0].sentences] == [BODY]
    assert [row.cells for row in checked.sections[0].flow_rows] == [CELLS]
    public = render_report(COMPANY, checked, (fragment(),), None, as_of_date='2026-10-23',
        filing_meta=filing_meta_from_raw({'rcept_no': SAVED['document_id'], 'rcept_dt': '20260331',
                                        'report_nm': '감사보고서 (2025.12)'}))
    fact, = public.fact_records
    source, = public.citations
    assert (fact.claim, fact.time_state, fact.as_of) == (BODY, 'past', '2025-12-31')
    assert source.reporting_period == PERIOD and source.document_id == SAVED['document_id']
    assert fact_primary_source_metadata_mismatches(fact, source) == ()
    assert public.sections[0].tables[0].rows == [list(CELLS)]
    final = _final_legacy_filter(public)
    assert final.fact_records == public.fact_records
    assert final.sections[0].tables == public.sections[0].tables


@pytest.mark.parametrize('label', ['소송사건', '소송사건 건수', '피고 소송 건수',
    '피고 소송사건 수', '피고로 계류 중인 소송사건 건수', '계류 소송 건수',
    '계류 중 소송 건수', '계류중인 소송사건 수'])
def test_count_category_is_bound_to_actual_role_not_arbitrary_metric(label):
    row = deepcopy(SAVED['review_body'])
    row['검증근거']['수치'][0]['항목'] = label
    row['검증근거']['수치'][0]['원문항목'] = '피고 소송 건수'
    assert problem(row=row) == ''


@pytest.mark.parametrize('label', ['원고 소송 건수', '피고 소송금액', '피고 보험사건 건수',
    '피고 소송 승소 건수', '피고 소송 예상 건수', '과거 소송 건수', '소송사건 건수 비율',
    '총 소송비용', '', ['피고 소송 건수']])
def test_other_role_or_metric_is_not_a_count_alias(label):
    for key in ('항목', '원문항목'):
        row = deepcopy(SAVED['review_body'])
        row['검증근거']['수치'][0][key] = label
        assert problem(row=row)


@pytest.mark.parametrize('flow', [False, True])
@pytest.mark.parametrize('change', ['role', 'count', 'money', 'period', 'persistent',
    'source_role', 'source_count', 'source_company', 'source_period', 'quote_other_sentence',
    'quote_not_exact', 'other_source_id', 'count_bound', 'money_bound', 'foreign_currency'])
def test_source_identity_role_period_value_and_quote_are_not_weakened(flow, change):
    text = FLOW_CELL_JOIN.join(CELLS) if flow else BODY
    row = deepcopy(SAVED['review_flow'] if flow else SAVED['review_body'])
    source, period = SOURCE, PERIOD
    if change == 'role': text = text.replace('피고', '원고')
    elif change == 'count': text = text.replace('9건', '8건')
    elif change == 'money': text = text.replace('4억원', '7억원')
    elif change == 'period': period = '2024-01-01/2024-12-31'
    elif change == 'persistent': text += ' 이후에도 계류 중이다.'
    elif change == 'source_role': source = source.replace('피고', '원고')
    elif change == 'source_count': source = source.replace('9건', '8건')
    elif change == 'source_company': source = source.replace('현재 회사가', '현재 경쟁회사가')
    elif change == 'source_period': source = source.replace('당기말 현재', '전기말 현재')
    elif change == 'quote_other_sentence':
        row['검증근거']['수치'][0]['원문'] += ' 계류중인 소송사건 등에 대해서는 소송의 결과를 합리적으로 예측할 수 없음에 따라 관련 충당부채를 계상하지 아니하였습니다.'
    elif change == 'quote_not_exact': row['검증근거']['수치'][0]['원문'] = row['검증근거']['수치'][0]['원문'].replace('9건으로', '9건;')
    elif change == 'other_source_id': row['검증근거']['수치'][0]['근거'] = '다른문서'
    elif change == 'count_bound': source = source.replace('9건으로', '9건 이상으로')
    elif change == 'money_bound': source = source.replace('405,535천원입니다', '405,535천원 이하입니다')
    else: source = source.replace('405,535천원', '405,535천달러')
    assert problem(text, row, source, period)


def test_foreign_publisher_and_numeric_proof_reuse_remain_rejected():
    assert problem(publisher='다른회사 주식회사')
    for index in (0, 1):
        row = deepcopy(SAVED['review_body'])
        extra = deepcopy(row['검증근거']['수치'][index])
        if '원문참조' in extra:
            extra['원문'] = row['검증근거']['수치'][0]['원문']
            del extra['원문참조']
        row['검증근거']['수치'].append(extra)
        assert problem(row=row)
    row = deepcopy(SAVED['review_body'])
    row['검증근거']['수치'][0]['표현'] = '9건'
    assert problem(row=row)


def test_same_length_date_mask_does_not_borrow_another_metrics_amount():
    text = BODY + ' 보험료는 약 4억원이다.'
    masked = litigation_numeric_text(text, ((SOURCE, PERIOD, COMPANY),), preserve_offsets=True)
    assert len(masked) == len(text) and masked.index('소송금액') == text.index('소송금액')
    assert problem(text=text)
    row = deepcopy(SAVED['review_body'])
    row['검증근거']['수치'].append({'표현': '보험료는 약 4억원', '항목': '보험료', '근거': '6',
        '원문': '2024년 보험료는 405,535천원입니다.', '원문항목': '보험료',
        '원문값': '405,535천원', '후보값': '약 4억원'})
    assert problem(text, row, SOURCE + ' 2024년 보험료는 405,535천원입니다.')


@pytest.mark.parametrize('role', ['피고', '원고'])
@pytest.mark.parametrize('reverse_entries', [False, True])
@pytest.mark.parametrize('quote_period', [False, True])
def test_consistent_roles_quote_terminator_and_proof_order_are_equivalent(role, reverse_entries, quote_period):
    text, source = BODY.replace('피고', role), SOURCE.replace('피고', role)
    row = deepcopy(SAVED['review_body'])
    entries = row['검증근거']['수치']
    count, money = entries
    count['항목'] = count['원문항목'] = f'{role} 소송 건수'
    count['원문'] = count['원문'].replace('피고', role)
    if not quote_period:
        count['원문'] = count['원문'].removesuffix('.')
    if reverse_entries:
        money['원문'] = count['원문']
        del money['원문참조']
        entries.reverse()
    assert problem(text, row, source) == ''


@pytest.mark.parametrize('change', ['negative_count', 'negative_money', 'count_percent',
    'count_people', 'money_ratio', 'reverse_quote_role', 'same_amount_other_metric'])
def test_sign_unit_role_and_metric_changes_never_share_a_proof(change):
    text, row = BODY, deepcopy(SAVED['review_body'])
    count, money = row['검증근거']['수치']
    if change == 'negative_count':
        text = text.replace('9건', '-9건')
        count['표현'] = count['표현'].replace('9건', '-9건')
        count['후보값'] = '-9건'
    elif change == 'negative_money':
        text = text.replace('4억원', '-4억원')
        money['표현'] = money['표현'].replace('4억원', '-4억원')
        money['후보값'] = '약 -4억원'
    elif change in ('count_percent', 'count_people'):
        replacement = '9%' if change == 'count_percent' else '9명'
        text = text.replace('9건', replacement)
        count['표현'] = count['표현'].replace('9건', replacement)
        count['후보값'] = replacement
    elif change == 'money_ratio':
        text = text.replace('4억원', '4%')
        money['표현'] = money['표현'].replace('4억원', '4%')
        money['후보값'] = '약 4%'
    elif change == 'reverse_quote_role': count['원문'] = count['원문'].replace('피고', '원고')
    else:
        text = text.replace('소송금액', '보험금액')
        money['표현'] = money['표현'].replace('소송금액', '보험금액')
        money['항목'] = money['원문항목'] = '보험금액'
    assert problem(text, row)


def test_false_actual_shape_is_never_promoted():
    rows = [dict(SAVED['review_body'], 결과='거짓'), dict(SAVED['review_flow'], 결과='거짓')]
    flow = FLOW_CELL_JOIN.join(CELLS)
    verdicts = _apply_grounding(json.dumps({'판정': rows}, ensure_ascii=False), {34: '거짓', 64: '거짓'},
        {34: (BODY, {'6': SOURCE}), 64: (flow, {'6': SOURCE})},
        diagnostic_contexts={34: (SECTION, '본문', BODY), 64: (SECTION, '도식', flow)},
        flow_cells_by_number={64: CELLS}, source_fragments_by_id={'6': fragment()},
        baseline_date='2026-10-23')
    assert verdicts == {34: '거짓', 64: '거짓'}


@pytest.mark.parametrize('flow', [False, True])
@pytest.mark.parametrize('terminator', ['?', '!'])
@pytest.mark.parametrize('quote_keeps_terminator', [False, True])
def test_nonassertive_source_is_not_promoted_by_clipping_its_terminator(flow, terminator, quote_keeps_terminator):
    text = FLOW_CELL_JOIN.join(CELLS) if flow else BODY
    row = deepcopy(SAVED['review_flow'] if flow else SAVED['review_body'])
    source = SOURCE.replace('405,535천원입니다.', f'405,535천원입니다{terminator}')
    entry = row['검증근거']['수치'][0]
    entry['원문'] = entry['원문'].removesuffix('.') + (terminator if quote_keeps_terminator else '')
    assert problem(text, row, source)


@pytest.mark.parametrize('change', ['role', 'count'])
@pytest.mark.parametrize('quote_period', [False, True])
def test_amount_only_flow_cannot_skip_its_explicit_role_and_count(change, quote_period):
    text = FLOW_CELL_JOIN.join(CELLS).replace('피고', '원고') if change == 'role' else FLOW_CELL_JOIN.join(CELLS).replace('9건', '8건')
    row = deepcopy(SAVED['review_flow'])
    if not quote_period:
        row['검증근거']['수치'][0]['원문'] = row['검증근거']['수치'][0]['원문'].removesuffix('.')
    assert problem(text, row)
