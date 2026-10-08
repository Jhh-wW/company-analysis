"""모집단과 명사 활동의 상태를 자기 인용에만 대조한다."""
import json

import pytest

from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_event_scope import response_current_activity_problem
from src.features.composer.tests.test_business_population_scope import _context
from src.features.composer import verify
from src.shared.revenue_population_scope import revenue_population_context_problem


@pytest.mark.parametrize('source,claim', [
    ('부품 수명 향상', '회사는 부품 수명 향상을 추진하고 있다.'),
    ('냉각 성능 향상. 고객사는 냉각 성능 향상을 추진하고 있다.', '회사는 냉각 성능 향상을 추진하고 있다.'),
    ('냉각 성능 향상; 부품 수명 향상을 추진하고 있다.', '회사는 냉각 성능 향상을 추진하고 있다.'),
    ('부품 수명 향상 예정', '회사는 부품 수명 향상을 추진하고 있다.'),
    ('부품 수명 향상 완료', '회사는 부품 수명 향상을 추진하고 있다.'),
    ('향후 부품 수명 향상을 추진하고 있다.', '회사는 부품 수명 향상을 추진하고 있다.'),
    ('연구 효율 증대', '회사는 연구 효율 증대를 추진하고 있다.'),
    ('연구 효율 증대', '회사는 연구 효율 증대가 진행 중이다.'),
])
def test_nominal_purpose_does_not_prove_current_progress(source, claim):
    assert response_current_activity_problem(claim, {'a': source}) == 'time_invalid'


@pytest.mark.parametrize('source,claim', [
    ('회사는 부품 수명 향상을 추진하고 있다.', '회사는 부품 수명 향상을 추진하고 있다.'),
    ('회사는 대형 제품에 대한 진출을 추진하고 있다.', '회사는 대형 제품 분야 진출을 추진하고 있다.'),
    ('연구 효율 증대를 추진 중이다.', '회사는 연구 효율 증대를 추진하고 있다.'),
    ('부품 수명 향상 완료', '회사는 부품 수명 향상을 완료했다.'),
    ('부품 수명 향상 예정', '회사는 부품 수명 향상을 계획하고 있다.'),
    ('부품 수명 향상', '대책에는 부품 수명 향상이 기재되어 있다.'),
])
def test_same_activity_progress_and_neutral_state_are_preserved(source, claim):
    assert not response_current_activity_problem(claim, {'a': source})


@pytest.mark.parametrize('subject', ['대다수 기업들이', '여러 기업은', '업계 기업이', '일반 제조사가'])
def test_general_business_population_is_not_the_company_problem(subject):
    text = f'부품 사업에서 {subject} 모든 공정을 내재화하기 어렵다는 제약이 존재한다.'
    source = text + ' 당사는 장비를 판매한다.'
    assert challenge_business_problem(text, {'a': source}, claim_slot='current_challenges:issue') == 'challenge_business_relation_unbound'
    assert not challenge_business_problem(text, {'a': source}, claim_slot='current_challenges:response')


@pytest.mark.parametrize('text', [
    '일반 제조사는 내재화가 어렵고 당사는 원자재 부족으로 제품 공급을 중단했다.',
    '당사는 부품 사업에서 인력 부족으로 공정 내재화에 어려움을 겪고 있다.',
    '부품 시장에서 합성기업은 주문 감소로 납품 중단을 겪고 있다.',
])
def test_explicit_current_company_constraint_is_preserved(text):
    assert not challenge_business_problem(text, {'a': text}, claim_slot='current_challenges:issue')


TABLE = '품목 | 매출액 | 비율 ; 설비 | 600 | 60% ; 부품 | 400 | 40%'


@pytest.mark.parametrize('claim', [
    '회사의 연결 기준 매출은 설비 판매가 압도적 비중을 차지한다.',
    '설비 판매는 회사 매출의 대부분을 차지한다.',
    '설비 판매는 회사의 주요 수익원이다.',
])
def test_qualitative_company_revenue_keeps_own_table_population(claim):
    assert revenue_population_context_problem(claim, {'a': TABLE}, {'a': _context(TABLE, '[제조 부문]')}) == 'scope_condition_unbound'


def test_local_population_and_direct_company_revenue_are_preserved():
    context = {'a': _context(TABLE, '[제조 부문]')}
    assert not revenue_population_context_problem('제조 부문 내 매출의 대부분은 설비 판매다.', {'a': TABLE}, context)
    assert not revenue_population_context_problem('제조 부문 내 연결 기준 매출은 설비 판매가 압도적 비중을 차지한다.', {'a': TABLE}, context)
    assert revenue_population_context_problem('연결 기준 매출은 설비 판매가 압도적 비중을 차지한다.',
        {'a': '연결 기준 ' + TABLE}, {'a': _context('연결 기준 ' + TABLE, '[제조 부문]')})
    assert not revenue_population_context_problem('설비 판매의 매출이 발생한다.', {'a': TABLE}, context)
    assert not revenue_population_context_problem('회사의 연결 기준 매출은 설비 판매가 압도적 비중을 차지한다.', {'a': '[회사 전체 매출] ' + TABLE}, {})


def test_context_cannot_borrow_another_fragment_population():
    assert revenue_population_context_problem('설비는 회사 매출의 대부분이다.',
        {'a': TABLE + ' '}, {'a': _context(TABLE, '[제조 부문]')}) == 'scope_condition_unbound'


def test_local_comparison_cannot_loan_its_population_to_later_company_subject():
    claim = '제조부문 내 설비는 전체 매출의 대부분을 차지하며 회사는 전체 매출의 대부분을 설비에서 얻는다.'
    assert revenue_population_context_problem(claim, {'a': TABLE},
        {'a': _context(TABLE, '[제조부문]')}) == 'scope_condition_unbound'


def test_routine_company_subject_cannot_loan_later_population_constraint():
    text = '당사는 설비를 판매하지만 일반 제조사는 내재화가 어렵다.'
    assert challenge_business_problem(text, {'a': text}, claim_slot='current_challenges:issue') == 'challenge_business_relation_unbound'


def test_model_true_cannot_loan_an_industry_population_or_segment_heading():
    entries = [('부품 사업에서 대다수 기업들이 내재화에 어려움을 겪고 있다.', 'current_challenges', 'issue', {}),
               ('회사의 연결 기준 매출은 설비 판매가 압도적 비중을 차지한다.', 'business_model', 'revenue_model', {'a': _context(TABLE, '[제조 부문]')})]
    for text, section, slot, contexts in entries:
        own = TABLE if section == 'business_model' else text + ' 당사는 장비를 판매한다.'
        raw = json.dumps([{'번호': 1, '장': section, '근거': ['a'], '결과': '참'}], ensure_ascii=False)
        problems = {}
        verdicts = verify._apply_grounding(raw, {1: '참'}, {1: (text, {'a': own})},
            diagnostic_contexts={1: (section, '본문', '검수')},
            claim_slots_by_number={1: f'{section}:{slot}'},
            section_context_by_source_id=contexts, grounding_problems=problems)
        assert verdicts[1] != '참' and problems[1]
