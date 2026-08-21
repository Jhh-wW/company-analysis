"""hard restart 뒤 LINK 생성 이력이 영구 running으로 남지 않는 계약."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.core import clock
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_IDENTIFY
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
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
