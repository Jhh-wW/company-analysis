"""실제14 정산 원형의 자기 소유 수취인 대상을 좁게 대조한다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.direct_support_constants import FLOW_CELL_JOIN
from src.features.composer.flow_review_binding import filter_reviewed_flow_rows
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.render import render_report
from src.features.composer.role_binding import role_binding_problem
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import _apply_grounding, verify_report


SAVED = json.loads((Path(__file__).parent / 'fixtures/fourteenth_business_flow.json').read_text(encoding='utf-8'))
ENTRIES = {item['번호']: item for item in SAVED['review_entries']}


def row(number=61):
    data = SAVED['rows'][str(number)]
    return FlowRow(tuple(data['칸']), tuple(data['인용']))


def guard(*, cells=None, proof=None, source=None, source_id='90'):
    cells = row().cells if cells is None else tuple(cells)
    return role_binding_problem(FLOW_CELL_JOIN.join(cells),
        {source_id: SAVED['sources']['90'] if source is None else source},
        ENTRIES[61]['검증근거'] if proof is None else proof, cells=cells)


def fragments():
    return tuple(CollectedFragment(fid, '공시', text, document_date='2026-03-31',
        document_identity='document:dart.fss.or.kr:20260331000296')
        for fid, text in SAVED['sources'].items())


def test_original_full_response_preserves_true61_and_false60_without_changes():
    before = deepcopy(SAVED)
    for field in ('writer_response', 'full_review_response'):
        assert hashlib.sha256(SAVED[field].encode()).hexdigest() == SAVED[field + '_sha256']
    assert all(hashlib.sha256(text.encode()).hexdigest() == SAVED['source_sha256'][fid]
               for fid, text in SAVED['sources'].items())
    problems = {}
    result = _apply_grounding(SAVED['full_review_response'], {60: '거짓', 61: '참'},
        {n: (FLOW_CELL_JOIN.join(row(n).cells), {fid: SAVED['sources'][fid] for fid in row(n).citations})
         for n in (60, 61)},
        diagnostic_contexts={n: ('business_model', '도식', '확인') for n in (60, 61)},
        flow_cells_by_number={n: row(n).cells for n in (60, 61)}, grounding_problems=problems)
    assert result[60] != '참' and result[61] == '참' and 61 not in problems
    assert SAVED == before


def test_same_approved_row_survives_technical_verify_receipt_and_render():
    original_rows = (row(60), row(61))
    report = ComposedReport((ComposedSection('business_model', (), flow_rows=original_rows),))
    requests = []

    def saved_review(prompt):
        requests.append(prompt)
        responses = []
        for item in review_items(prompt):
            n = 61 if item.citations == ('90',) else 60
            assert item.kind == '도식' and item.citations == row(n).citations
            assert row(n).cells[2] in item.text
            # 기술 DTO 번호만 대응한다. 원형 승인/거절·증명·내용을 바꾸지 않는다.
            responses.append(dict(ENTRIES[n], 번호=item.number))
        return json.dumps({'판정': responses}, ensure_ascii=False)

    checked = verify_report(report, fragments(), None, saved_review,
        baseline_date='2026-10-23', allow_sentence_rewrite=False,
        allowed_fragment_ids_by_section={'business_model': frozenset(SAVED['sources'])})
    assert len(requests) == 1 and checked.sections[0].flow_rows == (row(61),)
    assert checked.sections[0].flow_rows[0].review_binding is not None
    assert filter_reviewed_flow_rows(checked, fragments(), baseline_date='2026-10-23') == checked
    public = render_report('토스페이먼츠 주식회사', checked, fragments(), None, as_of_date='2026-10-23')
    assert public.sections[0].tables[0].rows == [list(row(61).cells)]


@pytest.mark.parametrize('target', ['회사의 가맹점', '당사의 가맹점'])
def test_explicit_self_possessive_keeps_the_same_complete_settlement(target):
    proof = deepcopy(ENTRIES[61]['검증근거'])
    proof['관계'][0]['대상'] = target
    assert not guard(proof=proof)


@pytest.mark.parametrize('target', ['다른회사의 가맹점', '자회사의 가맹점', '회사의 은행',
    '회사의 고객', '가맹점의 회사', '가맹점의 가맹점', '협력회사의 가맹점'])
def test_other_owner_or_recipient_cannot_borrow_self_source(target):
    proof = deepcopy(ENTRIES[61]['검증근거'])
    proof['관계'][0]['대상'] = target
    assert guard(proof=proof)


@pytest.mark.parametrize('old,new', [
    ('가맹점에 지급', '은행에 지급'), ('회사에 대금 지급', '회사가 원천사에 대금 지급'),
    ('수수료·부가가치세', '배송수수료·부가가치세'), ('차감 후', '차감하지 않고'),
    ('차감 후', '계약을 체결하면 차감 후'), ('가맹점에 지급', '가맹점이 회사에 지급'),
])
def test_candidate_direction_recipient_condition_and_fees_remain_strict(old, new):
    cells = list(row().cells)
    assert old in cells[2]
    cells[2] = cells[2].replace(old, new)
    assert guard(cells=cells)


@pytest.mark.parametrize('change', ['other_company', 'conditional', 'partial_quote', 'absent_quote',
    'uncited_source', 'revenue_inference', 'other_fee', 'wrong_kind', 'no_proof'])
def test_original_approval_does_not_cover_unbound_source_or_new_claim(change):
    proof = deepcopy(ENTRIES[61]['검증근거'])
    entry = proof['관계'][0]
    source, cells, source_id = SAVED['sources']['90'], list(row().cells), '90'
    if change in ('other_company', 'conditional'):
        new = '자회사는' if change == 'other_company' else '회사는 계약을 체결하면'
        source = source.replace('회사는', new)
        entry['원문'] = entry['원문'].replace('회사는', new)
    elif change == 'partial_quote':
        entry['원문'] = '회사의 가맹점으로 수수료와 부가가치세를 제외하여 지급하고 있습니다.'
    elif change == 'absent_quote':
        entry['원문'] += ' 회사는 추가로 매출을 인식합니다.'
    elif change == 'uncited_source':
        source_id = '다른번호'
    elif change == 'revenue_inference':
        cells[3] = '부가가치세를 회사 매출로 인식'
    elif change == 'other_fee':
        source = source.replace('수수료와', '배송수수료와')
        entry['원문'] = entry['원문'].replace('수수료와', '배송수수료와')
    elif change == 'wrong_kind':
        entry['유형'] = '역할'
    else:
        proof = {}
    assert guard(cells=cells, proof=proof, source=source, source_id=source_id)
