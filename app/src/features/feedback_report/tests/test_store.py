from __future__ import annotations

import sqlite3

import pytest

from src.features.feedback_report import constants, store


NOW = "2026-08-24T10:00:00+09:00"
DAY = "2026-08-24"


def _create(conn: sqlite3.Connection, **overrides: str) -> store.FeedbackReport:
    values: dict[str, str] = {
        "stage": constants.STAGE_REPORT,
        "category": constants.CATEGORY_WRONG_INFO,
        "body": "매출 수치가 실제 공시와 다릅니다",
        "company_name": "삼성전자",
        "report_ref": "corp-0001",
        "item_label": "재무 지표",
        "ref_url": "",
        "reporter_key": "link:abcd",
        "created_at": NOW,
        "day": DAY,
    }
    values.update(overrides)
    return store.create_report(conn, **values)


def test_스키마는_두_번_실행해도_같은_표만_남는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        store.ensure_schema(conn)
        store.ensure_schema(conn)
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == {
        store.TABLE_FEEDBACK_REPORTS,
        store.TABLE_FEEDBACK_REPORT_EVENTS,
    }


def test_접수하면_미처리_상태와_생성_사건이_함께_남는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        created = _create(conn)
        assert created.report_id == "RPT-20260824-001"
        assert created.status == constants.STATUS_OPEN
        assert created.created_at == NOW
        assert created.updated_at == NOW
        assert created.day == DAY
        events = conn.execute(
            f"SELECT action, to_status, actor FROM {store.TABLE_FEEDBACK_REPORT_EVENTS}"
        ).fetchall()
        assert events == [(store.EVENT_CREATED, constants.STATUS_OPEN, "link:abcd")]


def test_신고ID는_같은_날_안에서만_일련번호가_이어진다() -> None:
    with sqlite3.connect(":memory:") as conn:
        first = _create(conn)
        second = _create(conn)
        other_day = _create(
            conn, created_at="2026-08-25T09:00:00+09:00", day="2026-08-25"
        )
    assert first.report_id == "RPT-20260824-001"
    assert second.report_id == "RPT-20260824-002"
    assert other_day.report_id == "RPT-20260825-001"


def test_일련번호_999_다음은_자릿수가_늘어도_이어진다() -> None:
    with sqlite3.connect(":memory:") as conn:
        store.ensure_schema(conn)
        for serial in (998, 999):
            conn.execute(
                f"""INSERT INTO {store.TABLE_FEEDBACK_REPORTS}
                (report_id, created_at, day, reporter_key, stage, company_name,
                 report_ref, category, item_label, body, ref_url, status,
                 admin_note, updated_at)
                VALUES (?, ?, ?, '', ?, '', '', ?, '', '내용', '', ?, '', ?)""",
                (
                    f"RPT-20260824-{serial:03d}", NOW, DAY,
                    constants.STAGE_REPORT, constants.CATEGORY_OTHER,
                    constants.STATUS_OPEN, NOW,
                ),
            )
        assert store.next_report_id(conn, day=DAY) == "RPT-20260824-1000"


def test_사건_표는_고치거나_지울_수_없다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _create(conn)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                f"UPDATE {store.TABLE_FEEDBACK_REPORT_EVENTS} SET actor = 'x'"
            )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {store.TABLE_FEEDBACK_REPORT_EVENTS}")


def test_닫힌_목록_밖의_값은_DB_제약이_거른다() -> None:
    with sqlite3.connect(":memory:") as conn:
        store.ensure_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"""INSERT INTO {store.TABLE_FEEDBACK_REPORTS}
                (report_id, created_at, day, reporter_key, stage, company_name,
                 report_ref, category, item_label, body, ref_url, status,
                 admin_note, updated_at)
                VALUES ('RPT-20260824-001', ?, ?, '', '임의단계', '', '',
                        ?, '', '내용', '', ?, '', ?)""",
                (NOW, DAY, constants.CATEGORY_OTHER, constants.STATUS_OPEN, NOW),
            )


def test_읽기는_표를_새로_만들지_않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        assert store.get_report(conn, "RPT-20260824-001") is None
        assert store.list_reports(conn, limit=10, offset=0) == ([], 0)
        assert store.count_by_status(conn) == {
            status: 0 for status in constants.REPORT_STATUSES
        }
        assert store.count_for_reporter_day(conn, reporter_key="k", day=DAY) == 0
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (store.TABLE_FEEDBACK_REPORTS,),
        ).fetchone()


def test_검색어의_LIKE_특수문자는_문자_그대로_찾는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        _create(conn, body="점유율 100% 표기가 잘못됨")
        _create(conn, body="점유율 100프로 표기")
        items, total = store.list_reports(conn, keyword="100%", limit=10, offset=0)
    assert total == 1
    assert items[0].body == "점유율 100% 표기가 잘못됨"


def test_상태_갱신은_현재_상태가_어긋나면_아무것도_바꾸지_않는다() -> None:
    with sqlite3.connect(":memory:") as conn:
        created = _create(conn)
        assert not store.update_status(
            conn,
            report_id=created.report_id,
            from_status=constants.STATUS_REVIEWING,
            to_status=constants.STATUS_RESOLVED,
            admin_note="",
            actor="admin",
            now_iso=NOW,
        )
        unchanged = store.get_report(conn, created.report_id)
        assert unchanged is not None and unchanged.status == constants.STATUS_OPEN


def test_상태_갱신에서_빈_메모는_기존_메모를_지우지_않는다() -> None:
    later = "2026-08-24T11:00:00+09:00"
    with sqlite3.connect(":memory:") as conn:
        created = _create(conn)
        assert store.update_status(
            conn,
            report_id=created.report_id,
            from_status=constants.STATUS_OPEN,
            to_status=constants.STATUS_REVIEWING,
            admin_note="확인 중",
            actor="admin",
            now_iso=later,
        )
        assert store.update_status(
            conn,
            report_id=created.report_id,
            from_status=constants.STATUS_REVIEWING,
            to_status=constants.STATUS_RESOLVED,
            admin_note="",
            actor="admin",
            now_iso=later,
        )
        final = store.get_report(conn, created.report_id)
    assert final is not None
    assert final.status == constants.STATUS_RESOLVED
    assert final.admin_note == "확인 중"
    assert final.updated_at == later
