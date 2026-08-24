from __future__ import annotations

import sqlite3

import pytest

from src.features.feedback_report import constants, logic, store


NOW = "2026-08-24T10:00:00+09:00"


def _create(conn: sqlite3.Connection, **overrides: str) -> store.FeedbackReport:
    values: dict[str, str] = {
        "stage": constants.STAGE_REPORT,
        "category": constants.CATEGORY_WRONG_INFO,
        "body": "매출 수치가 실제 공시와 다릅니다",
        "company_name": "삼성전자",
        "reporter_key": "link:abcd",
        "now_iso": NOW,
    }
    values.update(overrides)
    return logic.create_report(conn, **values)


# ── 접수 ─────────────────────────────────────────────


def test_접수는_재연결_뒤에도_원문_그대로_복원된다(tmp_path) -> None:
    db_path = tmp_path / "storage.db"
    with sqlite3.connect(db_path) as conn:
        created = _create(
            conn,
            item_label="재무 지표",
            report_ref="corp-0001",
            ref_url="https://example.com/ir",
        )
    with sqlite3.connect(db_path) as conn:
        restored = logic.get_report(conn, created.report_id)
    assert restored == created
    assert restored is not None
    assert restored.report_id == "RPT-20260824-001"
    assert restored.status == constants.STATUS_OPEN
    assert restored.ref_url == "https://example.com/ir"


def test_본문은_HTML을_바꾸지_않고_원문으로_저장한다() -> None:
    raw_body = "<script>alert('x')</script> 수치가 다릅니다 & <b>강조</b>"
    with sqlite3.connect(":memory:") as conn:
        created = _create(conn, body=raw_body)
        assert created.body == raw_body


def test_닫힌_목록_밖의_단계와_유형은_접수를_거절한다() -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(logic.FeedbackReportError):
            _create(conn, stage="임의단계")
        with pytest.raises(logic.FeedbackReportError):
            _create(conn, category="임의유형")


def test_본문이_없거나_2000자를_넘으면_거절한다() -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(logic.FeedbackReportError):
            _create(conn, body="   ")
        with pytest.raises(logic.FeedbackReportError):
            _create(conn, body="가" * (constants.MAX_BODY_CHARS + 1))
        created = _create(conn, body="가" * constants.MAX_BODY_CHARS)
        assert len(created.body) == constants.MAX_BODY_CHARS


def test_참고URL은_http_https만_허용한다() -> None:
    with sqlite3.connect(":memory:") as conn:
        for bad in (
            "javascript:alert(1)",
            "ftp://example.com/a",
            "example.com/no-scheme",
            "https://",
            "https://exa mple.com",
        ):
            with pytest.raises(logic.FeedbackReportError):
                _create(conn, ref_url=bad)
        assert _create(conn, ref_url="http://example.com/a").ref_url == "http://example.com/a"
        assert _create(conn, ref_url="").ref_url == ""


def test_시간대_없는_접수_시각은_거절한다() -> None:
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(logic.FeedbackReportError):
            _create(conn, now_iso="2026-08-24T10:00:00")


# ── 하루 접수 상한 ─────────────────────────────────────


def test_같은_식별자는_하루_상한을_넘겨_접수할_수_없다() -> None:
    with sqlite3.connect(":memory:") as conn:
        for _ in range(constants.DAILY_CREATE_LIMIT_PER_REPORTER):
            _create(conn)
        with pytest.raises(logic.FeedbackReportLimitError):
            _create(conn)
        # 다른 식별자와 다음 날은 상한과 무관하게 접수된다.
        assert _create(conn, reporter_key="link:efgh")
        assert _create(conn, now_iso="2026-08-25T00:10:00+09:00")


# ── 목록·집계 ─────────────────────────────────────────


def _seed_variety(conn: sqlite3.Connection) -> None:
    _create(
        conn,
        company_name="삼성전자",
        category=constants.CATEGORY_WRONG_INFO,
        stage=constants.STAGE_REPORT,
        now_iso="2026-08-21T10:00:00+09:00",
    )
    _create(
        conn,
        company_name="현대자동차",
        category=constants.CATEGORY_STALE_BASIS,
        stage=constants.STAGE_GENERATING,
        body="2022년 기준 전략 정보가 최신으로 표시됨",
        now_iso="2026-08-22T10:00:00+09:00",
    )
    _create(
        conn,
        company_name="카카오",
        category=constants.CATEGORY_SOURCE_ERROR,
        stage=constants.STAGE_COMPANY_SELECT,
        body="인용된 기사 URL이 존재하지 않는 페이지로 연결됨",
        now_iso="2026-08-23T10:00:00+09:00",
    )


def test_목록은_최신순이고_페이지가_나뉜다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _seed_variety(conn)
        first_page = logic.list_reports(conn, page=1, page_size=2)
        second_page = logic.list_reports(conn, page=2, page_size=2)
    assert first_page.total == 3
    assert first_page.page_count == 2
    assert [item.company_name for item in first_page.items] == ["카카오", "현대자동차"]
    assert [item.company_name for item in second_page.items] == ["삼성전자"]


