"""명사형 도입에 새 실행 계획을 숨겨도 단일 증명으로 승인하지 않는다."""
import pytest

from src.features.composer.tests.test_future_proof_selection import prepared


@pytest.mark.parametrize("invented", ["공장 증설", "해외 진출", "창고 매각"])
def test_nominal_plan_with_independent_activity_requires_its_own_evidence(invented):
    source = "회사는 신제품을 개발할 예정이다."
    claim = f"회사는 {invented} 계획으로 신제품을 개발할 예정이다."
    assert prepared(claim, source)[0] == {}
