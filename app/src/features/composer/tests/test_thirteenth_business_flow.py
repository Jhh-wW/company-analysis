"""실제13의 원형 표 승인과 수취인·공제·지급의 좁은 의미 경계를 검증한다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.flow_review_binding import filter_reviewed_flow_rows
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.render import render_report
from src.features.composer.role_binding import role_binding_problem
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import _apply_grounding, verify_report


SAVED = json.loads((Path(__file__).parent / 'fixtures/thirteenth_business_flow.json').read_text(encoding='utf-8'))
BASELINE = '2026-10-23'


def row(number):
    value = SAVED['rows'][str(number)]
    return FlowRow(tuple(value['칸']), tuple(value['인용']))


def entry(number):
    return deepcopy(next(item for item in SAVED['review_entries'] if item['번호'] == number))


def fragments():
    # 저장 평문으로 만든 기술 DTO이며 실제 typed 입력 전체 재구성은 아니다.
    return tuple(CollectedFragment(fid, '공시', text, document_date='2026-03-31',
        document_identity='document:dart.fss.or.kr:20260331000296')
        for fid, text in SAVED['sources'].items())


def guard(number=62, *, cells=None, proof=None, source=None):
    candidate = row(number)
    cells = candidate.cells if cells is None else tuple(cells)
    return role_binding_problem(' → '.join(cells),
        {fid: source if source is not None else SAVED['sources'][fid] for fid in candidate.citations},
        entry(number)['검증근거'] if proof is None else proof, cells=cells)


def test_actual_original_table_approvals_are_consumed_without_rewriting():
    before = deepcopy(SAVED)
    rows = {n: row(n) for n in (61, 62)}
    problems = {}
    result = _apply_grounding(json.dumps({'판정': SAVED['review_entries']}, ensure_ascii=False),
        {n: '참' for n in rows},
        {n: (' → '.join(r.cells), {fid: SAVED['sources'][fid] for fid in r.citations})
         for n, r in rows.items()},
        diagnostic_contexts={n: ('business_model', '도식', '확인') for n in rows},
        flow_cells_by_number={n: r.cells for n, r in rows.items()}, grounding_problems=problems)
    assert result == {61: '참', 62: '참'} and not problems
    assert SAVED == before
    assert all(hashlib.sha256(text.encode()).hexdigest() == SAVED['source_sha256'][fid]
               for fid, text in SAVED['sources'].items())


def test_original_table_approval_through_verify_render_and_receipt_filter():
    rows = (row(61), row(62))
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=rows),))
    calls = []

    def saved_table_review(prompt):
        calls.append(prompt)
        submitted = []
        for item in review_items(prompt):
            number = 61 if item.citations == rows[0].citations else 62
            original = row(number)
            assert original.cells[2] in item.text and item.kind == '도식'
            assert item.citations == original.citations
            # 독립 기술 재현의 항목 번호만 옮긴다. 승인 문구와 근거는 원형이다.
            submitted.append(dict(entry(number), 번호=item.number))
        return json.dumps({'판정': submitted}, ensure_ascii=False)

    checked = verify_report(report, fragments(), None, saved_table_review, baseline_date=BASELINE,
        allowed_fragment_ids_by_section={'business_model': frozenset(SAVED['sources'])},
        allow_sentence_rewrite=False)
    assert len(calls) == 1 and checked.sections[0].flow_rows == rows
    assert all(r.review_binding is not None for r in checked.sections[0].flow_rows)
    assert all(r.review_binding is None for r in rows)
    assert filter_reviewed_flow_rows(checked, fragments(), baseline_date=BASELINE) == checked
    public = render_report('토스페이먼츠 주식회사', checked, fragments(), None, as_of_date=BASELINE)
    tables = [t for s in public.sections for t in s.tables]
    assert any('수수료 차감 후 가맹점 지급' in str(t.rows) for t in tables)
    assert any('부가가치세 차감 후 가맹점에 지급' in str(t.rows) for t in tables)


@pytest.mark.parametrize('number', [61, 62])
@pytest.mark.parametrize('verdict,proof', [('거짓', True), ('참', False)])
def test_false_or_absent_original_table_proof_is_not_promoted(number, verdict, proof):
    candidate = row(number)
    item = dict(entry(number), 결과=verdict, 검증근거=entry(number)['검증근거'] if proof else {})
    result = _apply_grounding(json.dumps({'판정': [item]}, ensure_ascii=False), {number: verdict},
        {number: (' → '.join(candidate.cells), {f: SAVED['sources'][f] for f in candidate.citations})},
        diagnostic_contexts={number: ('business_model', '도식', '확인')},
        flow_cells_by_number={number: candidate.cells})
    assert result[number] != '참'


@pytest.mark.parametrize('old,new', [
    ('원천사가 회사에 대금 지급', '회사가 원천사에 대금 지급'),
    ('가맹점에 지급', '은행에 지급'), ('가맹점에 지급', '원천사에 지급'),
    ('수수료·부가가치세', '수수료'), ('수수료·부가가치세', '배송수수료·부가가치세'),
    ('차감 후', '차감하지 않고'), ('차감 후', '차감할 계획으로'),
    ('차감 후', '계약이 체결된 경우에만 차감 후'),
    ('가맹점에 지급', '회사 매출로 인식'),
])
def test_changed_direction_recipient_fees_condition_or_revenue_is_rejected(old, new):
    original = row(62)
    assert old in original.cells[2]
    assert guard(cells=(*original.cells[:2], original.cells[2].replace(old, new), ''))


@pytest.mark.parametrize('old,new', [
    ('수수료 차감 후', '수수료·부가가치세 차감 후'),
    ('수수료 차감 후', '수수료가 아니라 부가가치세 차감 후'),
    ('가맹점 지급', '가맹점이 회사에 지급'),
    ('거래승인 시점에', '계약이 체결된 경우 거래승인 시점에'),
])
def test_short_writer_action_does_not_borrow_other_fees_or_direction(old, new):
    original = row(61)
    assert old in original.cells[2]
    assert guard(61, cells=(*original.cells[:2], original.cells[2].replace(old, new), ''))


@pytest.mark.parametrize('field,value', [
    ('대상', '은행'), ('대상', '다른회사의 가맹점'), ('근거', '다른번호'),
    ('원문', '회사의 가맹점으로 수수료와 부가가치세를 제외하여 지급하고 있습니다.'),
    ('유형', '역할'), ('역할값', '수수료 차감 후 가맹점 지급'),
    ('역할값', '수수료·부가가치세 차감 후 은행 지급'),
])
def test_changed_own_proof_or_partial_quote_does_not_prove_full_company_relation(field, value):
    proof = entry(62)['검증근거']
    proof['관계'][0][field] = value
    assert guard(proof=proof)


@pytest.mark.parametrize('old,new', [
    ('회사는 전자지불', '한빛회사는 전자지불'),
    ('회사는 전자지불', '회사는 계약을 체결한 경우 전자지불'),
    ('원천사로부터 대금을 받은 후', '회사가 원천사에 대금을 지급한 후'),
    ('회사의 가맹점으로', '회사의 은행으로'),
    ('수수료와 부가가치세', '수수료'),
    ('지급하고 있습니다', '지급하지 않습니다'),
])
def test_modified_source_and_quote_cannot_support_original_settlement(old, new):
    source = SAVED['sources']['90'].replace(old, new)
    proof = entry(62)['검증근거']
    proof['관계'][0]['원문'] = proof['관계'][0]['원문'].replace(old, new)
    assert guard(source=source, proof=proof)


@pytest.mark.parametrize('extra', ['부가가치세는 회사 매출', '로열티 수취', '타사가 운영'])
def test_other_cell_cannot_borrow_same_approval(extra):
    assert guard(cells=(*row(62).cells[:3], extra))


@pytest.mark.parametrize('number', [61, 62])
@pytest.mark.parametrize('fees', ['수수료와 부가가치세', '수수료 및 부가가치세'])
def test_closed_same_fee_connectors_preserve_role_relation(number, fees):
    proof = entry(number)['검증근거']
    cells = list(row(number).cells)
    if number == 62:
        proof['관계'][0]['역할값'] = proof['관계'][0]['역할값'].replace('수수료·부가가치세', fees)
        cells[2] = cells[2].replace('수수료·부가가치세', fees)
    assert not guard(number, cells=cells, proof=proof)


def test_merchant_proof_does_not_cover_another_product_or_another_action_cell():
    assert guard(cells=('', '회계 프로그램', row(62).cells[2], ''))
    assert guard(cells=('다른회사 PG 서비스', *row(62).cells[1:]))
    assert guard(cells=('', 'PG 서비스', row(62).cells[2], row(62).cells[2]))
