"""회계 항목과 직접 사업 관계의 경계를 확인한다."""
import pytest

from src.features.composer.revenue_activity_scope import revenue_activity_scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize(('claim', 'source', 'expected'), [
    ('판매비와관리비에는 수출제비용이 있다.', '수출제비용 | 20 | 30', ''),
    ('수출매출원가와 해외판매비가 기재되어 있다.', '수출제비용 | 20 | 30', ''),
    ('당사는 수출 매출 경로를 보유한다.', '수출제비용 | 20 | 30', SCOPE_CONDITION_UNBOUND),
    ('당사는 수출 매출 경로가 존재하며 임대 매출은 없다.', '수출제비용 | 20 | 30', SCOPE_CONDITION_UNBOUND),
    ('당사의 수출 매출은 확인되지 않았다.', '수출제비용 | 20 | 30', ''),
    ('당사는 수출 매출 경로가 존재한다.', '수출제비용 | 20 | 30. 당사는 수출 매출 경로가 존재하며 임대 매출은 없다.', ''),
    ('당사는 수출 매출 경로가 존재한다.', '수출제비용 | 20 | 30. 당사의 수출 매출은 확인되지 않았다.', SCOPE_CONDITION_UNBOUND),
    ('당사는 해외 고객에게 제품을 판매한다.', '수출제비용 | 20 | 30', SCOPE_CONDITION_UNBOUND),
    ('당사는 수출 매출 경로를 보유한다.', '수출제비용 | 20 | 30\n수출매출 | 80 | 90', ''),
    ('당사는 해외 고객에게 제품을 판매한다.', '당사는 해외 고객에게 제품을 판매한다. 수출제비용 | 20 | 30', ''),
    ('당사는 수출 매출 경로를 보유한다.', '수출제비용 | 20 | 30. 다른 회사는 수출 매출을 얻는다.', SCOPE_CONDITION_UNBOUND),
    ('당사의 임대 사업은 수출 매출을 얻는다.', '수출제비용 | 20 | 30. 당사는 센서 사업에서 수출 매출을 얻는다.', SCOPE_CONDITION_UNBOUND),
    ('당사는 수출 매출을 얻는다.', '수출제비용 | 20 | 30. 당사는 수출 매출을 얻을 예정이다.', SCOPE_CONDITION_UNBOUND),
    ('당사의 임대 사업은 수출 매출을 얻는다.', '수출제비용 | 20 | 30. 당사의 임대 사업은 수출 매출을 얻는다.', ''),
    ('임대매출은 당기부터 표시되어 있다.', '임대매출 | 50 | -', ''),
    ('임대매출은 당기에 인식되었다.', '임대매출 | 50 | 0', ''),
    ('임대 수익 경로는 당기에 새로 발생했다.', '임대매출 | 50 | -', SCOPE_CONDITION_UNBOUND),
    ('회사는 임대 사업을 새로 시작했다.', '임대매출 | 50 | 0', SCOPE_CONDITION_UNBOUND),
    ('회사는 임대 사업을 새로 시작했다.', '회사는 임대 사업을 새로 시작했다.', ''),
    ('회사는 임대 사업을 새로 시작했다.', '다른 회사는 임대 사업을 새로 시작했다.', SCOPE_CONDITION_UNBOUND),
    ('회사는 임대 사업을 새로 시작했다.', '회사는 센서 사업을 새로 시작했다.', SCOPE_CONDITION_UNBOUND),
    ('회사는 2025년에 임대 사업을 새로 시작했다.', '회사는 2019년에 임대 사업을 시작했다.', SCOPE_CONDITION_UNBOUND),
    ('회사는 2025년에 임대 사업을 새로 시작했다.', '회사는 2025년에 임대 사업을 새로 시작했다.', ''),
    ('회사는 임대 사업을 시작할 예정이다.', '임대매출 | 50 | -', ''),
])
def test_revenue_relation_scope(claim, source, expected):
    assert revenue_activity_scope_problem(claim, {'self': source}) == expected


def test_separate_own_quote_supports_export_and_start():
    sources = {'cost': '수출제비용 | 20 | 30',
               'sales': '회사는 해외 고객에게 제품을 판매한다.',
               'start': '회사는 임대 사업을 새로 시작했다.'}
    assert revenue_activity_scope_problem('회사는 해외 고객에게 제품을 판매한다.', sources) == ''
    assert revenue_activity_scope_problem('회사는 임대 사업을 새로 시작했다.', sources) == ''
