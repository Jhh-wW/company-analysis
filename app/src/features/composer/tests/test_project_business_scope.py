"""프로젝트 시설 종류의 자기 근거와 같은 계약행 경계를 확인한다."""
import pytest

from src.features.composer.project_business_scope import project_business_scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize(('candidate', 'source', 'expected'), [
    ('연구동 공사는 반도체 시설 관련 공사다.', '공사명 | 시설분류 | 발주처\n연구동 공사 | 반도체 시설 | 발주회사', ''),
    ('연구동 공사는 반도체 시설 관련 공사다.', '공사명 | 시설분류 | 발주처\n연구동 공사 | 반도체 공장 | 발주회사', ''),
    ('당사는 공항 관련 공사를 수행한다.', '공사명 | 발주처\n서해공항 수장공사 | 발주회사', ''),
    ('당사는 반도체 시설 공사를 수행한다.', '공사명 | 발주처\n반도체공장 수장공사 | 발주회사', ''),
    ('연구동 공사는 반도체 시설 관련 공사다.', '공사명 | 시설분류 | 발주처\n연구동 공사 | 교육 시설 | 발주회사', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 반도체 시설 관련 공사다.', '공사명 | 시설분류 | 발주처\n연구동 공사 | 교육 시설 | 발주회사\n시험동 공사 | 반도체 시설 | 발주회사', SCOPE_CONDITION_UNBOUND),
    ('당사는 반도체 시설 공사를 수행한다.', '공사명 | 발주처\n연구동 공사 | 반도체공장 운영회사', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 반도체 시설 관련 공사다.', '연구동 공사는 반도체 시설 관련 공사이다.', ''),
    ('연구동 공사는 반도체 시설 관련 공사다.', '시험동 공사는 반도체 시설 관련 공사이다.', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 반도체 시설 관련 공사다.', '연구동 공사는 반도체 시설 관련 공사로 추진할 예정이다.', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 반도체 시설 관련 공사다.', '연구동 공사는 반도체 시설 관련 공사가 아니다.', SCOPE_CONDITION_UNBOUND),
    ('당사는 반도체 시설 공사를 수행한다.', '다른 회사는 반도체 시설 공사를 수행한다.', SCOPE_CONDITION_UNBOUND),
    ('당사는 반도체 시설 공사를 수행한다.', '다른 회사의 계약입니다.\n공사명 | 시설분류\n연구동 공사 | 반도체 시설', SCOPE_CONDITION_UNBOUND),
    ('당사는 반도체 시설 공사를 수행한다.', '다른 회사의 계약입니다.\n공사명 | 시설분류\n연구동 공사 | 반도체 시설\n당사의 계약입니다.\n공사명 | 시설분류\n시험동 공사 | 반도체 시설', ''),
    ('당사는 공항 및 반도체 시설 관련 공사를 수행한다.', '공사명 | 시설분류\n공항 수장공사 | 공항\n시험동 공사 | 반도체 시설', ''),
    ('당사는 공항 및 반도체 시설 관련 공사를 수행한다.', '공사명 | 시설분류\n공항 수장공사 | 공항\n시험동 공사 | 교육 시설', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사와 시험동 공사가 주요 계약 목록에 있다.', '공사명 | 발주처\n연구동 공사 | 발주회사', ''),
    ('당사는 건설 공사용역을 수행한다.', '공사명 | 발주처\n연구동 공사 | 발주회사', ''),
    ('당사는 반도체 시설 공사를 수행한다.', '공사명 | 시설분류 | 시설분류\n연구동 공사 | 반도체 시설 | 교육 시설', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 과학 시설 공사다.', '공사명 | 시설분류\n연구동 공사 | 과학 시설', ''),
    ('연구동 공사는 도시계획 시설 공사다.', '공사명 | 시설분류\n연구동 공사 | 도시계획 시설', ''),
    ('당사의 연구동 공사는 반도체 시설 공사다.', '공사명 | 시설분류\n연구동 공사 | 반도체 공장', ''),
    ('당사는 반도체 시설 공사를 수행한다.', '다른 회사의 계약입니다.\n시험동 공사는 반도체 시설 공사다.', SCOPE_CONDITION_UNBOUND),
    ('당사는 반도체 시설 공사를 수행한다.', '공사명 | 발주처\n반도체 기업 사옥 공사 | 발주회사', SCOPE_CONDITION_UNBOUND),
    ('당사는 반도체 시설 공사를 수행한다.', '공사명 | 발주처\n반도체공장 운영회사 사옥 공사 | 발주회사', SCOPE_CONDITION_UNBOUND),
])
def test_project_classification_scope(candidate, source, expected):
    assert project_business_scope_problem(candidate, {'self': source}) == expected


def test_multiple_own_quotes_bind_project_classification():
    sources = {'row': '공사명 | 발주처\n연구동 공사 | 발주회사',
               'classification': '연구동 공사는 반도체 시설 관련 공사이다.'}
    assert project_business_scope_problem('연구동 공사는 반도체 시설 관련 공사다.', sources) == ''


def test_nfkc_project_name_preserved():
    source = '공사명 | 시설분류\nＡ동 공사 | 교육 시설'
    assert project_business_scope_problem('A동 공사는 교육 시설 공사다.', {'self': source}) == ''


@pytest.mark.parametrize(('candidate', 'expected'), [
    ('연구동 공사는 교육 시설 공사다. 시험동 공사는 반도체 시설 공사다.', ''),
    ('연구동 공사는 교육 시설 공사이며, 시험동 공사는 반도체 시설 공사다.', ''),
    ('연구동 공사는 반도체 시설 공사다. 시험동 공사는 교육 시설 공사다.', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 반도체 시설 공사이며, 시험동 공사는 교육 시설 공사다.', SCOPE_CONDITION_UNBOUND),
])
def test_each_project_clause_binds_own_classification(candidate, expected):
    source = '공사명 | 시설분류\n연구동 공사 | 교육 시설\n시험동 공사 | 반도체 시설'
    assert project_business_scope_problem(candidate, {'self': source}) == expected


@pytest.mark.parametrize(('candidate', 'expected'), [
    ('연구동 공사는 교육 시설 공사다.', ''),
    ('연구동 공사는 반도체 시설 공사다.', SCOPE_CONDITION_UNBOUND),
    ('시험동 공사는 교육 시설 공사다.', SCOPE_CONDITION_UNBOUND),
    ('연구동 공사는 교육 시설 공사이며, 시험동 공사는 반도체 시설 공사다.', ''),
])
def test_each_source_clause_binds_own_classification(candidate, expected):
    source = '연구동 공사는 교육 시설 공사이며, 시험동 공사는 반도체 시설 공사다.'
    assert project_business_scope_problem(candidate, {'self': source}) == expected
