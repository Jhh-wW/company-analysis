"""계약 목록의 금액과 실제 매출 구성의 증명을 구분한다."""
import pytest

from src.features.composer.business_population_scope import contract_revenue_priority_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.scope_guard import scope_problem

CONTRACT = '신공장 건설 | 예시발주처 | 2024.01.01~2026.12.31 | 93,632,000'


@pytest.mark.parametrize('claim', [
    '회사의 주요 수익원은 건설공사 용역으로, 발주처의 공사를 시공하고 있다.',
    '회사의 최대 수익원은 건설공사 용역이다.',
    '건설공사 용역이 주된 수익원이다.',
    '건설공사 용역의 매출 비중은 80%이다.',
    '건설공사 용역이 매출의 대부분이다.',
    '해석: 회사의 주요 수익원은 건설공사 용역이다.',
])
def test_contract_amount_does_not_prove_revenue_priority(claim):
    assert contract_revenue_priority_problem(claim, {'a': CONTRACT}) == SCOPE_CONDITION_UNBOUND
    assert scope_problem(claim, {'a': CONTRACT}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('claim', [
    '회사는 예시발주처의 신공장 건설공사를 수주하였다.',
    '신공장 건설공사의 계약금액은 93,632,000천원이다.',
    '주요 도급공사의 발주처는 예시발주처이다.',
    '건설공사 용역을 제공한다.',
])
def test_supported_contract_description_is_preserved(claim):
    assert contract_revenue_priority_problem(claim, {'a': CONTRACT}) == ''


@pytest.mark.parametrize('source', [
    '회사의 주요 수익원은 건설공사 용역이다.',
    '건설공사 용역이 주요 수익원이다.',
    '건설공사 용역의 매출 비중은 80%이다.',
    '품목 | 매출액 | 비중\n건설공사 용역 | 매출액 800백만원 | 80%',
    '품목 | 매출액 | 비중\n건설공사 용역 | 800 | 80%',
])
def test_same_activity_direct_revenue_evidence_is_preserved(source):
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == ''


@pytest.mark.parametrize('source', [
    '회사의 주요 수익원은 센서 판매이다.',
    '센서 판매의 매출 비중은 80%이다.',
    '건설공사 용역의 계약금액은 800백만원이다.',
    '건설공사 용역의 영업이익 비중은 80%이다.',
    '타사의 주요 수익원은 건설공사 용역이다.',
    '고객사의 건설공사 용역 매출 비중은 80%이다.',
    '자회사의 주요 수익원은 건설공사 용역이다.',
    '주요 수익원 | 예시발주처 | 2024.01.01~2026.12.31 | 100',
])
def test_foreign_activity_metric_or_owner_is_not_borrowed(source):
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == SCOPE_CONDITION_UNBOUND


def test_each_revenue_priority_assertion_needs_own_activity_support():
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다. 센서 판매가 최대 수익원이다.',
        {'a': CONTRACT, 'b': '회사의 주요 수익원은 건설공사 용역이다.'},
    ) == SCOPE_CONDITION_UNBOUND


def test_non_contract_numeric_rows_do_not_trigger_contract_boundary():
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 센서 판매이다.',
        {'a': '품목 | 매출액 | 비중\n센서 판매 | 800 | 80%'},
    ) == ''


@pytest.mark.parametrize('owner', ['고객사', '자회사'])
@pytest.mark.parametrize('amount', ['800', '매출액 800백만원'])
def test_explicit_foreign_table_title_cannot_supply_company_revenue(owner, amount):
    source = (
        f'{owner}의 매출 구성은 다음과 같다.\n품목 | 매출액 | 비중\n'
        f'건설공사 용역 | {amount} | 80%'
    )
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == SCOPE_CONDITION_UNBOUND


def test_new_own_revenue_table_title_restores_own_support():
    source = (
        '고객사의 매출 구성은 다음과 같다.\n품목 | 매출액 | 비중\n센서 판매 | 900 | 90%\n\n'
        '당사의 매출 구성은 다음과 같다.\n품목 | 매출액 | 비중\n건설공사 용역 | 800 | 80%'
    )
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == ''


def test_new_own_table_with_other_activity_does_not_restore_foreign_support():
    source = (
        '고객사의 매출 구성은 다음과 같다.\n품목 | 매출액 | 비중\n건설공사 용역 | 800 | 80%\n\n'
        '당사의 매출 구성은 다음과 같다.\n품목 | 매출액 | 비중\n센서 판매 | 900 | 90%'
    )
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == SCOPE_CONDITION_UNBOUND


def test_foreign_table_scope_does_not_leak_to_independent_own_source():
    foreign = '자회사의 매출 구성이다.\n품목 | 매출액 | 비중\n건설공사 용역 | 800 | 80%'
    own = '당사의 주요 수익원은 건설공사 용역이다.'
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.',
        {'a': CONTRACT, 'b': foreign, 'c': own},
    ) == ''


@pytest.mark.parametrize('owner', ['고객회사', '고객 회사', '다른회사', '다른 회사', '예시회사'])
def test_company_suffix_does_not_reset_foreign_table_ownership(owner):
    source = (
        '자회사의 매출 구성이다.\n품목 | 매출액 | 비중\n센서 판매 | 900 | 90%\n\n'
        f'{owner}의 매출 구성이다.\n품목 | 매출액 | 비중\n건설공사 용역 | 800 | 80%'
    )
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == SCOPE_CONDITION_UNBOUND


def test_introductory_phrase_before_own_table_title_restores_support():
    source = (
        '자회사의 매출 구성이다.\n품목 | 매출액 | 비중\n센서 판매 | 900 | 90%\n\n'
        '다음은 당사의 매출 구성이다.\n품목 | 매출액 | 비중\n건설공사 용역 | 800 | 80%'
    )
    assert contract_revenue_priority_problem(
        '회사의 주요 수익원은 건설공사 용역이다.', {'a': CONTRACT, 'b': source},
    ) == ''
