"""오류 신고 원장의 SQLite 정본.

현재 상태 표(``feedback_reports``)는 화면 조회용 projection이고,
모든 변경은 사건 표(``feedback_report_events``)에 append-only로 남긴다.
값 검증(닫힌 목록·길이·URL)은 logic 계층이 끝낸 뒤 들어온다는 전제이며,
DB CHECK 제약은 우회 저장을 막는 마지막 안전망이다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from src.features.feedback_report import constants


TABLE_FEEDBACK_REPORTS: Final[str] = "feedback_reports"
TABLE_FEEDBACK_REPORT_EVENTS: Final[str] = "feedback_report_events"

EVENT_CREATED: Final[str] = "created"
EVENT_STATUS_CHANGED: Final[str] = "status_changed"

#: 신고 ID 충돌(동시 접수) 시 일련번호를 다시 뽑는 최대 횟수.
_CREATE_ID_MAX_ATTEMPTS: Final[int] = 3

_SELECT_COLUMNS: Final[str] = (
    "report_id, created_at, day, reporter_key, stage, company_name, report_ref, "
    "category, item_label, body, ref_url, status, admin_note, updated_at"
)


class FeedbackReportStoreError(RuntimeError):
    """신고 원장이 손상됐거나 저장 계약을 지킬 수 없다."""


@dataclass(frozen=True)
class FeedbackReport:
    report_id: str
    created_at: str
    day: str
    reporter_key: str
    stage: str
    company_name: str
    report_ref: str
    category: str
    item_label: str
    body: str
    ref_url: str
    status: str
    admin_note: str
    updated_at: str


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """신고 표와 append-only 보호를 멱등으로 준비한다."""

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_FEEDBACK_REPORTS} (
            report_id     TEXT PRIMARY KEY,
            created_at    TEXT NOT NULL,
            day           TEXT NOT NULL,
            reporter_key  TEXT NOT NULL DEFAULT '',
            stage         TEXT NOT NULL CHECK(stage IN ({_quoted(constants.REPORT_STAGES)})),
            company_name  TEXT NOT NULL DEFAULT '',
            report_ref    TEXT NOT NULL DEFAULT '',
            category      TEXT NOT NULL CHECK(category IN ({_quoted(constants.REPORT_CATEGORIES)})),
            item_label    TEXT NOT NULL DEFAULT '',
            body          TEXT NOT NULL,
            ref_url       TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL CHECK(status IN ({_quoted(constants.REPORT_STATUSES)})),
            admin_note    TEXT NOT NULL DEFAULT '',
            updated_at    TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_FEEDBACK_REPORT_EVENTS} (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id     TEXT NOT NULL,
            action        TEXT NOT NULL CHECK(action IN ('{EVENT_CREATED}', '{EVENT_STATUS_CHANGED}')),
            from_status   TEXT NOT NULL DEFAULT '',
            to_status     TEXT NOT NULL DEFAULT '',
            admin_note    TEXT NOT NULL DEFAULT '',
            actor         TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_feedback_reports_day_reporter "
        f"ON {TABLE_FEEDBACK_REPORTS}(day, reporter_key)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_feedback_reports_status_created "
        f"ON {TABLE_FEEDBACK_REPORTS}(status, created_at DESC)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_feedback_report_events_report "
        f"ON {TABLE_FEEDBACK_REPORT_EVENTS}(report_id, id DESC)"
    )
    conn.execute(
        f"""CREATE TRIGGER IF NOT EXISTS {TABLE_FEEDBACK_REPORT_EVENTS}_no_update
        BEFORE UPDATE ON {TABLE_FEEDBACK_REPORT_EVENTS}
        BEGIN SELECT RAISE(ABORT, '{TABLE_FEEDBACK_REPORT_EVENTS} is append-only'); END"""
    )
    conn.execute(
        f"""CREATE TRIGGER IF NOT EXISTS {TABLE_FEEDBACK_REPORT_EVENTS}_no_delete
        BEFORE DELETE ON {TABLE_FEEDBACK_REPORT_EVENTS}
        BEGIN SELECT RAISE(ABORT, '{TABLE_FEEDBACK_REPORT_EVENTS} is append-only'); END"""
    )


def _table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE_FEEDBACK_REPORTS,),
        ).fetchone()
        is not None
    )


def _row_to_report(row: sqlite3.Row) -> FeedbackReport:
    return FeedbackReport(
        report_id=str(row[0]),
        created_at=str(row[1]),
        day=str(row[2]),
        reporter_key=str(row[3]),
        stage=str(row[4]),
        company_name=str(row[5]),
        report_ref=str(row[6]),
        category=str(row[7]),
        item_label=str(row[8]),
        body=str(row[9]),
        ref_url=str(row[10]),
        status=str(row[11]),
        admin_note=str(row[12]),
        updated_at=str(row[13]),
    )


def next_report_id(conn: sqlite3.Connection, *, day: str) -> str:
    """같은 KST 날짜 안에서 1부터 커지는 다음 신고 ID를 계산한다."""

    compact_day = str(day).replace("-", "")
    prefix = f"{constants.REPORT_ID_PREFIX}-{compact_day}-"
    row = conn.execute(
        # 일련번호가 999를 넘어 자릿수가 늘어도 «글자 수 → 사전순» 정렬이
        # 숫자 크기 순서와 일치하도록 길이를 먼저 비교한다.
        f"""SELECT report_id FROM {TABLE_FEEDBACK_REPORTS}
        WHERE report_id LIKE ?
        ORDER BY LENGTH(report_id) DESC, report_id DESC LIMIT 1""",
        (f"{prefix}%",),
    ).fetchone()
    serial = 1
    if row is not None:
        tail = str(row[0]).rsplit("-", 1)[-1]
        try:
            serial = int(tail) + 1
        except ValueError as exc:
            raise FeedbackReportStoreError("신고 ID 일련번호가 손상됐습니다") from exc
    return f"{prefix}{serial:0{constants.REPORT_ID_SERIAL_DIGITS}d}"


def create_report(
    conn: sqlite3.Connection,
    *,
    stage: str,
    category: str,
    body: str,
    company_name: str,
    report_ref: str,
    item_label: str,
    ref_url: str,
    reporter_key: str,
    created_at: str,
    day: str,
) -> FeedbackReport:
    """검증이 끝난 신고 한 건을 «미처리» 상태로 접수하고 사건을 남긴다."""

    ensure_schema(conn)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    last_error: sqlite3.IntegrityError | None = None
    for _attempt in range(_CREATE_ID_MAX_ATTEMPTS):
        report_id = next_report_id(conn, day=day)
        try:
            conn.execute(
                f"""INSERT INTO {TABLE_FEEDBACK_REPORTS}
                (report_id, created_at, day, reporter_key, stage, company_name,
                 report_ref, category, item_label, body, ref_url, status,
                 admin_note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)""",
                (
                    report_id, created_at, day, reporter_key, stage, company_name,
                    report_ref, category, item_label, body, ref_url,
                    constants.STATUS_OPEN, created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # 동시 접수로 같은 일련번호를 뽑은 드문 경우만 다시 시도한다.
            last_error = exc
            continue
        conn.execute(
            f"""INSERT INTO {TABLE_FEEDBACK_REPORT_EVENTS}
            (report_id, action, from_status, to_status, admin_note, actor, created_at)
            VALUES (?, ?, '', ?, '', ?, ?)""",
            (report_id, EVENT_CREATED, constants.STATUS_OPEN, reporter_key, created_at),
        )
        found = get_report(conn, report_id)
        if found is None:
            raise FeedbackReportStoreError("접수한 신고를 다시 읽지 못했습니다")
        return found
    raise FeedbackReportStoreError("신고 ID 발급이 반복 충돌했습니다") from last_error


def count_for_reporter_day(
    conn: sqlite3.Connection, *, reporter_key: str, day: str
) -> int:
    """하루 접수 상한 판정용: 같은 신고자 식별자의 KST 당일 접수 건수."""

    if not _table_exists(conn):
        return 0
    row = conn.execute(
        f"""SELECT COUNT(*) FROM {TABLE_FEEDBACK_REPORTS}
        WHERE reporter_key = ? AND day = ?""",
        (reporter_key, day),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def get_report(conn: sqlite3.Connection, report_id: str) -> FeedbackReport | None:
    """단건 조회. 표가 없으면 만들지 않고 None을 돌려준다."""

    if not _table_exists(conn):
        return None
    row = conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM {TABLE_FEEDBACK_REPORTS} WHERE report_id = ?",
        (str(report_id),),
    ).fetchone()
    return None if row is None else _row_to_report(row)


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def list_reports(
    conn: sqlite3.Connection,
    *,
    status: str = "",
    category: str = "",
    stage: str = "",
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    limit: int,
    offset: int,
) -> tuple[list[FeedbackReport], int]:
    """필터를 적용한 최신순 목록과 필터 기준 전체 건수를 함께 돌려준다."""

    if not _table_exists(conn):
        return [], 0
    where: list[str] = []
    params: list[str] = []
    for column, value in (("status", status), ("category", category), ("stage", stage)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    if date_from:
        where.append("day >= ?")
        params.append(date_from)
    if date_to:
        where.append("day <= ?")
        params.append(date_to)
    if keyword:
        like = f"%{_escape_like(keyword)}%"
        where.append(
            "(report_id LIKE ? ESCAPE '\\' OR company_name LIKE ? ESCAPE '\\' "
            "OR item_label LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like])
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    total_row = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE_FEEDBACK_REPORTS}{where_sql}", params
    ).fetchone()
    total = int(total_row[0]) if total_row is not None else 0
    rows = conn.execute(
        f"""SELECT {_SELECT_COLUMNS} FROM {TABLE_FEEDBACK_REPORTS}{where_sql}
        ORDER BY created_at DESC, report_id DESC LIMIT ? OFFSET ?""",
        (*params, int(limit), int(offset)),
    ).fetchall()
    return [_row_to_report(row) for row in rows], total


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """네 처리 상태 전부를 0 기본값과 함께 집계한다."""

    counts = {status: 0 for status in constants.REPORT_STATUSES}
    if not _table_exists(conn):
        return counts
    for row in conn.execute(
        f"SELECT status, COUNT(*) FROM {TABLE_FEEDBACK_REPORTS} GROUP BY status"
    ):
        key = str(row[0])
        if key in counts:
            counts[key] = int(row[1])
    return counts


def update_status(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    from_status: str,
    to_status: str,
    admin_note: str,
    actor: str,
    now_iso: str,
) -> bool:
    """현재 상태가 일치할 때만 바꾸고 사건을 남긴다. 어긋나면 False."""

    if not _table_exists(conn):
        return False
    cursor = conn.execute(
        f"""UPDATE {TABLE_FEEDBACK_REPORTS}
        SET status = ?,
            admin_note = CASE WHEN ? = '' THEN admin_note ELSE ? END,
            updated_at = ?
        WHERE report_id = ? AND status = ?""",
        (to_status, admin_note, admin_note, now_iso, str(report_id), from_status),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        f"""INSERT INTO {TABLE_FEEDBACK_REPORT_EVENTS}
        (report_id, action, from_status, to_status, admin_note, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(report_id), EVENT_STATUS_CHANGED, from_status, to_status, admin_note, actor, now_iso),
    )
    return True
