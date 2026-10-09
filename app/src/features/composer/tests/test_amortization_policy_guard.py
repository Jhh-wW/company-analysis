"""작성된 순수 상각 운영 문장은 제외하고 실제 사업 혼합은 보존한다."""
import pytest

from src.features.composer.accounting_policy_guard import accounting_policy_problem


@pytest.mark.parametrize("claim", (
    "개발비는 관련 제품 등의 판매 또는 사용이 가능한 시점부터 5년 동안 정액법으로 상각하며, 그 상각액을 제조원가로 계상하는 방식으로 운영된다.",
    "유형자산은 정률법으로 상각한다.",
    "감가상각비는 제조원가로 계상한다.",
))
def test_상각방법을운영방식으로쓴검수참도거절한다(claim):
    assert accounting_policy_problem(claim, section_id="operations_partners") == "accounting_policy_boilerplate"


@pytest.mark.parametrize("claim", (
    "회사는 제품을 제조하고 있다. 개발비는 5년 정액법으로 상각한다.",
    "개발비는 5년 정액법으로 상각하며 회사는 제품을 제조하고 있다.",
    "고객의 개발비를 5년 정액법으로 상각하며 회계 서비스를 제공하고 있다.",
    "감가상각비가 증가해 생산원가가 상승했다.",
))
def test_실제사업서비스와원가사건은이경계로제거하지않는다(claim):
    assert not accounting_policy_problem(claim, section_id="operations_partners")
