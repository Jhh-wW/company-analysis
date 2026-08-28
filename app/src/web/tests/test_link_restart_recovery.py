"""hard restart 뒤 LINK 생성 이력이 영구 running으로 남지 않는 계약."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.core import clock
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_IDENTIFY
from src.features.admin_dashboard import store as dashboard_store
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import paid_runtime, runtime
from src.web.main import app


def test_startup_interrupts_stale_LINK_run_with_confirmed_spend() -> None:
    raw_key = "restart-link-secret"
    run_id = "restart-interrupted-run"
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="데이터 분석",
            now_iso="2026-08-21T09:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=run_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="네이버",
            confirmed_company="네이버(주)",
            company_id="corp-naver",
        )
        spend_store.ensure_schema(conn)
        assert spend_store.append_spend(
            conn,
            run_id=run_id,
            phase=SPEND_PHASE_IDENTIFY,
            day=clock.today_kst(),
            bucket=raw_key,
            cost_krw=23.5,
            created_at="2026-08-21T10:00:10+09:00",
        )

    # 새 lifespan은 이전 프로세스의 메모리 작업을 이어갈 수 없으므로 DB의 running을
    # interrupted로 마감한다. 외부 요청이나 provider 호출은 전혀 필요 없다.
    with TestClient(app):
        with storage_db.connect() as conn:
            recovered = share_store.load_run(conn, run_id)

    assert recovered is not None
    assert recovered.status == share_store.RUN_STATUS_INTERRUPTED
    assert recovered.stop_step == "05_생성"
    assert recovered.stop_reason == "server_restart"
    assert recovered.finished_at
    assert recovered.internal_ai_cost_krw == 23.5


def test_attempt원장_전환뒤에도_LINK_재시작비용은_확정액만_복구한다() -> None:
    raw_key = "restart-attempt-link"
    run_id = "restart-attempt-run"
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="데이터 분석",
            now_iso="2026-08-21T09:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=run_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="네이버",
            confirmed_company="네이버(주)",
            company_id="corp-naver",
        )
        spend_store.ensure_schema(conn)
        assert spend_store.append_spend(
            conn,
            run_id=run_id,
            phase=SPEND_PHASE_IDENTIFY,
            day=clock.today_kst(),
            bucket=raw_key,
            cost_krw=23.5,
            created_at="2026-08-21T10:00:10+09:00",
        )

    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()
    runtime._recover_link_run_history()

    with storage_db.connect() as conn:
        recovered = share_store.load_run(conn, run_id)
    assert recovered is not None
    assert recovered.status == share_store.RUN_STATUS_INTERRUPTED
    assert recovered.internal_ai_cost_krw == 23.5


def test_startup_returns_MEMBER_success_slots_when_crashed_jobs_have_no_delivery() -> None:
    actor = "restart-member@example.com"
    day = clock.today_kst().isoformat()
    with storage_db.connect() as conn:
        for index in range(3):
            assert dashboard_store.reserve_member_run(
                conn,
                run_id=f"restart-member-{index}",
                actor_email=actor,
                day=day,
                now_iso=f"{day}T09:0{index}:00+09:00",
            )
        assert dashboard_store.member_can_start(
            conn,
            actor_email=actor,
            day=day,
        ) is False

    # provider나 외부 요청 없이 lifespan의 재시작 복구만 실행한다.
    with TestClient(app):
        pass

    with storage_db.connect() as conn:
        assert dashboard_store.member_usage_today(
            conn,
            actor_email=actor,
            day=day,
        ) == (0, 0)
        assert dashboard_store.member_can_start(
            conn,
            actor_email=actor,
            day=day,
        ) is True
        assert dashboard_store.list_reserved_member_runs(conn) == ()
