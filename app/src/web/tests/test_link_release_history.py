"""LINK 보고서의 출고 차단 상태를 영속 이력과 결속하는 회귀시험."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.cost_tracking import store as cost_store
from src.features.pipeline.port import Outcome
from src.features.report_standard.publish import (
    PublishBlockedError,
    PublishValidation,
)
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.web import job_runtime
from src.web.main import app
from src.web.routers import reports as reports_router


_REAL_RELEASE_STATE = reports_router._release_state


def _awaiting_link_run(*, raw_key: str, report_id: str) -> None:
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
            run_id=report_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="네이버",
            confirmed_company="네이버(주)",
            company_id="corp-naver",
        )
        assert share_store.finish_run(
            conn,
            run_id=report_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-08-21T10:01:00+09:00",
            report_id=report_id,
            internal_ai_cost_krw=123.0,
        )


def _publish_blocked(_report):
    raise PublishBlockedError(
        PublishValidation(False, reasons=("forced publish gate failure",))
    )


def test_publish_gate_출고시점에만_결속된_LINK_run을_막고_GET은_이력을_읽기만한다(
    monkeypatch,
):
    report_id = "44" * 16
    raw_key = "55" * 16
    _awaiting_link_run(raw_key=raw_key, report_id=report_id)
    job_runtime._JOBS.clear()
    validation_calls: list[str] = []

    def publication_blocked(_report):
        validation_calls.append("called")
        return _publish_blocked(_report)

    monkeypatch.setattr(reports_router, "_report_for_output", publication_blocked)

    with pytest.raises(PublishBlockedError):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="corp-naver",
            billing_bucket_id=raw_key,
            report=object(),
            actual_models=("test-model",),
            reused_from_cache=False,
        )

    assert validation_calls == ["called"]

    with TestClient(app) as client:
        # 실제 생성 흐름에서는 조사 시작 전에 /k가 이 쿠키를 발급한다.
        # 여기서는 시작 보고서가 없는 실행 이력만 만들었으므로 그 이미 끝난
        # 입구를 재연하지 않고, 재시작을 가로질러 살아야 할 쿠키+run 결속을 쓴다.
        client.cookies.set(KEY_COOKIE_NAME, raw_key)
        with storage_db.connect() as conn:
            assert share_store.load_run(conn, report_id) is not None
        blocked = client.get(f"/result/{report_id}", follow_redirects=False)

    assert blocked.status_code == 409
    assert "현재 보고서 기준을 통과한 근거가 충분하지 않아" in blocked.text
    # 공개 GET은 현재 validator를 다시 불러 상태나 판정을 바꾸지 않는다.
    assert validation_calls == ["called"]
    with storage_db.connect() as conn:
        run = share_store.load_run(conn, report_id)
        assert run is not None
        assert run.status == share_store.RUN_STATUS_STOPPED
        assert run.stop_step == "automatic_release_gate"
        assert run.stop_reason == "automatic_release_gate_stopped"
        assert run.finished_at
        assert run.internal_ai_cost_krw == 123.0


def test_publish_gate_history_storage_failure_does_not_mask_original_failure_or_409(
    monkeypatch,
):
    report_id = "56" * 16
    job_runtime._JOBS.clear()
    monkeypatch.setattr(reports_router, "_report_for_output", _publish_blocked)
    monkeypatch.setattr(
        share_store,
        "mark_release_stopped",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("storage down")),
    )

    # 부가 이력 저장 장애가 원래의 출고 차단 예외를 바꾸면 안 된다.
    with pytest.raises(PublishBlockedError):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="corp-test",
            billing_bucket_id="public",
            report=object(),
            actual_models=("test-model",),
            reused_from_cache=False,
        )

    # 재시작 뒤 공개 GET은 실패 intent를 읽어 같은 409를 내며, 동적 검사를
    # 다시 실행하지 않는다. 관리자는 현재 권한으로만 이 이력을 볼 수 있다.
    session = auth_logic.create_session(
        "admin@example.com", True, subject="google:test-release-admin"
    )
    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        blocked = client.get(f"/result/{report_id}", follow_redirects=False)

    assert blocked.status_code == 409
    assert "현재 보고서 기준을 통과한 근거가 충분하지 않아" in blocked.text


def test_direct_notion_publish_gate_stops_bound_link_without_calling_notion(
    monkeypatch,
):
    report_id = "66" * 16
    _awaiting_link_run(raw_key="77" * 16, report_id=report_id)
    session = auth_logic.create_session("admin@example.com", True)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: object())
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_report_for_output", _publish_blocked)
    monkeypatch.setattr(
        reports_router,
        "_supervise_notion_export",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("publish gate 뒤 Notion을 호출했습니다")
        ),
    )

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        blocked = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": auth_logic.csrf_token_for_session(session.token)},
            follow_redirects=False,
        )

    assert blocked.status_code == 409
    assert "현재 보고서 기준을 통과한 근거가 충분하지 않아" in blocked.text
    with storage_db.connect() as conn:
        run = share_store.load_run(conn, report_id)
    assert run is not None
    assert run.status == share_store.RUN_STATUS_STOPPED
    assert run.stop_step == "automatic_release_gate"
    assert run.stop_reason == "automatic_release_gate_stopped"


def test_release_uses_bound_run_id_for_cost_and_does_not_create_report_id_summary(
    monkeypatch,
):
    raw_key = "88" * 16
    run_id = "99" * 16
    report_id = "aa" * 16
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
        assert share_store.finish_run(
            conn,
            run_id=run_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-08-21T10:01:00+09:00",
            report_id=report_id,
            internal_ai_cost_krw=123.0,
        )
        cost_store.record_run_costs(
            conn,
            run_id=run_id,
            outcome=Outcome.REPORT,
            internal_ai_cost_krw=123.0,
            events=(
                cost_store.AiCostEvent(
                    stage="report_generation",
                    model_id="test-model",
                    input_tokens=10,
                    output_tokens=5,
                    cost_krw=123.0,
                ),
            ),
        )

    candidate = SimpleNamespace(pdf_sha256="a" * 64)
    release_record = SimpleNamespace(
        pdf_sha256="a" * 64,
        record_sha256="b" * 64,
        released_at="2026-08-21T10:02:00+09:00",
    )
    released = SimpleNamespace(record=release_record, content=b"pdf")
    monkeypatch.setattr(
        reports_router,
        "_candidate_for_report",
        lambda _report_id, _report: candidate,
    )
    monkeypatch.setattr(reports_router, "report_sha256", lambda _report: "c" * 64)
    monkeypatch.setattr(
        reports_router.pdf_release_store,
        "load_automatic_release_record",
        lambda *_args, **_kwargs: release_record,
    )
    monkeypatch.setattr(
        reports_router,
        "restore_automatic_release",
        lambda _report, _candidate, _record: released,
    )

    assert _REAL_RELEASE_STATE(report_id=report_id, report=object()) == (
        candidate,
        released,
    )

    with storage_db.connect() as conn:
        summaries = conn.execute(
            f"SELECT run_id, internal_ai_cost_krw, automatic_release_sha256 "
            f"FROM {cost_store.RUN_COST_TABLE} ORDER BY run_id"
        ).fetchall()
        events = conn.execute(
            f"SELECT run_id FROM {cost_store.AI_EVENT_TABLE} ORDER BY run_id"
        ).fetchall()
        run = share_store.load_run(conn, run_id)

    assert [tuple(row) for row in summaries] == [(run_id, 123.0, "b" * 64)]
    assert [tuple(row) for row in events] == [(run_id,)]
    assert run is not None
    assert run.status == share_store.RUN_STATUS_COMPLETED
    assert run.report_id == report_id
    assert run.release_sha256 == "b" * 64
