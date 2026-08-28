from __future__ import annotations

import sqlite3

import pytest

from src.features.admin_dashboard import store
from src.features.storage import constants as storage_constants
from src.features.storage import db


def test_error_blocks_immediately_and_events_are_append_only(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        error = store.record_error(
            conn,
            report_id="report-a",
            actor_email="member@example.com",
            area="매출 표",
            reason="원출처 수치와 다릅니다",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        assert error.status == store.REPORT_STATUS_PENDING
        assert store.report_is_blocked(conn, "report-a") is True
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_ERRORS} WHERE id = ?", (error.id,))


def test_manual_republication_requires_fix_and_recheck_and_keeps_error_event(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.register_report(
            conn,
            report_id="report-a",
            corp_type="상장사",
            payload_json='{"version": 1}',
            now_iso="2026-08-22T09:59:00+09:00",
        )
        store.record_error(
            conn, report_id="report-a", actor_email="member@example.com",
            area="출처", reason="링크가 다릅니다", now_iso="2026-08-22T10:00:00+09:00",
        )
        with pytest.raises(ValueError):
            store.change_report_state(
                conn, report_id="report-a", next_status=store.REPORT_STATUS_NORMAL,
                actor_email="admin@example.com", reason="바로 열기", now_iso="2026-08-22T10:01:00+09:00",
            )
        for status in (store.REPORT_STATUS_FIXING, store.REPORT_STATUS_RECHECKING):
            state = store.change_report_state(
                conn, report_id="report-a", next_status=status,
                actor_email="admin@example.com", reason="수동 처리", now_iso="2026-08-22T10:02:00+09:00",
            )
        with pytest.raises(ValueError):
            store.change_report_state(
                conn, report_id="report-a", next_status=store.REPORT_STATUS_NORMAL,
                actor_email="admin@example.com", reason="수정본 없음", now_iso="2026-08-22T10:03:00+09:00",
            )
        assert store.capture_report_snapshot(
            conn, report_id="report-a", version=2, payload_json='{"version": 2}',
            actor="admin@example.com", now_iso="2026-08-22T10:03:00+09:00",
        )
        state = store.change_report_state(
            conn, report_id="report-a", next_status=store.REPORT_STATUS_NORMAL,
            actor_email="admin@example.com", reason="수정본 수동 재공개", now_iso="2026-08-22T10:04:00+09:00",
        )
        assert state.blocked is False
        assert state.version == 2
        assert store.list_open_errors(conn) == []
        count = conn.execute(f"SELECT COUNT(*) FROM {store.TABLE_ERRORS}").fetchone()[0]
        assert count == 1


def test_survey_revision_preserves_each_submission(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        first = store.save_survey(
            conn, report_id="report-a", actor_email="member@example.com", rating=4,
            overall_feedback="전체 구성이 좋습니다", business_distinction="사업 구분이 보입니다",
            add_information="고객군", delete_information="", now_iso="2026-08-22T10:00:00+09:00",
        )
        second = store.save_survey(
            conn, report_id="report-a", actor_email="member@example.com", rating=5,
            overall_feedback="수정했습니다", business_distinction="차별점이 분명합니다",
            add_information="", delete_information="중복 설명", now_iso="2026-08-22T10:02:00+09:00",
        )
        assert (first, second) == (1, 2)
        assert conn.execute(f"SELECT COUNT(*) FROM {store.TABLE_SURVEY_EVENTS}").fetchone()[0] == 2
        snapshots = conn.execute(
            f"SELECT report_version, revision, rating FROM {store.TABLE_SURVEY_SNAPSHOT_EVENTS} ORDER BY id"
        ).fetchall()
        assert [(row["report_version"], row["revision"], row["rating"]) for row in snapshots] == [(1, 1, 4), (1, 2, 5)]
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_SURVEY_SNAPSHOT_EVENTS}")
        assert store.survey_summary(conn) == (1, 1)


def test_survey_answers_and_revisions_are_scoped_to_report_version(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {storage_constants.TABLE_REPORTS} VALUES (?, ?, ?, ?, ?, ?)",
            (
                "report-versioned", "CORP-VERSIONED", "직무",
                '{"company":"버전1"}', "2026-08-22", "2026-08-22",
            ),
        )
        store.register_report(
            conn,
            report_id="report-versioned",
            corp_type="상장사",
            payload_json='{"company":"버전1"}',
            now_iso="2026-08-22T09:00:00+09:00",
        )
        assert store.save_survey(
            conn,
            report_id="report-versioned",
            report_version=1,
            actor_email="member@example.com",
            rating=4,
            overall_feedback="버전1 소감",
            business_distinction="버전1 구분점",
            add_information="",
            delete_information="",
            now_iso="2026-08-22T10:00:00+09:00",
        ) == 1
        assert store.capture_report_snapshot(
            conn,
            report_id="report-versioned",
            version=2,
            payload_json='{"company":"버전2"}',
            actor="admin@example.com",
            now_iso="2026-08-22T11:00:00+09:00",
        )
        assert store.save_survey(
            conn,
            report_id="report-versioned",
            report_version=2,
            actor_email="member@example.com",
            rating=2,
            overall_feedback="버전2 소감",
            business_distinction="버전2 구분점",
            add_information="",
            delete_information="",
            now_iso="2026-08-22T12:00:00+09:00",
        ) == 1

        version_one = store.get_survey_snapshot(
            conn,
            report_id="report-versioned",
            report_version=1,
            actor_email="member@example.com",
        )
        version_two = store.get_survey_snapshot(
            conn,
            report_id="report-versioned",
            report_version=2,
            actor_email="member@example.com",
        )
        feedback = store.list_member_feedback(conn)
        event_revisions = conn.execute(
            f"""SELECT report_version, revision
            FROM {store.TABLE_SURVEY_SNAPSHOT_EVENTS}
            WHERE report_id = ? ORDER BY id""",
            ("report-versioned",),
        ).fetchall()

    assert version_one is not None and version_one.overall_feedback == "버전1 소감"
    assert version_two is not None and version_two.overall_feedback == "버전2 소감"
    assert [(row["report_version"], row["revision"]) for row in event_revisions] == [
        (1, 1),
        (2, 1),
    ]
    assert [(item.report_version, item.company) for item in feedback] == [
        (2, "버전2"),
        (1, "버전1"),
    ]


def test_member_feedback_displays_append_only_event_not_mutable_projection(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {storage_constants.TABLE_REPORTS} VALUES (?, ?, ?, ?, ?, ?)",
            (
                "report-audit",
                "CORP-AUDIT",
                "직무",
                '{"company":"감사기업"}',
                "2026-08-22",
                "2026-08-22",
            ),
        )
        store.register_report(
            conn,
            report_id="report-audit",
            corp_type="상장사",
            payload_json='{"company":"감사기업"}',
            now_iso="2026-08-22T09:00:00+09:00",
        )
        store.save_survey(
            conn,
            report_id="report-audit",
            actor_email="member@example.com",
            rating=5,
            overall_feedback="영구 기록의 원래 의견",
            business_distinction="영구 기록의 원래 구분점",
            add_information="원래 추가 정보",
            delete_information="",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        conn.execute(
            f"""UPDATE {store.TABLE_SURVEY_SNAPSHOTS}
            SET rating = 1, overall_feedback = '변조된 임시 의견',
                business_distinction = '변조된 임시 구분점'
            WHERE report_id = ? AND report_version = 1 AND actor_email = ?""",
            ("report-audit", "member@example.com"),
        )

        feedback = store.list_member_feedback(conn)

        assert len(feedback) == 1
        assert feedback[0].rating == 5
        assert feedback[0].overall_feedback == "영구 기록의 원래 의견"
        assert feedback[0].business_distinction == "영구 기록의 원래 구분점"
        assert feedback[0].snapshot_available is True

        conn.execute(
            f"""UPDATE {store.TABLE_SURVEY_SNAPSHOTS}
            SET revision = 99
            WHERE report_id = ? AND report_version = 1 AND actor_email = ?""",
            ("report-audit", "member@example.com"),
        )
        revision_mismatch = store.list_member_feedback(conn)

        conn.execute(
            f"""DELETE FROM {store.TABLE_SURVEY_SNAPSHOTS}
            WHERE report_id = ? AND report_version = 1 AND actor_email = ?""",
            ("report-audit", "member@example.com"),
        )
        projection_missing = store.list_member_feedback(conn)

    assert revision_mismatch[0].overall_feedback == "영구 기록의 원래 의견"
    assert revision_mismatch[0].snapshot_available is False
    assert projection_missing[0].overall_feedback == "영구 기록의 원래 의견"
    assert projection_missing[0].snapshot_available is False


def test_member_success_limit_reserves_concurrently_and_returns_failures(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for number in range(3):
            assert store.reserve_member_run(
                conn, run_id=f"run-{number}", actor_email="member@example.com",
                day="2026-08-22", now_iso="2026-08-22T10:00:00+09:00",
            )
        assert not store.reserve_member_run(
            conn, run_id="run-four", actor_email="member@example.com",
            day="2026-08-22", now_iso="2026-08-22T10:00:00+09:00",
        )
        assert store.settle_member_run(
            conn, run_id="run-1", succeeded=False, report_id="", now_iso="2026-08-22T10:01:00+09:00"
        )
        assert store.reserve_member_run(
            conn, run_id="run-four", actor_email="member@example.com",
            day="2026-08-22", now_iso="2026-08-22T10:02:00+09:00",
        )
        assert store.settle_member_run(
            conn, run_id="run-0", succeeded=True, report_id="report-0", now_iso="2026-08-22T10:03:00+09:00"
        )
        assert store.member_usage_today(conn, actor_email="member@example.com", day="2026-08-22") == (1, 2)


def test_member_restart_recovery_can_list_only_unsettled_reservations(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        assert store.reserve_member_run(
            conn, run_id="reserved", actor_email="member@example.com",
            day="2026-08-22", now_iso="2026-08-22T10:00:00+09:00",
        )
        assert store.reserve_member_run(
            conn, run_id="returned", actor_email="member@example.com",
            day="2026-08-22", now_iso="2026-08-22T10:01:00+09:00",
        )
        assert store.settle_member_run(
            conn, run_id="returned", succeeded=False, report_id="",
            now_iso="2026-08-22T10:02:00+09:00",
        )

        reservations = store.list_reserved_member_runs(conn)

    assert tuple(item.run_id for item in reservations) == ("reserved",)
    assert reservations[0].actor_email == "member@example.com"


def test_member_statistics_keeps_latest_result_type_and_cost_state(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        assert store.reserve_member_run(
            conn, run_id="success", actor_email="member@example.com", day="2026-08-22",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        assert store.settle_member_run(
            conn, run_id="success", succeeded=True, report_id="report-a",
            outcome="report", company_type=store.COMPANY_LISTED, cost_krw=120.5,
            cost_uncertain=False, now_iso="2026-08-22T10:01:00+09:00",
        )
        assert store.reserve_member_run(
            conn, run_id="failed", actor_email="member@example.com", day="2026-08-22",
            now_iso="2026-08-22T10:02:00+09:00",
        )
        assert store.settle_member_run(
            conn, run_id="failed", succeeded=False, report_id="", outcome="failed",
            company_type=store.COMPANY_UNDECIDED, cost_krw=0,
            cost_uncertain=True, now_iso="2026-08-22T10:03:00+09:00",
        )
        stats = store.member_run_statistics(conn, start_day="2026-08-16")

    assert stats["by_company"][store.COMPANY_LISTED] == {"success": 1, "failed": 0}
    assert stats["by_company"][store.COMPANY_UNDECIDED] == {"success": 0, "failed": 1}
    assert stats["settled"] == {"used": 1, "returned": 1, "reserved": 0}
    assert stats["confirmed_cost_krw"] == 120.5
    assert stats["uncertain_cost_events"] == 1


def test_recent_resolved_issue_and_error_snapshot_use_actual_resolution_event(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {storage_constants.TABLE_REPORTS} VALUES (?, ?, ?, ?, ?, ?)",
            (
                "report-resolved", "CORP-RESOLVED", "분석",
                '{"company":"해결전자"}', "2026-08-22", "2026-08-22",
            ),
        )
        store.register_report(
            conn, report_id="report-resolved", corp_type="상장사",
            payload_json='{"version": 1}', now_iso="2026-08-22T09:00:00+09:00",
        )
        store.record_error(
            conn, report_id="report-resolved", actor_email="member@example.com",
            area="출처", reason="원문 수치와 다름", now_iso="2026-08-22T10:00:00+09:00",
        )
        for status in (store.REPORT_STATUS_FIXING, store.REPORT_STATUS_RECHECKING):
            store.change_report_state(
                conn, report_id="report-resolved", next_status=status,
                actor_email="admin@example.com", reason="수동 검토",
                now_iso="2026-08-22T10:10:00+09:00",
            )
        store.capture_report_snapshot(
            conn, report_id="report-resolved", version=2,
            payload_json='{"version": 2}', actor="admin@example.com",
            now_iso="2026-08-22T10:20:00+09:00",
        )
        store.change_report_state(
            conn, report_id="report-resolved", next_status=store.REPORT_STATUS_NORMAL,
            actor_email="admin@example.com", reason="출처를 대조해 수정 완료",
            now_iso="2026-08-22T10:30:00+09:00",
        )

        resolved = store.list_recent_resolved_issues(conn)
        history = store.list_report_error_history(conn, report_id="report-resolved")

    assert resolved[0].corp_id == "CORP-RESOLVED"
    assert resolved[0].company == "해결전자"
    assert resolved[0].reason == "출처를 대조해 수정 완료"
    assert resolved[0].version == 2
    assert history[0].actor_email == "member@example.com"
    assert history[0].report_version == 1
    assert history[0].payload_json == '{"version": 1}'


def test_member_feedback_and_summary_apply_the_same_period(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for report_id, corp_id, company in (
            ("old-report", "OLD", "옛회사"),
            ("new-report", "NEW", "새회사"),
        ):
            payload_json = f'{{"company":"{company}"}}'
            conn.execute(
                f"INSERT INTO {storage_constants.TABLE_REPORTS} VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report_id, corp_id, "직무", payload_json,
                    "2026-08-01", "2026-08-01",
                ),
            )
            store.register_report(
                conn,
                report_id=report_id,
                corp_type="상장사",
                payload_json=payload_json,
                now_iso="2026-08-01T09:00:00+09:00",
            )
        store.save_survey(
            conn, report_id="old-report", actor_email="old@example.com", rating=2,
            overall_feedback="오래된 의견", business_distinction="오래된 구분점",
            add_information="", delete_information="", now_iso="2026-08-01T10:00:00+09:00",
        )
        store.save_survey(
            conn, report_id="new-report", actor_email="new@example.com", rating=5,
            overall_feedback="최근 의견", business_distinction="최근 구분점",
            add_information="시장", delete_information="중복",
            now_iso="2026-08-22T10:00:00+09:00",
        )

        feedback = store.list_member_feedback(conn, start_day="2026-08-16")
        summary = store.survey_summary(conn, start_day="2026-08-16")

    assert [item.corp_id for item in feedback] == ["NEW"]
    assert [item.company for item in feedback] == ["새회사"]
    assert feedback[0].add_information == "시장"
    assert feedback[0].report_version == 1
    assert feedback[0].snapshot_available is True
    assert len(feedback[0].report_payload_sha256) == 64
    assert summary == (1, 1)


def test_member_feedback_never_falls_back_to_current_report_when_snapshot_is_missing(
    tmp_path,
):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {storage_constants.TABLE_REPORTS} VALUES (?, ?, ?, ?, ?, ?)",
            (
                "legacy-report", "CORP-LEGACY", "직무", '{"company":"현재회사"}',
                "2026-08-01", "2026-08-01",
            ),
        )
        store.save_survey(
            conn,
            report_id="legacy-report",
            actor_email="member@example.com",
            rating=4,
            overall_feedback="과거 의견",
            business_distinction="과거 구분점",
            add_information="",
            delete_information="",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        feedback = store.list_member_feedback(conn)

    assert len(feedback) == 1
    assert feedback[0].company == "CORP-LEGACY"
    assert feedback[0].snapshot_available is False
    assert feedback[0].report_payload_sha256 == ""


def test_active_incidents_start_after_last_manual_normal_boundary(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.record_incident(
            conn, kind=store.INCIDENT_RATE_LIMIT, summary="old",
            now_iso="2026-08-22T09:00:00+09:00",
        )
        store.set_service_state(
            conn, status=store.SERVICE_NORMAL, cause="", impact="", next_action="",
            actor_email="admin@example.com", now_iso="2026-08-22T10:00:00+09:00",
        )
        store.record_incident(
            conn, kind=store.INCIDENT_RATE_LIMIT, summary="new",
            now_iso="2026-08-22T11:00:00+09:00",
        )
        active = store.list_active_incidents(conn)

    assert [item["summary"] for item in active] == ["new"]


def test_report_registration_preserves_original_company_type_and_event(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        state = store.register_report(
            conn, report_id="report-listed", corp_type="상장사", now_iso="2026-08-22T10:00:00+09:00"
        )
        repeat = store.register_report(
            conn, report_id="report-listed", corp_type="비상장 외감", now_iso="2026-08-22T10:01:00+09:00"
        )
        assert state.company_type == store.COMPANY_LISTED
        assert repeat.company_type == store.COMPANY_LISTED
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_REPORT_EVENTS} WHERE report_id = ?", ("report-listed",)
        ).fetchone()[0] == 1


def test_same_member_repeated_error_blocks_each_report_without_global_maintenance(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for report_id, actor_email in (
            ("report-a", "Member@Example.com"),
            ("report-b", "member@example.com"),
        ):
            store.record_error(
                conn, report_id=report_id, actor_email=actor_email,
                area="source", reason="same source mismatch", now_iso="2026-08-22T10:00:00+09:00",
            )
        service = store.get_service_state(conn)
        assert service.status == store.SERVICE_NORMAL
        assert store.report_is_blocked(conn, "report-a")
        assert store.report_is_blocked(conn, "report-b")
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_SERVICE_EVENTS} WHERE actor = ?", ("system:repeated-error",)
        ).fetchone()[0] == 0


def test_distinct_member_free_text_reports_never_control_global_maintenance(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for report_id, actor_email in (
            ("report-a", "first@example.com"),
            ("report-b", "second@example.com"),
        ):
            store.record_error(
                conn, report_id=report_id, actor_email=actor_email,
                area="source", reason="same source mismatch", now_iso="2026-08-22T10:00:00+09:00",
            )
        service = store.get_service_state(conn)
        assert service.status == store.SERVICE_NORMAL
        assert store.report_is_blocked(conn, "report-a")
        assert store.report_is_blocked(conn, "report-b")
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_SERVICE_EVENTS} WHERE actor = ?", ("system:repeated-error",)
        ).fetchone()[0] == 0


def test_distinct_members_on_one_report_do_not_enter_global_maintenance(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for actor_email in ("first@example.com", "second@example.com"):
            store.record_error(
                conn, report_id="report-a", actor_email=actor_email,
                area="source", reason="same source mismatch", now_iso="2026-08-22T10:00:00+09:00",
            )

        assert store.get_service_state(conn).status == store.SERVICE_NORMAL
        assert store.report_is_blocked(conn, "report-a")


def test_single_critical_incident_enters_maintenance_and_is_append_only(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        error = store.record_error(
            conn, report_id="report-a", actor_email="member@example.com", area="보안",
            reason="권한 경계가 의심됩니다",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        store.record_incident(
            conn,
            kind=store.INCIDENT_SECURITY,
            summary="코드가 권한 경계 위반을 확인했습니다",
            error_id=error.id,
            report_id=error.report_id,
            now_iso="2026-08-22T10:00:01+09:00",
        )
        incidents = store.list_incidents(conn)
        service = store.get_service_state(conn)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_INCIDENTS}")

    assert error.report_id == "report-a"
    assert incidents[0]["kind"] == store.INCIDENT_SECURITY
    assert service.status == store.SERVICE_MAINTENANCE


def test_repeated_rate_limit_stays_scoped_and_does_not_enter_global_maintenance(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for minute in ("00", "01"):
            store.record_incident(
                conn, kind=store.INCIDENT_RATE_LIMIT, summary="DART candidate rate limit",
                stage="candidate_resolution", now_iso=f"2026-08-22T10:{minute}:00+09:00",
            )
        service = store.get_service_state(conn)
        incidents = store.list_incidents(conn)

    assert service.status == store.SERVICE_NORMAL
    assert len(incidents) == 2


def test_provider_response_incident_is_recorded_without_global_maintenance(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.record_incident(
            conn,
            kind=store.INCIDENT_PROVIDER_RESPONSE,
            summary="DART 응답 변경 의심",
            stage="candidate_resolution",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        service = store.get_service_state(conn)
        incidents = store.list_incidents(conn)

    assert service.status == store.SERVICE_NORMAL
    assert incidents[0]["kind"] == store.INCIDENT_PROVIDER_RESPONSE


def test_source_global_incident_keeps_immediate_maintenance_safety_line(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.record_incident(
            conn,
            kind=store.INCIDENT_SOURCE_GLOBAL,
            summary="DART 원문이 전체적으로 잘못 매핑됨",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        service = store.get_service_state(conn)

    assert service.status == store.SERVICE_MAINTENANCE


def test_report_snapshot_and_link_open_review_are_append_only(tmp_path):
    target = tmp_path / "dashboard.db"
    key_hash = "a" * 64
    with db.connect(target) as conn:
        state = store.register_report(
            conn,
            report_id="report-versioned",
            corp_type="상장사",
            payload_json='{"version": 1}',
            now_iso="2026-08-22T10:00:00+09:00",
        )
        assert state.version == 1
        version = conn.execute(
            f"SELECT payload_json FROM {store.TABLE_REPORT_VERSIONS} WHERE report_id = ? AND version = 1",
            ("report-versioned",),
        ).fetchone()
        assert version["payload_json"] == '{"version": 1}'
        assert store.link_open_seen_id(conn, key_hash=key_hash) == 0
        assert store.mark_link_opens_seen(
            conn,
            key_hash=key_hash,
            last_seen_open_id=7,
            actor_email="admin@example.com",
            now_iso="2026-08-22T10:01:00+09:00",
        ) == 7
        assert store.link_open_seen_id(conn, key_hash=key_hash) == 7
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_REPORT_VERSIONS} WHERE report_id = ?", ("report-versioned",))


def test_report_snapshot_reader_rejects_missing_or_sha_mismatched_payload(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        assert store.get_report_snapshot(
            conn, report_id="missing-report", version=1
        ) is None
        conn.execute(
            f"""INSERT INTO {store.TABLE_REPORT_VERSIONS}
            (report_id, version, payload_json, payload_sha256, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "corrupt-report",
                1,
                '{"company":"현재회사"}',
                "0" * 64,
                "system",
                "2026-08-22T10:00:00+09:00",
            ),
        )
        with pytest.raises(store.ReportSnapshotIntegrityError):
            store.get_report_snapshot(
                conn, report_id="corrupt-report", version=1
            )


def test_approved_payload_uses_a_distinct_corrected_version(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.register_report(
            conn, report_id="report-versioned", corp_type="상장사", payload_json='{"version": 1}',
            now_iso="2026-08-22T10:00:00+09:00",
        )
        store.record_error(
            conn, report_id="report-versioned", actor_email="member@example.com", area="출처",
            reason="근거가 맞지 않습니다", now_iso="2026-08-22T10:01:00+09:00",
        )
        for status in (store.REPORT_STATUS_FIXING, store.REPORT_STATUS_RECHECKING):
            state = store.change_report_state(
                conn, report_id="report-versioned", next_status=status, actor_email="admin@example.com",
                reason="수정 원본 재검사", now_iso="2026-08-22T10:02:00+09:00",
            )
        assert not store.capture_report_snapshot(
            conn, report_id="report-versioned", version=2, payload_json='{"version": 1}',
            actor="admin@example.com", now_iso="2026-08-22T10:03:00+09:00",
        )
        assert store.capture_report_snapshot(
            conn, report_id="report-versioned", version=2, payload_json='{"version": 2}',
            actor="admin@example.com", now_iso="2026-08-22T10:03:00+09:00",
        )
        assert store.report_snapshot_exists(conn, report_id="report-versioned", version=2)
        state = store.change_report_state(
            conn, report_id="report-versioned", next_status=store.REPORT_STATUS_NORMAL,
            actor_email="admin@example.com", reason="수정 원본 수동 재공개",
            now_iso="2026-08-22T10:04:00+09:00",
        )
        assert state.version == 2

        assert store.approved_report_payload(conn, report_id="report-versioned") == '{"version": 2}'


def test_approved_payload_rejects_sha_mismatched_report_version(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.register_report(
            conn,
            report_id="report-corrupt",
            corp_type="상장사",
            payload_json='{"company":"원본기업"}',
            now_iso="2026-08-22T10:00:00+09:00",
        )
        conn.execute(f"DROP TRIGGER {store.TABLE_REPORT_VERSIONS}_no_update")
        conn.execute(
            f"""UPDATE {store.TABLE_REPORT_VERSIONS}
            SET payload_json = ? WHERE report_id = ? AND version = 1""",
            ('{"company":"변조기업"}', "report-corrupt"),
        )

        with pytest.raises(store.ReportSnapshotIntegrityError):
            store.approved_report_payload(conn, report_id="report-corrupt")


def test_error_snapshot_event_is_bound_to_the_report_version(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.register_report(
            conn, report_id="report-versioned", corp_type="상장사", payload_json='{"version": 1}',
            now_iso="2026-08-22T10:00:00+09:00",
        )
        error = store.record_error(
            conn, report_id="report-versioned", actor_email="member@example.com",
            area="출처", reason="근거가 맞지 않습니다", now_iso="2026-08-22T10:01:00+09:00",
        )
        snapshot = conn.execute(
            f"SELECT report_version, report_payload_sha256 FROM {store.TABLE_ERROR_SNAPSHOT_EVENTS} WHERE error_id = ?",
            (error.id,),
        ).fetchone()
        assert snapshot["report_version"] == 1
        assert len(snapshot["report_payload_sha256"]) == 64
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"UPDATE {store.TABLE_ERROR_SNAPSHOT_EVENTS} SET report_version = 2")


def test_external_status_uses_existing_event_or_explicit_unavailable(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        store.record_external_status(
            conn, provider="Anthropic", status="normal", last_success_at="2026-08-22T10:00:00+09:00",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        store.record_external_status(
            conn, provider="Anthropic", status="error", error_at="2026-08-22T10:02:00+09:00",
            error_summary="provider timeout", now_iso="2026-08-22T10:02:00+09:00",
        )
        cards = store.external_status_cards(
            conn, providers=(("Anthropic", True), ("DART", True), ("Places", False)),
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_EXTERNAL_STATUS_EVENTS}")

    assert cards[0]["status"] == "error"
    assert cards[0]["last_success_at"] == "2026-08-22T10:00:00+09:00"
    assert cards[0]["error_at"] == "2026-08-22T10:02:00+09:00"
    assert cards[1]["status"] == "unavailable"
    assert cards[2]["status"] == "not_used"
