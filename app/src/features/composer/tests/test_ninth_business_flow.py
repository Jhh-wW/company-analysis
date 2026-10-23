"""실제 9차 표 검수 응답의 무료 소비와 정산·회계정책 경계를 검증한다."""
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
from src.features.composer.role_binding import role_binding_problem, _binding_problem, _units
from src.features.composer.role_binding_constants import ROLE_BINDING_PAIR_DEGENERATE
from src.features.composer.verify import _apply_grounding, verify_report

SAVED = json.loads((Path(__file__).parent / 'fixtures/ninth_business_flow.json').read_text(encoding='utf-8'))
BASELINE = '2026-10-23'


def row(number=55):
    value = SAVED['rows'][str(number)]
    return FlowRow(tuple(value['칸']), tuple(value['인용']))


def entry(number=55):
    return deepcopy(next(item for item in SAVED['review_entries'] if item['번호'] == number))


def fragments():
    # 보존 원문을 소비하는 기술 회귀의 메타이며 실제 전체 typed 입력 복원은 아니다.
    return tuple(CollectedFragment(fid, '공시', text,
        document_date='2026-03-31', document_identity='document:dart.fss.or.kr:20260331000296',
        source_document_id='20260331000296') for fid, text in SAVED['sources'].items())


def guard(candidate=None, source=None, proof=None, *, as_flow=True):
    candidate = candidate or row()
    return role_binding_problem(' → '.join(candidate.cells),
        {'90': source if source is not None else SAVED['sources']['90']},
        entry()['검증근거'] if proof is None else proof,
        cells=candidate.cells if as_flow else None)


def test_actual_ninth_table_verdicts_keep_own_pg_and_drop_policy_without_rewriting_approvals():
    before = deepcopy(SAVED)
    rows = {n: row(n) for n in (53, 54, 55)}
    problems = {}
    verdicts = _apply_grounding(json.dumps({'판정': SAVED['review_entries']}, ensure_ascii=False),
        {n: '참' for n in rows},
        {n: (' → '.join(r.cells), {fid: SAVED['sources'][fid] for fid in r.citations}) for n, r in rows.items()},
        diagnostic_contexts={n: ('business_model', '도식', '확인') for n in rows},
        flow_cells_by_number={n: r.cells for n, r in rows.items()}, grounding_problems=problems)
    assert verdicts[53] != '참' and verdicts[54] == verdicts[55] == '참'
    assert ROLE_BINDING_PAIR_DEGENERATE in problems[53]
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=tuple(
        r for n, r in rows.items() if verdicts[n] == '참')),))
    checked, notes = check_diagram_numbers(report, fragments())
    assert checked.sections[0].flow_rows == (rows[55],)
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in note for note in notes)
    assert all(hashlib.sha256(text.encode()).hexdigest() == SAVED['source_sha256'][fid]
               for fid, text in SAVED['sources'].items())
    assert SAVED == before


def test_same_table_claim_uses_own_review_then_render_and_normal_flow_receipt_filter():
    candidate = row()
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=(candidate,)),))
    original = entry()
    calls = []
    def saved_review(prompt):
        calls.append(prompt)
        assert candidate.cells[2] in prompt
        # 단독 회귀의 번호만 옮기며 실제 표 후보·원문·판정·증명은 바꾸지 않는다.
        return json.dumps({'판정': [dict(original, 번호=1)]}, ensure_ascii=False)
    checked = verify_report(report, fragments(), None, saved_review, baseline_date=BASELINE,
        allowed_fragment_ids_by_section={'business_model': frozenset(SAVED['sources'])},
        allow_sentence_rewrite=False)
    kept, = checked.sections[0].flow_rows
    assert len(calls) == 1 and kept.cells == candidate.cells and kept.citations == candidate.citations
    assert kept.review_binding is not None and candidate.review_binding is None
    assert filter_reviewed_flow_rows(checked, fragments(), baseline_date=BASELINE) == checked
    public = render_report('토스페이먼츠 주식회사', checked, fragments(), None, as_of_date=BASELINE)
    assert any('전자지불결제대행업' in str(table.rows) and '수수료' in str(table.rows)
               for section in public.sections for table in section.tables)
    assert entry() == original


@pytest.mark.parametrize('target', ['회사', '부가가치세', '다른 가맹점', '전자상거래업', 'PG 서비스'])
def test_business_target_requires_exact_source_business_and_separate_candidate_cell(target):
    proof = entry()['검증근거']
    proof['관계'][0]['대상'] = target
    assert guard(proof=proof)


