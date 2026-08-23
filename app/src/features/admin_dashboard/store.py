"""관리 대시보드의 SQLite 정본.

모든 변경은 사건 표에 append-only로 남긴다. 현재 상태 표는 화면을 빠르게 읽기
위한 projection일 뿐이며, 보고서 본문(``reports``)과 LINK 이력은 수정하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from src.core.persisted_json import validate_persisted_json_text
from src.features.storage import constants as storage_constants


TABLE_REPORT_STATES: Final[str] = "dashboard_report_states"
TABLE_REPORT_EVENTS: Final[str] = "dashboard_report_events"
TABLE_SURVEYS: Final[str] = "dashboard_surveys"
TABLE_SURVEY_EVENTS: Final[str] = "dashboard_survey_events"
TABLE_ERRORS: Final[str] = "dashboard_report_errors"
TABLE_SERVICE_STATE: Final[str] = "dashboard_service_state"
TABLE_SERVICE_EVENTS: Final[str] = "dashboard_service_events"
TABLE_MEMBER_USAGE: Final[str] = "dashboard_member_usage"
TABLE_MEMBER_USAGE_EVENTS: Final[str] = "dashboard_member_usage_events"
TABLE_MEMBER_RUN_SUMMARY_EVENTS: Final[str] = "dashboard_member_run_summary_events"
TABLE_LINK_OPEN_REVIEWS: Final[str] = "dashboard_link_open_reviews"
TABLE_LINK_OPEN_REVIEW_EVENTS: Final[str] = "dashboard_link_open_review_events"
TABLE_REPORT_VERSIONS: Final[str] = "dashboard_report_versions"
TABLE_ERROR_SNAPSHOTS: Final[str] = "dashboard_error_snapshots"
TABLE_SURVEY_SNAPSHOTS: Final[str] = "dashboard_survey_snapshots"
TABLE_ERROR_SNAPSHOT_EVENTS: Final[str] = "dashboard_error_snapshot_events"
TABLE_SURVEY_SNAPSHOT_EVENTS: Final[str] = "dashboard_survey_snapshot_events"
TABLE_EXTERNAL_STATUS_EVENTS: Final[str] = "dashboard_external_status_events"
TABLE_INCIDENTS: Final[str] = "dashboard_incidents"
TABLE_WEEKLY_REPORTS: Final[str] = "dashboard_weekly_reports"
TABLE_OPERATION_CLAIMS: Final[str] = "dashboard_operation_claims"
TABLE_OPERATION_EVENTS: Final[str] = "dashboard_operation_events"
TABLE_REPORT_TRASH: Final[str] = "dashboard_report_trash"
TABLE_REPORT_TRASH_EVENTS: Final[str] = "dashboard_report_trash_events"

OPERATION_WEEKLY_XLSX: Final[str] = "weekly_xlsx"
OPERATION_TRASH_CLEANUP: Final[str] = "trash_cleanup"
OPERATION_STATUSES: Final[frozenset[str]] = frozenset({"running", "succeeded", "failed"})
TRASH_ACTIVE: Final[str] = "active"
TRASH_TRASHED: Final[str] = "trashed"
TRASH_PURGED: Final[str] = "purged"
TRASH_STATUSES: Final[frozenset[str]] = frozenset({TRASH_ACTIVE, TRASH_TRASHED, TRASH_PURGED})

INCIDENT_SECURITY: Final[str] = "security"
INCIDENT_COST: Final[str] = "cost"
INCIDENT_SOURCE_GLOBAL: Final[str] = "source_global"
INCIDENT_PROVIDER_RESPONSE: Final[str] = "provider_response"
INCIDENT_RATE_LIMIT: Final[str] = "rate_limit"
INCIDENT_KINDS: Final[frozenset[str]] = frozenset(
    {INCIDENT_SECURITY, INCIDENT_COST, INCIDENT_SOURCE_GLOBAL, INCIDENT_PROVIDER_RESPONSE, INCIDENT_RATE_LIMIT}
)
_IMMEDIATE_MAINTENANCE_INCIDENTS: Final[frozenset[str]] = frozenset(
    {INCIDENT_SECURITY, INCIDENT_COST, INCIDENT_SOURCE_GLOBAL, INCIDENT_PROVIDER_RESPONSE}
)

MEMBER_DAILY_SUCCESS_LIMIT: Final[int] = 3
MEMBER_USAGE_RESERVED: Final[str] = "reserved"
MEMBER_USAGE_USED: Final[str] = "used"
MEMBER_USAGE_RETURNED: Final[str] = "returned"

REPORT_STATUS_PENDING: Final[str] = "pending"
REPORT_STATUS_FIXING: Final[str] = "fixing"
REPORT_STATUS_RECHECKING: Final[str] = "rechecking"
REPORT_STATUS_RECHECK_FAILED: Final[str] = "recheck_failed"
REPORT_STATUS_NORMAL: Final[str] = "normal"
REPORT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        REPORT_STATUS_PENDING,
        REPORT_STATUS_FIXING,
        REPORT_STATUS_RECHECKING,
        REPORT_STATUS_RECHECK_FAILED,
        REPORT_STATUS_NORMAL,
    }
)

SERVICE_NORMAL: Final[str] = "normal"
SERVICE_MAINTENANCE: Final[str] = "maintenance"
SERVICE_STATUSES: Final[frozenset[str]] = frozenset(
    {SERVICE_NORMAL, SERVICE_MAINTENANCE}
)

COMPANY_LISTED: Final[str] = "listed"
COMPANY_AUDITED: Final[str] = "audited"
COMPANY_UNDECIDED: Final[str] = "undecided"
COMPANY_TYPES: Final[frozenset[str]] = frozenset(
    {COMPANY_LISTED, COMPANY_AUDITED, COMPANY_UNDECIDED}
)

_REPORT_CORP_TYPE_MAP: Final[dict[str, str]] = {
    "상장사": COMPANY_LISTED,
    "비상장 외감": COMPANY_AUDITED,
}

_CREATE_SQL: Final[tuple[str, ...]] = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_REPORT_STATES} (
        report_id     TEXT PRIMARY KEY,
        status        TEXT NOT NULL CHECK(status IN (
            'pending', 'fixing', 'rechecking', 'recheck_failed', 'normal'
        )),
        blocked       INTEGER NOT NULL CHECK(blocked IN (0, 1)),
        company_type  TEXT NOT NULL CHECK(company_type IN ('listed', 'audited', 'undecided')),
        version       INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
        updated_at    TEXT NOT NULL,
        updated_by    TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_REPORT_EVENTS} (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id     TEXT NOT NULL,
        action        TEXT NOT NULL,
        from_status   TEXT NOT NULL DEFAULT '',
        to_status     TEXT NOT NULL DEFAULT '',
        blocked       INTEGER NOT NULL CHECK(blocked IN (0, 1)),
        actor         TEXT NOT NULL,
        reason        TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SURVEYS} (
        report_id             TEXT NOT NULL,
        actor_email           TEXT NOT NULL,
        rating                INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        overall_feedback      TEXT NOT NULL,
        business_distinction  TEXT NOT NULL,
        add_information       TEXT NOT NULL DEFAULT '',
        delete_information    TEXT NOT NULL DEFAULT '',
        revision              INTEGER NOT NULL CHECK(revision >= 1),
        created_at            TEXT NOT NULL,
        updated_at            TEXT NOT NULL,
        PRIMARY KEY (report_id, actor_email)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SURVEY_EVENTS} (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id             TEXT NOT NULL,
        actor_email           TEXT NOT NULL,
        revision              INTEGER NOT NULL,
        rating                INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        overall_feedback      TEXT NOT NULL,
        business_distinction  TEXT NOT NULL,
        add_information       TEXT NOT NULL DEFAULT '',
        delete_information    TEXT NOT NULL DEFAULT '',
        created_at            TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_ERRORS} (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id     TEXT NOT NULL,
        actor_email   TEXT NOT NULL,
        area          TEXT NOT NULL,
        reason        TEXT NOT NULL,
        status        TEXT NOT NULL CHECK(status IN ('pending', 'fixing', 'rechecking', 'recheck_failed', 'normal')),
        created_at    TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SERVICE_STATE} (
        singleton     INTEGER PRIMARY KEY CHECK(singleton = 1),
        status        TEXT NOT NULL CHECK(status IN ('normal', 'maintenance')),
        cause         TEXT NOT NULL DEFAULT '',
        impact        TEXT NOT NULL DEFAULT '',
        next_action   TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL,
        updated_by    TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SERVICE_EVENTS} (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        status        TEXT NOT NULL CHECK(status IN ('normal', 'maintenance')),
        cause         TEXT NOT NULL DEFAULT '',
        impact        TEXT NOT NULL DEFAULT '',
        next_action   TEXT NOT NULL DEFAULT '',
        actor         TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_MEMBER_USAGE} (
        run_id        TEXT PRIMARY KEY,
        actor_email   TEXT NOT NULL,
        day           TEXT NOT NULL,
        state         TEXT NOT NULL CHECK(state IN ('reserved', 'used', 'returned')),
        report_id     TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL,
        settled_at    TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_MEMBER_USAGE_EVENTS} (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL,
        actor_email   TEXT NOT NULL,
        day           TEXT NOT NULL,
        state         TEXT NOT NULL CHECK(state IN ('reserved', 'used', 'returned')),
        report_id     TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_MEMBER_RUN_SUMMARY_EVENTS} (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id         TEXT NOT NULL,
        actor_email    TEXT NOT NULL,
        day            TEXT NOT NULL,
        state          TEXT NOT NULL CHECK(state IN ('reserved', 'used', 'returned')),
        outcome        TEXT NOT NULL DEFAULT '',
        company_type   TEXT NOT NULL DEFAULT 'undecided' CHECK(company_type IN ('listed', 'audited', 'undecided')),
        report_id      TEXT NOT NULL DEFAULT '',
        cost_krw       REAL NOT NULL DEFAULT 0 CHECK(cost_krw >= 0),
        cost_state     TEXT NOT NULL DEFAULT 'not_incurred' CHECK(cost_state IN ('confirmed', 'uncertain', 'not_incurred')),
        created_at     TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_LINK_OPEN_REVIEWS} (
        link_key_hash      TEXT PRIMARY KEY,
        last_seen_open_id  INTEGER NOT NULL DEFAULT 0 CHECK(last_seen_open_id >= 0),
        updated_at         TEXT NOT NULL DEFAULT '',
        updated_by         TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_LINK_OPEN_REVIEW_EVENTS} (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        link_key_hash      TEXT NOT NULL,
        last_seen_open_id  INTEGER NOT NULL CHECK(last_seen_open_id >= 0),
        actor              TEXT NOT NULL,
        created_at         TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_REPORT_VERSIONS} (
        report_id       TEXT NOT NULL,
        version         INTEGER NOT NULL CHECK(version >= 1),
        payload_json    TEXT NOT NULL,
        payload_sha256  TEXT NOT NULL,
        actor           TEXT NOT NULL,
        created_at      TEXT NOT NULL,
        PRIMARY KEY (report_id, version)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_ERROR_SNAPSHOTS} (
        error_id        INTEGER PRIMARY KEY,
        report_id       TEXT NOT NULL,
        report_version  INTEGER NOT NULL CHECK(report_version >= 1),
        created_at      TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SURVEY_SNAPSHOTS} (
        report_id             TEXT NOT NULL,
        report_version        INTEGER NOT NULL CHECK(report_version >= 1),
        actor_email           TEXT NOT NULL,
        rating                INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        overall_feedback      TEXT NOT NULL,
        business_distinction  TEXT NOT NULL,
        add_information       TEXT NOT NULL DEFAULT '',
        delete_information    TEXT NOT NULL DEFAULT '',
        revision              INTEGER NOT NULL CHECK(revision >= 1),
        created_at            TEXT NOT NULL,
        updated_at            TEXT NOT NULL,
        PRIMARY KEY (report_id, report_version, actor_email)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_ERROR_SNAPSHOT_EVENTS} (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        error_id              INTEGER NOT NULL,
        report_id             TEXT NOT NULL,
        report_version        INTEGER NOT NULL CHECK(report_version >= 1),
        report_payload_sha256 TEXT NOT NULL DEFAULT '',
        created_at            TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_SURVEY_SNAPSHOT_EVENTS} (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id             TEXT NOT NULL,
        report_version        INTEGER NOT NULL CHECK(report_version >= 1),
        report_payload_sha256 TEXT NOT NULL DEFAULT '',
        actor_email           TEXT NOT NULL,
        revision              INTEGER NOT NULL CHECK(revision >= 1),
        rating                INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        overall_feedback      TEXT NOT NULL,
        business_distinction  TEXT NOT NULL,
        add_information       TEXT NOT NULL DEFAULT '',
        delete_information    TEXT NOT NULL DEFAULT '',
        created_at            TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_EXTERNAL_STATUS_EVENTS} (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        provider         TEXT NOT NULL,
        status           TEXT NOT NULL CHECK(status IN ('normal', 'error', 'not_used')),
        last_success_at  TEXT NOT NULL DEFAULT '',
        error_at         TEXT NOT NULL DEFAULT '',
        error_summary    TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_INCIDENTS} (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        error_id         INTEGER NOT NULL DEFAULT 0,
        report_id        TEXT NOT NULL DEFAULT '',
        kind             TEXT NOT NULL CHECK(kind IN ('security', 'cost', 'source_global', 'provider_response', 'rate_limit')),
        stage            TEXT NOT NULL DEFAULT '',
        incurred_cost_krw REAL NOT NULL DEFAULT 0 CHECK(incurred_cost_krw >= 0),
        summary          TEXT NOT NULL,
        created_at       TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_WEEKLY_REPORTS} (
        week_start       TEXT PRIMARY KEY,
        week_end         TEXT NOT NULL,
        workbook_blob    BLOB NOT NULL,
        workbook_sha256  TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        created_by       TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_OPERATION_CLAIMS} (
        operation_key    TEXT PRIMARY KEY,
        operation        TEXT NOT NULL CHECK(operation IN ('weekly_xlsx', 'trash_cleanup')),
        period_key       TEXT NOT NULL,
        status           TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
        started_at       TEXT NOT NULL,
        finished_at      TEXT NOT NULL DEFAULT '',
        actor            TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_OPERATION_EVENTS} (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_key    TEXT NOT NULL,
        operation        TEXT NOT NULL CHECK(operation IN ('weekly_xlsx', 'trash_cleanup')),
        status           TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
        detail           TEXT NOT NULL DEFAULT '',
        actor            TEXT NOT NULL,
        created_at       TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_REPORT_TRASH} (
        report_id        TEXT PRIMARY KEY,
        status           TEXT NOT NULL CHECK(status IN ('active', 'trashed', 'purged')),
        trashed_at       TEXT NOT NULL DEFAULT '',
        trashed_by       TEXT NOT NULL DEFAULT '',
        restored_at      TEXT NOT NULL DEFAULT '',
        restored_by      TEXT NOT NULL DEFAULT '',
        purged_at        TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_REPORT_TRASH_EVENTS} (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id        TEXT NOT NULL,
        action           TEXT NOT NULL CHECK(action IN ('trashed', 'restored', 'purged')),
        actor            TEXT NOT NULL,
        reason           TEXT NOT NULL DEFAULT '',
        created_at       TEXT NOT NULL
    )
    """,
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_errors_report ON {TABLE_ERRORS}(report_id, created_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_report_events_report ON {TABLE_REPORT_EVENTS}(report_id, created_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_survey_events_report ON {TABLE_SURVEY_EVENTS}(report_id, created_at DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_member_usage_day ON {TABLE_MEMBER_USAGE}(actor_email, day, state)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_member_run_summary_day ON {TABLE_MEMBER_RUN_SUMMARY_EVENTS}(day, id DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_link_open_review_events_link ON {TABLE_LINK_OPEN_REVIEW_EVENTS}(link_key_hash, id DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_report_versions_report ON {TABLE_REPORT_VERSIONS}(report_id, version DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_survey_snapshots_report ON {TABLE_SURVEY_SNAPSHOTS}(report_id, report_version DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_error_snapshot_events_report ON {TABLE_ERROR_SNAPSHOT_EVENTS}(report_id, report_version DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_survey_snapshot_events_report ON {TABLE_SURVEY_SNAPSHOT_EVENTS}(report_id, report_version DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_external_status_events_provider ON {TABLE_EXTERNAL_STATUS_EVENTS}(provider, id DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_incidents_created ON {TABLE_INCIDENTS}(id DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_operation_events_key ON {TABLE_OPERATION_EVENTS}(operation_key, id DESC)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_report_trash_status ON {TABLE_REPORT_TRASH}(status, trashed_at)",
    f"CREATE INDEX IF NOT EXISTS idx_dashboard_report_trash_events_report ON {TABLE_REPORT_TRASH_EVENTS}(report_id, id DESC)",
)

_APPEND_ONLY_TABLES: Final[tuple[str, ...]] = (
    TABLE_REPORT_EVENTS,
    TABLE_SURVEY_EVENTS,
    TABLE_ERRORS,
    TABLE_SERVICE_EVENTS,
    TABLE_MEMBER_USAGE_EVENTS,
    TABLE_MEMBER_RUN_SUMMARY_EVENTS,
    TABLE_LINK_OPEN_REVIEW_EVENTS,
    TABLE_REPORT_VERSIONS,
    TABLE_ERROR_SNAPSHOTS,
    TABLE_ERROR_SNAPSHOT_EVENTS,
    TABLE_SURVEY_SNAPSHOT_EVENTS,
    TABLE_EXTERNAL_STATUS_EVENTS,
    TABLE_INCIDENTS,
    TABLE_OPERATION_EVENTS,
    TABLE_REPORT_TRASH_EVENTS,
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """대시보드 표와 append-only 보호를 멱등으로 준비한다."""
    for statement in _CREATE_SQL:
        conn.execute(statement)
    for table in _APPEND_ONLY_TABLES:
        conn.execute(
            f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update
            BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
        )
        conn.execute(
            f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete
            BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
        )


def _clean(value: str, *, maximum: int = 3000) -> str:
    return str(value or "").strip()[:maximum]


def _report_company(payload_json: str, *, fallback: str) -> str:
    """저장 보고서의 표시용 회사명을 읽고, 옛 자료면 내부 ID로 안전하게 대체한다."""
    try:
        payload = json.loads(str(payload_json or ""))
    except (TypeError, ValueError):
        payload = {}
    company = (
        _clean(payload.get("company", ""), maximum=300)
        if isinstance(payload, dict)
        else ""
    )
    return company or _clean(fallback, maximum=300)


def _actor(email: str) -> str:
    """비어 있거나 비정상인 actor가 사건을 익명으로 만들지 않게 정규화한다."""
    clean = _clean(email, maximum=320).lower()
    if not clean or "@" not in clean:
        raise ValueError("인증된 이메일이 필요합니다")
    return clean


def actor_digest(email: str) -> str:
    """요약·감사 표시에 원문 이메일 대신 사용할 고정 식별자."""
    return hashlib.sha256(_actor(email).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ReportState:
    report_id: str
    status: str
    blocked: bool
    company_type: str
    version: int
    updated_at: str


@dataclass(frozen=True)
class ReportError:
    id: int
    report_id: str
    actor_email: str
    area: str
    reason: str
    status: str
    created_at: str


@dataclass(frozen=True)
class ReportErrorHistory:
    """신고자와 신고 당시 immutable 보고서 원본을 묶은 감사 조회."""

    id: int
    report_id: str
    actor_email: str
    area: str
    reason: str
    reported_status: str
    created_at: str
    report_version: int
    report_payload_sha256: str
    payload_json: str


class ReportSnapshotIntegrityError(RuntimeError):
    """저장된 버전 원본과 함께 보존한 SHA-256이 일치하지 않음."""


@dataclass(frozen=True)
class ReportSnapshot:
    """관리자가 읽는 immutable 보고서 버전 원본."""

    report_id: str
    version: int
    payload_json: str
    payload_sha256: str
    actor: str
    created_at: str


@dataclass(frozen=True)
class SurveySnapshot:
    """한 MEMBER·한 보고서 버전에 결속된 최신 설문 답변."""

    report_id: str
    report_version: int
    actor_email: str
    rating: int
    overall_feedback: str
    business_distinction: str
    add_information: str
    delete_information: str
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ResolvedIssue:
    """관리자가 재검사 뒤 정상으로 다시 연 최근 문제."""

    report_id: str
    corp_id: str
    company: str
    reason: str
    resolved_at: str
    version: int


@dataclass(frozen=True)
class MemberFeedback:
    """친구 화면에서 읽는 MEMBER·보고서 버전별 최신 설문 원문."""

    report_id: str
    corp_id: str
    company: str
    job: str
    actor_email: str
    rating: int
    overall_feedback: str
    business_distinction: str
    add_information: str
    delete_information: str
    revision: int
    created_at: str
    updated_at: str
    report_version: int
    report_payload_sha256: str
    snapshot_available: bool


@dataclass(frozen=True)
class ServiceState:
    status: str
    cause: str
    impact: str
    next_action: str
    updated_at: str


@dataclass(frozen=True)
class WeeklyReport:
    week_start: str
    week_end: str
    workbook_sha256: str
    created_at: str


@dataclass(frozen=True)
class TrashRecord:
    report_id: str
    status: str
    trashed_at: str
    restored_at: str
    purged_at: str


def weekly_period(week_start: str) -> tuple[str, str]:
    """월요일을 받아 주간 파일의 월~일 경계를 KST 날짜로 고정한다."""
    clean_start = _clean(week_start, maximum=10)
    try:
        start = datetime.fromisoformat(f"{clean_start}T00:00:00")
    except ValueError as exc:
        raise ValueError("주간 파일 기준일은 YYYY-MM-DD 형식이어야 합니다") from exc
    if start.weekday() != 0:
        raise ValueError("주간 파일 기준일은 월요일이어야 합니다")
    return start.date().isoformat(), (start + timedelta(days=6)).date().isoformat()


def operation_key(operation: str, period_key: str) -> str:
    clean_operation = _clean(operation, maximum=40)
    clean_period = _clean(period_key, maximum=40)
    if clean_operation not in {OPERATION_WEEKLY_XLSX, OPERATION_TRASH_CLEANUP} or not clean_period:
        raise ValueError("정기 작업 종류와 기준일이 필요합니다")
    return f"{clean_operation}:{clean_period}"


def claim_operation(
    conn: sqlite3.Connection,
    *,
    operation: str,
    period_key: str,
    actor_email: str,
    now_iso: str,
    reclaim_before_iso: str = "",
) -> str | None:
    """성공 작업만 닫고, 실패·오래 멈춘 작업은 원자적으로 재점유한다."""
    key = operation_key(operation, period_key)
    clean_operation = _clean(operation, maximum=40)
    clean_period = _clean(period_key, maximum=40)
    clean_now = _clean(now_iso, maximum=40)
    clean_reclaim_before = _clean(reclaim_before_iso, maximum=40)
    try:
        parsed_now = datetime.fromisoformat(clean_now)
        parsed_reclaim_before = (
            datetime.fromisoformat(clean_reclaim_before)
            if clean_reclaim_before
            else None
        )
    except ValueError as exc:
        raise ValueError("정기 작업 점유 시각이 올바르지 않습니다") from exc
    if parsed_now.tzinfo is None or (
        parsed_reclaim_before is not None and parsed_reclaim_before.tzinfo is None
    ):
        raise ValueError("정기 작업 점유 시각에는 시간대가 필요합니다")
    if parsed_reclaim_before is not None and parsed_reclaim_before > parsed_now:
        raise ValueError("정기 작업 재점유 기준은 실행 시각보다 늦을 수 없습니다")
    actor = actor_digest(actor_email)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        f"""SELECT status, started_at FROM {TABLE_OPERATION_CLAIMS}
        WHERE operation_key = ?""",
        (key,),
    ).fetchone()
    detail = ""
    if row is None:
        cursor = conn.execute(
            f"""INSERT OR IGNORE INTO {TABLE_OPERATION_CLAIMS}
            (operation_key, operation, period_key, status, started_at, actor)
            VALUES (?, ?, ?, 'running', ?, ?)""",
            (key, clean_operation, clean_period, clean_now, actor),
        )
        if cursor.rowcount != 1:
            return None
    elif str(row["status"]) == "succeeded":
        return None
    elif str(row["status"]) == "failed":
        cursor = conn.execute(
            f"""UPDATE {TABLE_OPERATION_CLAIMS}
            SET status = 'running', started_at = ?, finished_at = '', actor = ?
            WHERE operation_key = ? AND status = 'failed'""",
            (clean_now, actor, key),
        )
        if cursor.rowcount != 1:
            return None
        detail = "failed_retry"
    else:
        if parsed_reclaim_before is None:
            return None
        try:
            started_at = datetime.fromisoformat(str(row["started_at"]))
        except ValueError:
            return None
        if started_at.tzinfo is None or started_at >= parsed_reclaim_before:
            return None
        cursor = conn.execute(
            f"""UPDATE {TABLE_OPERATION_CLAIMS}
            SET started_at = ?, finished_at = '', actor = ?
            WHERE operation_key = ? AND status = 'running' AND started_at = ?""",
            (clean_now, actor, key, str(row["started_at"])),
        )
        if cursor.rowcount != 1:
            return None
        conn.execute(
            f"""INSERT INTO {TABLE_OPERATION_EVENTS}
            (operation_key, operation, status, detail, actor, created_at)
            VALUES (?, ?, 'failed', 'stale_running_reclaimed', ?, ?)""",
            (key, clean_operation, actor, clean_now),
        )
        detail = "stale_retry"
    conn.execute(
        f"""INSERT INTO {TABLE_OPERATION_EVENTS}
        (operation_key, operation, status, detail, actor, created_at)
        VALUES (?, ?, 'running', ?, ?, ?)""",
        (key, clean_operation, detail, actor, clean_now),
    )
    return key


def operation_claim_status(
    conn: sqlite3.Connection, *, operation: str, period_key: str
) -> str:
    """현재 claim projection 상태를 반환한다. 기록이 없으면 빈 문자열이다."""
    row = conn.execute(
        f"SELECT status FROM {TABLE_OPERATION_CLAIMS} WHERE operation_key = ?",
        (operation_key(operation, period_key),),
    ).fetchone()
    return "" if row is None else str(row["status"])


def complete_operation(
    conn: sqlite3.Connection, *, key: str, status: str, detail: str, now_iso: str
) -> bool:
    """claim을 한 번만 마감하고, 성공·실패 모두 append-only 사건으로 남긴다."""
    clean_key = _clean(key, maximum=100)
    clean_status = _clean(status, maximum=20)
    if clean_status not in {"succeeded", "failed"}:
        raise ValueError("정기 작업 마감 상태가 올바르지 않습니다")
    row = conn.execute(
        f"SELECT operation, actor FROM {TABLE_OPERATION_CLAIMS} WHERE operation_key = ?",
        (clean_key,),
    ).fetchone()
    if row is None:
        return False
    cursor = conn.execute(
        f"""UPDATE {TABLE_OPERATION_CLAIMS}
        SET status = ?, finished_at = ? WHERE operation_key = ? AND status = 'running'""",
        (clean_status, now_iso, clean_key),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        f"""INSERT INTO {TABLE_OPERATION_EVENTS}
        (operation_key, operation, status, detail, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (clean_key, str(row["operation"]), clean_status, _clean(detail), str(row["actor"]), now_iso),
    )
    return True


def fail_stalled_operations(
    conn: sqlite3.Connection, *, before_iso: str, now_iso: str
) -> int:
    """이전 KST 날짜에 끝나지 않은 대시보드 작업을 재시작하지 않고 실패로 닫는다."""
    clean_before = _clean(before_iso, maximum=40)
    clean_now = _clean(now_iso, maximum=40)
    try:
        datetime.fromisoformat(clean_before)
        datetime.fromisoformat(clean_now)
    except ValueError as exc:
        raise ValueError("멈춘 작업 정리 시각이 올바르지 않습니다") from exc
    rows = conn.execute(
        f"""SELECT operation_key, operation, actor FROM {TABLE_OPERATION_CLAIMS}
        WHERE status = 'running' AND started_at < ? ORDER BY started_at ASC""",
        (clean_before,),
    ).fetchall()
    closed = 0
    for row in rows:
        key = str(row["operation_key"])
        cursor = conn.execute(
            f"""UPDATE {TABLE_OPERATION_CLAIMS}
            SET status = 'failed', finished_at = ?
            WHERE operation_key = ? AND status = 'running'""",
            (clean_now, key),
        )
        if cursor.rowcount != 1:
            continue
        conn.execute(
            f"""INSERT INTO {TABLE_OPERATION_EVENTS}
            (operation_key, operation, status, detail, actor, created_at)
            VALUES (?, ?, 'failed', 'previous_kst_day_not_finished', ?, ?)""",
            (key, str(row["operation"]), str(row["actor"]), clean_now),
        )
        closed += 1
    return closed


def list_operation_claims(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, object]]:
    rows = conn.execute(
        f"""SELECT operation_key, operation, period_key, status, started_at, finished_at
        FROM {TABLE_OPERATION_CLAIMS} ORDER BY started_at DESC LIMIT ?""",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [dict(row) for row in rows]


def list_failed_operation_issues(conn: sqlite3.Connection, *, limit: int = 20) -> list[dict[str, object]]:
    rows = conn.execute(
        f"""SELECT e.operation_key, e.operation, e.detail, e.created_at
        FROM {TABLE_OPERATION_EVENTS} AS e
        JOIN {TABLE_OPERATION_CLAIMS} AS c ON c.operation_key = e.operation_key
        WHERE e.status = 'failed' AND c.status = 'failed'
          AND c.started_at = (
            SELECT MAX(c2.started_at) FROM {TABLE_OPERATION_CLAIMS} AS c2
            WHERE c2.operation = c.operation
          )
        ORDER BY e.created_at ASC, e.id ASC LIMIT ?""",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [dict(row) for row in rows]


def save_weekly_report(
    conn: sqlite3.Connection, *, week_start: str, workbook_blob: bytes,
    actor_email: str, now_iso: str,
) -> bool:
    """관리자 다운로드 전용 주간 파일을 같은 주에 한 번만 저장한다."""
    start, end = weekly_period(week_start)
    blob = bytes(workbook_blob)
    if not blob or len(blob) > 10 * 1024 * 1024:
        raise ValueError("주간 XLSX 파일 크기가 올바르지 않습니다")
    cursor = conn.execute(
        f"""INSERT OR IGNORE INTO {TABLE_WEEKLY_REPORTS}
        (week_start, week_end, workbook_blob, workbook_sha256, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (start, end, blob, hashlib.sha256(blob).hexdigest(), now_iso, actor_digest(actor_email)),
    )
    return cursor.rowcount == 1


def load_weekly_report_blob(conn: sqlite3.Connection, *, week_start: str) -> bytes | None:
    start, _end = weekly_period(week_start)
    row = conn.execute(
        f"SELECT workbook_blob FROM {TABLE_WEEKLY_REPORTS} WHERE week_start = ?", (start,)
    ).fetchone()
    return None if row is None else bytes(row["workbook_blob"])


def list_weekly_reports(conn: sqlite3.Connection, *, limit: int = 12) -> list[WeeklyReport]:
    rows = conn.execute(
        f"""SELECT week_start, week_end, workbook_sha256, created_at
        FROM {TABLE_WEEKLY_REPORTS} ORDER BY week_start DESC LIMIT ?""",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [
        WeeklyReport(str(row["week_start"]), str(row["week_end"]), str(row["workbook_sha256"]), str(row["created_at"]))
        for row in rows
    ]


def trash_report(
    conn: sqlite3.Connection, *, report_id: str, actor_email: str, reason: str, now_iso: str
) -> bool:
    """보고서를 즉시 모든 통계·공개 경계에서 빼고 복구 가능한 휴지통으로 옮긴다."""
    clean_id = _clean(report_id, maximum=128)
    actor = actor_digest(actor_email)
    clean_reason = _clean(reason)
    if not clean_id or not clean_reason:
        raise ValueError("휴지통 이동 이유가 필요합니다")
    row = conn.execute(
        f"SELECT status FROM {TABLE_REPORT_TRASH} WHERE report_id = ?", (clean_id,)
    ).fetchone()
    if row is None:
        cursor = conn.execute(
            f"""INSERT INTO {TABLE_REPORT_TRASH}
            (report_id, status, trashed_at, trashed_by) VALUES (?, 'trashed', ?, ?)""",
            (clean_id, now_iso, actor),
        )
    elif str(row["status"]) == TRASH_ACTIVE:
        cursor = conn.execute(
            f"""UPDATE {TABLE_REPORT_TRASH}
            SET status = 'trashed', trashed_at = ?, trashed_by = ?, restored_at = '', restored_by = ''
            WHERE report_id = ? AND status = 'active'""",
            (now_iso, actor, clean_id),
        )
    else:
        return False
    if cursor.rowcount != 1:
        return False
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_TRASH_EVENTS}
        (report_id, action, actor, reason, created_at) VALUES (?, 'trashed', ?, ?, ?)""",
        (clean_id, actor, clean_reason, now_iso),
    )
    return True


def restore_report_from_trash(
    conn: sqlite3.Connection, *, report_id: str, actor_email: str, reason: str, now_iso: str
) -> bool:
    """30일 보관 중인 보고서만 관리자 사유와 함께 통계·공개 대상에 되돌린다."""
    clean_id = _clean(report_id, maximum=128)
    actor = actor_digest(actor_email)
    clean_reason = _clean(reason)
    if not clean_id or not clean_reason:
        raise ValueError("휴지통 복구 이유가 필요합니다")
    cursor = conn.execute(
        f"""UPDATE {TABLE_REPORT_TRASH}
        SET status = 'active', restored_at = ?, restored_by = ?
        WHERE report_id = ? AND status = 'trashed'""",
        (now_iso, actor, clean_id),
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_TRASH_EVENTS}
        (report_id, action, actor, reason, created_at) VALUES (?, 'restored', ?, ?, ?)""",
        (clean_id, actor, clean_reason, now_iso),
    )
    return True


def report_is_trashed(conn: sqlite3.Connection, report_id: str) -> bool:
    row = conn.execute(
        f"SELECT status FROM {TABLE_REPORT_TRASH} WHERE report_id = ?", (_clean(report_id, maximum=128),)
    ).fetchone()
    return row is not None and str(row["status"]) in {TRASH_TRASHED, TRASH_PURGED}


def trash_record(conn: sqlite3.Connection, report_id: str) -> TrashRecord | None:
    row = conn.execute(
        f"""SELECT report_id, status, trashed_at, restored_at, purged_at
        FROM {TABLE_REPORT_TRASH} WHERE report_id = ?""",
        (_clean(report_id, maximum=128),),
    ).fetchone()
    if row is None:
        return None
    return TrashRecord(
        str(row["report_id"]), str(row["status"]), str(row["trashed_at"]),
        str(row["restored_at"]), str(row["purged_at"]),
    )


def list_trashed_reports(conn: sqlite3.Connection, *, limit: int = 100) -> list[TrashRecord]:
    rows = conn.execute(
        f"""SELECT report_id, status, trashed_at, restored_at, purged_at
        FROM {TABLE_REPORT_TRASH} WHERE status = 'trashed'
        ORDER BY trashed_at ASC LIMIT ?""",
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    return [
        TrashRecord(str(row["report_id"]), str(row["status"]), str(row["trashed_at"]),
                    str(row["restored_at"]), str(row["purged_at"]))
        for row in rows
    ]


def purge_expired_trash(conn: sqlite3.Connection, *, now_iso: str) -> int:
    """30일이 지난 휴지통의 원 저장 보고서를 AI 호출 없이 영구 삭제한다."""
    try:
        cutoff = (datetime.fromisoformat(now_iso) - timedelta(days=30)).isoformat(timespec="seconds")
    except ValueError as exc:
        raise ValueError("휴지통 정리 시각이 올바르지 않습니다") from exc
    rows = conn.execute(
        f"""SELECT report_id FROM {TABLE_REPORT_TRASH}
        WHERE status = 'trashed' AND trashed_at <> '' AND trashed_at <= ?""",
        (cutoff,),
    ).fetchall()
    purged = 0
    for row in rows:
        report_id = str(row["report_id"])
        conn.execute(
            f"DELETE FROM {storage_constants.TABLE_LAYER1_CACHE} WHERE report_id = ?", (report_id,)
        )
        conn.execute(
            f"DELETE FROM {TABLE_SURVEYS} WHERE report_id = ?", (report_id,)
        )
        conn.execute(
            f"DELETE FROM {TABLE_REPORT_STATES} WHERE report_id = ?", (report_id,)
        )
        conn.execute(
            f"DELETE FROM {storage_constants.TABLE_REPORTS} WHERE report_id = ?", (report_id,)
        )
        cursor = conn.execute(
            f"""UPDATE {TABLE_REPORT_TRASH}
            SET status = 'purged', purged_at = ? WHERE report_id = ? AND status = 'trashed'""",
            (now_iso, report_id),
        )
        if cursor.rowcount != 1:
            continue
        conn.execute(
            f"""INSERT INTO {TABLE_REPORT_TRASH_EVENTS}
            (report_id, action, actor, reason, created_at)
            VALUES (?, 'purged', 'system:trash-cleanup', '30_day_retention', ?)""",
            (report_id, now_iso),
        )
        purged += 1
    return purged


def _state_from_row(row: sqlite3.Row) -> ReportState:
    return ReportState(
        report_id=str(row["report_id"]), status=str(row["status"]),
        blocked=bool(row["blocked"]), company_type=str(row["company_type"]),
        version=int(row["version"]), updated_at=str(row["updated_at"]),
    )


def get_report_state(conn: sqlite3.Connection, report_id: str) -> ReportState:
    clean_id = _clean(report_id, maximum=128)
    if not clean_id:
        raise ValueError("보고서 ID가 필요합니다")
    row = conn.execute(
        f"SELECT report_id, status, blocked, company_type, version, updated_at FROM {TABLE_REPORT_STATES} WHERE report_id = ?",
        (clean_id,),
    ).fetchone()
    if row is None:
        return ReportState(clean_id, REPORT_STATUS_NORMAL, False, COMPANY_UNDECIDED, 1, "")
    return _state_from_row(row)


def company_type_from_report(corp_type: str) -> str:
    """저장 보고서의 정본 회사 유형을 대시보드용 세 분류로 고정한다."""
    return _REPORT_CORP_TYPE_MAP.get(_clean(corp_type, maximum=40), COMPANY_UNDECIDED)


def register_report(
    conn: sqlite3.Connection, *, report_id: str, corp_type: str, now_iso: str,
    payload_json: str = "",
) -> ReportState:
    """새 보고서만 초기 projection에 등록한다.

    원 보고서는 수정하지 않으며, 이미 등록된 보고서의 상태·버전도 덮어쓰지 않는다.
    """
    clean_id = _clean(report_id, maximum=128)
    if not clean_id:
        raise ValueError("보고서 ID가 필요합니다.")
    existing = conn.execute(
        f"SELECT report_id FROM {TABLE_REPORT_STATES} WHERE report_id = ?", (clean_id,)
    ).fetchone()
    if existing is not None:
        return get_report_state(conn, clean_id)
    company_type = company_type_from_report(corp_type)
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_STATES}
        (report_id, status, blocked, company_type, version, updated_at, updated_by)
        VALUES (?, ?, 0, ?, 1, ?, '')""",
        (clean_id, REPORT_STATUS_NORMAL, company_type, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_EVENTS}
        (report_id, action, from_status, to_status, blocked, actor, reason, created_at)
        VALUES (?, 'report_registered', '', ?, 0, 'system', '', ?)""",
        (clean_id, REPORT_STATUS_NORMAL, now_iso),
    )
    if payload_json:
        capture_report_snapshot(
            conn,
            report_id=clean_id,
            version=1,
            payload_json=payload_json,
            actor="system",
            now_iso=now_iso,
        )
    return get_report_state(conn, clean_id)


def capture_report_snapshot(
    conn: sqlite3.Connection, *, report_id: str, version: int, payload_json: str,
    actor: str, now_iso: str,
) -> bool:
    """새 공개 버전의 원본 payload를 append-only로 동결한다."""
    clean_id = _clean(report_id, maximum=128)
    payload = str(payload_json or "")
    clean_actor = _clean(actor, maximum=80) or "system"
    if not clean_id or int(version) < 1 or not payload:
        raise ValueError("보고서 버전 원본을 저장할 수 없습니다.")
    validate_persisted_json_text(payload)
    payload_sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    duplicate = conn.execute(
        f"""SELECT version FROM {TABLE_REPORT_VERSIONS}
        WHERE report_id = ? AND payload_sha256 = ? LIMIT 1""",
        (clean_id, payload_sha256),
    ).fetchone()
    if duplicate is not None:
        return False
    cursor = conn.execute(
        f"""INSERT OR IGNORE INTO {TABLE_REPORT_VERSIONS}
        (report_id, version, payload_json, payload_sha256, actor, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            clean_id,
            int(version),
            payload,
            payload_sha256,
            clean_actor,
            now_iso,
        ),
    )
    return cursor.rowcount == 1


def report_snapshot_exists(
    conn: sqlite3.Connection, *, report_id: str, version: int
) -> bool:
    """요청한 공개 버전의 immutable 원본이 이미 동결됐는지 확인한다."""
    row = conn.execute(
        f"""SELECT 1 FROM {TABLE_REPORT_VERSIONS}
        WHERE report_id = ? AND version = ? LIMIT 1""",
        (_clean(report_id, maximum=128), int(version)),
    ).fetchone()
    return row is not None


def get_report_snapshot(
    conn: sqlite3.Connection, *, report_id: str, version: int
) -> ReportSnapshot | None:
    """요청 버전의 원본을 읽고 저장 당시 SHA-256과 다시 대조한다.

    누락은 ``None``으로 구분하지만, 원본과 지문이 다르면 현재 보고서로 대신하지
    못하도록 별도 무결성 오류를 낸다.
    """

    clean_id = _clean(report_id, maximum=128)
    clean_version = int(version)
    if not clean_id or clean_version < 1:
        raise ValueError("올바른 보고서 버전이 필요합니다.")
    row = conn.execute(
        f"""SELECT report_id, version, payload_json, payload_sha256, actor, created_at
        FROM {TABLE_REPORT_VERSIONS} WHERE report_id = ? AND version = ?""",
        (clean_id, clean_version),
    ).fetchone()
    if row is None:
        return None
    payload_json = str(row["payload_json"])
    payload_sha256 = str(row["payload_sha256"])
    actual_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    if not payload_sha256 or actual_sha256 != payload_sha256:
        raise ReportSnapshotIntegrityError(
            "보고서 스냅샷 원본과 SHA-256이 일치하지 않습니다."
        )
    return ReportSnapshot(
        report_id=str(row["report_id"]),
        version=int(row["version"]),
        payload_json=payload_json,
        payload_sha256=payload_sha256,
        actor=str(row["actor"]),
        created_at=str(row["created_at"]),
    )


def approved_report_payload(conn: sqlite3.Connection, *, report_id: str) -> str:
    """정상 공개로 승인된 버전의 immutable payload만 돌려준다."""
    state = get_report_state(conn, report_id)
    if state.status != REPORT_STATUS_NORMAL or state.blocked:
        return ""
    snapshot = get_report_snapshot(
        conn,
        report_id=report_id,
        version=state.version,
    )
    return "" if snapshot is None else snapshot.payload_json


def report_is_blocked(conn: sqlite3.Connection, report_id: str) -> bool:
    return get_report_state(conn, report_id).blocked


def record_error(
    conn: sqlite3.Connection, *, report_id: str, actor_email: str, area: str,
    reason: str, now_iso: str, incident_kind: str = "",
) -> ReportError:
    """신고를 남기고 같은 transaction에서 결과 공개를 즉시 닫는다."""
    clean_id = _clean(report_id, maximum=128)
    clean_area = _clean(area, maximum=1000)
    clean_reason = _clean(reason, maximum=3000)
    actor = _actor(actor_email)
    if not clean_id or not clean_area or not clean_reason:
        raise ValueError("신고 항목과 이유를 모두 입력해야 합니다")
    old = get_report_state(conn, clean_id)
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_STATES}
        (report_id, status, blocked, company_type, version, updated_at, updated_by)
        VALUES (?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            status=excluded.status, blocked=1, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (clean_id, REPORT_STATUS_PENDING, old.company_type, old.version, now_iso, actor_digest(actor)),
    )
    cursor = conn.execute(
        f"INSERT INTO {TABLE_ERRORS} (report_id, actor_email, area, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (clean_id, actor, clean_area, clean_reason, REPORT_STATUS_PENDING, now_iso),
    )
    snapshot = conn.execute(
        f"SELECT payload_sha256 FROM {TABLE_REPORT_VERSIONS} WHERE report_id = ? AND version = ?",
        (clean_id, old.version),
    ).fetchone()
    payload_sha256 = "" if snapshot is None else str(snapshot["payload_sha256"])
    conn.execute(
        f"""INSERT INTO {TABLE_ERROR_SNAPSHOTS}
        (error_id, report_id, report_version, created_at) VALUES (?, ?, ?, ?)""",
        (int(cursor.lastrowid), clean_id, old.version, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_ERROR_SNAPSHOT_EVENTS}
        (error_id, report_id, report_version, report_payload_sha256, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (int(cursor.lastrowid), clean_id, old.version, payload_sha256, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_EVENTS}
        (report_id, action, from_status, to_status, blocked, actor, reason, created_at)
        VALUES (?, 'error_reported', ?, ?, 1, ?, ?, ?)""",
        (clean_id, old.status, REPORT_STATUS_PENDING, actor_digest(actor), "member_report", now_iso),
    )
    clean_incident = _clean(incident_kind, maximum=40)
    if clean_incident:
        record_incident(
            conn,
            kind=clean_incident,
            summary=clean_reason,
            error_id=int(cursor.lastrowid),
            report_id=clean_id,
            now_iso=now_iso,
        )
    _enter_maintenance_for_repeated_error(
        conn, area=clean_area, reason=clean_reason, now_iso=now_iso
    )
    return ReportError(int(cursor.lastrowid), clean_id, actor, clean_area, clean_reason, REPORT_STATUS_PENDING, now_iso)


def _enter_maintenance_for_repeated_error(
    conn: sqlite3.Connection, *, area: str, reason: str, now_iso: str
) -> None:
    """서로 다른 두 MEMBER·보고서에서 같은 신고가 확인되면 점검으로 전환한다.

    자유 서술을 추정하거나 외부 호출로 심각도를 판정하지 않는다. 정확히 같은
    대상과 원인이 서로 다른 인증 MEMBER와 서로 다른 보고서에 기록된 경우에만
    보수적으로 전환한다. 한 MEMBER의 반복 신고는 각 보고서만 차단하며 전역 점검을
    만들지 않는다. 정상 복귀는 관리자 POST로만 가능하다.
    """
    current = get_service_state(conn)
    if current.status == SERVICE_MAINTENANCE:
        return
    row = conn.execute(
        f"""SELECT COUNT(DISTINCT report_id) AS report_count,
            COUNT(DISTINCT actor_email) AS actor_count
        FROM {TABLE_ERRORS} WHERE area = ? AND reason = ?""",
        (area, reason),
    ).fetchone()
    if int(row["report_count"]) < 2 or int(row["actor_count"]) < 2:
        return
    impact = "같은 원인의 오류 신고가 2건 접수되어 관련 결과·다운로드·공유를 차단했습니다."
    next_action = "원인 확인과 재검사 기록을 남긴 뒤, 운영자가 직접 재시작합니다."
    conn.execute(
        f"""INSERT INTO {TABLE_SERVICE_STATE}
        (singleton, status, cause, impact, next_action, updated_at, updated_by)
        VALUES (1, ?, ?, ?, ?, ?, 'system:repeated-error')
        ON CONFLICT(singleton) DO UPDATE SET status=excluded.status, cause=excluded.cause,
            impact=excluded.impact, next_action=excluded.next_action,
            updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (SERVICE_MAINTENANCE, reason, impact, next_action, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_SERVICE_EVENTS}
        (status, cause, impact, next_action, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (SERVICE_MAINTENANCE, reason, impact, next_action, "system:repeated-error", now_iso),
    )


_ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    REPORT_STATUS_PENDING: frozenset({REPORT_STATUS_FIXING}),
    REPORT_STATUS_FIXING: frozenset({REPORT_STATUS_RECHECKING}),
    REPORT_STATUS_RECHECKING: frozenset({REPORT_STATUS_NORMAL, REPORT_STATUS_RECHECK_FAILED}),
    REPORT_STATUS_RECHECK_FAILED: frozenset({REPORT_STATUS_FIXING}),
    REPORT_STATUS_NORMAL: frozenset({REPORT_STATUS_PENDING}),
}


def change_report_state(
    conn: sqlite3.Connection, *, report_id: str, next_status: str, actor_email: str,
    reason: str, now_iso: str, company_type: str = "",
) -> ReportState:
    """관리자만 호출하는 수동 상태 전이.

    ``normal``은 재검사 뒤의 명시적 재공개에만 열리므로 오류 신고가 들어온
    결과가 자동으로 되살아나는 길은 없다.
    """
    clean_id = _clean(report_id, maximum=128)
    target = _clean(next_status, maximum=40)
    actor = _actor(actor_email)
    if target not in REPORT_STATUSES:
        raise ValueError("지원하지 않는 보고서 상태입니다")
    old = get_report_state(conn, clean_id)
    if target not in _ALLOWED_TRANSITIONS.get(old.status, frozenset()):
        raise ValueError("허용되지 않은 보고서 상태 전이입니다")
    if (
        target == REPORT_STATUS_NORMAL
        and not report_snapshot_exists(conn, report_id=clean_id, version=old.version + 1)
    ):
        raise ValueError("재공개 전에는 검증된 수정본 원본을 먼저 등록해야 합니다.")
    clean_reason = _clean(reason)
    if not clean_reason:
        raise ValueError("상태 변경 이유와 재검사 기록이 필요합니다")
    requested_type = _clean(company_type, maximum=40) or old.company_type
    if requested_type not in COMPANY_TYPES:
        raise ValueError("지원하지 않는 회사 유형입니다")
    blocked = target != REPORT_STATUS_NORMAL
    version = old.version + (1 if target == REPORT_STATUS_NORMAL else 0)
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_STATES}
        (report_id, status, blocked, company_type, version, updated_at, updated_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET status=excluded.status, blocked=excluded.blocked,
            company_type=excluded.company_type, version=excluded.version,
            updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (clean_id, target, int(blocked), requested_type, version, now_iso, actor_digest(actor)),
    )
    # 오류 사건은 당시 ``pending`` 상태를 보존한다. 현재 처리 상태는 위 projection
    # 하나에서 읽는다. 그러므로 해결했다고 과거 신고 행을 UPDATE하지 않는다.
    conn.execute(
        f"""INSERT INTO {TABLE_REPORT_EVENTS}
        (report_id, action, from_status, to_status, blocked, actor, reason, created_at)
        VALUES (?, 'manual_status_change', ?, ?, ?, ?, ?, ?)""",
        (clean_id, old.status, target, int(blocked), actor_digest(actor), clean_reason, now_iso),
    )
    return ReportState(clean_id, target, blocked, requested_type, version, now_iso)


def save_survey(
    conn: sqlite3.Connection, *, report_id: str, actor_email: str, rating: int,
    overall_feedback: str, business_distinction: str, add_information: str,
    delete_information: str, now_iso: str, report_version: int = 1,
) -> int:
    """한 MEMBER·한 보고서 스냅샷의 최신값과 수정 이력을 함께 남긴다."""
    clean_id = _clean(report_id, maximum=128)
    actor = _actor(actor_email)
    if not clean_id or int(rating) not in range(1, 6):
        raise ValueError("별점은 1~5점이어야 합니다")
    overall = _clean(overall_feedback)
    distinction = _clean(business_distinction)
    if not overall or not distinction:
        raise ValueError("전체 소감과 사업상 구분점 답변은 필수입니다")
    existing = conn.execute(
        f"SELECT revision FROM {TABLE_SURVEYS} WHERE report_id = ? AND actor_email = ?",
        (clean_id, actor),
    ).fetchone()
    revision = 1 if existing is None else int(existing["revision"]) + 1
    optional_add = _clean(add_information)
    optional_delete = _clean(delete_information)
    snapshot_version = max(1, int(report_version))
    conn.execute(
        f"""INSERT INTO {TABLE_SURVEYS}
        (report_id, actor_email, rating, overall_feedback, business_distinction, add_information, delete_information, revision, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id, actor_email) DO UPDATE SET rating=excluded.rating,
          overall_feedback=excluded.overall_feedback, business_distinction=excluded.business_distinction,
          add_information=excluded.add_information, delete_information=excluded.delete_information,
          revision=excluded.revision, updated_at=excluded.updated_at""",
        (clean_id, actor, int(rating), overall, distinction, optional_add, optional_delete, revision, now_iso, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_SURVEY_EVENTS}
        (report_id, actor_email, revision, rating, overall_feedback, business_distinction, add_information, delete_information, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (clean_id, actor, revision, int(rating), overall, distinction, optional_add, optional_delete, now_iso),
    )
    snapshot = conn.execute(
        f"SELECT revision FROM {TABLE_SURVEY_SNAPSHOTS} WHERE report_id = ? AND report_version = ? AND actor_email = ?",
        (clean_id, snapshot_version, actor),
    ).fetchone()
    snapshot_revision = 1 if snapshot is None else int(snapshot["revision"]) + 1
    conn.execute(
        f"""INSERT INTO {TABLE_SURVEY_SNAPSHOTS}
        (report_id, report_version, actor_email, rating, overall_feedback, business_distinction,
         add_information, delete_information, revision, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id, report_version, actor_email) DO UPDATE SET
          rating=excluded.rating, overall_feedback=excluded.overall_feedback,
          business_distinction=excluded.business_distinction, add_information=excluded.add_information,
          delete_information=excluded.delete_information, revision=excluded.revision,
          updated_at=excluded.updated_at""",
        (clean_id, snapshot_version, actor, int(rating), overall, distinction,
         optional_add, optional_delete, snapshot_revision, now_iso, now_iso),
    )
    report_snapshot = conn.execute(
        f"SELECT payload_sha256 FROM {TABLE_REPORT_VERSIONS} WHERE report_id = ? AND version = ?",
        (clean_id, snapshot_version),
    ).fetchone()
    payload_sha256 = "" if report_snapshot is None else str(report_snapshot["payload_sha256"])
    conn.execute(
        f"""INSERT INTO {TABLE_SURVEY_SNAPSHOT_EVENTS}
        (report_id, report_version, report_payload_sha256, actor_email, revision, rating,
         overall_feedback, business_distinction, add_information, delete_information, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (clean_id, snapshot_version, payload_sha256, actor, snapshot_revision, int(rating),
         overall, distinction, optional_add, optional_delete, now_iso),
    )
    return snapshot_revision


def get_survey_snapshot(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    report_version: int,
    actor_email: str,
) -> SurveySnapshot | None:
    """현재 열어 본 보고서 버전에 해당하는 MEMBER 답변만 읽는다."""

    clean_id = _clean(report_id, maximum=128)
    clean_version = int(report_version)
    actor = _actor(actor_email)
    if not clean_id or clean_version < 1:
        raise ValueError("올바른 보고서 버전이 필요합니다.")
    row = conn.execute(
        f"""SELECT report_id, report_version, actor_email, rating,
            overall_feedback, business_distinction, add_information,
            delete_information, revision, created_at, updated_at
        FROM {TABLE_SURVEY_SNAPSHOTS}
        WHERE report_id = ? AND report_version = ? AND actor_email = ?""",
        (clean_id, clean_version, actor),
    ).fetchone()
    if row is None:
        return None
    return SurveySnapshot(
        report_id=str(row["report_id"]),
        report_version=int(row["report_version"]),
        actor_email=str(row["actor_email"]),
        rating=int(row["rating"]),
        overall_feedback=str(row["overall_feedback"]),
        business_distinction=str(row["business_distinction"]),
        add_information=str(row["add_information"]),
        delete_information=str(row["delete_information"]),
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def get_service_state(conn: sqlite3.Connection) -> ServiceState:
    row = conn.execute(
        f"SELECT status, cause, impact, next_action, updated_at FROM {TABLE_SERVICE_STATE} WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return ServiceState(SERVICE_NORMAL, "", "", "", "")
    return ServiceState(*(str(row[key]) for key in ("status", "cause", "impact", "next_action", "updated_at")))


def record_external_status(
    conn: sqlite3.Connection, *, provider: str, status: str, now_iso: str,
    last_success_at: str = "", error_at: str = "", error_summary: str = "",
) -> None:
    """외부 시험 호출 없이 이미 일어난 성공·오류만 append-only로 남긴다."""
    clean_provider = _clean(provider, maximum=80)
    clean_status = _clean(status, maximum=20)
    if not clean_provider or clean_status not in {"normal", "error", "not_used"}:
        raise ValueError("외부 연결 상태 기록이 올바르지 않습니다.")
    conn.execute(
        f"""INSERT INTO {TABLE_EXTERNAL_STATUS_EVENTS}
        (provider, status, last_success_at, error_at, error_summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            clean_provider,
            clean_status,
            _clean(last_success_at, maximum=40),
            _clean(error_at, maximum=40),
            _clean(error_summary, maximum=1000),
            now_iso,
        ),
    )


def external_status_cards(
    conn: sqlite3.Connection, *, providers: tuple[tuple[str, bool], ...]
) -> list[dict[str, str]]:
    """연결별 마지막 사건을 읽고, 기록이 없으면 추측하지 않고 상태를 표시한다."""
    cards: list[dict[str, str]] = []
    for provider, configured in providers:
        latest = conn.execute(
            f"""SELECT status, last_success_at, error_at, error_summary
            FROM {TABLE_EXTERNAL_STATUS_EVENTS} WHERE provider = ? ORDER BY id DESC LIMIT 1""",
            (provider,),
        ).fetchone()
        last_success = conn.execute(
            f"""SELECT last_success_at FROM {TABLE_EXTERNAL_STATUS_EVENTS}
            WHERE provider = ? AND status = 'normal' AND last_success_at <> '' ORDER BY id DESC LIMIT 1""",
            (provider,),
        ).fetchone()
        recent_error = conn.execute(
            f"""SELECT error_at, error_summary FROM {TABLE_EXTERNAL_STATUS_EVENTS}
            WHERE provider = ? AND status = 'error' ORDER BY id DESC LIMIT 1""",
            (provider,),
        ).fetchone()
        if latest is None:
            status = "unavailable" if configured else "not_used"
            cards.append(
                {
                    "provider": provider,
                    "status": status,
                    "last_success_at": "",
                    "error_at": "",
                    "error_summary": "기존 실행 기록이 없어 확인 불가" if configured else "아직 사용 안 함",
                }
            )
            continue
        cards.append(
            {
                "provider": provider,
                "status": str(latest["status"]),
                "last_success_at": "" if last_success is None else str(last_success["last_success_at"]),
                "error_at": "" if recent_error is None else str(recent_error["error_at"]),
                "error_summary": "" if recent_error is None else str(recent_error["error_summary"]),
            }
        )
    return cards


def record_incident(
    conn: sqlite3.Connection, *, kind: str, summary: str, now_iso: str,
    error_id: int = 0, report_id: str = "", stage: str = "", incurred_cost_krw: float = 0.0,
) -> None:
    """구조화된 운영 incident를 남기고, 중대한 것은 한 건만으로 새 생성을 닫는다."""
    clean_kind = _clean(kind, maximum=40)
    clean_summary = _clean(summary, maximum=3000)
    clean_report = _clean(report_id, maximum=128)
    clean_stage = _clean(stage, maximum=100)
    if clean_kind not in INCIDENT_KINDS or not clean_summary:
        raise ValueError("incident 종류와 요약이 필요합니다.")
    try:
        clean_cost = max(0.0, float(incurred_cost_krw))
    except (TypeError, ValueError):
        clean_cost = 0.0
    conn.execute(
        f"""INSERT INTO {TABLE_INCIDENTS}
        (error_id, report_id, kind, stage, incurred_cost_krw, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (max(0, int(error_id)), clean_report, clean_kind, clean_stage, clean_cost, clean_summary, now_iso),
    )
    repeated_rate_limit = False
    if clean_kind == INCIDENT_RATE_LIMIT:
        row = conn.execute(
            f"SELECT COUNT(*) AS count FROM {TABLE_INCIDENTS} WHERE kind = ? AND summary = ?",
            (INCIDENT_RATE_LIMIT, clean_summary),
        ).fetchone()
        repeated_rate_limit = int(row["count"]) >= 2
    if clean_kind not in _IMMEDIATE_MAINTENANCE_INCIDENTS and not repeated_rate_limit:
        return
    if get_service_state(conn).status == SERVICE_MAINTENANCE:
        return
    impact = (
        "같은 rate limit이 반복되어 새 보고서 생성을 멈췄습니다."
        if repeated_rate_limit
        else "중대한 전역 문제가 1건 확인되어 새 보고서 생성을 멈췄습니다."
    )
    next_action = "원인·수정 내용과 관련 보고서 재검사·전체 시험을 기록한 뒤 관리자가 직접 재가동합니다."
    conn.execute(
        f"""INSERT INTO {TABLE_SERVICE_STATE}
        (singleton, status, cause, impact, next_action, updated_at, updated_by)
        VALUES (1, ?, ?, ?, ?, ?, 'system:critical-incident')
        ON CONFLICT(singleton) DO UPDATE SET status=excluded.status, cause=excluded.cause,
            impact=excluded.impact, next_action=excluded.next_action,
            updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (SERVICE_MAINTENANCE, clean_summary, impact, next_action, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_SERVICE_EVENTS}
        (status, cause, impact, next_action, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (SERVICE_MAINTENANCE, clean_summary, impact, next_action, "system:critical-incident", now_iso),
    )


def list_incidents(conn: sqlite3.Connection, *, limit: int = 25) -> list[dict[str, object]]:
    rows = conn.execute(
        f"""SELECT id, error_id, report_id, kind, stage, incurred_cost_krw, summary, created_at
        FROM {TABLE_INCIDENTS} ORDER BY id DESC LIMIT ?""",
        (max(1, min(limit, 500)),),
    ).fetchall()
    return [dict(row) for row in rows]


def list_active_incidents(
    conn: sqlite3.Connection, *, limit: int = 25
) -> list[dict[str, object]]:
    """마지막 수동 정상 복귀 뒤에 생긴 운영 사고만 오래된 순으로 읽는다."""
    boundary = conn.execute(
        f"""SELECT created_at FROM {TABLE_SERVICE_EVENTS}
        WHERE status = ? ORDER BY id DESC LIMIT 1""",
        (SERVICE_NORMAL,),
    ).fetchone()
    params: list[object] = []
    query = f"""SELECT id, error_id, report_id, kind, stage,
        incurred_cost_krw, summary, created_at FROM {TABLE_INCIDENTS}"""
    if boundary is not None:
        query += " WHERE created_at > ?"
        params.append(str(boundary["created_at"]))
    query += " ORDER BY created_at ASC, id ASC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    return [dict(row) for row in conn.execute(query, tuple(params)).fetchall()]


def set_service_state(
    conn: sqlite3.Connection, *, status: str, cause: str, impact: str,
    next_action: str, actor_email: str, now_iso: str,
) -> ServiceState:
    clean_status = _clean(status, maximum=30)
    actor = _actor(actor_email)
    if clean_status not in SERVICE_STATUSES:
        raise ValueError("지원하지 않는 서비스 상태입니다")
    clean_cause, clean_impact, clean_next = _clean(cause), _clean(impact), _clean(next_action)
    current = get_service_state(conn)
    if clean_status == SERVICE_MAINTENANCE and not all((clean_cause, clean_impact, clean_next)):
        raise ValueError("점검 중에는 원인·영향·다음 행동을 모두 기록해야 합니다")
    if (
        current.status == SERVICE_MAINTENANCE
        and clean_status == SERVICE_NORMAL
        and not all((clean_cause, clean_impact, clean_next))
    ):
        raise ValueError("재시작에는 원인·수정·재검사 기록을 모두 남겨야 합니다")
    conn.execute(
        f"""INSERT INTO {TABLE_SERVICE_STATE}
        (singleton, status, cause, impact, next_action, updated_at, updated_by)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET status=excluded.status, cause=excluded.cause,
          impact=excluded.impact, next_action=excluded.next_action,
          updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (clean_status, clean_cause, clean_impact, clean_next, now_iso, actor_digest(actor)),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_SERVICE_EVENTS}
        (status, cause, impact, next_action, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (clean_status, clean_cause, clean_impact, clean_next, actor_digest(actor), now_iso),
    )
    return ServiceState(clean_status, clean_cause, clean_impact, clean_next, now_iso)


def list_open_errors(conn: sqlite3.Connection, *, limit: int = 100) -> list[ReportError]:
    rows = conn.execute(
        f"""SELECT e.id, e.report_id, e.actor_email, e.area, e.reason,
                  COALESCE(s.status, e.status) AS status, e.created_at
        FROM {TABLE_ERRORS} AS e
        LEFT JOIN {TABLE_REPORT_STATES} AS s ON s.report_id = e.report_id
        LEFT JOIN {TABLE_REPORT_TRASH} AS t ON t.report_id = e.report_id
        WHERE COALESCE(s.status, e.status) <> ?
          AND (t.status IS NULL OR t.status = ?)
        ORDER BY e.created_at ASC, e.id ASC LIMIT ?""",
        (REPORT_STATUS_NORMAL, TRASH_ACTIVE, max(1, min(int(limit), 500))),
    ).fetchall()
    return [
        ReportError(int(row["id"]), str(row["report_id"]), str(row["actor_email"]),
                    str(row["area"]), str(row["reason"]), str(row["status"]), str(row["created_at"]))
        for row in rows
    ]


def list_report_error_history(
    conn: sqlite3.Connection, *, report_id: str
) -> list[ReportErrorHistory]:
    """해결 뒤에도 신고자·당시 버전·원본을 지우지 않고 모두 읽는다."""
    clean_id = _clean(report_id, maximum=128)
    rows = conn.execute(
        f"""SELECT e.id, e.report_id, e.actor_email, e.area, e.reason,
            e.status AS reported_status, e.created_at,
            COALESCE(s.report_version, 1) AS report_version,
            COALESCE(v.payload_sha256, '') AS report_payload_sha256,
            COALESCE(v.payload_json, '') AS payload_json
        FROM {TABLE_ERRORS} AS e
        LEFT JOIN {TABLE_ERROR_SNAPSHOTS} AS s ON s.error_id = e.id
        LEFT JOIN {TABLE_REPORT_VERSIONS} AS v
          ON v.report_id = s.report_id AND v.version = s.report_version
        WHERE e.report_id = ?
        ORDER BY e.created_at DESC, e.id DESC""",
        (clean_id,),
    ).fetchall()
    return [
        ReportErrorHistory(
            id=int(row["id"]),
            report_id=str(row["report_id"]),
            actor_email=str(row["actor_email"]),
            area=str(row["area"]),
            reason=str(row["reason"]),
            reported_status=str(row["reported_status"]),
            created_at=str(row["created_at"]),
            report_version=int(row["report_version"]),
            report_payload_sha256=str(row["report_payload_sha256"]),
            payload_json=str(row["payload_json"]),
        )
        for row in rows
    ]


def survey_summary(
    conn: sqlite3.Connection, *, start_day: str = ""
) -> tuple[int, int]:
    clean_start = _clean(start_day, maximum=10)
    query = f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN s.rating >= 4 THEN 1 ELSE 0 END), 0) AS helpful
            FROM {TABLE_SURVEY_SNAPSHOTS} AS s
            LEFT JOIN {TABLE_REPORT_TRASH} AS t ON t.report_id = s.report_id
            WHERE (t.status IS NULL OR t.status = ?)"""
    params: list[object] = [TRASH_ACTIVE]
    if clean_start:
        query += " AND substr(s.updated_at, 1, 10) >= ?"
        params.append(clean_start)
    row = conn.execute(query, tuple(params)).fetchone()
    return int(row["total"]), int(row["helpful"])


def list_recent_resolved_issues(
    conn: sqlite3.Connection, *, limit: int = 5
) -> list[ResolvedIssue]:
    """지금도 정상 공개 중인 보고서의 최근 수동 해결 기록을 돌려준다.

    최초 생성 때의 ``normal`` 등록은 해결이 아니므로 제외한다. 해결 뒤 다시 신고된
    보고서도 현재는 미해결이므로 제외해 오늘 화면이 과거 상태를 현재처럼 말하지 않게
    한다.
    """
    rows = conn.execute(
        f"""SELECT e.report_id, r.corp_id, r.payload_json, e.reason, e.created_at, s.version
        FROM {TABLE_REPORT_EVENTS} AS e
        JOIN {TABLE_REPORT_STATES} AS s ON s.report_id = e.report_id
        JOIN {storage_constants.TABLE_REPORTS} AS r ON r.report_id = e.report_id
        LEFT JOIN {TABLE_REPORT_TRASH} AS t ON t.report_id = e.report_id
        WHERE e.action = 'manual_status_change'
          AND e.to_status = ?
          AND s.status = ?
          AND s.blocked = 0
          AND (t.status IS NULL OR t.status = ?)
        ORDER BY e.created_at DESC, e.id DESC LIMIT ?""",
        (
            REPORT_STATUS_NORMAL,
            REPORT_STATUS_NORMAL,
            TRASH_ACTIVE,
            max(1, min(int(limit), 100)),
        ),
    ).fetchall()
    return [
        ResolvedIssue(
            report_id=str(row["report_id"]),
            corp_id=str(row["corp_id"]),
            company=_report_company(
                row["payload_json"], fallback=str(row["corp_id"])
            ),
            reason=str(row["reason"]),
            resolved_at=str(row["created_at"]),
            version=int(row["version"]),
        )
        for row in rows
    ]


def list_member_feedback(
    conn: sqlite3.Connection, *, start_day: str = "", limit: int = 200
) -> list[MemberFeedback]:
    """선택 기간의 MEMBER 설문을 정확한 보고서 버전별로 돌려준다.

    append-only 설문 사건이 저장한 SHA-256과 immutable 버전 표의 SHA-256이 같고,
    원본을 다시 계산한 SHA-256까지 같을 때만 관리자 스냅샷 링크를 연다. 누락·손상
    자료를 현재 보고서 원본으로 대신하지 않는다.
    """

    clean_start = _clean(start_day, maximum=10)
    query = f"""WITH latest_snapshot_events AS (
            SELECT event.*
            FROM {TABLE_SURVEY_SNAPSHOT_EVENTS} AS event
            JOIN (
                SELECT report_id, report_version, actor_email, MAX(id) AS event_id
                FROM {TABLE_SURVEY_SNAPSHOT_EVENTS}
                GROUP BY report_id, report_version, actor_email
            ) AS latest ON latest.event_id = event.id
        )
        SELECT event.report_id, event.report_version, r.corp_id, r.job,
            event.actor_email, event.rating, event.overall_feedback,
            event.business_distinction, event.add_information,
            event.delete_information, event.revision,
            event.created_at, event.created_at AS updated_at,
            s.revision AS projection_revision,
            COALESCE(event.report_payload_sha256, '') AS event_sha256,
            COALESCE(version.payload_sha256, '') AS stored_sha256,
            COALESCE(version.payload_json, '') AS snapshot_payload_json
        FROM latest_snapshot_events AS event
        LEFT JOIN {TABLE_SURVEY_SNAPSHOTS} AS s
          ON event.report_id = s.report_id
         AND event.report_version = s.report_version
         AND event.actor_email = s.actor_email
        JOIN {storage_constants.TABLE_REPORTS} AS r
          ON r.report_id = event.report_id
        LEFT JOIN {TABLE_REPORT_VERSIONS} AS version
          ON version.report_id = event.report_id
         AND version.version = event.report_version
         AND version.payload_sha256 = event.report_payload_sha256
        LEFT JOIN {TABLE_REPORT_TRASH} AS t ON t.report_id = event.report_id
        WHERE (t.status IS NULL OR t.status = ?)"""
    params: list[object] = [TRASH_ACTIVE]
    if clean_start:
        query += " AND substr(event.created_at, 1, 10) >= ?"
        params.append(clean_start)
    query += " ORDER BY event.created_at DESC, event.report_id ASC, event.report_version DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    rows = conn.execute(query, tuple(params)).fetchall()
    feedback: list[MemberFeedback] = []
    for row in rows:
        payload_json = str(row["snapshot_payload_json"])
        event_sha256 = str(row["event_sha256"])
        stored_sha256 = str(row["stored_sha256"])
        event_revision = int(row["revision"])
        projection_revision = (
            None
            if row["projection_revision"] is None
            else int(row["projection_revision"])
        )
        snapshot_available = bool(
            payload_json
            and event_sha256
            and projection_revision is not None
            and event_revision == projection_revision
            and event_sha256 == stored_sha256
            and hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            == stored_sha256
        )
        feedback.append(
            MemberFeedback(
                report_id=str(row["report_id"]),
                corp_id=str(row["corp_id"]),
                company=_report_company(
                    payload_json if snapshot_available else "",
                    fallback=str(row["corp_id"]),
                ),
                job=str(row["job"]),
                actor_email=str(row["actor_email"]),
                rating=int(row["rating"]),
                overall_feedback=str(row["overall_feedback"]),
                business_distinction=str(row["business_distinction"]),
                add_information=str(row["add_information"]),
                delete_information=str(row["delete_information"]),
                revision=event_revision,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                report_version=int(row["report_version"]),
                report_payload_sha256=(
                    stored_sha256 if snapshot_available else ""
                ),
                snapshot_available=snapshot_available,
            )
        )
    return feedback


def member_usage_today(conn: sqlite3.Connection, *, actor_email: str, day: str) -> tuple[int, int]:
    """(확정 성공, 진행 예약) 수를 돌려준다. 반환된 실패는 세지 않는다."""
    actor = _actor(actor_email)
    row = conn.execute(
        f"""SELECT
          COALESCE(SUM(CASE WHEN state = ? THEN 1 ELSE 0 END), 0) AS used,
          COALESCE(SUM(CASE WHEN state = ? THEN 1 ELSE 0 END), 0) AS reserved
        FROM {TABLE_MEMBER_USAGE} WHERE actor_email = ? AND day = ?""",
        (MEMBER_USAGE_USED, MEMBER_USAGE_RESERVED, actor, _clean(day, maximum=20)),
    ).fetchone()
    return int(row["used"]), int(row["reserved"])


def member_can_start(conn: sqlite3.Connection, *, actor_email: str, day: str) -> bool:
    used, reserved = member_usage_today(conn, actor_email=actor_email, day=day)
    return used + reserved < MEMBER_DAILY_SUCCESS_LIMIT


def reserve_member_run(
    conn: sqlite3.Connection, *, run_id: str, actor_email: str, day: str, now_iso: str
) -> bool:
    """성공 3건의 동시 경쟁을 닫는 진행 예약.

    새 보고서가 실제로 끝나기 전에는 ``reserved``라서 실패 시 반환할 수 있다.
    SQLite write transaction에서 현재 성공·예약을 같이 읽어, 탭 세 개가 동시에
    네 번째 성공을 만들지 못하게 한다.
    """
    clean_run = _clean(run_id, maximum=128)
    actor = _actor(actor_email)
    clean_day = _clean(day, maximum=20)
    if not clean_run or not clean_day:
        raise ValueError("MEMBER 사용 예약의 실행 ID와 날짜가 필요합니다")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    existing = conn.execute(
        f"SELECT actor_email, day, state FROM {TABLE_MEMBER_USAGE} WHERE run_id = ?", (clean_run,)
    ).fetchone()
    if existing is not None:
        if str(existing["actor_email"]) != actor or str(existing["day"]) != clean_day:
            raise ValueError("같은 실행 ID의 MEMBER 사용자가 다릅니다")
        return str(existing["state"]) in {MEMBER_USAGE_RESERVED, MEMBER_USAGE_USED}
    if not member_can_start(conn, actor_email=actor, day=clean_day):
        return False
    conn.execute(
        f"INSERT INTO {TABLE_MEMBER_USAGE} (run_id, actor_email, day, state, created_at) VALUES (?, ?, ?, ?, ?)",
        (clean_run, actor, clean_day, MEMBER_USAGE_RESERVED, now_iso),
    )
    conn.execute(
        f"INSERT INTO {TABLE_MEMBER_USAGE_EVENTS} (run_id, actor_email, day, state, created_at) VALUES (?, ?, ?, ?, ?)",
        (clean_run, actor, clean_day, MEMBER_USAGE_RESERVED, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_MEMBER_RUN_SUMMARY_EVENTS}
        (run_id, actor_email, day, state, created_at) VALUES (?, ?, ?, ?, ?)""",
        (clean_run, actor, clean_day, MEMBER_USAGE_RESERVED, now_iso),
    )
    return True


def settle_member_run(
    conn: sqlite3.Connection, *, run_id: str, succeeded: bool, report_id: str, now_iso: str,
    outcome: str = "", company_type: str = COMPANY_UNDECIDED, cost_krw: float = 0.0,
    cost_uncertain: bool = False,
) -> bool:
    """예약을 성공 1건으로 확정하거나 실패·취소로 반환한다. 멱등이다."""
    clean_run = _clean(run_id, maximum=128)
    row = conn.execute(
        f"SELECT actor_email, day, state FROM {TABLE_MEMBER_USAGE} WHERE run_id = ?", (clean_run,)
    ).fetchone()
    if row is None:
        return False
    old_state = str(row["state"])
    target = MEMBER_USAGE_USED if succeeded else MEMBER_USAGE_RETURNED
    if old_state == target:
        return True
    if old_state != MEMBER_USAGE_RESERVED:
        return False
    clean_report = _clean(report_id, maximum=128) if succeeded else ""
    clean_outcome = _clean(outcome, maximum=80)
    clean_company_type = _clean(company_type, maximum=30)
    if clean_company_type not in COMPANY_TYPES:
        clean_company_type = COMPANY_UNDECIDED
    try:
        clean_cost = max(0.0, float(cost_krw))
    except (TypeError, ValueError):
        clean_cost = 0.0
    cost_state = "uncertain" if cost_uncertain else (
        "confirmed" if clean_cost > 0 else "not_incurred"
    )
    conn.execute(
        f"UPDATE {TABLE_MEMBER_USAGE} SET state = ?, report_id = ?, settled_at = ? WHERE run_id = ? AND state = ?",
        (target, clean_report, now_iso, clean_run, MEMBER_USAGE_RESERVED),
    )
    conn.execute(
        f"INSERT INTO {TABLE_MEMBER_USAGE_EVENTS} (run_id, actor_email, day, state, report_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (clean_run, str(row["actor_email"]), str(row["day"]), target, clean_report, now_iso),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_MEMBER_RUN_SUMMARY_EVENTS}
        (run_id, actor_email, day, state, outcome, company_type, report_id, cost_krw, cost_state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (clean_run, str(row["actor_email"]), str(row["day"]), target,
         clean_outcome, clean_company_type, clean_report, clean_cost, cost_state, now_iso),
    )
    return True


def member_run_statistics(conn: sqlite3.Connection, *, start_day: str = "") -> dict[str, object]:
    """MEMBER 실행의 최신 사건만 읽어 기간·유형·비용·예약 차이를 투명하게 집계한다."""
    clean_start = _clean(start_day, maximum=20)
    query = f"""SELECT run_id, state, company_type, report_id, cost_krw, cost_state
        FROM {TABLE_MEMBER_RUN_SUMMARY_EVENTS}"""
    params: tuple[str, ...] = ()
    if clean_start:
        query += " WHERE day >= ?"
        params = (clean_start,)
    query += " ORDER BY id DESC"
    latest: dict[str, sqlite3.Row] = {}
    for row in conn.execute(query, params).fetchall():
        latest.setdefault(str(row["run_id"]), row)
    by_company = {
        company_type: {"success": 0, "failed": 0}
        for company_type in COMPANY_TYPES
    }
    settled = {"used": 0, "returned": 0, "reserved": 0}
    confirmed_cost = 0.0
    uncertain_cost_events = 0
    recorded_runs = 0
    for row in latest.values():
        report_id = str(row["report_id"])
        if report_id and report_is_trashed(conn, report_id):
            continue
        recorded_runs += 1
        state = str(row["state"])
        if state not in settled:
            continue
        settled[state] += 1
        company_type = str(row["company_type"])
        if company_type not in by_company:
            company_type = COMPANY_UNDECIDED
        if state == MEMBER_USAGE_USED:
            by_company[company_type]["success"] += 1
        elif state == MEMBER_USAGE_RETURNED:
            by_company[company_type]["failed"] += 1
        if str(row["cost_state"]) == "confirmed":
            confirmed_cost += float(row["cost_krw"])
        elif str(row["cost_state"]) == "uncertain":
            uncertain_cost_events += 1
    return {
        "by_company": by_company,
        "settled": settled,
        "confirmed_cost_krw": confirmed_cost,
        "uncertain_cost_events": uncertain_cost_events,
        "recorded_runs": recorded_runs,
    }


def link_open_seen_id(conn: sqlite3.Connection, *, key_hash: str) -> int:
    """관리자가 마지막으로 확인한 LINK 요청 event id를 읽는다."""
    clean_hash = _clean(key_hash, maximum=64).lower()
    row = conn.execute(
        f"SELECT last_seen_open_id FROM {TABLE_LINK_OPEN_REVIEWS} WHERE link_key_hash = ?",
        (clean_hash,),
    ).fetchone()
    return 0 if row is None else int(row["last_seen_open_id"])


def mark_link_opens_seen(
    conn: sqlite3.Connection, *, key_hash: str, last_seen_open_id: int,
    actor_email: str, now_iso: str,
) -> int:
    """상세를 연 시점까지의 새 접속 표시를 해제하고 감사 이력을 남긴다."""
    clean_hash = _clean(key_hash, maximum=64).lower()
    actor = _actor(actor_email)
    if len(clean_hash) != 64 or any(char not in "0123456789abcdef" for char in clean_hash):
        raise ValueError("올바른 LINK 식별자가 필요합니다.")
    latest = max(0, int(last_seen_open_id))
    previous = link_open_seen_id(conn, key_hash=clean_hash)
    if latest <= previous:
        return previous
    conn.execute(
        f"""INSERT INTO {TABLE_LINK_OPEN_REVIEWS}
        (link_key_hash, last_seen_open_id, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(link_key_hash) DO UPDATE SET last_seen_open_id=excluded.last_seen_open_id,
            updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (clean_hash, latest, now_iso, actor_digest(actor)),
    )
    conn.execute(
        f"""INSERT INTO {TABLE_LINK_OPEN_REVIEW_EVENTS}
        (link_key_hash, last_seen_open_id, actor, created_at) VALUES (?, ?, ?, ?)""",
        (clean_hash, latest, actor_digest(actor), now_iso),
    )
    return latest
