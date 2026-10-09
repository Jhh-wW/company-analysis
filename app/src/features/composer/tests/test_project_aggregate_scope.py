"""종합 공사 목록과 개별 공사의 시설 분류는 같은 원문 관계를 유지한다."""
import pytest

from src.features.composer.project_business_scope import project_business_scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize(('claim', 'source', 'expected'), [
    ('주요 도급공사 목록은 반도체 시설, 공항, 철도 등 다양한 현장에 걸쳐 있다.',
     '공사명 | 발주처\n연구동 공사 | 반도체회사\n공항 공사 | 건설회사', SCOPE_CONDITION_UNBOUND),
    ('주요 도급공사 목록은 반도체 시설, 공항, 철도 등 다양한 현장에 걸쳐 있다.',
     '공사명 | 시설분류\n연구동 공사 | 반도체 시설\n공항 공사 | 공항\n역 공사 | 철도', ''),
    ('연구동 공사는 반도체 시설, 공항 등 현장에 해당한다.',
     '공사명 | 시설분류\n연구동 공사 | 교육 시설\n시험동 공사 | 반도체 시설', SCOPE_CONDITION_UNBOUND),
    ('당사는 교육 시설을 포함하는 공사 사업을 수행한다.',
     '당사는 교육 시설 공사를 수행한다.', ''),
    ('당사는 교육 시설을 포함하는 공사 사업을 수행한다.',
     '다른 회사는 교육 시설 공사를 수행한다.', SCOPE_CONDITION_UNBOUND),
    ('당기 도급공사 목록에는 연구동 공사와 시험동 공사가 있다.',
     '공사명 | 발주처\n연구동 공사 | 건설회사', ''),
])
def test_aggregate_facility_scope(claim, source, expected):
    assert project_business_scope_problem(claim, {'self': source}) == expected
