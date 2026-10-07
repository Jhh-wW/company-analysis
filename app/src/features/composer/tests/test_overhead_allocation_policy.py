"""조건부 원가 배부 규칙과 실제 가동·원가 사실을 구분한다."""
import pytest

from src.features.composer.accounting_policy_guard import accounting_policy_problem, accounting_policy_mixed

POLICY = "연결회사는 실제조업도가 정상조업도에 미달하는 경우 배부되지 않은 고정제조간접원가를 발생한 기간의 비용으로 인식합니다."


@pytest.mark.parametrize("section", ("operations_partners", "current_challenges", "identity"))
def test_conditional_cost_allocation_does_not_supply_business_body(section):
    assert accounting_policy_problem(POLICY, section_id=section) == "accounting_policy_boilerplate"


@pytest.mark.parametrize("actual", (
    "실제조업도가 정상조업도에 미달했다.",
    "공장 가동이 중단되어 조업도가 낮아졌다.",
    "생산원가가 급증했다.",
    "정상조업도보다 실제 가동률이 낮아 생산단위당 원가가 증가했다.",
    "연결회사는 공장 가동을 중단했다. 고정제조간접원가 부담이 커졌다.",
))
def test_actual_low_activity_interruption_and_cost_growth_are_preserved(actual):
    assert not accounting_policy_problem(actual, section_id="operations_partners")
    assert not accounting_policy_problem(actual + " " + POLICY, section_id="operations_partners")
    assert accounting_policy_mixed(actual + " " + POLICY, section_id="operations_partners")


@pytest.mark.parametrize("actual", ("생산이 중단됐다", "공장가동이 정지됐다", "생산원가가 급증했다", "생산이 중단되어", "생산원가가 급증해"))
def test_same_sentence_actual_business_fact_is_not_removed_with_conditional_accounting(actual):
    mixed = actual + ", " + POLICY
    assert not accounting_policy_problem(mixed, section_id="operations_partners")
