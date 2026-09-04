"""저장된 임시 본문과 실제 출고 완료를 같은 상태로 오인하지 않는다."""

from __future__ import annotations

import pytest

from src.features.admin_dashboard import store
from src.features.storage import db


_STAGED_AT = "2026-09-04T10:00:00+09:00"
_PUBLISHED_AT = "2026-09-04T10:01:00+09:00"
_PAYLOAD = '{"company":"가나다전자","version":1}'


def test_staging은_차단되고_delivery성공transaction에서만_정상승격한다(tmp_path):
    target = tmp_path / "staged-publication.db"
    with db.connect(target) as conn:
        staged = store.stage_report(
            conn,
            report_id="staged-report",
            corp_type="상장사",
            now_iso=_STAGED_AT,
            payload_json=_PAYLOAD,
        )
        assert staged.status == store.REPORT_STATUS_PENDING
        assert staged.blocked is True
        assert store.report_is_unpublished_staging(conn, "staged-report")
        assert not store.report_access_is_revoked(conn, "staged-report")
        assert store.approved_report_payload(conn, report_id="staged-report") == ""

    with db.connect(target) as conn:
        assert store.publish_staged_report(
            conn,
            report_id="staged-report",
            now_iso=_PUBLISHED_AT,
        )
        published = store.get_report_state(conn, "staged-report")
        assert published.status == store.REPORT_STATUS_NORMAL
        assert published.blocked is False
        assert not store.report_is_unpublished_staging(conn, "staged-report")
        assert not store.report_access_is_revoked(conn, "staged-report")
        assert store.approved_report_payload(
            conn, report_id="staged-report"
        ) == _PAYLOAD

        # 응답만 잃은 COMPLETE 재시도가 정상 상태를 다시 쓰거나 사건을 늘리지 않는다.
        assert not store.publish_staged_report(
            conn,
            report_id="staged-report",
            now_iso="2026-09-04T10:02:00+09:00",
        )
        published_events = conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_REPORT_EVENTS} "
            "WHERE report_id=? AND action=?",
            ("staged-report", store.REPORT_EVENT_PUBLISHED),
        ).fetchone()[0]
        assert published_events == 1


def test_publish뒤_transaction실패는_staging차단상태를_보존한다(tmp_path):
    target = tmp_path / "staged-rollback.db"
    with db.connect(target) as conn:
        store.stage_report(
            conn,
            report_id="rollback-report",
            corp_type="비상장 외감",
            now_iso=_STAGED_AT,
            payload_json=_PAYLOAD,
        )

    with pytest.raises(RuntimeError, match="commit 직전 실패"):
        with db.connect(target) as conn:
            assert store.publish_staged_report(
                conn,
                report_id="rollback-report",
                now_iso=_PUBLISHED_AT,
            )
            raise RuntimeError("commit 직전 실패")

    with db.connect(target) as conn:
        state = store.get_report_state(conn, "rollback-report")
        assert state.status == store.REPORT_STATUS_PENDING
        assert state.blocked is True
        assert store.approved_report_payload(conn, report_id="rollback-report") == ""
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_REPORT_EVENTS} "
            "WHERE report_id=? AND action=?",
            ("rollback-report", store.REPORT_EVENT_PUBLISHED),
        ).fetchone()[0] == 0


def test_사람이신고한_pending은_delivery성공으로_자동승격하지않는다(tmp_path):
    target = tmp_path / "manual-pending.db"
    with db.connect(target) as conn:
        store.register_report(
            conn,
            report_id="manual-report",
            corp_type="상장사",
            now_iso=_STAGED_AT,
            payload_json=_PAYLOAD,
        )
        store.record_error(
            conn,
            report_id="manual-report",
            actor_email="member@example.com",
            area="출처",
            reason="원문과 다릅니다",
            now_iso=_PUBLISHED_AT,
        )
        with pytest.raises(ValueError, match="staging 보고서 상태"):
            store.publish_staged_report(
                conn,
                report_id="manual-report",
                now_iso="2026-09-04T10:02:00+09:00",
            )
        state = store.get_report_state(conn, "manual-report")
        assert state.status == store.REPORT_STATUS_PENDING
        assert state.blocked is True
        assert not store.report_is_unpublished_staging(conn, "manual-report")
        assert store.report_access_is_revoked(conn, "manual-report")


def test_intent없는_옛등록보고서는_staging으로_소급분류하지않는다(tmp_path):
    target = tmp_path / "legacy-no-intent.db"
    with db.connect(target) as conn:
        store.register_report(
            conn,
            report_id="legacy-report",
            corp_type="상장사",
            now_iso=_STAGED_AT,
            payload_json=_PAYLOAD,
        )
        assert not store.report_is_unpublished_staging(conn, "legacy-report")


def test_staging뒤_다른사건이붙어도_publish전원본은_legacy가아니다(tmp_path):
    target = tmp_path / "staged-followup-event.db"
    with db.connect(target) as conn:
        store.stage_report(
            conn,
            report_id="staged-followup",
            corp_type="상장사",
            now_iso=_STAGED_AT,
            payload_json=_PAYLOAD,
        )
        store.record_error(
            conn,
            report_id="staged-followup",
            actor_email="member@example.com",
            area="출처",
            reason="출고 전 신고",
            now_iso=_PUBLISHED_AT,
        )
        assert store.report_is_unpublished_staging(conn, "staged-followup")
        assert store.report_access_is_revoked(conn, "staged-followup")
        with pytest.raises(ValueError, match="staging 보고서 상태"):
            store.publish_staged_report(
                conn,
                report_id="staged-followup",
                now_iso="2026-09-04T10:02:00+09:00",
            )


def test_publish뒤_수동사건은_다시미출고staging으로_분류하지않는다(tmp_path):
    target = tmp_path / "published-followup-event.db"
    with db.connect(target) as conn:
        store.stage_report(
            conn,
            report_id="published-followup",
            corp_type="상장사",
            now_iso=_STAGED_AT,
            payload_json=_PAYLOAD,
        )
        assert store.publish_staged_report(
            conn,
            report_id="published-followup",
            now_iso=_PUBLISHED_AT,
        )
        store.record_error(
            conn,
            report_id="published-followup",
            actor_email="member@example.com",
            area="출처",
            reason="출고 뒤 신고",
            now_iso="2026-09-04T10:02:00+09:00",
        )
        assert not store.report_is_unpublished_staging(conn, "published-followup")
        assert store.report_access_is_revoked(conn, "published-followup")
