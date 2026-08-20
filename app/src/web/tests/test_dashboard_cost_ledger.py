"""관리 화면 비용은 품질 JSONL이 아니라 SQLite 단계 원장을 본다."""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

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
from src.web import paid_runtime, runtime
from src.web.recording import records_path
from src.web.routers import admin as admin_router


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
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
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
        # startup 복원 뒤에 품질 JSONL만 추가해, 이 시험이 비교하려는 두 원장을
        # 분리한다. startup 전에 넣으면 의도대로 legacy 비용 미확정 health가 된다.
        append_record(_record(9999.0), records_path())
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin/dashboard")

    assert response.status_code == 200
    assert "123원" in response.text
    assert "9,999원" not in response.text
    assert "비용 미확정 실행" in response.text
    assert "1건" in response.text
    assert "SQLite 비용 원장 기준" in response.text
    assert 'aria-labelledby="service-status-title"' in response.text
    assert 'aria-labelledby="recent-requests-title"' in response.text
    assert 'role="region" tabindex="0"' in response.text
    assert response.text.count('scope="col"') >= 13


def test_대시보드는_현재_정상실행중인_표식을_비용미확정으로_세지_않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        ticket = paid_runtime._begin_paid_phase(
            run_id="healthy-active-run",
            phase=SPEND_PHASE_IDENTIFY,
            share_key="bucket",
        )
        assert ticket is not None

        response = client.get("/admin/dashboard")

        paid_runtime._cancel_paid_phase(ticket)

    assert response.status_code == 200
    assert "비용 미확정 실행" in response.text
    assert "0건" in response.text


def test_UTC호스트의_KST월경계에도_9월원장과_대시보드기준일이_같다(
    monkeypatch,
):
    """KST 9월 1일 00:30은 UTC host-local 날짜로는 아직 8월 31일이다."""
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    kst_today = dt.date(2026, 9, 1)
    clock_calls: list[dt.date] = []

    def kst_month_boundary() -> dt.date:
        clock_calls.append(kst_today)
        return kst_today

    class UTCDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 8, 31)

    monkeypatch.setattr(
        admin_router,
        "clock",
        SimpleNamespace(
            today_kst=kst_month_boundary,
            business_day_label=lambda day: f"{day.isoformat()} (한국시간)",
        ),
    )
    # 수정 전 route의 host-local `dt.date.today()`가 남아 있으면 8월을 고른다.
    monkeypatch.setattr(admin_router, "dt", SimpleNamespace(date=UTCDate))
    append_record(
        replace(_record(0.0), at="2026-09-01T00:15:00+09:00"),
        records_path(),
    )
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn,
            run_id="august-cost",
            phase=SPEND_PHASE_IDENTIFY,
            day=dt.date(2026, 8, 31),
            bucket="bucket",
            cost_krw=654.0,
            created_at="2026-08-31T23:59:00+09:00",
        )
        spend_store.append_spend(
            conn,
            run_id="september-cost",
            phase=SPEND_PHASE_IDENTIFY,
            day=kst_today,
            bucket="bucket",
            cost_krw=321.0,
            created_at="2026-09-01T00:01:00+09:00",
        )

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get("/admin/dashboard")

    compact = " ".join(response.text.split())
    assert response.status_code == 200
    assert "321원" in response.text
    assert "654원" not in response.text
    assert "<b>1</b><span>오늘 처리 건수</span>" in compact
    assert "비용·일일 통계 기준일: <strong>2026-09-01 (한국시간)</strong>" in compact
    assert clock_calls == [kst_today]


def test_대시보드는_비용저장소_health_false를_정상으로_꾸미지않는다(
    monkeypatch,
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        with storage_db.connect() as conn:
            spend_store.append_spend(
                conn,
                run_id="must-hide-unhealthy-cost",
                phase=SPEND_PHASE_IDENTIFY,
                day=admin_router.clock.today_kst(),
                bucket="bucket",
                cost_krw=321.0,
                created_at="2026-08-18T10:00:00+09:00",
            )
        monkeypatch.setattr(paid_runtime, "_BUDGET_STORE_HEALTHY", False)
        response = client.get("/admin/dashboard")

    compact = " ".join(response.text.split())
    assert response.status_code == 503
    assert "no-store" in response.headers["cache-control"].split(", ")
    assert response.headers["x-request-id"]
    assert 'role="alert"' in response.text
    assert "비용 원장을 확인할 수 없습니다" in response.text
    assert "<b>확인 불가</b>" in compact
    assert "<b>비용 원장 확인 불가</b>" in compact
    assert "정상 상태로 판단하지 않습니다" in response.text
    assert "321원" not in response.text


def test_대시보드는_비용DB예외를_0원이나_정상으로_꾸미지않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    def broken_month(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic ledger failure")

    monkeypatch.setattr(admin_router.spend_store, "load_month", broken_month)

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        with storage_db.connect() as conn:
            spend_store.append_spend(
                conn,
                run_id="must-hide-mid-read-cost",
                phase=SPEND_PHASE_IDENTIFY,
                day=admin_router.clock.today_kst(),
                bucket="bucket",
                cost_krw=456.0,
                created_at="2026-08-18T10:00:00+09:00",
            )
        response = client.get("/admin/dashboard")

    compact = " ".join(response.text.split())
    assert response.status_code == 503
    assert 'role="alert"' in response.text
    assert "<b>확인 불가</b>" in compact
    assert "<b>비용 원장 확인 불가</b>" in compact
    assert "정상 상태로 판단하지 않습니다" in response.text
    assert "456원" not in response.text


def test_대시보드는_원장조회중_health가_깨져도_읽은값을_숨긴다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    original_load_month = admin_router.spend_store.load_month

    def unhealthy_after_read(*args, **kwargs):
        result = original_load_month(*args, **kwargs)
        paid_runtime._BUDGET_STORE_HEALTHY = False
        return result

    monkeypatch.setattr(admin_router.spend_store, "load_month", unhealthy_after_read)

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        with storage_db.connect() as conn:
            spend_store.append_spend(
                conn,
                run_id="must-hide-mid-read-cost",
                phase=SPEND_PHASE_IDENTIFY,
                day=admin_router.clock.today_kst(),
                bucket="bucket",
                cost_krw=789.0,
                created_at="2026-08-18T10:00:00+09:00",
            )
        response = client.get("/admin/dashboard")

    compact = " ".join(response.text.split())
    assert response.status_code == 503
    assert "<b>확인 불가</b>" in compact
    assert "<b>비용 원장 확인 불가</b>" in compact
    assert "789원" not in response.text
