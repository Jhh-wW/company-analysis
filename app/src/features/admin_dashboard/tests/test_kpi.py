"""3분 구분점 응답 KPI의 시간·멱등·집계 계약."""

import sqlite3

from src.features.admin_dashboard import kpi, store


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_schema(conn)
    kpi.ensure_schema(conn)
    return conn


def test_첫_열람과_첫_설문만_측정한다() -> None:
    conn = _connection()

    assert kpi.record_first_view(
        conn,
        report_id="report-1",
        report_version=1,
        actor_email="Member@Example.com",
        now_iso="2026-08-22T10:00:00+09:00",
    )
    assert not kpi.record_first_view(
        conn,
        report_id="report-1",
        report_version=1,
        actor_email="member@example.com",
        now_iso="2026-08-22T10:01:00+09:00",
    )
    measured = kpi.record_first_survey(
        conn,
        report_id="report-1",
        report_version=1,
        actor_email="member@example.com",
        now_iso="2026-08-22T10:03:00+09:00",
    )

    assert measured == kpi.KpiMeasurement(elapsed_sec=180, within_target=True)
    assert kpi.record_first_survey(
        conn,
        report_id="report-1",
        report_version=1,
        actor_email="member@example.com",
        now_iso="2026-08-22T10:03:01+09:00",
    ) is None
    assert conn.execute(
        f"SELECT COUNT(*) FROM {kpi.TABLE_EVENTS}"
    ).fetchone()[0] == 2


def test_3분을_넘기거나_시계가_역행하면_성공으로_세지_않는다() -> None:
    conn = _connection()
    for report_id in ("late", "clock-skew"):
        assert kpi.record_first_view(
            conn,
            report_id=report_id,
            report_version=1,
            actor_email="member@example.com",
            now_iso="2026-08-22T10:00:00+09:00",
        )

    late = kpi.record_first_survey(
        conn,
        report_id="late",
        report_version=1,
        actor_email="member@example.com",
        now_iso="2026-08-22T10:03:01+09:00",
    )
    skew = kpi.record_first_survey(
        conn,
        report_id="clock-skew",
        report_version=1,
        actor_email="member@example.com",
        now_iso="2026-08-22T09:59:59+09:00",
    )

    assert late == kpi.KpiMeasurement(elapsed_sec=181, within_target=False)
    assert skew is None


def test_휴지통_보고서는_KPI_요약에서_제외한다() -> None:
    conn = _connection()
    for report_id, elapsed in (("active", 120), ("trashed", 90)):
        assert kpi.record_first_view(
            conn,
            report_id=report_id,
            report_version=1,
            actor_email="member@example.com",
            now_iso="2026-08-22T10:00:00+09:00",
        )
        assert kpi.record_first_survey(
            conn,
            report_id=report_id,
            report_version=1,
            actor_email="member@example.com",
            now_iso=f"2026-08-22T10:0{elapsed // 60}:{elapsed % 60:02d}+09:00",
        )
    conn.execute(
        f"""INSERT INTO {store.TABLE_REPORT_TRASH}
        (report_id, status, trashed_at, trashed_by)
        VALUES ('trashed', ?, '2026-08-22T10:10:00+09:00', 'actor')""",
        (store.TRASH_TRASHED,),
    )

    assert kpi.summary(conn) == kpi.KpiSummary(
        measured_responses=1,
        within_target=1,
    )


def test_시간대_없는_시각은_측정하지_않는다() -> None:
    conn = _connection()

    try:
        kpi.record_first_view(
            conn,
            report_id="report-1",
            report_version=1,
            actor_email="member@example.com",
            now_iso="2026-08-22T10:00:00",
        )
    except ValueError as error:
        assert "시간대" in str(error)
    else:
        raise AssertionError("시간대 없는 측정 시각이 허용됐습니다")
