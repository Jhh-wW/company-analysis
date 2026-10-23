"""실제10 표검수를 그대로 소비하며 정산 의미와 설립목적의 경계를 검증한다."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.diagram_check import check_diagram_numbers
from src.features.composer.diagram_review_constants import FLOW_ACCOUNTING_REVENUE_CODE
from src.features.composer.flow_review_binding import filter_reviewed_flow_rows
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.render import render_report
from src.features.composer.role_binding import role_binding_problem
from src.features.composer.scope_guard import flow_scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import _apply_grounding, verify_report

SAVED = json.loads((Path(__file__).parent / 'fixtures/tenth_business_flow.json').read_text(encoding='utf-8'))
BASELINE = '2026-10-23'


def row(number):
    value = SAVED['rows'][str(number)]
    return FlowRow(tuple(value['칸']), tuple(value['인용']))


def entry(number):
    return deepcopy(next(item for item in SAVED['review_entries'] if item['번호'] == number))


def fragments():
    # 보존 평문을 소비하는 시험 DTO다. 이 메타를 실제 typed 원입력 재구성으로 주장하지 않는다.
    return tuple(CollectedFragment(fid, '공시', text, document_date='2026-03-31',
        document_identity='document:dart.fss.or.kr:20260331000296') for fid, text in SAVED['sources'].items())


def guard(number=62, *, cells=None, proof=None, source=None):
    candidate = row(number)
    cells = candidate.cells if cells is None else cells
    return role_binding_problem(' → '.join(cells),
        {fid: source if source is not None else SAVED['sources'][fid] for fid in candidate.citations},
        entry(number)['검증근거'] if proof is None else proof, cells=cells)


def test_actual_supplement_review_keeps_both_pg_rows_but_foundation_only_operations_is_rejected():
    before = deepcopy(SAVED)
    rows = {n: row(n) for n in (61, 62, 67)}
    problems = {}
    result = _apply_grounding(json.dumps({'판정': SAVED['review_entries']}, ensure_ascii=False),
        {n: '참' for n in rows},
        {n: (' → '.join(r.cells), {fid: SAVED['sources'][fid] for fid in r.citations}) for n, r in rows.items()},
        diagnostic_contexts={n: ('business_model' if n != 67 else 'operations_partners', '도식', '확인') for n in rows},
        flow_cells_by_number={n: r.cells for n, r in rows.items()}, grounding_problems=problems)
    assert result[61] == result[62] == '참' and result[67] != '참'
    assert problems[67] == SCOPE_CONDITION_UNBOUND
    assert SAVED == before
    assert all(hashlib.sha256(text.encode()).hexdigest() == SAVED['source_sha256'][fid]
               for fid, text in SAVED['sources'].items())


def test_actual_pg_table_review_to_render_and_receipt_filter_without_new_approval():
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
            # 단독 재현의 번호만 매핑한다. 원후보·원문·표 판정·검증근거는 그대로다.
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
    assert any('가맹점' in str(t.rows) and '수수료' in str(t.rows) for s in public.sections for t in s.tables)


@pytest.mark.parametrize('number', [61, 62])
@pytest.mark.parametrize('verdict,proof', [('거짓', True), ('참', False)])
def test_false_or_missing_own_table_evidence_still_blocks(number, verdict, proof):
    candidate = row(number)
    item = dict(entry(number), 결과=verdict, 검증근거=entry(number)['검증근거'] if proof else {})
    result = _apply_grounding(json.dumps({'판정': [item]}, ensure_ascii=False), {number: verdict},
        {number: (' → '.join(candidate.cells), {f: SAVED['sources'][f] for f in candidate.citations})},
        diagnostic_contexts={number: ('business_model', '도식', '확인')}, flow_cells_by_number={number: candidate.cells})
    assert result[number] != '참'


@pytest.mark.parametrize('old,new', [
    ('원천사로부터', '가맹점으로부터'), ('가맹점에', '원천사에'),
    ('가맹점에', '은행에'), ('수수료·부가가치세', '수수료'),
    ('수수료·부가가치세', '배송수수료·부가가치세'),
    ('제외 지급', '회사 수익으로 인식'), ('수취 후', '수취하지 않은 후'),
    ('수취 후', '수취할 계획으로'), ('수취 후', '계약이 체결된 경우에만 수취 후'),
])
def test_changed_receipt_direction_fees_negation_or_condition_is_not_covered(old, new):
    candidate = row(61)
    assert old in candidate.cells[2]
    assert guard(61, cells=(*candidate.cells[:2], candidate.cells[2].replace(old, new), ''))


@pytest.mark.parametrize('field,value', [
    ('대상', '다른회사 PG 서비스'), ('대상', '회사의 다른 사업'),
    ('근거', '다른번호'), ('원문', '회사가 가맹점에 수수료를 제외하고 지급합니다.'),
    ('유형', '역할'), ('역할값', '수수료를 제외하여 지급'),
    ('역할값', '수수료와 부가가치세를 회사 수익으로 인식'),
    ('역할값', '수수료와 부가가치세를 제외하여 은행에 지급'),
])
def test_submitted_target_kind_quote_or_deduction_cannot_be_changed(field, value):
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
def test_changed_source_and_matching_quote_do_not_prove_original_graph(old, new):
    source = SAVED['sources']['90'].replace(old, new)
    proof = entry(62)['검증근거']
    proof['관계'][0]['원문'] = proof['관계'][0]['원문'].replace(old, new)
    assert guard(source=source, proof=proof)


def test_receipt_proof_only_covers_receipt_and_deduction_only_covers_deduction():
    proof = entry(61)['검증근거']
    for item in proof['관계']:
        assert guard(61, proof={'관계': [item]})
    assert not guard(61)
    for extra in ('부가가치세는 회사 매출', '로열티 수취', '타사가 운영'):
        assert guard(cells=(*row(62).cells[:3], extra))


@pytest.mark.parametrize('role', [
    '수수료와 부가가치세를 제외하여 지급하고 있습니다',
    '수수료 및 부가가치세 차감 후 지급한다',
    '가맹점으로 수수료와 부가가치세를 제외하여 지급하고 있다',
    '수수료·부가가치세 차감 후 가맹점에 지급',
])
def test_same_deduction_relation_is_not_tied_to_one_recipient_position_or_ending(role):
    # 실제 표 증명의 의미만 달리 적은 합성 대조다. 새 실제 검수 승인으로 쓰지 않는다.
    proof = entry(62)['검증근거']
    proof['관계'][0]['역할값'] = role
    assert not guard(proof=proof)


@pytest.mark.parametrize('receipt', ['대금 수취 후', '거래금액을 받은 후', '대금을 받은 후'])
@pytest.mark.parametrize('deduction', ['수수료와 부가가치세를 제외하여 지급', '수수료 및 부가가치세 차감 후 지급'])
def test_same_complete_source_graph_allows_closed_receipt_and_deduction_variants(receipt, deduction):
    candidate = row(61)
    action = '원천사로부터 ' + receipt + ' 가맹점에 ' + deduction
    assert not guard(61, cells=('', 'PG 서비스', action, ''))


def test_accounting_only_rows_stay_removed_and_real_payment_plus_accounting_stays():
    policy = tuple(FlowRow(tuple(r['칸']), tuple(r['인용'])) for r in SAVED['policy_rows'])
    mixed = replace(row(62), cells=(*row(62).cells[:2], row(62).cells[2] + '; 거래승인 시점에 수익 인식', ''))
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=policy + (mixed,)),))
    checked, notes = check_diagram_numbers(report, fragments())
    assert checked.sections[0].flow_rows == (mixed,)
    assert sum(FLOW_ACCOUNTING_REVENUE_CODE in note for note in notes) == len(policy)


@pytest.mark.parametrize('action', ['전자지급결제대행업 수행', '전자지급결제대행업 운영',
    '전자지급결제대행업 영위', '현재 전자지급결제대행업 수행', '회사는 현재 전자지급결제대행업 수행'])
def test_nominal_current_activity_cannot_borrow_founding_purpose(action):
    cells = (row(67).cells[0], action, row(67).cells[2])
    assert flow_scope_problem(cells, {'1': SAVED['sources']['1']}) == SCOPE_CONDITION_UNBOUND
    for source_id in ('49', '87'):
        assert not flow_scope_problem(cells, {'1': SAVED['sources']['1'], source_id: SAVED['sources'][source_id]})


@pytest.mark.parametrize('extra', [
    '회사는 현재 운송업을 영위하고 있다.',
    '회사는 전자지급결제대행업을 영위할 계획이다.',
    '고객사는 전자지급결제대행업을 영위하고 있다.',
    '회사는 전자지급결제대행업을 영위하는 경우를 가정한다.',
])
def test_other_business_owner_plan_or_hypothesis_does_not_release_purpose(extra):
    assert flow_scope_problem(row(67).cells, {'1': SAVED['sources']['1'], 'extra': extra}) == SCOPE_CONDITION_UNBOUND


def test_historical_purpose_and_unrelated_current_operating_contracts_are_not_blanket_blocked():
    assert not flow_scope_problem(('설립 목적', '전자지급결제대행업 수행', ''), {'1': SAVED['sources']['1']})
    assert not flow_scope_problem(('과거 사업', '전자지급결제대행업 수행', ''), {'1': SAVED['sources']['1']})
    assert not flow_scope_problem(('서비스', '회계 프로그램 운영', '기업 고객'), {'x': '회사는 현재 회계 프로그램을 운영하고 있다.'})
