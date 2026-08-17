"""관리 화면 비용은 품질 JSONL이 아니라 SQLite 단계 원장을 본다."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_IDENTIFY, SPEND_PHASE_OCR
from src.features.observability import constants as obs
from src.features.observability.records import RunRecord, append_record
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import db as storage_db
from src.web import main
from src.web.recording import records_path


def _record(cost_krw: float) -> RunRecord:
    return RunRecord(
        run_id="quality-row",
        at=dt.datetime.now().isoformat(timespec="seconds"),
        corp_type=obs.CORP_TYPE_UNKNOWN,
        job="영업",
        end_step=obs.END_STEP_IDENTIFY,
        cache_hit=obs.CACHE_HIT_NONE,
        fragments_collected=0,
        fragments_cited=0,
        sentences_made=0,
        sentences_passed=0,
        cells_filled=0,
        cells_missing=[],
        cells_suspect=[],
        grade=obs.GRADE_NONE,
        human_check=obs.HUMAN_CHECK_NONE,
        cost_krw=cost_krw,
        elapsed_sec=1.0,
        model="quality-model",
    )


def test_대시보드는_원장비용과_미확정요청을_따로_보인다(monkeypatch):
    monkeypatch.setattr(main, "_PIPELINE", DemoPipeline())
    append_record(_record(9999.0), records_path())
    today = dt.date.today()
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn,
            run_id="paid-run",
            phase=SPEND_PHASE_IDENTIFY,
            day=today,
            bucket="bucket",
            cost_krw=123.0,
            created_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
        spend_store.begin_inflight(
            conn,
            run_id="uncertain-run",
            phase=SPEND_PHASE_OCR,
            day=today,
            bucket="bucket",
            started_at=dt.datetime.now().isoformat(timespec="seconds"),
        )

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin/dashboard")

    assert response.status_code == 200
    assert "123원" in response.text
    assert "9,999원" not in response.text
    assert "비용 미확정 실행" in response.text
    assert "1건" in response.text
    assert "SQLite 비용 원장 기준" in response.text


def test_대시보드는_현재_정상실행중인_표식을_비용미확정으로_세지_않는다(monkeypatch):
    monkeypatch.setattr(main, "_PIPELINE", DemoPipeline())

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        ticket = main._begin_paid_phase(
            run_id="healthy-active-run",
            phase=SPEND_PHASE_IDENTIFY,
            share_key="bucket",
        )
        assert ticket is not None

        response = client.get("/admin/dashboard")

        main._cancel_paid_phase(ticket)

    assert response.status_code == 200
    assert "비용 미확정 실행" in response.text
    assert "0건" in response.text
