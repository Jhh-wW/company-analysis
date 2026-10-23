"""실제14의 완곡한 손실 인과와 숫자·증감 설명의 경계를 검증한다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.financial_interpretation import financial_interpretation_problem
from src.features.composer.financial_interpretation_constants import NET_LOSS_CAUSE_UNBOUND
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    PerformanceTable, filing_meta_from_raw,
)
from src.features.composer.render import render_report
from src.features.composer import verify as verify_module
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import _apply_grounding, verify_report
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


SAVED = json.loads((Path(__file__).parent / 'fixtures/fourteenth_financial_interpretation.json')
                   .read_text(encoding='utf-8'))
CLAIM = next(item['text'] for item in SAVED['claims'] if item['number'] == 35)
ROW = next(row for row in SAVED['review_rows'] if row['번호'] == 35)
SOURCES = {key: SAVED['sources'][key] for key in ('5', '97')}
CAUSE = '영업비용 증가'
EFFECT = '순손실'


def test_actual_review_response_and_source_hashes_remain_unchanged_and_only_bad_cause_changes(monkeypatch):
    original = deepcopy(SAVED)
    assert hashlib.sha256(SAVED['raw_review_response'].encode()).hexdigest() == SAVED['response_sha256']
    assert {key: hashlib.sha256(source.encode()).hexdigest() for key, source in SOURCES.items()} == {
        key: SAVED['source_sha256'][key] for key in SOURCES}
    claims = [item for item in SAVED['claims'] if 26 <= item['number'] <= 35]
    def replay():
        diagnostics, problems = [], {}
        verdicts = _apply_grounding(SAVED['raw_review_response'],
            {item['number']: '참' for item in claims},
            {item['number']: (item['text'], {key: SAVED['sources'][key] for key in item['citations']})
             for item in claims}, diagnostics=diagnostics,
            diagnostic_contexts={item['number']: ('past_changes', '본문', item['text']) for item in claims},
            grounding_problems=problems, baseline_date='2026-10-23')
        return verdicts, problems, diagnostics

    # 이 검사만 제거한 대조군이다. 다른 기존 검수의 탈락을 승인으로 바꾸지 않는다.
    with monkeypatch.context() as isolated:
        isolated.setattr(verify_module, 'financial_interpretation_problem', lambda *_args: '')
        baseline, baseline_problems, _ = replay()
    verdicts, problems, diagnostics = replay()
    assert baseline[35] == '참'
    assert verdicts == baseline | {35: '근거결속실패'}
    assert problems == baseline_problems | {35: NET_LOSS_CAUSE_UNBOUND}
    outcome, = (item for item in observed_review_outcomes(diagnostics)
                if item['reason_code'] == NET_LOSS_CAUSE_UNBOUND)
    assert outcome['reason_code'] == NET_LOSS_CAUSE_UNBOUND
    assert outcome['candidate_sha256'] == hashlib.sha256(CLAIM.encode()).hexdigest()
    assert SAVED == original


@pytest.mark.parametrize('text', [
    CLAIM,
    '순손실은 영업비용 증가에 따른 결과로 읽힌다.',
    '순손실은 영업비용이 늘어난 데 따른 결과로 해석된다.',
    '순손실은 영업비용 증가 때문으로 읽힌다.',
    '순손실은 영업비용 증가에 기인한 것으로 해석된다.',
    '영업비용 증가 영향으로 순손실이 이어진 것으로 읽힌다.',
    '영업비용 증가의 결과로 순손실이 이어졌다고 해석할 수 있다.',
    '순손실은 영업비용 증가의 영향으로 읽힌다.',
    '순손실은 영업비용 증가를 반영한 것으로 해석된다.',
    '순손실의 배경에는 영업비용 증가가 있다고 읽힌다.',
    '영업비용 증가가 순손실의 배경으로 읽힌다.',
    '영업비용 증가가 순손실의 이유라고 해석된다.',
    '영업비용 증가가 순손실의 요인으로 읽힌다.',
    '순손실의 이유는 영업비용 증가로 읽힌다.',
    '금융수익도 증가했다. 순손실은 영업비용 증가에 따른 결과로 읽힌다.',
    '순손실은 영업비용 증가에 따른 결과로 읽힌다. 원인이라고 단정할 수는 없다.',
])
def test_parallel_financial_numbers_do_not_support_softened_operating_cause(text):
    assert financial_interpretation_problem(text, SOURCES) == NET_LOSS_CAUSE_UNBOUND


@pytest.mark.parametrize('text', [
    *[item['text'] for item in SAVED['claims'] if 26 <= item['number'] <= 34],
    *SAVED['canonical_performance_prose'],
    '영업비용은 전기보다 늘었고 두 해 모두 순손실이 발생했다.',
    '영업비용 증가가 순손실의 원인이라는 뜻은 아니다.',
    '순손실은 영업비용 증가에 따른 결과라고 볼 수 없다.',
    '순손실은 영업비용 증가에 따른 결과는 아니다.',
    '순손실은 영업비용 증가에 따른 결과로 읽히지 않는다.',
    '순손실은 영업비용 증가를 반영하지 않는다.',
    '순손실은 영업비용 증가를 반영한 결과라고 볼 수 없다.',
    '영업비용 증가 영향으로 순손실이 발생한 것은 아니다.',
    '영업비용 증가와 순손실의 인과는 이 표만으로 설명할 수 없다.',
    '영업손실과 순손실 수치에 당기 실적이 반영되어 있다.',
    '비용 증감의 배경은 확인되지 않았으나 순손실 규모는 줄었다.',
    '영업수익 총액보다 영업비용 총액이 컸기 때문에 영업손실이 발생했다.',
    '영업수익 증가액보다 영업비용 증가액이 작았기 때문에 영업손실이 줄었다.',
])
def test_valid_figures_parallel_description_and_explicit_denial_remain_available(text):
    assert financial_interpretation_problem(text, SOURCES) == ''


@pytest.mark.parametrize('text', [
    '순손실은 영업비용 증가에 따른 결과로 읽힌다.',
    '순손실은 영업비용 증가의 영향으로 읽힌다.',
    '영업비용 증가의 결과로 순손실이 이어졌다.',
    '순손실은 영업비용 증가를 반영한 것으로 읽힌다.',
    '순손실의 배경에는 영업비용 증가가 있다고 읽힌다.',
    '영업비용 증가가 순손실의 배경으로 읽힌다.',
    '영업비용 증가가 순손실의 이유라고 해석된다.',
])
def test_direct_same_cause_source_is_kept_but_foreign_conditional_or_parallel_proof_is_not(text):
    # 정확한 원문을 옮기는 양성과, 다른 문장으로 재서술한 인과의 양성을 함께 둔다.
    assert financial_interpretation_problem(text, {'own': text}) == ''
    proof = {'관계': [{'유형': '인과', '근거': 'own', '원문': text,
                       '원인': CAUSE, '결과': EFFECT}]}
    claim = '영업비용 증가 때문에 순손실이 이어졌다.'
    assert financial_interpretation_problem(claim, {'own': text}, proof) == ''
    assert financial_interpretation_problem(claim, {'other': text}, proof) == NET_LOSS_CAUSE_UNBOUND
    assert financial_interpretation_problem(text, {'own': '만약 ' + text}) == NET_LOSS_CAUSE_UNBOUND
    assert financial_interpretation_problem(text, SOURCES, proof) == NET_LOSS_CAUSE_UNBOUND


@pytest.mark.parametrize('source', [
    '순손실은 영업비용 증가에 따른 결과는 아니다.',
    '순손실은 영업비용 증가를 반영한 것은 아니다.',
    '순손실은 영업비용 증가에 따른 결과라고 보기 어렵다.',
    '영업비용 증가와 순손실은 각각 전기에 비해 변동하였다.',
    '순손실 증가가 영업비용 증가의 주요 원인이다.',
])
def test_false_negative_reversed_and_parallel_source_relations_cannot_support_a_cause(source):
    claim = '영업비용 증가 때문에 순손실이 이어졌다.'
    proof = {'관계': [{'유형': '인과', '근거': 'own', '원문': source,
                       '원인': CAUSE, '결과': EFFECT}]}
    assert financial_interpretation_problem(claim, {'own': source}, proof) == NET_LOSS_CAUSE_UNBOUND


@pytest.mark.parametrize('source', [
    '경쟁사의 순손실은 영업비용 증가에 따른 결과로 읽힌다.',
    '한빛회사의 순손실은 영업비용 증가에 따른 결과로 읽힌다.',
    '만약 순손실은 영업비용 증가에 따른 결과로 읽힌다.',
    '영업비용이 더 증가한다면 순손실은 영업비용 증가에 따른 결과로 읽힐 수 있다.',
    '순손실은 영업비용 증가에 따른 결과로 읽힌다고 가정한다.',
    '영업비용 증가의 영향으로 경쟁사의 순손실이 이어졌다.',
    '영업비용 증가의 영향으로 한빛회사는 순손실이 이어졌다.',
])
@pytest.mark.parametrize('trim_quote', [False, True])
def test_causal_proof_cannot_drop_source_actor_or_condition(source, trim_quote):
    claim = '영업비용 증가 때문에 순손실이 이어졌다.'
    quote = source[source.index('순손실은'):] if trim_quote and '순손실은' in source else source
    proof = {'관계': [{'유형': '인과', '근거': 'own', '원문': quote,
                       '원인': CAUSE, '결과': EFFECT}]}
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '검증근거': proof}]}, ensure_ascii=False)
    problems = {}
    verdicts = _apply_grounding(raw, {1: '참'}, {1: (claim, {'own': source})},
        diagnostic_contexts={1: ('past_changes', '본문', claim)}, grounding_problems=problems)
    assert verdicts[1] == '근거결속실패'
    assert problems[1] == NET_LOSS_CAUSE_UNBOUND


@pytest.mark.parametrize('prefix', ['', '회사의 ', '당사의 ', '회사는 ', '당사는 '])
def test_same_company_direct_cause_keeps_valid_rephrasing(prefix):
    source = prefix + '순손실은 영업비용 증가에 따른 결과로 읽힌다.'
    claim = '영업비용 증가 때문에 순손실이 이어졌다.'
    proof = {'관계': [{'유형': '인과', '근거': 'own', '원문': source,
                       '원인': CAUSE, '결과': EFFECT}]}
    assert financial_interpretation_problem(claim, {'own': source}, proof) == ''
    assert financial_interpretation_problem(source, {'own': source}, proof) == ''


def test_explicit_named_scope_is_preserved_without_deciding_its_identity():
    source = '한빛회사의 순손실은 영업비용 증가에 따른 결과로 읽힌다.'
    claim = '한빛회사의 영업비용 증가 때문에 순손실이 이어졌다.'
    proof = {'관계': [{'유형': '인과', '근거': 'own', '원문': source,
                       '원인': CAUSE, '결과': EFFECT}]}
    assert financial_interpretation_problem(claim, {'own': source}, proof) == ''


def test_same_company_scope_inside_causal_pair_is_kept():
    source = '영업비용 증가의 영향으로 회사의 순손실이 이어졌다.'
    claim = '영업비용 증가 때문에 순손실이 이어졌다.'
    proof = {'관계': [{'유형': '인과', '근거': 'own', '원문': source,
                       '원인': CAUSE, '결과': EFFECT}]}
    assert financial_interpretation_problem(claim, {'own': source}, proof) == ''


def test_render_retains_actual_table_and_actual_good_neighbor_without_new_model_call():
    neighbor = next(item for item in SAVED['claims'] if item['number'] == 29)
    neighbor_row = next(row for row in SAVED['review_rows'] if row['번호'] == 29)
    fragments = tuple(CollectedFragment(key, '재무', source,
        document_date='2026-03-31', document_title='감사보고서 (2025.12)',
        source_document_id='20260331000296', reporting_period='2025-12-31',
        source_publisher='토스페이먼츠 주식회사') for key, source in SOURCES.items())
    t = SAVED['performance_table']
    table = PerformanceTable(t['caption'], tuple(t['headers']), tuple(map(tuple, t['rows'])),
        unit=t['display_unit'], cite=t['cite'], raw_rows=tuple(map(tuple, t['raw_rows'])),
        scale_divisor=t['scale_divisor'], scale_places=t['scale_places'], entity_scope=t['entity_scope'],
        raw_unit=t['raw_unit'], unit_dimension=t['unit_dimension'])
    report = ComposedReport((ComposedSection('past_changes', (
        ComposedSentence(CLAIM, ('5', '97'), '해석', planned_claim_slot='past_changes:change_context'),
        ComposedSentence(neighbor['text'], ('5',), '확인', planned_claim_slot='past_changes:operating_costs'),
    )),))
    calls = []

    def review(prompt):
        calls.append(prompt)
        rows = []
        for item in review_items(prompt):
            original = ROW if item.text == CLAIM else neighbor_row
            rows.append(dict(original, 번호=item.number))
        return json.dumps({'판정': rows}, ensure_ascii=False)

    diagnostics = []
    checked = verify_report(report, fragments, table, review,
        baseline_date='2026-10-23', diagnostics=diagnostics)
    assert len(calls) == 1
    assert [sentence.text for sentence in checked.sections[0].sentences] == [neighbor['text']]
    public = render_report('토스페이먼츠', checked, fragments, table, as_of_date='2026-10-23',
        filing_meta=filing_meta_from_raw({'rcept_no': '20260331000296', 'rcept_dt': '20260331',
                                        'report_nm': '감사보고서 (2025.12)'}))
    section, = (section for section in public.sections if section.cell == 'past_changes')
    assert section.tables[0].rows == [list(row) for row in table.rows]
    assert section.tables[0].raw_rows == [list(row) for row in table.raw_rows]
    assert neighbor['text'] in str(section.prose_lines)
    assert CLAIM not in str(section.prose_lines)
    assert not any(fact.claim == CLAIM for fact in public.fact_records)
    assert observed_review_outcomes(diagnostics)[0]['reason_code'] == NET_LOSS_CAUSE_UNBOUND
