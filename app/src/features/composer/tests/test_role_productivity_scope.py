"""성질을 나타내는 생산성과 실제 생산 역할을 구분한다."""
import pytest
from src.features.composer.role_binding import claims_role_or_fee


@pytest.mark.parametrize('text', ['생산성 향상', '생산성향상을 위한 투자', '시스템 고도화 및 생산성 향상'])
def test_productivity_is_not_a_production_role(text):
    assert not claims_role_or_fee('', (text,))


@pytest.mark.parametrize('text', ['생산', '생산 및 제작', '부품을 생산한다', '생산하고 있다', '생산성 향상과 부품 생산'])
def test_actual_production_roles_keep_binding(text):
    assert claims_role_or_fee('', (text,))
