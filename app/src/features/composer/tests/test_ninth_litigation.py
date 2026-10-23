"""실제 9차 미검수 소송 후보를 새 검수 경로에 보내는 기술 회귀."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.absence_claim_guard import absence_claim_problem
from src.features.composer.diagram_check import check_diagram_numbers
from src.features.composer.constants import FLOW_HEADERS_BY_SECTION
from src.features.composer.litigation_draft import prepare_litigation_draft
from src.features.composer.logic import _compose_one_section
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow, filing_meta_from_raw
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_seventh_litigation import _final_legacy_filter
from src.features.composer.verify import verify_report
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches

SAVED = json.loads((Path(__file__).parent / 'fixtures/ninth_litigation.json').read_text(encoding='utf-8'))
SOURCE, PERIOD, COMPANY = SAVED['source'], SAVED['reporting_period'], SAVED['source_publisher']
SECTION, SLOT = 'current_challenges', 'current_challenges:issue'
ORIGINAL = SAVED['body']['글']
EXPECTED = '2025년 12월 31일 기준 회사가 피고로 계류 중인 소송사건은 9건이며 소송금액은 약 4억원 규모이다.'
CELLS = tuple(SAVED['flow']['칸'])


def fragment(**changes):
    return replace(CollectedFragment('6', '소송·분쟁', SOURCE,
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        source_document_id='20260331000296', reporting_period=PERIOD, source_publisher=COMPANY), **changes)


def sentence(text=ORIGINAL, **changes):
    return replace(ComposedSentence(text, ('6',), '확인', planned_claim_slot=SLOT), **changes)


def prepare(item=None, frag=None, company=COMPANY):
    return prepare_litigation_draft(item or sentence(), {'6': frag or fragment()}, company_name=company)


def test_actual_writer_response_only_prepares_unsigned_fact_and_preserves_original():
    assert not SAVED['actual_review_approval']
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == SAVED['source_sha256']
    assert hashlib.sha256(SAVED['writer_response'].encode()).hexdigest() == SAVED['writer_response_sha256']
    assert absence_claim_problem(ORIGINAL)
    result = _compose_one_section(SECTION, '실제 저장 입력 재생', lambda _: SAVED['writer_response'],
        source_fragments=(fragment(),), company_name=COMPANY)
    candidate, = [s for s in result.sentences if s.citations == ('6',)]
    assert candidate.text == EXPECTED and not absence_claim_problem(candidate.text)
    assert candidate.verification_state == 'unverified' and candidate.structured_claim is None
    assert not candidate.verified_fact_id and candidate.citations == ('6',)
    assert candidate.planned_claim_slot == SLOT and ORIGINAL == SAVED['body']['글']
    assert result.flow_rows[0].cells == CELLS and result.flow_rows[0].review_binding is None


@pytest.mark.parametrize('text, expected', [
    (ORIGINAL.replace('규모이나,', '규모이며,'), EXPECTED),
    (ORIGINAL.replace('규모이나,', '규모이다.'), EXPECTED),
    (ORIGINAL.replace('약 4억원 규모이나', '405,535천원이나'), EXPECTED.replace('약 4억원 규모', '405,535천원')),
    (ORIGINAL.replace('회사', '토스페이먼츠 주식회사', 1), EXPECTED.replace('회사', '토스페이먼츠 주식회사', 1)),
    ('2025년 12월 31일 기준 회사가 피고로 계류 중인 소송사건은 9건이다. 공시 자료에는 사업상 제약 설명이 없다.',
     '2025년 12월 31일 기준 회사가 피고로 계류 중인 소송사건은 9건이다.'),
])
def test_closed_snapshot_survives_other_absence_commentary_forms(text, expected):
    assert prepare(sentence(text)).text == expected


@pytest.mark.parametrize('change', [
    'wrong_role', 'wrong_count', 'wrong_money', 'count_as_money', 'wrong_unit', 'wrong_company',
    'other_publisher', 'other_target', 'other_period', 'no_period', 'no_document', 'no_date',
    'persistent', 'after_date', 'conditional', 'hypothetical_source', 'foreign_source',
    'different_metric', 'wrong_slot', 'reviewed', 'sealed_id', 'no_citation', 'multiple_citations',
    'bound_count', 'bound_money', 'source_count_bound', 'source_amount_bound', 'source_condition',
])
def test_unsupported_or_already_reviewed_claim_is_never_repaired(change):
    original, frag, company = sentence(), fragment(), COMPANY
    if change == 'wrong_role': original = replace(original, text=ORIGINAL.replace('피고', '원고'))
    elif change == 'wrong_count': original = replace(original, text=ORIGINAL.replace('9건', '99건'))
    elif change == 'wrong_money': original = replace(original, text=ORIGINAL.replace('4억원', '7억원'))
    elif change == 'count_as_money': original = replace(original, text=ORIGINAL.replace('약 4억원', '9원'))
    elif change == 'wrong_unit': original = replace(original, text=ORIGINAL.replace('4억원', '4만달러'))
    elif change == 'wrong_company': original = replace(original, text=ORIGINAL.replace('회사가', '다른회사가', 1))
    elif change == 'other_publisher': frag = replace(frag, source_publisher='다른회사 주식회사')
    elif change == 'other_target': company = '다른회사 주식회사'
    elif change == 'other_period': frag = replace(frag, reporting_period='2024-01-01/2024-12-31')
    elif change == 'no_period': frag = replace(frag, reporting_period='')
    elif change == 'no_document': frag = replace(frag, source_document_id='')
    elif change == 'no_date': original = replace(original, text=ORIGINAL.replace('2025년 12월 31일 기준', '당기말 현재'))
    elif change == 'persistent': original = replace(original, text=ORIGINAL + ' 이후에도 계류 중이다.')
    elif change == 'after_date': original = replace(original, text=ORIGINAL.replace('기준 회사', '기준일을 지나 현재 회사'))
    elif change == 'conditional': original = replace(original, text='만약 ' + ORIGINAL)
    elif change == 'hypothetical_source': frag = replace(frag, text='가상의 예시입니다. ' + SOURCE)
    elif change == 'foreign_source': frag = replace(frag, text=SOURCE.replace('현재 회사가', '현재 경쟁회사가'))
    elif change == 'different_metric': original = replace(original, text=ORIGINAL.replace('소송금액', '매출액'))
    elif change == 'wrong_slot': original = replace(original, planned_claim_slot='current_challenges:response')
    elif change == 'reviewed': original = replace(original, verification_state='verified')
    elif change == 'sealed_id': original = replace(original, verified_fact_id='already-approved')
    elif change == 'no_citation': original = replace(original, citations=())
    elif change == 'multiple_citations': original = replace(original, citations=('6', 'other'))
    elif change == 'bound_count': original = replace(original, text=ORIGINAL.replace('9건이며', '9건 이상이며'))
    elif change == 'bound_money': original = replace(original, text=ORIGINAL.replace('규모이나', '이하이나'))
    elif change == 'source_count_bound': frag = replace(frag, text=SOURCE.replace('9건으로', '9건 이상으로'))
    elif change == 'source_amount_bound': frag = replace(frag, text=SOURCE.replace('405,535천원입니다', '405,535천원 이하입니다'))
    else: frag = replace(frag, text=SOURCE.replace('405,535천원입니다', '405,535천원이 될 수도 있습니다'))
    assert prepare(original, frag, company) == original


def test_supported_plain_sentence_and_other_chapter_are_unchanged():
    plain = sentence(EXPECTED)
    assert prepare(plain) == plain
    other = sentence(planned_claim_slot='identity:corporate_identity')
    assert prepare(other) == other


def test_actual_flow_period_enters_review_without_altering_cells_or_receipt():
    row = FlowRow(CELLS, ('6',))
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))
    checked, errors = check_diagram_numbers(report, (fragment(),))
    assert not errors and checked.sections[0].flow_rows == (row,)
    assert checked.sections[0].flow_rows[0].review_binding is None


@pytest.mark.parametrize('change', ['other_period','no_period','wrong_date','wrong_role','wrong_count',
    'persistent','other_sentence_date','other_section','other_source_id'])
def test_date_mask_cannot_lend_year_to_other_claims_or_sources(change):
    cells, frag, section, ids = CELLS, fragment(), SECTION, ('6',)
    if change == 'other_period': frag = replace(frag, reporting_period='2024-01-01/2024-12-31')
    elif change == 'no_period': frag = replace(frag, reporting_period='')
    elif change == 'wrong_date': cells = (CELLS[0].replace('2025년', '2026년'), CELLS[1])
    elif change == 'wrong_role': cells = (CELLS[0].replace('피고', '원고'), CELLS[1])
    elif change == 'wrong_count': cells = (CELLS[0].replace('9건', '2025건'), CELLS[1])
    elif change == 'persistent': cells = (CELLS[0], CELLS[1] + ' / 현재까지 계류 중')
    elif change == 'other_sentence_date': cells = (CELLS[0].replace('계류 중 (', '계류 중. 다른 문서 ('), CELLS[1])
    elif change == 'other_section': section = 'past_changes'
    else:
        ids = ('other',)
        frag = replace(frag, fragment_id='other', text='당기말 현재 회사가 피고로 계류중인 소송사건은 1건이다.')
    report = ComposedReport((ComposedSection(section, (), flow_rows=(FlowRow(cells, ids),)),))
    checked, _ = check_diagram_numbers(report, (frag,))
    assert not checked.sections[0].flow_rows


def _fresh_test_review(prompt, verdict='참'):
    """기술 시험대역. 실제9에는 이 후보의 모델 승인 응답이 없다."""
    assert SOURCE in prompt and PERIOD in prompt
    result=[]
    for item in review_items(prompt):
        assert item.citations == ('6',) and item.section == SECTION
        flow_text = ' '.join(f'{label}: {cell}' for label, cell in zip(FLOW_HEADERS_BY_SECTION[SECTION], CELLS))
        assert item.text == (EXPECTED if item.kind == '문장' else flow_text)
        proof={}
        if item.kind == '문장':
            proof={'수치':[
                {'표현':'소송사건은 9건이며', '항목':'소송사건 건수', '근거':'6',
                 '원문':'피고로 계류중인 소송사건은 9건으로 소송금액은 405,535천원입니다',
                 '원문항목':'소송사건 건수','원문값':'9건','후보값':'9건'},
                {'표현':'소송금액은 약 4억원 규모','항목':'소송금액','근거':'6',
                 '원문':'소송금액은 405,535천원입니다','원문항목':'소송금액','원문값':'405,535천원','후보값':'약 4억원'}]}
        result.append({'번호':item.number,'장':SECTION,'근거':['6'],'결과':verdict,
                       '근거대조':'기술 시험: 동일 자기 원문의 소송 현황과 정본 기간 대조','검증근거':proof})
    return json.dumps({'판정':result},ensure_ascii=False)


@pytest.mark.parametrize('verdict', ['참','거짓'])
def test_new_review_is_required_through_body_flow_fact_and_final_filter(verdict):
    prepared=prepare()
    report=ComposedReport((ComposedSection(SECTION,(prepared,),flow_rows=(FlowRow(CELLS,('6',)),)),))
    calls=[]
    def reviewer(prompt):
        calls.append(prompt)
        return _fresh_test_review(prompt,verdict)
    checked=verify_report(report,(fragment(),),None,reviewer,baseline_date='2026-10-23',
        allowed_fragment_ids_by_section={SECTION:frozenset({'6'})},allow_sentence_rewrite=False)
    assert len(calls)==1
    if verdict=='거짓':
        assert not checked.sections[0].sentences and not checked.sections[0].flow_rows
        return
    assert [s.text for s in checked.sections[0].sentences]==[EXPECTED]
    assert [r.cells for r in checked.sections[0].flow_rows]==[CELLS]
    public=render_report(COMPANY,checked,(fragment(),),None,as_of_date='2026-10-23',
        filing_meta=filing_meta_from_raw({'rcept_no':'20260331000296','rcept_dt':'20260331','report_nm':'감사보고서 (2025.12)'}))
    fact,=public.fact_records
    source,=public.citations
    assert (fact.claim,fact.time_state,fact.as_of)==(EXPECTED,'past','2025-12-31')
    assert source.reporting_period==PERIOD and not fact_primary_source_metadata_mismatches(fact,source)
    assert public.sections[0].tables[0].rows==[list(CELLS)]
    final=_final_legacy_filter(public)
    assert final.fact_records==public.fact_records and final.sections[0].tables==public.sections[0].tables