def test_목록은_상태_유형_단계_기간_검색어로_거른다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _seed_variety(conn)
        by_category = logic.list_reports(conn, category=constants.CATEGORY_STALE_BASIS)
        assert [item.company_name for item in by_category.items] == ["현대자동차"]

        by_stage = logic.list_reports(conn, stage=constants.STAGE_COMPANY_SELECT)
        assert [item.company_name for item in by_stage.items] == ["카카오"]

        by_period = logic.list_reports(
            conn, date_from="2026-08-22", date_to="2026-08-22"
        )
        assert [item.company_name for item in by_period.items] == ["현대자동차"]

        by_keyword = logic.list_reports(conn, keyword="기사 URL")
        assert [item.company_name for item in by_keyword.items] == ["카카오"]

        reviewing = logic.list_reports(conn, status=constants.STATUS_REVIEWING)
        assert reviewing.total == 0

        target = logic.list_reports(conn, keyword="현대자동차").items[0]
        logic.change_status(
            conn,
            report_id=target.report_id,
            to_status=constants.STATUS_REVIEWING,
            actor="admin",
            now_iso="2026-08-24T09:00:00+09:00",
        )
        reviewing_after = logic.list_reports(conn, status=constants.STATUS_REVIEWING)
        assert [item.company_name for item in reviewing_after.items] == ["현대자동차"]


def test_잘못된_필터값은_전체가_아니라_오류로_알린다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _seed_variety(conn)
        with pytest.raises(logic.FeedbackReportError):
            logic.list_reports(conn, status="임의상태")
        with pytest.raises(logic.FeedbackReportError):
            logic.list_reports(conn, date_from="2026/08/22")
        with pytest.raises(logic.FeedbackReportError):
            logic.list_reports(conn, date_from="2026-08-23", date_to="2026-08-21")


def test_상태별_건수는_없는_상태도_0으로_돌려준다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _seed_variety(conn)
        counts = logic.count_by_status(conn)
    assert counts == {
        constants.STATUS_OPEN: 3,
        constants.STATUS_REVIEWING: 0,
        constants.STATUS_RESOLVED: 0,
        constants.STATUS_REJECTED: 0,
    }


# ── 상태 변경 ─────────────────────────────────────────


def test_상태는_미처리_검토중_처리완료_순서로만_바뀐다() -> None:
    later = "2026-08-24T11:00:00+09:00"
    with sqlite3.connect(":memory:") as conn:
        created = _create(conn)
        # 검토를 건너뛴 종결은 거절한다.
        with pytest.raises(logic.FeedbackReportError):
            logic.change_status(
                conn,
                report_id=created.report_id,
                to_status=constants.STATUS_RESOLVED,
            )
        reviewing = logic.change_status(
            conn,
            report_id=created.report_id,
            to_status=constants.STATUS_REVIEWING,
            actor="admin",
            now_iso=later,
        )
        assert reviewing.status == constants.STATUS_REVIEWING
        assert reviewing.updated_at == later
        resolved = logic.change_status(
            conn,
            report_id=created.report_id,
            to_status=constants.STATUS_RESOLVED,
            admin_note="공시 수치로 정정",
            actor="admin",
            now_iso=later,
        )
        assert resolved.status == constants.STATUS_RESOLVED
        assert resolved.admin_note == "공시 수치로 정정"
        # 종결된 신고는 다시 열 수 없다.
        with pytest.raises(logic.FeedbackReportError):
            logic.change_status(
                conn,
                report_id=created.report_id,
                to_status=constants.STATUS_REVIEWING,
            )


def test_검토중에서_반려로_끝낼_수_있고_사건이_모두_남는다() -> None:
    later = "2026-08-24T11:00:00+09:00"
    with sqlite3.connect(":memory:") as conn:
        created = _create(conn)
        logic.change_status(
            conn, report_id=created.report_id,
            to_status=constants.STATUS_REVIEWING, actor="admin", now_iso=later,
        )
        rejected = logic.change_status(
            conn, report_id=created.report_id,
            to_status=constants.STATUS_REJECTED,
            admin_note="원문과 맥락 상이하지 않음", actor="admin", now_iso=later,
        )
        assert rejected.status == constants.STATUS_REJECTED
        actions = [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in conn.execute(
                f"SELECT action, from_status, to_status "
                f"FROM {store.TABLE_FEEDBACK_REPORT_EVENTS} ORDER BY id"
            )
        ]
    assert actions == [
        (store.EVENT_CREATED, "", constants.STATUS_OPEN),
        (store.EVENT_STATUS_CHANGED, constants.STATUS_OPEN, constants.STATUS_REVIEWING),
        (store.EVENT_STATUS_CHANGED, constants.STATUS_REVIEWING, constants.STATUS_REJECTED),
    ]


def test_없는_신고의_상태는_바꿀_수_없다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _create(conn)
        with pytest.raises(logic.FeedbackReportError):
            logic.change_status(
                conn,
                report_id="RPT-20260824-999",
                to_status=constants.STATUS_REVIEWING,
            )
