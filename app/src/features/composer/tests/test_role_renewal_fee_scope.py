"""계약 기간 갱신과 유료·금융 반복 흐름의 과금 요구를 구분한다."""
import pytest

from src.features.composer.role_binding import (
    role_binding_hint_lines, role_binding_problem, role_binding_requirements,
)
from src.features.composer.role_binding_constants import ROLE_BINDING_MISSING
from src.features.composer.verify import _apply_grounding
import json


@pytest.mark.parametrize('text', [
    '당사는 원재료 구매 계약을 체결하고 계약은 연단위로 매년 갱신된다.',
    '회사는 공급 계약의 기간을 연장했다.',
    '구매 계약 갱신', '원재료 공급계약 연장',
])
def test_nonfinancial_contract_duration_does_not_require_fee(text):
    requirements = role_binding_requirements(text, {'1': text})
    assert not requirements.required
    assert role_binding_hint_lines(requirements, False) == ''
    assert role_binding_problem(text, {'1': text}, {}) == ''


@pytest.mark.parametrize('text', [
    '고객은 유료 구독을 매년 갱신한다.',
    '회사는 유료로 갱신한다.',
    '회사는 이용료 계약을 갱신한다.',
    '고객은 예금 만기를 연장한다.',
    '고객은 대출 기간을 연장한다.',
    '회사는 계약 갱신 수익을 받는다.',
    '회사는 계약 연장으로 반복 수익을 얻는다.',
])
def test_explicit_financial_renewal_still_requires_fee(text):
    requirements = role_binding_requirements(text, {'1': text})
    assert requirements.required
    assert all(item.kind == '과금' for item in requirements.required)
    assert role_binding_problem(text, {'1': text}, {}) == ROLE_BINDING_MISSING


def test_other_service_fee_does_not_turn_contract_renewal_into_fee():
    text = '회사는 원재료 계약을 갱신하며 별도 서비스의 수수료를 받는다.'
    requirements = role_binding_requirements(text, {'1': text})
    assert [item.marker for item in requirements.required] == ['수수료']
    assert role_binding_problem(text, {'1': text}, {}) == ROLE_BINDING_MISSING


def test_financial_flow_cells_keep_renewal_binding():
    cells = ['구독 서비스', '유료 구독 갱신', '이용자']
    assert any(item.marker == '갱신' for item in role_binding_requirements('', {'1': ''}, cells).required)


def test_same_renewal_financial_proof_is_preserved():
    text = '고객은 유료 구독을 갱신한다.'
    entries = {'관계': [{'근거': '1', '대상': '유료 구독', '역할값': '갱신',
                       '원문': text, '유형': '과금'}]}
    assert role_binding_problem(text, {'1': text}, entries) == ''


def test_unrelated_service_proof_cannot_cover_financial_renewal():
    text = '고객은 유료 구독을 갱신한다.'
    other = '회사는 서비스 수수료를 받는다.'
    entries = {'관계': [{'근거': '2', '대상': '서비스', '역할값': '수수료',
                       '원문': other, '유형': '과금'}]}
    assert role_binding_problem(text, {'1': text, '2': other}, entries)


@pytest.mark.parametrize('financial', [False, True])
def test_actual_grounding_entry_uses_same_fee_requirement(financial):
    text = ('고객은 유료 구독을 갱신한다.' if financial else
            '회사는 원재료 구매 계약을 매년 갱신한다.')
    response = {'번호': 1, '장': 'operations_partners', '근거': ['1'],
                '결과': '참', '검증근거': {}}
    problems = {}
    verdicts = _apply_grounding(json.dumps({'판정': [response]}, ensure_ascii=False),
                               {1: '참'}, {1: (text, {'1': text})}, grounding_problems=problems)
    assert verdicts == {1: '근거결속실패' if financial else '참'}
    assert problems == ({1: ROLE_BINDING_MISSING} if financial else {})
