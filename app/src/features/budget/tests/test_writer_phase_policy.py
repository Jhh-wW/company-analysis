"""명시 작성 선택의 예약 정책은 기존 한도와 독립된 무제한이 아니다."""
import pytest
import datetime as dt
import sqlite3

from src.features.budget import state_machine
from src.features.budget.constants import PAID_PHASE_PROVIDER_BUDGET_KRW, SPEND_PHASE_PIPELINE
from src.features.budget.writer_phase_policy import writer_pipeline_reservation_krw


@pytest.mark.parametrize("model,is_v2,expected", [
    ("", True, 2000), ("", False, 2000),
    ("claude-haiku-4-5", True, 2000), ("claude-haiku-4-5", False, 2000),
    ("claude-sonnet-4-6", True, 4000), ("claude-sonnet-4-6", False, 2000),
])
def test_only_explicit_v2_sonnet_has_additional_reservation(model, is_v2, expected):
    assert writer_pipeline_reservation_krw(model, is_v2=is_v2) == expected
    assert PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE] == 2000


@pytest.mark.parametrize("model,is_v2", [(None, True), ("unknown", True), ("claude-sonnet-4-6", 1)])
def test_unknown_or_ambiguous_selection_is_rejected(model, is_v2):
    with pytest.raises(ValueError):
        writer_pipeline_reservation_krw(model, is_v2=is_v2)


@pytest.mark.parametrize("daily_limit,total_limit,prior_cost,allowed", [
    (15000, None, 0, 3), (15000, 4000, 0, 1), (15000, 4000, 1553.26, 0),
])
def test_larger_reservation_still_obeys_atomic_daily_link_and_prior_exposure(daily_limit, total_limit, prior_cost, allowed):
    conn = sqlite3.connect(":memory:")
    state_machine.prepare_cutover(conn, migrated_at="2026-10-09T01:00:00+09:00")
    def begin(index):
        return state_machine.begin_phase(conn, run_id=f"writer-{index}", phase=SPEND_PHASE_PIPELINE,
            day=dt.date(2026, 10, 9), bucket="writer-policy-test",
            reservation_krw=writer_pipeline_reservation_krw("claude-sonnet-4-6", is_v2=True),
            bucket_limit_krw=daily_limit, run_limit_krw=4000, bucket_total_limit_krw=total_limit,
            bucket_prior_cost_krw=prior_cost, lease_owner_id=f"owner-{index}",
            lease_expires_at="2026-10-09T02:30:00+09:00", started_at="2026-10-09T01:00:00+09:00")
    try:
        for index in range(allowed):
            assert begin(index).reservation_krw == 4000
        with pytest.raises(state_machine.AdmissionLimitExceeded):
            begin(allowed)
    finally:
        conn.close()
