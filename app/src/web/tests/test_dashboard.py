"""승인된 운영 대시보드의 권한·차단·HTML 새로고침 계약."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from src.features.admin_dashboard import kpi as dashboard_kpi
from src.features.admin_dashboard import maintenance as dashboard_maintenance
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.backup import status as backup_status
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist
from src.features.sharelink import store as share_store
from src.features.storage import constants as storage_constants
from src.features.storage import db, reports
from src.web import main, runtime
from src.web.routers import dashboard as dashboard_router
from src.web.routers import maintenance as maintenance_router
from src.web.routers import reports as reports_router


def _session(client: TestClient, *, email: str, is_admin: bool) -> str:
    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return auth_logic.csrf_token_for_session(session.token)


def _seed_report() -> str:
    report_id = "dashboard-report-1"
    with db.connect() as conn:
        reports.save(conn, report_id, "CORP-001", "", build_demo_report())
    return report_id


def test_admin_dashboard_has_no_public_json_and_refresh_is_admin_only(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        denied = client.get("/admin/refresh/today", follow_redirects=False)
        assert denied.status_code in (302, 303, 307)
        csrf = _session(client, email="admin@example.com", is_admin=True)
        response = client.get("/admin")
        fragment = client.get("/admin/refresh/today")
        settings = client.get("/admin/settings")

    assert csrf
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "오늘" in response.text and "문제·보고서" in response.text
    assert fragment.status_code == 200 and fragment.headers["content-type"].startswith("text/html")
    assert settings.status_code == 200


def test_member_error_closes_result_and_pdf_until_manual_republish(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    report_id = _seed_report()
    with db.connect() as conn:
        assert allowlist.invite(conn, email="member@example.com", note="", now_iso="2026-08-22T10:00:00+09:00")
    with TestClient(main.app) as client:
        csrf = _session(client, email="member@example.com", is_admin=False)
        reported = client.post(
            f"/reports/{report_id}/errors", follow_redirects=False,
            data={"area": "매출 표", "reason": "원출처와 다릅니다", "csrf_token": csrf},
        )
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)

    assert reported.status_code == 303
    assert result.status_code == 409
    assert pdf.status_code == 409
    assert "오류 신고가 접수되어" in result.text


def test_survey_is_member_only_and_revision_is_visible_to_admin(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    report_id = _seed_report()
    with db.connect() as conn:
        allowlist.invite(conn, email="member@example.com", note="친구", now_iso="2026-08-22T10:00:00+09:00")
    with TestClient(main.app) as member:
        csrf = _session(member, email="member@example.com", is_admin=False)
        viewed = member.get(f"/result/{report_id}")
        saved = member.post(
            f"/reports/{report_id}/survey", follow_redirects=False,
            data={"rating": "5", "overall_feedback": "유용합니다", "business_distinction": "구분점이 잘 보입니다", "csrf_token": csrf},
        )
    with TestClient(main.app) as admin:
        _session(admin, email="admin@example.com", is_admin=True)
        detail = admin.get(f"/admin/reports/{report_id}")

    assert saved.status_code == 303
    assert viewed.status_code == 200
    assert detail.status_code == 200
    assert "member@example.com" in detail.text
    assert "5점" in detail.text
    with db.connect() as conn:
        assert dashboard_kpi.summary(conn) == dashboard_kpi.KpiSummary(
            measured_responses=1,
            within_target=1,
        )


def test_admin_dashboard_labels_three_minute_metric_as_response_not_accuracy(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()

    with TestClient(main.app) as client:
        _session(client, email="admin@example.com", is_admin=True)
        response = client.get("/admin/members")

    assert response.status_code == 200
    assert "3분 내 구분점 응답" in response.text
    assert "근거 정확성은 관리자 검토" in response.text


def test_member_revocation_blocks_existing_session_result_and_pdf(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    report_id = _seed_report()
    with db.connect() as conn:
        assert allowlist.invite(conn, email="member@example.com", note="", now_iso="2026-08-22T10:00:00+09:00")
    with TestClient(main.app) as client:
        _session(client, email="member@example.com", is_admin=False)
        with db.connect() as conn:
            assert allowlist.revoke(conn, "member@example.com")
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)

    assert result.status_code == 403
    assert pdf.status_code == 403


def test_link_open_confirmation_is_csrf_post_and_get_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    raw_key = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
    with db.connect() as conn:
        assert share_store.insert_new(conn, key=raw_key, company="CORP", job="", now_iso="2026-08-22T10:00:00+09:00")
        assert share_store.mark_opened(conn, raw_key, "2026-08-22T10:01:00+09:00")
    key_hash = share_store.key_hash_of(raw_key)
    with TestClient(main.app) as client:
        csrf = _session(client, email="admin@example.com", is_admin=True)
        first = client.get(f"/admin/links/{key_hash}")
        second = client.get(f"/admin/links/{key_hash}")
        confirmed = client.post(
            f"/admin/links/{key_hash}/opens/confirm",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        cleared = client.get(f"/admin/links/{key_hash}")

    assert "새 접속 확인" in first.text
    assert "새 접속 확인" in second.text
    assert confirmed.status_code == 303
    assert "새로 확인할 접속이 없습니다" in cleared.text


def test_link_list_sorts_by_recent_open_and_marks_unseen(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    with db.connect() as conn:
        assert share_store.insert_new(conn, key="old-link-key", company="OLD", job="", now_iso="2026-08-22T10:00:00+09:00")
        assert share_store.insert_new(conn, key="new-link-key", company="NEW", job="", now_iso="2026-08-22T10:01:00+09:00")
        assert share_store.mark_opened(conn, "old-link-key", "2026-08-22T10:02:00+09:00")
        assert share_store.mark_opened(conn, "new-link-key", "2026-08-22T10:03:00+09:00")
    with TestClient(main.app) as client:
        _session(client, email="admin@example.com", is_admin=True)
        response = client.get("/admin/links")

    assert response.status_code == 200
    assert response.text.index("NEW") < response.text.index("OLD")
    assert "새 접속 1건" in response.text


def test_member_page_has_period_controls_and_does_not_fake_legacy_summary(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        _session(client, email="admin@example.com", is_admin=True)
        response = client.get("/admin/members?period=7d")

    assert response.status_code == 200
    assert "최근 7일" in response.text
    assert "확인 불가" in response.text


def test_settings_default_is_read_only_and_component_states_are_not_faked(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "fixture-client-id")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        _session(client, email="admin@example.com", is_admin=True)
        readonly = client.get("/admin/settings")
        change = client.get("/admin/settings/change")

    assert readonly.status_code == 200
    assert "변경하기" in readonly.text
    assert 'action="/admin/settings/service"' not in readonly.text
    assert "아직 사용 안 함" in readonly.text
    assert '<h2>Google</h2><p><strong>확인 불가</strong>' in readonly.text
    assert "이미 완료됐거나 다른 실행이 진행 중이면" in change.text
    assert "오늘 정리가 이미 완료됐거나 진행 중이면" in change.text
    assert change.status_code == 200
    assert 'action="/admin/settings/service"' in change.text


def test_settings_shows_persisted_backup_failure_instead_of_configuration_guess(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setattr(
        dashboard_router.clock,
        "iso_now_kst",
        lambda: "2026-08-22T12:00:00+09:00",
    )
    runtime._PIPELINE = DemoPipeline()
    with db.connect() as conn:
        backup_status.record_failure(
            conn,
            now_iso="2026-08-22T04:00:00+09:00",
            failure_summary=backup_status.FAILURE_EXECUTION,
        )
    with TestClient(main.app) as client:
        _session(client, email="admin@example.com", is_admin=True)
        response = client.get("/admin/settings")

    assert response.status_code == 200
    assert "최근 실행 실패" in response.text
    assert "마지막 시도 2026-08-22T04:00:00+09:00" in response.text
    assert backup_status.FAILURE_EXECUTION in response.text
    assert "환경변수 설정 여부가 아니라 실제 백업" in response.text


def test_admin_can_make_and_download_last_completed_weekly_xlsx(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setattr(
        dashboard_maintenance.clock,
        "now_kst",
        lambda: datetime.fromisoformat("2026-08-24T04:10:00+09:00"),
    )
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        csrf = _session(client, email="admin@example.com", is_admin=True)
        made = client.post(
            "/admin/settings/weekly-reports/run",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        downloaded = client.get("/admin/settings/weekly-reports/2026-08-17/download")

    assert made.status_code == 303
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert load_workbook(BytesIO(downloaded.content)).sheetnames == [
        "한눈에 보기", "친구 이용", "피드백·문제"
    ]


def test_admin_button_and_internal_cron_use_the_same_maintenance_service(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    calls = []

    def shared_service(
        connect,
        *,
        operation,
        actor_email=dashboard_maintenance.SYSTEM_ACTOR_EMAIL,
    ):
        calls.append((connect, operation, actor_email))
        return dashboard_maintenance.MaintenanceResult(
            operation=operation,
            period_key=(
                "2026-08-17"
                if operation == dashboard_maintenance.OPERATION_WEEKLY
                else "2026-08-24"
            ),
            status="already_done",
        )

    monkeypatch.setattr(
        dashboard_maintenance,
        "run_current_operation",
        shared_service,
    )
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        csrf = _session(client, email="admin@example.com", is_admin=True)
        manual_weekly = client.post(
            "/admin/settings/weekly-reports/run",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        manual_cleanup = client.post(
            "/admin/settings/trash-cleanup/run",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
    cron_weekly = maintenance_router._run(dashboard_maintenance.OPERATION_WEEKLY)
    cron_cleanup = maintenance_router._run(dashboard_maintenance.OPERATION_CLEANUP)

    assert manual_weekly.status_code == 303
    assert manual_cleanup.status_code == 303
    assert cron_weekly.status == "already_done"
    assert cron_cleanup.status == "already_done"
    assert [call[1] for call in calls] == [
        dashboard_maintenance.OPERATION_WEEKLY,
        dashboard_maintenance.OPERATION_CLEANUP,
        dashboard_maintenance.OPERATION_WEEKLY,
        dashboard_maintenance.OPERATION_CLEANUP,
    ]
    assert calls[0][2] == "admin@example.com"
    assert calls[1][2] == "admin@example.com"
    assert calls[2][2] == dashboard_maintenance.SYSTEM_ACTOR_EMAIL
    assert calls[3][2] == dashboard_maintenance.SYSTEM_ACTOR_EMAIL
    assert cron_weekly.period_key == "2026-08-17"
    assert cron_cleanup.period_key == "2026-08-24"


def test_admin_trash_blocks_public_result_until_restore(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    report_id = _seed_report()
    with TestClient(main.app) as client:
        csrf = _session(client, email="admin@example.com", is_admin=True)
        trashed = client.post(
            f"/admin/reports/{report_id}/trash",
            data={"reason": "duplicate", "csrf_token": csrf},
            follow_redirects=False,
        )
        blocked = client.get(f"/result/{report_id}", follow_redirects=False)
        notion_blocked = client.post(
            f"/notion/{report_id}",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        restored = client.post(
            f"/admin/reports/{report_id}/restore",
            data={"reason": "keep", "csrf_token": csrf},
            follow_redirects=False,
        )

    assert trashed.status_code == 303
    assert blocked.status_code == 409
    assert notion_blocked.status_code == 409
    assert restored.status_code == 303


def test_corrected_snapshot_is_required_before_republish_and_overrides_live_memory(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    runtime._PIPELINE = DemoPipeline()
    report_id = _seed_report()
    with db.connect() as conn:
        original = reports.load(conn, report_id)
        assert original is not None
        dashboard_store.register_report(
            conn,
            report_id=report_id,
            corp_type=original.corp_type,
            payload_json=reports.report_to_json(original),
            now_iso="2026-08-22T10:00:00+09:00",
        )
        dashboard_store.record_error(
            conn,
            report_id=report_id,
            actor_email="member@example.com",
            area="출처",
            reason="수정이 필요합니다",
            now_iso="2026-08-22T10:01:00+09:00",
        )
        for status in (dashboard_store.REPORT_STATUS_FIXING, dashboard_store.REPORT_STATUS_RECHECKING):
            dashboard_store.change_report_state(
                conn,
                report_id=report_id,
                next_status=status,
                actor_email="admin@example.com",
                reason="수정본 재검사",
                now_iso="2026-08-22T10:02:00+09:00",
            )
        corrected_payload = reports.report_to_json(
            replace(original, generated_at="2026-08-20")
        )

    with TestClient(main.app) as client:
        csrf = _session(client, email="admin@example.com", is_admin=True)
        early_publish = client.post(
            f"/admin/reports/{report_id}/state",
            data={
                "status": "normal", "company_type": "listed",
                "reason": "수정본 수동 재공개", "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        registered = client.post(
            f"/admin/reports/{report_id}/corrected-payload",
            data={"corrected_payload_json": corrected_payload, "csrf_token": csrf},
            follow_redirects=False,
        )
        published = client.post(
            f"/admin/reports/{report_id}/state",
            data={
                "status": "normal", "company_type": "listed",
                "reason": "수정본 수동 재공개", "csrf_token": csrf,
            },
            follow_redirects=False,
        )

    assert early_publish.status_code == 400
    assert registered.status_code == 303
    assert published.status_code == 303
    approved = reports_router._approved_public_report(report_id, original)
    assert approved is not None
    assert approved.generated_at == "2026-08-20"
