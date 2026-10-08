"""투자 목적 전체의 쉼표·및 동치만 보존한다."""
import pytest

from src.features.composer.business_population_scope import (
    _same_investment_purpose_list, section_investment_plan_problem,
)
from src.features.composer.future_plan_guard import future_plan_prose_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.tests.test_business_population_scope import _context


SOURCE = '향후 투자 계획은 시스템 고도화 및 생산성 향상 및 품질 강화를 위해 투자 수행 예정입니다.'


@pytest.mark.parametrize('purposes', [
    '시스템 고도화, 생산성 향상 및 품질 강화',
    '시스템 고도화 및 생산성 향상, 품질 강화',
    '시스템 고도화, 생산성 향상, 품질 강화',
])
def test_complete_ordered_purpose_list_is_preserved(purposes):
    claim = f'시제품 부문은 향후 {purposes}를 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(claim, {'a': SOURCE}, {'a': _context(SOURCE)}) == ''


def test_source_comma_and_candidate_conjunction_are_equivalent():
    source = SOURCE.replace(' 및 ', ', ')
    claim = '시제품 부문은 시스템 고도화 및 생산성 향상 및 품질 강화를 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(claim, {'a': source}, {'a': _context(source)}) == ''


@pytest.mark.parametrize('purposes', [
    '시스템 고도화, 품질 강화',
    '품질 강화, 생산성 향상 및 시스템 고도화',
    '시스템 고도화, 생산성 향상 및 품질 강화 및 수출 확대',
    '시스템 고도화, 생산성 향상 및 품질 개선',
    '시스템 고도화 또는 생산성 향상, 품질 강화',
])
def test_changed_purpose_list_is_not_newly_supported(purposes):
    claim = f'시제품 부문은 {purposes}를 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(claim, {'a': SOURCE}, {'a': _context(SOURCE)}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('owner', ['회사', '장비 부문', '고객사'])
def test_list_equivalence_does_not_supply_local_owner(owner):
    claim = f'{owner}는 시스템 고도화, 생산성 향상 및 품질 강화를 위해 투자를 수행할 예정이다.'
    assert section_investment_plan_problem(claim, {'a': SOURCE}, {'a': _context(SOURCE)}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize(('candidate', 'source'), [
    ('1,000개설비확충및품질강화', '1및000개설비확충,품질강화'),
    ('시스템고도화,,품질강화', '시스템고도화및품질강화'),
    ('시스템고도화,품질강화,품질강화', '시스템고도화및품질강화'),
])
def test_number_empty_or_duplicate_items_are_not_equivalent(candidate, source):
    assert not _same_investment_purpose_list(candidate, source)


def test_guidance_example_uses_literal_target_and_activity():
    source = '향후 투자 계획은 시스템 고도화 및 품질 강화를 위해 투자 수행 예정입니다.'
    claim = '시제품 부문의 ' + source
    proof = [{'대상': '향후 투자 계획', '활동': '시스템 고도화 및 품질 강화를 위해 투자 수행',
              '근거': 'a', '원문': source, '양태': '계획'}]
    assert future_plan_prose_problem(claim, {'a': source}, {'미래근거': proof}, claim_slot='future_strategy:stated_plan') == ''


@pytest.mark.parametrize('activity', [
    '시스템 고도화 및 생산성 향상 및 품질 강화를 위해 투자 수행 예정',
    '시스템 고도화 및 생산성 향상, 품질 강화를 위해 투자를 수행할 예정',
])
def test_scope_equivalence_does_not_repair_nonliteral_future_proof(activity):
    claim = '시제품 부문은 향후 시스템 고도화 및 생산성 향상, 품질 강화를 위해 투자를 수행할 예정이다.'
    proof = [{'대상': '시제품 부문', '활동': activity, '근거': 'a', '원문': SOURCE, '양태': '계획'}]
    assert section_investment_plan_problem(claim, {'a': SOURCE}, {'a': _context(SOURCE)}) == ''
    assert future_plan_prose_problem(claim, {'a': SOURCE}, {'미래근거': proof}, claim_slot='future_strategy:stated_plan')
