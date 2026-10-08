"""현재 진행 중인 후원과 미래 계약 갱신을 독립적으로 구분한다."""
import datetime as dt

import pytest

from src.features.news_intake.contract_execution_context import contract_execution_context_problem
from src.features.news_intake.models import NewsCompanyContext


@pytest.mark.parametrize("current", [
    "예시제조는 2023년부터 '도시 경연'을 후원하고 있다. ",
    "예시제조는 현재 '도시 경연'을 후원하고 있다. ",
])
def test_existing_fulfillment_is_not_erased_by_future_renewal(current):
    future = "예시제조는 2027년부터 '도시 경연'의 후원을 연장한다. "
    signed = "예시제조는 후원 연장 계약을 체결했다. "
    claim = "예시제조는 '도시 경연'을 후원한다."
    assert contract_execution_context_problem(
        claim, current + future + signed + claim, NewsCompanyContext("예시제조"),
        dt.date(2026, 10, 9), temporal_status="ongoing",
    ) == ""
