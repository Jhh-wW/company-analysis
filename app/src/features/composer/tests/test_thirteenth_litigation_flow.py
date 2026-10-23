"""실제13의 동일 원문·동일 참 응답을 후단에서 소비하며 새 승인을 만들지 않는다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.direct_support_constants import FLOW_CELL_JOIN
from src.features.composer.flow_review_binding import filter_reviewed_flow_rows
from src.features.composer.grounding import grounding_problem
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, FlowRow, filing_meta_from_raw,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_seventh_litigation import _final_legacy_filter
from src.features.composer.verify import _apply_grounding, verify_report


SAVED = json.loads((Path(__file__).parent / 'fixtures/thirteenth_litigation_flow.json')
                   .read_text(encoding='utf-8'))
SOURCE, PERIOD, COMPANY = SAVED['source'], SAVED['reporting_period'], SAVED['source_publisher']
CELLS = tuple(SAVED['flow']['칸'])
TEXT = FLOW_CELL_JOIN.join(CELLS)


def fragment():
    return CollectedFragment('6', '소송·분쟁', SOURCE,
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        source_document_id=SAVED['document_id'], reporting_period=PERIOD,
        source_publisher=COMPANY)


def problem(text=TEXT, row=None, source=SOURCE, period=PERIOD, publisher=COMPANY):
    return grounding_problem(text, {'6': source}, row or SAVED['review_flow'],
        litigation_sources=((source, period, publisher),))


def test_actual_thirteenth_unchanged_numeric_proof_reaches_post_review():
    before = deepcopy(SAVED)
    for field in ('source', 'writer_response', 'full_review_response'):
        assert hashlib.sha256(SAVED[field].encode()).hexdigest() == SAVED[field + '_sha256']
    assert SAVED['review_flow']['결과'] == '참'
    assert problem() == ''
    issues = {}
    result = _apply_grounding(SAVED['full_review_response'], {64: '참'},
        {64: (TEXT, {'6': SOURCE})},
        diagnostic_contexts={64: ('current_challenges', '도식', TEXT)},
        flow_cells_by_number={64: CELLS}, source_fragments_by_id={'6': fragment()},
        grounding_problems=issues, baseline_date='2026-10-23')
    assert result[64] == '참'
    assert 64 not in issues
    assert SAVED == before


def test_same_flow_review_receipt_render_and_final_filter_keep_snapshot():
    # 저장 DTO 전체 재구성이 아닌 후단 계약 시험이다. 새 모델 승인으로 해석하지 않는다.
    original = FlowRow(CELLS, ('6',))
    report = ComposedReport((ComposedSection('current_challenges', (), flow_rows=(original,)),))
    calls = []

    def saved_review(prompt):
        assert SOURCE in prompt and PERIOD in prompt and COMPANY in prompt
        calls.append(prompt)
        item, = review_items(prompt)
        assert item.kind == '도식' and item.section == 'current_challenges'
        assert item.citations == ('6',) and all(cell in item.text for cell in CELLS)
        # 기술 시험의 항목 번호만 대응한다. 원형 주장·참 판정·수치 증명은 바꾸지 않는다.
        return json.dumps({'판정': [dict(SAVED['review_flow'], 번호=item.number)]}, ensure_ascii=False)

    checked = verify_report(report, (fragment(),), None, saved_review,
        baseline_date='2026-10-23', allow_sentence_rewrite=False,
        allowed_fragment_ids_by_section={'current_challenges': frozenset({'6'})})
    assert len(calls) == 1 and checked.sections[0].flow_rows == (original,)
    assert original.review_binding is None
    assert checked.sections[0].flow_rows[0].review_binding is not None
    assert filter_reviewed_flow_rows(checked, (fragment(),), baseline_date='2026-10-23') == checked
    public = render_report(COMPANY, checked, (fragment(),), None, as_of_date='2026-10-23',
        filing_meta=filing_meta_from_raw({'rcept_no': SAVED['document_id'], 'rcept_dt': '20260331',
                                        'report_nm': '감사보고서 (2025.12)'}))
    assert public.sections[0].tables[0].rows == [list(CELLS)]
    source, = public.citations
    assert source.reporting_period == PERIOD and source.document_id == SAVED['document_id']
    final = _final_legacy_filter(public)
    assert final.sections[0].tables == public.sections[0].tables


@pytest.mark.parametrize('verdict,proof', [('거짓', True), ('참', False)])
def test_false_or_absent_original_proof_is_not_promoted(verdict, proof):
    row = dict(SAVED['review_flow'], 결과=verdict,
        검증근거=SAVED['review_flow']['검증근거'] if proof else {})
    result = _apply_grounding(json.dumps({'판정': [row]}, ensure_ascii=False), {64: verdict},
        {64: (TEXT, {'6': SOURCE})},
        diagnostic_contexts={64: ('current_challenges', '도식', TEXT)},
        flow_cells_by_number={64: CELLS}, source_fragments_by_id={'6': fragment()},
        baseline_date='2026-10-23')
    assert result[64] != '참'


@pytest.mark.parametrize('prefix,valid', [
    ('다른회사가 ', False), ('한빛회사는 ', False), ('가상 예시: ', False),
    ('자회사의 ', False), ('협력회사가 ', False),
    ('토스페이먼츠는 ', True), ('토스페이먼츠 주식회사가 ', True),
])
def test_trailing_date_compact_subject_is_bound_to_own_publisher(prefix, valid):
    cells = (prefix + CELLS[0], CELLS[1])
    text = FLOW_CELL_JOIN.join(cells)
    assert (problem(text) == '') is valid
    result = _apply_grounding(SAVED['full_review_response'], {64: '참'},
        {64: (text, {'6': SOURCE})},
        diagnostic_contexts={64: ('current_challenges', '도식', text)},
        flow_cells_by_number={64: cells}, source_fragments_by_id={'6': fragment()},
        baseline_date='2026-10-23')
    assert (result[64] == '참') is valid


@pytest.mark.parametrize('surface', ['피고 소송', '피고 계류 소송', '피고 계류중 소송',
    '피고 계류 중인 소송사건', '피고  계류  소송'])
@pytest.mark.parametrize('reverse', [False, True])
def test_equivalent_pending_count_surface_keeps_role_period_and_proof(surface, reverse):
    # 합성 표현 경계의 순수 결속 검사이며 새 모델의 참 판정을 뜻하지 않는다.
    row = deepcopy(SAVED['review_flow'])
    row['검증근거']['수치'][1]['표현'] = surface + ' 9건'
    if reverse:
        row['검증근거']['수치'].reverse()
    assert problem(TEXT.replace('피고 계류 소송', surface), row) == ''


@pytest.mark.parametrize('change', [
    'role', 'count', 'unit', 'amount', 'currency', 'period', 'source_period',
    'source_company', 'count_metric', 'duplicate_proof', 'upper_bound', 'extension',
])
def test_changed_role_period_company_value_metric_or_duplicate_stays_rejected(change):
    text, source, period = TEXT, SOURCE, PERIOD
    row = deepcopy(SAVED['review_flow'])
    amount, count = row['검증근거']['수치']
    if change == 'role':
        text = text.replace('피고', '원고'); count['표현'] = count['표현'].replace('피고', '원고')
    elif change == 'count':
        text = text.replace('9건', '8건'); count.update(표현='피고 계류 소송 8건', 후보값='8건')
    elif change == 'unit':
        text = text.replace('9건', '9명'); count.update(표현='피고 계류 소송 9명', 후보값='9명')
    elif change == 'amount':
        text = text.replace('4억원', '5억원'); amount.update(표현='소송금액 약 5억원', 후보값='약 5억원')
    elif change == 'currency':
        text = text.replace('4억원', '4억달러'); amount.update(표현='소송금액 약 4억달러', 후보값='약 4억달러')
    elif change == 'period':
        text = text.replace('2025년', '2024년')
    elif change == 'source_period':
        period = '2024-01-01/2024-12-31'
    elif change == 'source_company':
        source = source.replace('당기말 현재 회사가', '당기말 현재 다른회사가')
        count['원문'] = count['원문'].replace('당기말 현재 회사가', '당기말 현재 다른회사가')
    elif change == 'count_metric':
        count.update(항목='매출 건수', 원문항목='매출 건수')
    elif change == 'duplicate_proof':
        row['검증근거']['수치'].append(deepcopy(count))
    elif change == 'upper_bound':
        text = text.replace('9건', '9건 이상'); count['표현'] += ' 이상'
    elif change == 'extension':
        text = text.replace('2025년 12월 31일 기준', '2025년 12월 31일 이후에도 현재')
    assert problem(text, row, source, period)
