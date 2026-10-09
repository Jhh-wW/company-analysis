"""모델의 참 판정도 자기 원문이 지원하지 않는 범위·상태를 통과시키지 않는다."""
import json

import pytest

from src.features.composer.verify import _apply_grounding, _table_grounding_source, _append_grounding_diagnostic
from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.port import PerformanceTable
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


@pytest.mark.parametrize('kind', ['본문', '요약'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_research_title_is_not_current_execution_even_when_model_says_true(kind, wrapped):
    claim = '회사는 저소음 구동기 과제를 추진하고 있으며 소음 개선을 기대효과로 제시했다.'
    source = '연구과제 | 연구기간 | 연구결과 및 기대효과; 저소음 구동기 | 22/01~25/12 | 소음 개선'
    entry = {'번호': 1, '결과': '참', '근거': ['a']}
    raw = json.dumps({'검수결과': {'competitive_position': [entry]}} if wrapped else {'판정': [entry]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {'a': source})},
                             diagnostic_contexts={1: ('competitive_position', kind, '확인')},
                             grounding_problems=problems)
    assert result[1] != '참'
    assert problems[1] == 'time_invalid'


@pytest.mark.parametrize('claim,source', [
    ('회사는 저소음 구동기 과제를 연구과제 표에 기재했다.',
     '연구과제 | 연구기간 | 연구결과 및 기대효과; 저소음 구동기 | 22/01~25/12 | 소음 개선'),
    ('회사는 저소음 구동기 과제를 진행하고 있다.',
     '연구과제 | 연구기간 | 연구결과 및 기대효과; 저소음 구동기 | 22/01~25/12 | 현재 진행하고 있다'),
])
def test_research_record_and_direct_execution_survive_model_true(claim, source):
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '근거': ['a']}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {'a': source})},
                             diagnostic_contexts={1: ('competitive_position', '본문', '확인')},
                             grounding_problems=problems)
    assert result[1] == '참'
    assert not problems


@pytest.mark.parametrize('kind', ['본문', '요약'])
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('declaration', [None, '미래근거', '관계'])
def test_product_table_cannot_borrow_global_financial_scope(kind, wrapped, declaration):
    claim = '별도 기준 매출 구성표에서 제어밸브 제품은 산업 배관용으로 공급된다.'
    source = '품목 | 구체적용도 | 주요상표 | 매출액 ; 제어밸브 | 산업 배관용 | 가온 | 100'
    entry = {'번호': 1, '결과': '참', '근거': ['a']}
    if declaration:
        entry['검증근거'] = {declaration: [{'근거': TABLE_SOURCE_ID}]}
    raw = json.dumps({'검수결과': {'business_model': [entry]}} if wrapped
                     else {'판정': [entry]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {
        'a': source, TABLE_SOURCE_ID: '별도 재무제표\n항목 | 2025년\n매출액 | 100억원',
    })}, diagnostic_contexts={1: ('business_model', kind, '확인')},
        grounding_problems=problems)
    assert result[1] != '참'
    assert problems[1] == 'accounting_scope_unbound'
    diagnostics = []
    _append_grounding_diagnostic(diagnostics, section_id='summary' if kind == '요약' else 'business_model', kind=kind,
                                 reason_code=problems[1], candidate_text=claim,
                                 sources={'a': source})
    observed = observed_review_outcomes(diagnostics)
    assert len(observed) == 1
    assert observed[0]['reason_code'] == 'accounting_scope_unbound'
    assert observed[0]['verification_items'] == ('회계 범위',)


@pytest.mark.parametrize('kind', ['본문', '요약'])
def test_product_table_keeps_its_explicit_financial_scope(kind):
    claim = '별도 기준 매출 구성표에서 제어밸브 제품은 산업 배관용으로 공급된다.'
    source = '별도 재무제표\n품목 | 구체적용도 | 주요상표 | 매출액 ; 제어밸브 | 산업 배관용 | 가온 | 100'
    raw = json.dumps({'판정': [{'번호': 1, '결과': '참', '근거': ['a']}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: '참'}, {1: (claim, {'a': source})},
                             diagnostic_contexts={1: ('business_model', kind, '확인')},
                             grounding_problems=problems)
    assert result[1] == '참'
    assert not problems


@pytest.mark.parametrize('kind', ['본문', '요약'])
def test_validated_numeric_table_keeps_its_own_accounting_scope(kind):
    claim = '연결 매출액은 1,683억원이다.'
    table = _table_grounding_source(PerformanceTable(
        caption='실적', headers=('항목', '2024'), rows=(('매출액', '1,683'),),
        unit='억원', entity_scope='consolidated',
    ))
    entry = {'번호': 1, '결과': '참', '근거': ['a'], '검증근거': {'수치': [{
        '표현': '연결 매출액은 1,683억원', '항목': '매출액', '근거': TABLE_SOURCE_ID,
        '원문': '매출액 | 2024년 | 1,683억원', '원문항목': '매출액', '원문값': '1,683억원',
    }]}}
    problems = {}
    result = _apply_grounding(json.dumps({'판정': [entry]}, ensure_ascii=False),
                             {1: '참'}, {1: (claim, {'a': '회사는 제어밸브를 공급한다.',
                                                     TABLE_SOURCE_ID: table})},
                             diagnostic_contexts={1: ('past_changes', kind, '확인')},
                             grounding_problems=problems)
    assert result[1] == '참', problems
    assert not problems


@pytest.mark.parametrize('scope,label', [('consolidated', '연결 재무제표'),
                                       ('separate', '별도 재무제표'), ('', ''), ('unknown', '')])
def test_table_source_preserves_only_the_verified_table_scope(scope, label):
    table = PerformanceTable(caption='실적', headers=('항목', '2024'),
                             rows=(('매출액', '1,683'),), unit='억원', entity_scope=scope)
    numeric_row = '매출액 | 2024년 | 1,683억원'
    assert _table_grounding_source(table) == (label + '\n' if label else '') + numeric_row


@pytest.mark.parametrize('kind', ['본문', '요약'])
def test_matching_global_number_does_not_prove_the_product_accounting_scope(kind):
    claim = '별도 기준 정밀 제어장치의 매출액은 2,400억원이다.'
    table = _table_grounding_source(PerformanceTable(
        caption='실적', headers=('항목', '2025'), rows=(('매출액', '2,400'),),
        unit='억원', entity_scope='separate',
    ))
    source = '품목 | 용도 | 매출액 ; 정밀 제어장치 | 수처리 설비용 | 2400'
    entry = {'번호': 1, '결과': '참', '근거': ['a'], '검증근거': {'수치': [{
        '표현': '매출액은 2,400억원', '항목': '매출액', '근거': TABLE_SOURCE_ID,
        '원문': '매출액 | 2025년 | 2,400억원', '원문항목': '매출액', '원문값': '2,400억원',
    }]}}
    problems = {}
    result = _apply_grounding(json.dumps({'판정': [entry]}, ensure_ascii=False),
                             {1: '참'}, {1: (claim, {'a': source, TABLE_SOURCE_ID: table})},
                             diagnostic_contexts={1: ('past_changes', kind, '확인')},
                             grounding_problems=problems)
    assert result[1] != '참'
    assert problems[1] == 'accounting_scope_unbound'
