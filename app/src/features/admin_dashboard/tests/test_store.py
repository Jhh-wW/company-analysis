from __future__ import annotations

import sqlite3

import pytest

from src.features.admin_dashboard import store
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


def test_repeated_error_enters_maintenance_but_never_restarts_automatically(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for report_id in ("report-a", "report-b"):
            store.record_error(
                conn, report_id=report_id, actor_email="member@example.com",
                area="source", reason="same source mismatch", now_iso="2026-08-22T10:00:00+09:00",
            )
        service = store.get_service_state(conn)
        assert service.status == store.SERVICE_MAINTENANCE
        assert "2" in service.impact
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_SERVICE_EVENTS} WHERE actor = ?", ("system:repeated-error",)
        ).fetchone()[0] == 1


def test_single_critical_incident_enters_maintenance_and_is_append_only(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        error = store.record_error(
            conn, report_id="report-a", actor_email="member@example.com", area="보안",
            reason="권한 경계가 의심됩니다", incident_kind=store.INCIDENT_SECURITY,
            now_iso="2026-08-22T10:00:00+09:00",
        )
        incidents = store.list_incidents(conn)
        service = store.get_service_state(conn)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_INCIDENTS}")

    assert error.report_id == "report-a"
    assert incidents[0]["kind"] == store.INCIDENT_SECURITY
    assert service.status == store.SERVICE_MAINTENANCE


def test_repeated_rate_limit_enters_maintenance_on_second_incident(tmp_path):
    target = tmp_path / "dashboard.db"
    with db.connect(target) as conn:
        for minute in ("00", "01"):
            store.record_incident(
                conn, kind=store.INCIDENT_RATE_LIMIT, summary="DART candidate rate limit",
                stage="candidate_resolution", now_iso=f"2026-08-22T10:{minute}:00+09:00",
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