@pytest.mark.parametrize('old,new', [
    ('회사가 수수료', '가맹점이 수수료'),
    ('가맹점에 지급', '회사에 지급'),
    ('가맹점에 지급', '다른 수취인에게 지급'),
    ('수수료·부가가치세', '배송수수료·부가가치세'),
    ('수수료·부가가치세', '수수료'),
    ('차감 후', '회사 수익으로 인식 후'),
    ('차감 후', '계약이 체결된 경우에만 차감 후'),
    ('가맹점에 지급', '가맹점에 지급하지 않음'),
    ('원천사가 회사에 대금 지급', '회사가 원천사에 대금 지급'),
])
def test_actual_table_cannot_borrow_receipt_for_changed_actor_fees_direction_or_condition(old, new):
    candidate = row()
    changed = replace(candidate, cells=(*candidate.cells[:2], candidate.cells[2].replace(old, new), ''))
    assert guard(changed)


@pytest.mark.parametrize('old,new', [
    ('회사는 전자지불', '자회사는 전자지불'),
    ('회사는 전자지불', '회사는 계약을 체결하면 전자지불'),
    ('원천사로부터 대금을 받은 후', '원천사에 대금을 준 후'),
    ('회사의 가맹점으로', '회사의 다른 수취인에게'),
    ('수수료와 부가가치세', '배송수수료와 부가가치세'),
    ('지급하고 있습니다', '지급하고 있지 않습니다'),
])
def test_source_and_exact_quote_mutation_do_not_prove_original_relation(old, new):
    source = SAVED['sources']['90'].replace(old, new)
    proof = entry()['검증근거']
    proof['관계'][0]['원문'] = proof['관계'][0]['원문'].replace(old, new)
    assert guard(source=source, proof=proof)


def test_business_alias_variants_require_same_spelling_in_all_three_own_locations():
    for business in ('전자지급결제대행업', '결제대행업'):
        candidate = replace(row(), cells=('', business, row().cells[2], ''))
        proof = entry()['검증근거']
        proof['관계'][0]['대상'] = business
        source = SAVED['sources']['90'].replace('전자지불결제대행업', business)
        assert guard(candidate, source, proof) == ''
        assert guard(candidate, SAVED['sources']['90'], proof)


def test_flow_exception_does_not_cover_prose_tax_revenue_or_other_extra_fee():
    # 산문 전체의 발동 여부와 구분해 새 예외의 도식 한정 자체를 대조한다.
    problem, spans, _, _ = _binding_problem(entry()['검증근거']['관계'][0],
        _units(' → '.join(row().cells), None), {'90': SAVED['sources']['90']}, False)
    assert problem and not spans
    assert guard(replace(row(), cells=(*row().cells[:3], '부가가치세는 회사 수익')))
    assert guard(replace(row(), cells=(*row().cells[:3], '로열티 수취')))
    assert guard(proof={})
    assert guard(source='교육을 위한 가상의 회사 사례입니다. ' + SAVED['sources']['90'])


@pytest.mark.parametrize('verdict,proof', [('거짓', 'original'), ('참', 'missing')])
def test_original_false_or_missing_table_proof_never_becomes_true(verdict, proof):
    candidate = row()
    item = dict(entry(), 결과=verdict, 검증근거=entry()['검증근거'] if proof == 'original' else {})
    result = _apply_grounding(json.dumps({'판정': [item]}, ensure_ascii=False), {55: verdict},
        {55: (' → '.join(candidate.cells), {'90': SAVED['sources']['90']})},
        diagnostic_contexts={55: ('business_model', '도식', '확인')}, flow_cells_by_number={55: candidate.cells})
    assert result[55] != '참'


@pytest.mark.parametrize('policy', [
    '일련의 구별되는 용역 제공 → 청구권이 있는 금액으로 수익 인식',
    '일련의 구별되는 용역을 제공하고 ⇒ 청구권이 있는 금액으로 수익을 인식한다',
    '청구권 기준 수익 인식',
    '청구권이 있는 금액으로 수익을 인식함',
    '수행의무 이행 시점에 수익 인식 → 청구권 기준 수익 인식',
])
def test_policy_only_action_is_not_a_customer_transaction_even_without_practical_expedient_label(policy):
    candidate = replace(row(54), cells=('', '자금관리 서비스·기타용역', policy, ''))
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=(candidate,)),))
    checked, notes = check_diagram_numbers(report, fragments())
    assert checked.sections[0].flow_rows == ()
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in note for note in notes)


@pytest.mark.parametrize('transaction', [
    '고객에게 월 구독료를 받는다',
    '가맹점 결제액에서 수수료를 차감하고 지급한다',
    '고객 자금을 관리하고 계좌이체와 정산 서비스를 제공한다',
])
@pytest.mark.parametrize('policy_first', [True, False])
def test_mixed_actual_transaction_is_preserved_in_either_order(transaction, policy_first):
    policy = '일련의 구별되는 용역 제공 → 청구권이 있는 금액으로 수익 인식'
    action = ' → '.join((policy, transaction) if policy_first else (transaction, policy))
    candidate = replace(row(54), cells=('', '자금관리 서비스', action, ''))
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=(candidate,)),))
    checked, notes = check_diagram_numbers(report, fragments())
    assert checked.sections[0].flow_rows == (candidate,)
    assert not any(FLOW_ACCOUNTING_REVENUE_CODE in note for note in notes)
