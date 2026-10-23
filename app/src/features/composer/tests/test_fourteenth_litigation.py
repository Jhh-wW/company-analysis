"""실제14의 기준일 뒤 쉼표와 정확 자기 법인·기말·소송 수치를 대조한다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.direct_support_constants import FLOW_CELL_JOIN
from src.features.composer.grounding import grounding_problem
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow, filing_meta_from_raw,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_seventh_litigation import _final_legacy_filter
from src.features.composer.verify import _apply_grounding, verify_report
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches


SAVED = json.loads((Path(__file__).parent / 'fixtures/fourteenth_litigation.json').read_text(encoding='utf-8'))
SOURCE, PERIOD, COMPANY = SAVED['source'], SAVED['reporting_period'], SAVED['source_publisher']
BODY, CELLS = SAVED['body']['글'], tuple(SAVED['flow']['칸'])
SECTION, SLOT = 'current_challenges', 'current_challenges:issue'


def fragment():
    return CollectedFragment('6', '소송·분쟁', SOURCE, source_document_id=SAVED['document_id'],
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        reporting_period=PERIOD, source_publisher=COMPANY)


def problem(text=BODY, *, proof=None, source=SOURCE, period=PERIOD, publisher=COMPANY):
    return grounding_problem(text, {'6': source}, SAVED['review_body'] if proof is None else proof,
        litigation_sources=((source, period, publisher),))


def test_original_actual39_and63_response_is_consumed_without_any_proof_change():
    before = deepcopy(SAVED)
    for field in ('source', 'writer_response', 'full_review_response'):
        assert hashlib.sha256(SAVED[field].encode()).hexdigest() == SAVED[field + '_sha256']
    issues = {}
    result = _apply_grounding(SAVED['full_review_response'], {39: '참', 63: '참'},
        {39: (BODY, {'6': SOURCE}), 63: (FLOW_CELL_JOIN.join(CELLS), {'6': SOURCE})},
        diagnostic_contexts={39: (SECTION, '본문', '해석'), 63: (SECTION, '도식', '확인')},
        claim_slots_by_number={39: SLOT}, flow_cells_by_number={63: CELLS},
        source_fragments_by_id={'6': fragment()}, grounding_problems=issues, baseline_date='2026-10-23')
    assert {n: result[n] for n in (39, 63)} == {39: '참', 63: '참'}
    assert 39 not in issues and 63 not in issues and SAVED == before


def test_same_body_and_flow_reach_fact_date_render_and_final_filter():
    sentence = ComposedSentence(BODY, ('6',), '해석', planned_claim_slot=SLOT)
    report = ComposedReport((ComposedSection(SECTION, (sentence,), flow_rows=(FlowRow(CELLS, ('6',)),)),))
    calls = []

    def saved_review(prompt):
        calls.append(prompt)
        assert SOURCE in prompt and PERIOD in prompt and COMPANY in prompt
        rows = []
        for item in review_items(prompt):
            assert item.section == SECTION and item.citations == ('6',)
            if item.kind == '문장':
                assert item.text == BODY
                entry = SAVED['review_body']
            else:
                assert all(cell in item.text for cell in CELLS)
                entry = SAVED['review_flow']
            # 단위 기술 DTO의 번호 대응이며 실제 전체 요청을 재실행한 것은 아니다.
            rows.append(dict(entry, 번호=item.number))
        assert len(rows) == 2
        return json.dumps({'판정': rows}, ensure_ascii=False)

    checked = verify_report(report, (fragment(),), None, saved_review, baseline_date='2026-10-23',
        allowed_fragment_ids_by_section={SECTION: frozenset({'6'})}, allow_sentence_rewrite=False)
    assert len(calls) == 1 and [s.text for s in checked.sections[0].sentences] == [BODY]
    assert [r.cells for r in checked.sections[0].flow_rows] == [CELLS]
    public = render_report(COMPANY, checked, (fragment(),), None, as_of_date='2026-10-23',
        filing_meta=filing_meta_from_raw({'rcept_no': SAVED['document_id'], 'rcept_dt': '20260331',
                                        'report_nm': '감사보고서 (2025.12)'}))
    fact, = public.fact_records
    source, = public.citations
    assert (fact.claim, fact.time_state, fact.as_of) == (BODY, 'past', '2025-12-31')
    assert source.reporting_period == PERIOD and fact_primary_source_metadata_mismatches(fact, source) == ()
    assert public.sections[0].tables[0].rows == [list(CELLS)]
    final = _final_legacy_filter(public)
    assert final.fact_records == public.fact_records and final.sections[0].tables == public.sections[0].tables


@pytest.mark.parametrize('company', ['토스페이먼츠', '토스페이먼츠 주식회사', '주식회사 토스페이먼츠'])
@pytest.mark.parametrize('separator', [', ', ',  ', ' '])
def test_same_date_and_normalized_company_accept_only_boundary_punctuation(company, separator):
    text = BODY.replace('기준, 토스페이먼츠가', '기준' + separator + company + '가')
    assert problem(text) == ''


@pytest.mark.parametrize('change', ['other_company', 'empty_publisher', 'other_publisher',
    'other_period', 'other_date', 'plaintiff', 'count', 'amount', 'currency', 'continued',
    'separate_sentence', 'hypothetical', 'other_source_company', 'duplicate_proof'])
def test_original_true_does_not_cover_changed_subject_period_or_numbers(change):
    text, source, period, publisher = BODY, SOURCE, PERIOD, COMPANY
    proof = deepcopy(SAVED['review_body'])
    if change == 'other_company': text = text.replace('토스페이먼츠', '한빛회사')
    elif change == 'empty_publisher': publisher = ''
    elif change == 'other_publisher': publisher = '한빛회사'
    elif change == 'other_period': period = '2024-01-01/2024-12-31'
    elif change == 'other_date': text = text.replace('2025년', '2024년')
    elif change == 'plaintiff': text = text.replace('피고로', '원고로')
    elif change == 'count': text = text.replace('9건', '8건')
    elif change == 'amount': text = text.replace('4억원', '5억원')
    elif change == 'currency': text = text.replace('4억원', '4억달러')
    elif change == 'continued': text = text.replace('기준,', '이후에도 현재,')
    elif change == 'separate_sentence': text = text.replace('기준,', '기준이다. 현재')
    elif change == 'hypothetical': text = text.replace('기준,', '기준, 가상 사례로')
    elif change == 'other_source_company': source = source.replace('현재 회사가', '현재 자회사가')
    else: proof['검증근거']['수치'].append(deepcopy(proof['검증근거']['수치'][0]))
    assert problem(text, proof=proof, source=source, period=period, publisher=publisher)


@pytest.mark.parametrize('verdict,proof', [('거짓', True), ('참', False)])
def test_false_or_absent_proof_is_never_promoted(verdict, proof):
    entry = dict(SAVED['review_body'], 결과=verdict,
        검증근거=SAVED['review_body']['검증근거'] if proof else {})
    result = _apply_grounding(json.dumps({'판정': [entry]}, ensure_ascii=False), {39: verdict},
        {39: (BODY, {'6': SOURCE})}, diagnostic_contexts={39: (SECTION, '본문', '해석')},
        claim_slots_by_number={39: SLOT}, source_fragments_by_id={'6': fragment()}, baseline_date='2026-10-23')
    assert result[39] != '참'


@pytest.mark.parametrize('prefix', ['가상 예시: ', '한빛회사는 '])
def test_context_before_dated_named_subject_is_not_silently_dropped(prefix):
    text = prefix + BODY
    assert problem(text)
    result = _apply_grounding(SAVED['full_review_response'], {39: '참'},
        {39: (text, {'6': SOURCE})}, diagnostic_contexts={39: (SECTION, '본문', '해석')},
        claim_slots_by_number={39: SLOT}, source_fragments_by_id={'6': fragment()},
        baseline_date='2026-10-23')
    assert result[39] != '참'
