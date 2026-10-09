"""미래 수식어를 투자 목적으로 오인하지 않으면서 부문과 목적은 보존한다."""
import pytest

from src.features.composer.business_population_scope import section_investment_plan_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.tests.test_business_population_scope import _context

SOURCE = '진행중인 투자는 아래와 같으며, 향후 투자 계획은 정보 시스템 고도화 및 생산성 향상을 위해 투자 수행 예정입니다.'


@pytest.mark.parametrize('prefix', ['', '향후 ', '앞으로 ', '향후 투자 계획으로 '])
def test_future_adjunct_is_not_a_different_investment_purpose(prefix):
    claim = f'시제품 부문은 {prefix}정보 시스템 고도화 및 생산성 향상을 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(claim, {'a': SOURCE}, {'a': _context(SOURCE)}) == ''


@pytest.mark.parametrize('claim', [
    '회사는 향후 정보 시스템 고도화 및 생산성 향상을 위해 투자를 수행할 예정이다.',
    '장비 부문은 향후 정보 시스템 고도화 및 생산성 향상을 위해 투자를 수행할 예정이다.',
    '시제품 부문은 향후 고객 데이터센터 증설을 위해 투자를 수행할 예정이다.',
])
def test_future_adjunct_does_not_supply_missing_owner_or_other_purpose(claim):
    assert section_investment_plan_problem(claim, {'a': SOURCE}, {'a': _context(SOURCE)}) == SCOPE_CONDITION_UNBOUND


def test_each_original_investment_purpose_can_be_used_separately():
    source = ('향후 투자 계획은 정보 시스템 고도화를 위해 투자 수행 예정입니다. '
              '생산성 향상을 위해 투자 수행 예정입니다.')
    for purpose in ('정보 시스템 고도화', '생산성 향상'):
        claim = f'시제품 부문은 향후 {purpose}를 위해 투자를 수행할 예정이다.'
        assert section_investment_plan_problem(claim, {'a': source}, {'a': _context(source)}) == ''


def test_other_explicit_division_in_same_fragment_does_not_supply_purpose():
    source = ('시제품 부문은 정보 시스템 고도화를 위해 투자 수행 예정이며 '
              '장비 부문은 생산성 향상을 위해 투자 수행 예정입니다.')
    assert section_investment_plan_problem(
        '시제품 부문은 향후 생산성 향상을 위해 투자를 수행할 예정이다.',
        {'a': source}, {'a': _context(source)},
    ) == SCOPE_CONDITION_UNBOUND


def test_two_supported_purposes_in_one_claim_keep_the_same_division():
    source = ('향후 투자 계획은 정보 시스템 고도화를 위해 투자 수행 예정입니다. '
              '생산성 향상을 위해 투자 수행 예정입니다.')
    claim = ('기타부문(시제품)은 정보 시스템 고도화를 위해 투자를 수행할 예정이며 '
             '생산성 향상을 위해 투자를 수행할 예정이다.')
    assert section_investment_plan_problem(claim, {'a': source}, {'a': _context(source)}) == ''


def test_date_inside_purpose_is_not_removed_by_adjunct_normalization():
    source = '향후 투자 계획은 2027년 생산성 향상을 위해 투자 수행 예정입니다.'
    assert section_investment_plan_problem(
        '시제품 부문은 향후 2028년 생산성 향상을 위해 투자를 수행할 예정이다.',
        {'a': source}, {'a': _context(source)},
    ) == SCOPE_CONDITION_UNBOUND
