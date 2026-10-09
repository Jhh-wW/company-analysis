"""직접 비중의 회사 자신 주어를 보존하며 다른 소유·분모를 빌리지 않는다."""
import pytest

from src.shared.revenue_population_scope import revenue_population_claim_problem

TABLE = '품목 매출액 비율 센서 60 60% 장비 40 40%. '
DIRECT = '당사의 경우도 마찬가지로 센서 매출이 전체 매출의 약 60%를 차지하고 있습니다.'


@pytest.mark.parametrize('candidate', [
    '당사의 경우 센서 매출이 전체 매출의 약 60%를 차지하고 있다.',
    '당사의 경우도 마찬가지로 센서 매출이 전체 매출의 약 60%를 차지하고 있습니다.',
    '회사의 경우는 센서 매출이 전체 매출의 약 60%를 차지한다.',
    '일반 시장에서는 센서 비중이 높으며, 당사의 경우도 마찬가지로 센서 매출이 전체 매출의 약 60%를 차지한다.',
])
def test_explicit_self_case_is_preserved(candidate):
    assert not revenue_population_claim_problem(candidate, {'1': TABLE + DIRECT})


@pytest.mark.parametrize('candidate, source', [
    ('고객사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('자회사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('경쟁사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT.replace('당사의', '고객사의')),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT.replace('전체 매출의', '전체 시장 매출의')),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT.replace('전체 매출의', '시장의')),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE),
    ('당사의 경우 장비 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('당사의 경우 센서 매출이 전체 매출의 약 70%를 차지한다.', TABLE + DIRECT),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', '[제조 부문] ' + TABLE + DIRECT),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + '2024년 ' + DIRECT),
    ('2025년 당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('당사의 경우 센서 매출은 중요하며, 고객사의 센서 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('당사의 경우 센서 매출은 중요하며, 장비 매출이 전체 매출의 약 60%를 차지한다.', TABLE + DIRECT),
    ('당사의 경우 센서 매출이 전체 매출의 약 60%를 차지하고 장비 매출이 전체 매출의 약 70%를 차지한다.', TABLE + DIRECT),
])
def test_self_case_does_not_supply_other_population(candidate, source):
    assert revenue_population_claim_problem(candidate, {'1': source})


def test_scope_permission_does_not_replace_numeric_proof():
    from src.features.composer.grounding import grounding_problem
    candidate = '당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.'
    assert not revenue_population_claim_problem(candidate, {'1': TABLE + DIRECT})
    entry = {'검증근거': {'수치': [{
        '표현': '센서 매출이 전체 매출의 약 60%를 차지한다', '항목': '센서 매출 비중',
        '근거': '1', '원문': '센서 매출이 전체 매출의 약 60%를 차지하고 있습니다',
        '원문항목': '센서 매출 비중', '원문값': '약 60%', '후보값': '약 60%',
    }]}}
    detail = {}
    assert grounding_problem(candidate, {'1': TABLE + DIRECT}, entry, detail=detail) == 'semantic_grounding_invalid'
    assert detail['stage'] == 'expression_not_in_candidate'


@pytest.mark.parametrize('owner', ['당사의 경우', '당사는', ''])
def test_connected_basis_cannot_borrow_individual_direct_share(owner):
    source = '개별재무제표 기준 매출액 ' + TABLE + DIRECT
    candidate = f'연결재무제표 기준으로 {owner} 센서 매출이 전체 매출의 약 60%를 차지한다.'
    assert revenue_population_claim_problem(candidate, {'1': source})


@pytest.mark.parametrize('source_basis, candidate_basis', [
    ('연결재무제표 기준', '연결재무제표 기준'),
    ('개별재무제표 기준', '개별재무제표 기준'),
    ('별도 기준', '개별재무제표 기준'),
])
def test_same_reporting_basis_preserves_self_case(source_basis, candidate_basis):
    source = source_basis + ' 매출액 ' + TABLE + DIRECT
    candidate = candidate_basis + '으로 당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.'
    assert not revenue_population_claim_problem(candidate, {'1': source})


def test_source_without_reporting_basis_does_not_prove_new_connected_basis():
    candidate = '연결재무제표 기준으로 당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.'
    assert revenue_population_claim_problem(candidate, {'1': TABLE + DIRECT})


def test_direct_share_uses_the_nearest_basis_and_not_an_earlier_table():
    source = '연결재무제표 기준 매출액 ' + TABLE + '개별재무제표 기준 매출액 ' + TABLE + DIRECT
    connected = '연결재무제표 기준으로 당사의 경우 센서 매출이 전체 매출의 약 60%를 차지한다.'
    individual = connected.replace('연결', '개별')
    assert revenue_population_claim_problem(connected, {'1': source})
    assert not revenue_population_claim_problem(individual, {'1': source})
