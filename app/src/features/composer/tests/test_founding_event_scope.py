"""설립 수식절은 분할 관련 미수금이나 다른 법인의 사건에서 빌리지 않는다."""
import pytest

from src.features.composer.founding_event_scope import founding_event_scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


CLAIM = '측정기 사업 부문을 인적분할하여 설립된 주식회사 새길과는 미수금 관계가 남아 있다.'
RELATION = '회사는 측정기 사업 부문을 인적분할하여 주식회사 새길을 설립하였다.'
RECEIVABLE = '주식회사 새길에 대한 미수금에는 분할과 관련하여 미정산된 자산과 부채가 포함되어 있다.'


@pytest.mark.parametrize('source', [
    RECEIVABLE,
    '회사는 측정기 사업 부문을 인적분할하였다.',
    '주식회사 새길은 측정기 사업을 영위할 목적으로 설립되었다.',
    '회사는 측정기 사업 부문을 인적분할하여 주식회사 나래를 설립하였다.',
    '회사는 조립기 사업 부문을 인적분할하여 주식회사 새길을 설립하였다.',
    '회사는 측정기 사업 부문을 물적분할하여 주식회사 새길을 설립하였다.',
    '회사는 측정기 사업 부문을 인적분할하여 주식회사 새길을 설립할 예정이다.',
    '회사는 측정기 사업 부문을 인적분할하여 주식회사 새길을 설립하지 않았다.',
])
def test_unbound_founding_relation_is_rejected(source):
    assert founding_event_scope_problem(CLAIM, {'1': source}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('source', [
    RELATION,
    '측정기 사업 부문을 인적분할하여 설립된 주식회사 새길은 독립 법인이다.',
    '측정기 사업 부문을 인적분할하여 설립한 주식회사 새길과 거래한다.',
    '주식회사 새길은 측정기 사업 부문을 인적분할하여 설립되었다.',
    '회사는 측정기 사업 부문을 인적분할해 주식회사 새길을 설립했다.',
])
def test_explicit_same_founding_relation_is_preserved(source):
    assert founding_event_scope_problem(CLAIM, {'1': source}) == ''


@pytest.mark.parametrize('claim', [
    RECEIVABLE,
    '회사는 측정기 사업을 영위할 목적으로 설립된 법인이다.',
    '회사는 측정기 사업 부문을 인적분할하였다.',
    '주식회사 새길과 미정산 자산·부채가 남아 미수금 관계가 지속된다.',
])
def test_receivable_and_original_founding_purpose_do_not_trigger(claim):
    assert founding_event_scope_problem(claim, {'1': RECEIVABLE}) == ''


def test_separate_quotes_do_not_construct_a_new_founding_relation():
    sources = {'1': '회사는 측정기 사업 부문을 인적분할하였다.',
               '2': '주식회사 새길은 설립된 법인이다.'}
    assert founding_event_scope_problem(CLAIM, sources) == SCOPE_CONDITION_UNBOUND


def test_explicit_self_owner_does_not_borrow_foreign_event():
    claim = '당사의 측정기 사업 부문을 인적분할하여 설립된 주식회사 새길과 거래한다.'
    source = '다른회사의 측정기 사업 부문을 인적분할하여 주식회사 새길을 설립하였다.'
    assert founding_event_scope_problem(claim, {'1': source}) == SCOPE_CONDITION_UNBOUND


def test_same_foreign_company_event_can_be_reported():
    claim = '다른회사의 측정기 사업 부문을 인적분할하여 설립된 주식회사 새길과 거래한다.'
    source = '다른회사의 측정기 사업 부문을 인적분할하여 주식회사 새길을 설립하였다.'
    assert founding_event_scope_problem(claim, {'1': source}) == ''


def test_unspecified_split_mode_can_use_explicit_mode():
    claim = '측정기 사업 부문을 분할하여 설립된 주식회사 새길과 거래한다.'
    assert founding_event_scope_problem(claim, {'1': RELATION}) == ''


def test_company_name_ending_is_not_removed_from_an_active_relation():
    claim = '측정기 사업 부문을 인적분할하여 설립된 주식회사 나래와 거래한다.'
    source = '회사는 측정기 사업 부문을 인적분할하여 주식회사 나래가를 설립하였다.'
    assert founding_event_scope_problem(claim, {'1': source}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('prefix', ['2021년 ', '이후 ', '당시 '])
def test_closed_prefix_does_not_remove_the_explicit_self_owner(prefix):
    claim = '당사의 측정기 사업 부문을 인적분할하여 설립된 주식회사 새길과 거래한다.'
    assert founding_event_scope_problem(claim, {'1': prefix + RELATION}) == ''


@pytest.mark.parametrize('tail', ['는 주장을 부인했다.', '는 보도는 사실이 아니다.',
                                  '는 주장을 인정하지 않았다.'])
def test_explicit_denial_of_the_same_founding_event_is_not_support(tail):
    source = RELATION.removesuffix('설립하였다.') + '설립했다' + tail
    assert founding_event_scope_problem(CLAIM, {'1': source}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize('tail', [' 다른 계약을 취소했다는 주장을 부인했다.',
                                  ', 다른 부문을 매각했다는 주장을 부인했다.'])
def test_denial_of_another_event_does_not_remove_the_founding_event(tail):
    assert founding_event_scope_problem(CLAIM, {'1': RELATION + tail}) == ''
