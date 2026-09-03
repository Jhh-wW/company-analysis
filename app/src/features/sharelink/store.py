"""발급한 열쇠 링크를 저장하고 찾는다.

★ 저장이 필요한 이유 두 가지
  ① **서버를 껐다 켜도 링크가 살아 있어야 한다.** 인사팀은 며칠 뒤에 열어본다.
  ② 링크 요청 횟수와 최초·최근 요청 시각을 남긴다. 메신저 미리보기·봇도 GET을
     보낼 수 있으므로 사람이나 개인을 식별하는 기록이라고 주장하지 않는다.

★ 표를 «새로» 만든다. 기존 `reports` 표는 안 건드린다 —
  이미 들어 있는 자료에 열을 더하면 옛 줄을 어떻게 채울지가 문제가 된다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Optional

from src.core import clock
from src.features.budget import spend_store as budget_spend_store
from src.features.budget import state_machine as budget_state_machine
from src.features.sharelink import constants, logic

#: 발급한 링크를 담는 표.
TABLE_SHARE_LINKS = "share_links"
TABLE_OPEN_EVENTS = "share_link_open_events"
TABLE_OPEN_WINDOWS = "share_link_open_windows"
TABLE_ACCESS_SUBJECTS = "share_link_access_subjects"
TABLE_RUN_HISTORY = "share_link_run_history"
TABLE_REPORT_VIEW_EVENTS = "share_link_report_view_events"
#: 링크의 만료·한도를 «누가 언제 얼마에서 얼마로» 바꿨는지 남기는 표.
#: ★ 관리자 감사 원장(`admin_audit_events`)은 사유 코드가 48자 영숫자라 금액·날짜를
#:   담을 수 없다. 그래서 「무엇이 어떻게 바뀌었나」는 이 표가, 「누가 승인했나」는
#:   감사 원장이 각각 맡는다. 두 곳에 같은 transaction으로 함께 쓴다.
TABLE_BUDGET_ADJUSTMENTS = "share_link_budget_adjustments"
INDEX_RUN_REPORT_ID = f"idx_{TABLE_RUN_HISTORY}_report_id_unique"
INDEX_OPEN_WINDOWS_LINK_WINDOW = f"idx_{TABLE_OPEN_WINDOWS}_link_window_unique"
INDEX_ACCESS_SUBJECT_WINDOW = f"idx_{TABLE_ACCESS_SUBJECTS}_window_subject_unique"
INDEX_REPORT_VIEW_EVENTS_LINK_TIME = (
    f"idx_{TABLE_REPORT_VIEW_EVENTS}_link_time"
)
INDEX_REPORT_VIEW_EVENTS_LINK_REPORT = (
    f"idx_{TABLE_REPORT_VIEW_EVENTS}_link_report_unique"
)
INDEX_BUDGET_ADJUSTMENTS_LINK_TIME = f"idx_{TABLE_BUDGET_ADJUSTMENTS}_link_time"

#: 이력에 남길 변경 종류. 표 하나로 묶는 이유 — 화면이 한 곳이고 종류가 몇 안 된다.
ADJUSTMENT_KIND_EXPIRES: Final[str] = "expires"
ADJUSTMENT_KIND_DAILY_BUDGET: Final[str] = "daily_budget"
ADJUSTMENT_KIND_TOTAL_BUDGET: Final[str] = "total_budget"
ADJUSTMENT_KINDS: Final[frozenset[str]] = frozenset(
    {
        ADJUSTMENT_KIND_EXPIRES,
        ADJUSTMENT_KIND_DAILY_BUDGET,
        ADJUSTMENT_KIND_TOTAL_BUDGET,
    }
)

RUN_STATUS_RUNNING: Final[str] = "running"
RUN_STATUS_AWAITING_RELEASE: Final[str] = "awaiting_release"
RUN_STATUS_COMPLETED: Final[str] = "completed"
RUN_STATUS_STOPPED: Final[str] = "stopped"
RUN_STATUS_INTERRUPTED: Final[str] = "interrupted"
RUN_STATUSES: Final[frozenset[str]] = frozenset(
    {
        RUN_STATUS_RUNNING,
        RUN_STATUS_AWAITING_RELEASE,
        RUN_STATUS_COMPLETED,
        RUN_STATUS_STOPPED,
        RUN_STATUS_INTERRUPTED,
    }
)

#: 표 만들기. `db.py`가 서버 시작 때 부른다.
CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SHARE_LINKS} (
    key_hash        TEXT PRIMARY KEY,
    company         TEXT NOT NULL,
    job             TEXT NOT NULL,
    -- ★ 미리 구워 둔 보고서 번호. 비어 있으면 「아직 안 구웠다」는 뜻이다.
    report_id       TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    -- ★ GET 요청 기록. 메신저 미리보기·보안 봇·재시도도 포함될 수 있다.
    opened_count    INTEGER NOT NULL DEFAULT 0,
    first_opened_at TEXT NOT NULL DEFAULT '',
    last_opened_at  TEXT NOT NULL DEFAULT '',
    -- 삭제하지 않고 권한만 닫아 과거 접속·생성 이력을 보존한다.
    revoked_at      TEXT NOT NULL DEFAULT '',
    -- ★ 이 링크가 «수명 전체»에 쓸 수 있는 AI 비용(원). 비어 있으면(기존 링크
    --   전부) `constants.LINK_TOTAL_BUDGET_KRW` 기본값을 쓴다. 이미 뿌린 링크에
    --   값을 채워 넣지 않으려고 일부러 NULL 허용이다.
    total_budget_krw REAL DEFAULT NULL
        CHECK (total_budget_krw IS NULL OR total_budget_krw >= 0),
    -- ★ 이 링크가 닫히는 날(KST ``YYYY-MM-DD``). 그날 00:00부터 안 열린다.
    --   비어 있으면 «만료 열이 생기기 전에 저장된 행»이라는 뜻이고, 저장소를
    --   준비할 때 옛 규칙(60일)으로 계산해 채워 굳힌다. 기본값을 90일로 올려도
    --   이미 뿌린 링크가 저절로 30일 더 열리지 않게 하려는 것이다 (D-G8).
    expires_at      TEXT NOT NULL DEFAULT '',
    -- ★ 관리자가 붙이는 표시용 이름(예: 「하이브 인사팀」). 내부 메모(`note`)와
    --   달리 관리 화면에 그대로 보인다. 받는 사람 화면에는 쓰지 않는다.
    audience_label  TEXT NOT NULL DEFAULT ''
)
"""
CREATE_BUDGET_ADJUSTMENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_BUDGET_ADJUSTMENTS} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    link_key_hash TEXT NOT NULL,
    kind          TEXT NOT NULL,
    old_value     TEXT NOT NULL,
    new_value     TEXT NOT NULL,
    reason        TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (link_key_hash) REFERENCES {TABLE_SHARE_LINKS}(key_hash),
    CHECK (kind IN ('{ADJUSTMENT_KIND_EXPIRES}',
                    '{ADJUSTMENT_KIND_DAILY_BUDGET}',
                    '{ADJUSTMENT_KIND_TOTAL_BUDGET}')),
    CHECK (length(reason) > 0)
)
"""
CREATE_BUDGET_ADJUSTMENTS_NO_UPDATE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_BUDGET_ADJUSTMENTS}_no_update
BEFORE UPDATE ON {TABLE_BUDGET_ADJUSTMENTS}
BEGIN SELECT RAISE(ABORT, 'share link adjustments are append-only'); END
"""
CREATE_BUDGET_ADJUSTMENTS_NO_DELETE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_BUDGET_ADJUSTMENTS}_no_delete
BEFORE DELETE ON {TABLE_BUDGET_ADJUSTMENTS}
BEGIN SELECT RAISE(ABORT, 'share link adjustments are append-only'); END
"""
CREATE_BUDGET_ADJUSTMENTS_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS {INDEX_BUDGET_ADJUSTMENTS_LINK_TIME}
ON {TABLE_BUDGET_ADJUSTMENTS}(link_key_hash, id DESC)
"""
CREATE_OPEN_EVENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_OPEN_EVENTS} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    link_key_hash TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    FOREIGN KEY (link_key_hash) REFERENCES {TABLE_SHARE_LINKS}(key_hash)
)
"""
CREATE_OPEN_EVENTS_NO_UPDATE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_OPEN_EVENTS}_no_update
BEFORE UPDATE ON {TABLE_OPEN_EVENTS}
BEGIN SELECT RAISE(ABORT, 'share link open events are append-only'); END
"""
CREATE_OPEN_EVENTS_NO_DELETE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_OPEN_EVENTS}_no_delete
BEFORE DELETE ON {TABLE_OPEN_EVENTS}
BEGIN SELECT RAISE(ABORT, 'share link open events are append-only'); END
"""
CREATE_OPEN_EVENTS_NO_INSERT_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_OPEN_EVENTS}_no_insert
BEFORE INSERT ON {TABLE_OPEN_EVENTS}
BEGIN SELECT RAISE(ABORT, 'LINK 접속 구형 이력은 봉인되었습니다'); END
"""
CREATE_OPEN_WINDOWS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_OPEN_WINDOWS} (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    link_key_hash     TEXT NOT NULL,
    window_started_at TEXT NOT NULL,
    opened_count      INTEGER NOT NULL,
    first_opened_at   TEXT NOT NULL,
    last_opened_at    TEXT NOT NULL,
    FOREIGN KEY (link_key_hash) REFERENCES {TABLE_SHARE_LINKS}(key_hash),
    CHECK (opened_count BETWEEN 1 AND {constants.OPEN_WINDOW_MAX_COUNT})
)
"""
CREATE_OPEN_WINDOWS_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_OPEN_WINDOWS_LINK_WINDOW}
ON {TABLE_OPEN_WINDOWS}(link_key_hash, window_started_at)
"""
CREATE_ACCESS_SUBJECTS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_ACCESS_SUBJECTS} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id      INTEGER NOT NULL,
    requester_hash TEXT NOT NULL,
    opened_count   INTEGER NOT NULL,
    FOREIGN KEY (window_id) REFERENCES {TABLE_OPEN_WINDOWS}(id) ON DELETE CASCADE,
    CHECK (length(requester_hash) = 64),
    CHECK (requester_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK (opened_count BETWEEN 1 AND {constants.LEGACY_ACCESS_PER_REQUESTER_LIMIT})
)
"""
CREATE_ACCESS_SUBJECTS_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_ACCESS_SUBJECT_WINDOW}
ON {TABLE_ACCESS_SUBJECTS}(window_id, requester_hash)
"""
CREATE_ACCESS_SUBJECTS_NO_INSERT_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_ACCESS_SUBJECTS}_no_insert
BEFORE INSERT ON {TABLE_ACCESS_SUBJECTS}
BEGIN SELECT RAISE(ABORT, 'LINK 요청자 식별값 수집은 폐기되었습니다'); END
"""
CREATE_ACCESS_SUBJECTS_NO_UPDATE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_ACCESS_SUBJECTS}_no_update
BEFORE UPDATE ON {TABLE_ACCESS_SUBJECTS}
BEGIN SELECT RAISE(ABORT, 'LINK 요청자 식별값 수집은 폐기되었습니다'); END
"""
CREATE_RUN_HISTORY_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_RUN_HISTORY} (
    run_id                   TEXT PRIMARY KEY,
    link_key_hash            TEXT NOT NULL,
    started_at               TEXT NOT NULL,
    input_company            TEXT NOT NULL,
    confirmed_company        TEXT NOT NULL,
    company_id               TEXT NOT NULL,
    status                   TEXT NOT NULL,
    stop_step                TEXT NOT NULL DEFAULT '',
    stop_reason              TEXT NOT NULL DEFAULT '',
    report_id                TEXT NOT NULL DEFAULT '',
    pdf_sha256               TEXT NOT NULL DEFAULT '',
    release_sha256           TEXT NOT NULL DEFAULT '',
    finished_at              TEXT NOT NULL DEFAULT '',
    internal_ai_cost_krw     REAL NOT NULL DEFAULT 0,
    customer_charge_krw      REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (link_key_hash) REFERENCES {TABLE_SHARE_LINKS}(key_hash),
    CHECK (status IN (
        'running', 'awaiting_release', 'completed', 'stopped', 'interrupted'
    )),
    CHECK (internal_ai_cost_krw >= 0),
    CHECK (customer_charge_krw >= 0)
)
"""
CREATE_RUN_HISTORY_NO_DELETE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_RUN_HISTORY}_no_delete
BEFORE DELETE ON {TABLE_RUN_HISTORY}
BEGIN SELECT RAISE(ABORT, 'share link run history is preserved'); END
"""
CREATE_RUN_REPORT_ID_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_RUN_REPORT_ID}
ON {TABLE_RUN_HISTORY}(report_id) WHERE report_id <> ''
"""
CREATE_REPORT_VIEW_EVENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_REPORT_VIEW_EVENTS} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    link_key_hash TEXT NOT NULL,
    report_id     TEXT NOT NULL,
    viewed_at     TEXT NOT NULL,
    FOREIGN KEY (link_key_hash) REFERENCES {TABLE_SHARE_LINKS}(key_hash)
)
"""
CREATE_REPORT_VIEW_EVENTS_NO_UPDATE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_REPORT_VIEW_EVENTS}_no_update
BEFORE UPDATE ON {TABLE_REPORT_VIEW_EVENTS}
BEGIN SELECT RAISE(ABORT, 'share link report view events are append-only'); END
"""
CREATE_REPORT_VIEW_EVENTS_NO_DELETE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {TABLE_REPORT_VIEW_EVENTS}_no_delete
BEFORE DELETE ON {TABLE_REPORT_VIEW_EVENTS}
BEGIN SELECT RAISE(ABORT, 'share link report view events are append-only'); END
"""
CREATE_REPORT_VIEW_EVENTS_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS {INDEX_REPORT_VIEW_EVENTS_LINK_TIME}
ON {TABLE_REPORT_VIEW_EVENTS}(link_key_hash, viewed_at DESC, id DESC)
"""
CREATE_REPORT_VIEW_EVENTS_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_REPORT_VIEW_EVENTS_LINK_REPORT}
ON {TABLE_REPORT_VIEW_EVENTS}(link_key_hash, report_id)
"""
_LEGACY_TABLE = "share_links_legacy_raw_key"
_MIGRATION_SAVEPOINT = "migrate_share_links_key_hash"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ShareLink:
    """발급한 링크 하나."""

    key_hash: str
    company: str
    job: str
    report_id: str
    note: str
    created_at: str
    opened_count: int
    first_opened_at: str
    last_opened_at: str
    revoked_at: str
    #: 이 링크의 «수명 전체» 예산(원). ``None``이면 기본 상한을 쓴다.
    total_budget_krw: Optional[float] = None
    #: 이 링크가 닫히는 날(KST ``YYYY-MM-DD``). 빈 값이면 발급일 + 기본 수명.
    expires_at: str = ""
    #: 관리자 화면에만 보이는 표시용 이름. 받는 사람에게는 안 보낸다.
    audience_label: str = ""

    @property
    def effective_total_budget_krw(self) -> float:
        """이 링크가 수명 전체에 쓸 수 있는 돈(원).

        ★ 비어 있으면 기본 상한(`constants.LINK_TOTAL_BUDGET_KRW`)이다 —
          이미 뿌린 링크에 값을 채워 넣지 않으려고 NULL을 그대로 둔다.
        ★ 저장이 깨져 음수·NaN이 들어오면 **기본값으로 되살리지 않고 0원으로 닫는다.**
          깨진 숫자로 돈을 쓰는 것보다 링크 하나가 멈추는 편이 낫다.
          정상 경로에서는 표의 CHECK가 음수를 애초에 막는다.
        """
        value = self.total_budget_krw
        if value is None:
            return constants.LINK_TOTAL_BUDGET_KRW
        if not math.isfinite(value) or value < 0:
            return 0.0
        return float(value)

    @property
    def is_baked(self) -> bool:
        """미리 구워 둔 보고서가 있는가. 있으면 인사팀이 0원으로 바로 본다."""
        return bool(self.report_id)

    @property
    def key(self) -> str:
        """관리 화면에서 쓰는 비밀이 아닌 영속 식별자."""

        return self.key_hash

    @property
    def is_revoked(self) -> bool:
        """관리자가 권한을 닫았지만 감사 이력은 보존된 링크인가."""

        return bool(self.revoked_at)


@dataclass(frozen=True)
class ShareLinkOpenEvent:
    id: int
    link_key_hash: str
    opened_at: str
    opened_count: int = 1
    window_started_at: str = ""


@dataclass(frozen=True)
class ShareLinkRun:
    run_id: str
    link_key_hash: str
    started_at: str
    input_company: str
    confirmed_company: str
    company_id: str
    status: str
    stop_step: str
    stop_reason: str
    report_id: str
    pdf_sha256: str
    release_sha256: str
    finished_at: str
    internal_ai_cost_krw: float
    customer_charge_krw: float


@dataclass(frozen=True)
class ShareLinkAdjustment:
    """링크의 만료·한도를 바꾼 기록 한 줄. 열쇠 원문은 담지 않는다."""

    id: int
    link_key_hash: str
    kind: str
    old_value: str
    new_value: str
    reason: str
    actor_id: str
    created_at: str


@dataclass(frozen=True)
class ShareLinkReportViewEvent:
    """LINK로 기존 연결 보고서를 연 비식별 append-only 사건."""

    id: int
    link_key_hash: str
    report_id: str
    viewed_at: str


def _row_to_link(row: sqlite3.Row | tuple) -> ShareLink:
    return ShareLink(
        key_hash=row[0],
        company=row[1],
        job=row[2],
        report_id=row[3],
        note=row[4],
        created_at=row[5],
        opened_count=row[6],
        first_opened_at=row[7],
        last_opened_at=row[8],
        revoked_at=row[9],
        total_budget_krw=None if row[10] is None else float(row[10]),
        expires_at=str(row[11] or ""),
        audience_label=str(row[12] or ""),
    )


_COLUMNS = (
    "key_hash, company, job, report_id, note, created_at, "
    "opened_count, first_opened_at, last_opened_at, revoked_at, "
    "total_budget_krw, expires_at, audience_label"
)

_RUN_COLUMNS = (
    "run_id, link_key_hash, started_at, input_company, confirmed_company, "
    "company_id, status, stop_step, stop_reason, report_id, pdf_sha256, "
    "release_sha256, finished_at, internal_ai_cost_krw, customer_charge_krw"
)


def _row_to_run(row: sqlite3.Row | tuple) -> ShareLinkRun:
    return ShareLinkRun(
        run_id=row[0],
        link_key_hash=row[1],
        started_at=row[2],
        input_company=row[3],
        confirmed_company=row[4],
        company_id=row[5],
        status=row[6],
        stop_step=row[7],
        stop_reason=row[8],
        report_id=row[9],
        pdf_sha256=row[10],
        release_sha256=row[11],
        finished_at=row[12],
        internal_ai_cost_krw=float(row[13]),
        customer_charge_krw=float(row[14]),
    )


def key_hash_of(key: str) -> str:
    """URL·쿠키 원문을 DB 조회용 SHA-256 지문으로 바꾼다."""

    normalized = str(key or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_key_hash(value: str) -> bool:
    return bool(_HASH_RE.fullmatch(str(value or "").strip().lower()))


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    escaped = table.replace('"', '""')
    return tuple(
        str(row[1]) for row in conn.execute(f'PRAGMA table_xinfo("{escaped}")')
    )


def _table_state(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()
    if row is None:
        return "missing"
    if str(row[0]) != "table":
        return "unexpected"
    columns = _table_columns(conn, table)
    if columns and columns[0] == "key_hash" and "key" not in columns:
        return "hashed"
    if columns and columns[0] == "key" and "key_hash" not in columns:
        return "raw"
    return "unexpected"


def _verify_run_report_id_index(conn: sqlite3.Connection) -> None:
    """이름만 같은 index를 1:1 report 결속으로 오인하지 않는다."""

    schema_row = conn.execute(
        "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
        (INDEX_RUN_REPORT_ID,),
    ).fetchone()
    if (
        schema_row is None
        or str(schema_row[0]) != "index"
        or str(schema_row[1]) != TABLE_RUN_HISTORY
        or not str(schema_row[2] or "").strip()
    ):
        raise RuntimeError("share_link_run_history report_id 1:1 index가 올바르지 않습니다")

    escaped_table = TABLE_RUN_HISTORY.replace('"', '""')
    index_rows = conn.execute(f'PRAGMA index_list("{escaped_table}")').fetchall()
    index_row = next(
        (row for row in index_rows if str(row[1]) == INDEX_RUN_REPORT_ID),
        None,
    )
    escaped_index = INDEX_RUN_REPORT_ID.replace('"', '""')
    key_columns = tuple(
        str(row[2])
        for row in conn.execute(f'PRAGMA index_xinfo("{escaped_index}")')
        if int(row[5]) == 1
    )
    predicate_match = re.search(
        r"\bWHERE\b(?P<predicate>.+)$",
        str(schema_row[2]),
        flags=re.IGNORECASE | re.DOTALL,
    )
    predicate = (
        re.sub(r"\s+", "", predicate_match.group("predicate")).lower()
        if predicate_match is not None
        else ""
    )
    if (
        index_row is None
        or int(index_row[2]) != 1
        or int(index_row[4]) != 1
        or key_columns != ("report_id",)
        or predicate != "report_id<>''"
    ):
        raise RuntimeError("share_link_run_history report_id 1:1 index가 올바르지 않습니다")


def _verify_unique_index(
    conn: sqlite3.Connection,
    *,
    table: str,
    index: str,
    expected_columns: tuple[str, ...],
) -> None:
    """bounded 집계의 이름·소유 표·전체 UNIQUE 계약을 함께 확인한다."""

    schema_row = conn.execute(
        "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
        (index,),
    ).fetchone()
    escaped_table = table.replace('"', '""')
    index_rows = conn.execute(f'PRAGMA index_list("{escaped_table}")').fetchall()
    index_row = next((row for row in index_rows if str(row[1]) == index), None)
    escaped_index = index.replace('"', '""')
    key_columns = tuple(
        str(row[2])
        for row in conn.execute(f'PRAGMA index_xinfo("{escaped_index}")')
        if int(row[5]) == 1
    )
    if (
        schema_row is None
        or str(schema_row[0]) != "index"
        or str(schema_row[1]) != table
        or not str(schema_row[2] or "").strip()
        or index_row is None
        or int(index_row[2]) != 1
        or int(index_row[4]) != 0
        or key_columns != expected_columns
    ):
        raise RuntimeError(f"{table} unique index가 올바르지 않습니다")


def _normalized_schema_sql(value: object) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).lower()
    return normalized.replace("create trigger if not exists", "create trigger")


def _verify_report_view_event_schema(conn: sqlite3.Connection) -> None:
    """조회 사건 표·외래키·append-only trigger를 정확히 확인한다."""

    escaped_table = TABLE_REPORT_VIEW_EVENTS.replace('"', '""')
    columns = tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]), int(row[6]))
        for row in conn.execute(f'PRAGMA table_xinfo("{escaped_table}")')
    )
    expected_columns = (
        ("id", "INTEGER", 0, None, 1, 0),
        ("link_key_hash", "TEXT", 1, None, 0, 0),
        ("report_id", "TEXT", 1, None, 0, 0),
        ("viewed_at", "TEXT", 1, None, 0, 0),
    )
    foreign_keys = tuple(
        (str(row[2]), str(row[3]), str(row[4]))
        for row in conn.execute(f'PRAGMA foreign_key_list("{escaped_table}")')
    )
    if columns != expected_columns or foreign_keys != (
        (TABLE_SHARE_LINKS, "link_key_hash", "key_hash"),
    ):
        raise RuntimeError("share_link_report_view_events 표 계약이 올바르지 않습니다")

    trigger_contracts = (
        (
            f"{TABLE_REPORT_VIEW_EVENTS}_no_update",
            CREATE_REPORT_VIEW_EVENTS_NO_UPDATE_SQL,
        ),
        (
            f"{TABLE_REPORT_VIEW_EVENTS}_no_delete",
            CREATE_REPORT_VIEW_EVENTS_NO_DELETE_SQL,
        ),
    )
    for trigger_name, expected_sql in trigger_contracts:
        row = conn.execute(
            "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
            (trigger_name,),
        ).fetchone()
        if (
            row is None
            or str(row[0]) != "trigger"
            or str(row[1]) != TABLE_REPORT_VIEW_EVENTS
            or _normalized_schema_sql(row[2])
            != _normalized_schema_sql(expected_sql)
        ):
            raise RuntimeError(
                "share_link_report_view_events append-only trigger가 올바르지 않습니다"
            )


def _access_subject_trigger_contracts() -> tuple[tuple[str, str], ...]:
    return (
        (
            f"{TABLE_ACCESS_SUBJECTS}_no_insert",
            CREATE_ACCESS_SUBJECTS_NO_INSERT_SQL,
        ),
        (
            f"{TABLE_ACCESS_SUBJECTS}_no_update",
            CREATE_ACCESS_SUBJECTS_NO_UPDATE_SQL,
        ),
    )


def _access_subject_tombstone_is_current(conn: sqlite3.Connection) -> bool:
    row = conn.execute(f"SELECT COUNT(*) FROM {TABLE_ACCESS_SUBJECTS}").fetchone()
    if row is None or int(row[0]) != 0:
        return False

    for trigger_name, expected_sql in _access_subject_trigger_contracts():
        trigger = conn.execute(
            "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ?",
            (trigger_name,),
        ).fetchone()
        if (
            trigger is None
            or str(trigger[0]) != "trigger"
            or str(trigger[1]) != TABLE_ACCESS_SUBJECTS
            or _normalized_schema_sql(trigger[2])
            != _normalized_schema_sql(expected_sql)
        ):
            return False
    return True


def _verify_access_subject_tombstone(conn: sqlite3.Connection) -> None:
    """폐기한 요청자 표가 비어 있고 재수집 차단 trigger가 정확한지 확인한다."""

    if not {"id", "window_id", "requester_hash", "opened_count"}.issubset(
        _table_columns(conn, TABLE_ACCESS_SUBJECTS)
    ):
        raise RuntimeError("share_link_access_subjects tombstone 표가 올바르지 않습니다")
    if not _access_subject_tombstone_is_current(conn):
        raise RuntimeError(
            "share_link_access_subjects 개인정보 삭제 또는 재수집 차단을 "
            "완결하지 못했습니다"
        )


def _freeze_legacy_expiry(conn: sqlite3.Connection) -> None:
    """만료일이 아직 안 적힌 옛 행에 «그 행이 원래 닫히던 날»을 적어 굳힌다.

    ★ 왜 필요한가 — 기본 수명이 60일에서 90일로 바뀌었다. 만료일을
      계산으로만 두면, 상수를 바꾼 순간 **이미 뿌려 둔 링크가 30일 더 열린다.**
      아무도 결정한 적 없는 노출 연장이므로, 옛 행은 옛 규칙으로 계산해 표에
      적어 둔다. 새 발급만 90일을 받는다.

    ★ 날짜 계산을 SQL의 ``date()``로 하지 않는다 — SQLite는 시간대가 붙은
      값을 UTC로 바꿔 버려서 KST 자정 근처 발급일이 하루 어긋난다. 대상 행은
      수십 건이라 Python에서 정확히 계산하는 편이 싸고 맞다.

    ★ 멱등이다 — ``WHERE expires_at = ''``이라 이미 굳은 행은 건드리지 않는다.
      읽을 수 없는 발급 시각은 빈 값으로 남긴다. 그런 행은 판정이 «닫힘»이다.
    """

    rows = conn.execute(
        f"SELECT key_hash, created_at FROM {TABLE_SHARE_LINKS} "
        "WHERE expires_at = ''"
    ).fetchall()
    frozen: list[tuple[str, str]] = []
    for row in rows:
        expiry = logic.expiry_date_of(
            str(row[1] or ""),
            max_age_days=constants.LEGACY_LINK_MAX_AGE_DAYS,
        )
        if expiry is None:
            continue
        frozen.append((expiry.isoformat(), str(row[0])))
    if frozen:
        conn.executemany(
            f"UPDATE {TABLE_SHARE_LINKS} SET expires_at = ? "
            "WHERE key_hash = ? AND expires_at = ''",
            frozen,
        )


def _copy_legacy_rows(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        f"""
        SELECT key, company, job, report_id, note, created_at,
               opened_count, first_opened_at, last_opened_at
          FROM {_LEGACY_TABLE}
        """
    ).fetchall()
    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {TABLE_SHARE_LINKS}
            (key_hash, company, job, report_id, note, created_at,
             opened_count, first_opened_at, last_opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (key_hash_of(row[0]), *tuple(row[1:]))
            for row in rows
        ],
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """원문 열쇠를 지문으로 전환하고 보존형 방문·생성 이력을 추가한다."""

    savepoint = _MIGRATION_SAVEPOINT
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        current = _table_state(conn, TABLE_SHARE_LINKS)
        legacy = _table_state(conn, _LEGACY_TABLE)
        if current == "raw" and legacy == "missing":
            conn.execute(
                f"ALTER TABLE {TABLE_SHARE_LINKS} RENAME TO {_LEGACY_TABLE}"
            )
            current, legacy = "missing", "raw"
        if current == "missing" and legacy == "missing":
            conn.execute(CREATE_SQL)
        elif current == "missing" and legacy == "raw":
            conn.execute(CREATE_SQL)
            _copy_legacy_rows(conn)
            conn.execute(f"DROP TABLE {_LEGACY_TABLE}")
        elif current == "hashed" and legacy == "raw":
            _copy_legacy_rows(conn)
            conn.execute(f"DROP TABLE {_LEGACY_TABLE}")
        elif current != "hashed" or legacy != "missing":
            raise RuntimeError(
                "지원할 수 없는 share_links 마이그레이션 상태입니다 "
                f"(current={current}, legacy={legacy})"
            )
        columns = set(_table_columns(conn, TABLE_SHARE_LINKS))
        if "revoked_at" not in columns:
            conn.execute(
                f"ALTER TABLE {TABLE_SHARE_LINKS} "
                "ADD COLUMN revoked_at TEXT NOT NULL DEFAULT ''"
            )
            columns = set(_table_columns(conn, TABLE_SHARE_LINKS))
        if "expires_at" not in columns:
            # ★ NOT NULL DEFAULT ''로 붙인다 — 옛 행은 빈 값으로 들어오고,
            #   바로 아래 `_freeze_legacy_expiry`가 옛 규칙(60일)으로 채운다.
            conn.execute(
                f"ALTER TABLE {TABLE_SHARE_LINKS} "
                "ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''"
            )
            columns = set(_table_columns(conn, TABLE_SHARE_LINKS))
        if "audience_label" not in columns:
            conn.execute(
                f"ALTER TABLE {TABLE_SHARE_LINKS} "
                "ADD COLUMN audience_label TEXT NOT NULL DEFAULT ''"
            )
            columns = set(_table_columns(conn, TABLE_SHARE_LINKS))
        if "total_budget_krw" not in columns:
            # ★ NULL 허용으로 붙인다 — 이미 뿌린 링크에 금액을 «채워 넣지» 않는다.
            #   비어 있으면 읽는 쪽이 기본 상한을 쓴다
            #   (`ShareLink.effective_total_budget_krw`).
            conn.execute(
                f"ALTER TABLE {TABLE_SHARE_LINKS} "
                "ADD COLUMN total_budget_krw REAL DEFAULT NULL "
                "CHECK (total_budget_krw IS NULL OR total_budget_krw >= 0)"
            )
            columns = set(_table_columns(conn, TABLE_SHARE_LINKS))
        required_link_columns = {
            "key_hash",
            "company",
            "job",
            "report_id",
            "note",
            "created_at",
            "opened_count",
            "first_opened_at",
            "last_opened_at",
            "revoked_at",
            "total_budget_krw",
            "expires_at",
            "audience_label",
        }
        if not required_link_columns.issubset(columns):
            raise RuntimeError("share_links 필수 열이 없습니다")
        _freeze_legacy_expiry(conn)

        conn.execute(CREATE_OPEN_EVENTS_SQL)
        conn.execute(CREATE_OPEN_EVENTS_NO_UPDATE_SQL)
        conn.execute(CREATE_OPEN_EVENTS_NO_DELETE_SQL)
        conn.execute(CREATE_OPEN_EVENTS_NO_INSERT_SQL)
        conn.execute(CREATE_OPEN_WINDOWS_SQL)
        conn.execute(CREATE_OPEN_WINDOWS_UNIQUE_INDEX_SQL)
        conn.execute(CREATE_ACCESS_SUBJECTS_SQL)
        conn.execute(CREATE_ACCESS_SUBJECTS_UNIQUE_INDEX_SQL)
        _verify_unique_index(
            conn,
            table=TABLE_OPEN_WINDOWS,
            index=INDEX_OPEN_WINDOWS_LINK_WINDOW,
            expected_columns=("link_key_hash", "window_started_at"),
        )
        _verify_unique_index(
            conn,
            table=TABLE_ACCESS_SUBJECTS,
            index=INDEX_ACCESS_SUBJECT_WINDOW,
            expected_columns=("window_id", "requester_hash"),
        )
        # 개인정보를 수집하지 않는다는 화면 계약에 맞춰 과거의 IP 파생 지문만
        # 정확히 지운다. PRAGMA secure_delete는 savepoint보다 먼저 켰다. 링크의
        # 전체 요청 횟수·시간 구간·생성/조회 감사 이력은 이 DELETE 대상이 아니다.
        if not _access_subject_tombstone_is_current(conn):
            for trigger_name, _expected_sql in _access_subject_trigger_contracts():
                escaped_trigger = trigger_name.replace('"', '""')
                conn.execute(f'DROP TRIGGER IF EXISTS "{escaped_trigger}"')
            conn.execute(f"DELETE FROM {TABLE_ACCESS_SUBJECTS}")
            conn.execute(CREATE_ACCESS_SUBJECTS_NO_INSERT_SQL)
            conn.execute(CREATE_ACCESS_SUBJECTS_NO_UPDATE_SQL)
        _verify_access_subject_tombstone(conn)
        conn.execute(CREATE_RUN_HISTORY_SQL)
        conn.execute(CREATE_RUN_HISTORY_NO_DELETE_SQL)
        conn.execute(CREATE_BUDGET_ADJUSTMENTS_SQL)
        conn.execute(CREATE_BUDGET_ADJUSTMENTS_NO_UPDATE_SQL)
        conn.execute(CREATE_BUDGET_ADJUSTMENTS_NO_DELETE_SQL)
        conn.execute(CREATE_BUDGET_ADJUSTMENTS_INDEX_SQL)
        conn.execute(CREATE_REPORT_VIEW_EVENTS_SQL)
        conn.execute(CREATE_REPORT_VIEW_EVENTS_NO_UPDATE_SQL)
        conn.execute(CREATE_REPORT_VIEW_EVENTS_NO_DELETE_SQL)
        conn.execute(CREATE_REPORT_VIEW_EVENTS_INDEX_SQL)
        conn.execute(CREATE_REPORT_VIEW_EVENTS_UNIQUE_INDEX_SQL)
        _verify_report_view_event_schema(conn)
        _verify_unique_index(
            conn,
            table=TABLE_REPORT_VIEW_EVENTS,
            index=INDEX_REPORT_VIEW_EVENTS_LINK_REPORT,
            expected_columns=("link_key_hash", "report_id"),
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE_OPEN_EVENTS}_link_time "
            f"ON {TABLE_OPEN_EVENTS}(link_key_hash, opened_at, id)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE_RUN_HISTORY}_link_time "
            f"ON {TABLE_RUN_HISTORY}(link_key_hash, started_at, run_id)"
        )
        try:
            conn.execute(CREATE_RUN_REPORT_ID_UNIQUE_INDEX_SQL)
        except sqlite3.IntegrityError as exc:
            # 어느 실행이 진짜 소유자인지 임의 선택하거나 과거 행을 지우지 않는다.
            raise RuntimeError(
                "share_link_run_history에 중복 report_id가 있어 안전한 "
                "1:1 결속 마이그레이션을 중단했습니다"
            ) from exc
        _verify_run_report_id_index(conn)
        if (
            _table_state(conn, TABLE_SHARE_LINKS) != "hashed"
            or _table_state(conn, _LEGACY_TABLE) != "missing"
        ):
            raise RuntimeError("share_links 해시 스키마 전환을 완결하지 못했습니다")
        if not {"id", "link_key_hash", "opened_at"}.issubset(
            _table_columns(conn, TABLE_OPEN_EVENTS)
        ):
            raise RuntimeError("share_link_open_events 스키마가 올바르지 않습니다")
        if not {
            "id",
            "link_key_hash",
            "window_started_at",
            "opened_count",
            "first_opened_at",
            "last_opened_at",
        }.issubset(_table_columns(conn, TABLE_OPEN_WINDOWS)):
            raise RuntimeError("share_link_open_windows 스키마가 올바르지 않습니다")
        _verify_access_subject_tombstone(conn)
        if not {
            "run_id",
            "link_key_hash",
            "started_at",
            "input_company",
            "confirmed_company",
            "company_id",
            "status",
            "report_id",
            "release_sha256",
        }.issubset(_table_columns(conn, TABLE_RUN_HISTORY)):
            raise RuntimeError("share_link_run_history 스키마가 올바르지 않습니다")
        if not {"id", "link_key_hash", "report_id", "viewed_at"}.issubset(
            _table_columns(conn, TABLE_REPORT_VIEW_EVENTS)
        ):
            raise RuntimeError("share_link_report_view_events 스키마가 올바르지 않습니다")
        if not {
            "id",
            "link_key_hash",
            "kind",
            "old_value",
            "new_value",
            "reason",
            "actor_id",
            "created_at",
        }.issubset(_table_columns(conn, TABLE_BUDGET_ADJUSTMENTS)):
            raise RuntimeError(
                "share_link_budget_adjustments 스키마가 올바르지 않습니다"
            )
    except BaseException:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        finally:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def insert_new(
    conn: sqlite3.Connection,
    *,
    key: str,
    company: str,
    job: str,
    report_id: str = "",
    note: str = "",
    audience_label: str = "",
    now_iso: str,
) -> bool:
    """새 열쇠만 삽입한다. 충돌한 기존 링크는 절대 덮어쓰지 않는다.

    Args:
        audience_label: 관리 화면에만 보이는 표시용 이름(예: 「하이브 인사팀」).

    Returns:
        삽입했으면 ``True``, 같은 열쇠가 이미 있으면 ``False``.

    ★ 만료일을 **발급 순간에 적어 굳힌다**. 계산으로만 두면 나중에 기본
      수명 상수를 바꿀 때 이미 뿌린 링크의 만료가 조용히 따라 움직인다.
    """
    expiry = logic.expiry_date_of(now_iso)
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_SHARE_LINKS}
            (key_hash, company, job, report_id, note, created_at,
             expires_at, audience_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key_hash) DO NOTHING
        """,
        (
            key_hash_of(key),
            company,
            job,
            report_id,
            note,
            now_iso,
            "" if expiry is None else expiry.isoformat(),
            audience_label,
        ),
    )
    return cursor.rowcount > 0


def set_expires_at(
    conn: sqlite3.Connection, *, key_hash: str, expires_at: str
) -> bool:
    """링크의 만료일을 새 날짜로 바꾼다. 판정은 부르는 쪽이 이미 마쳤다.

    Args:
        expires_at: KST ``YYYY-MM-DD``. 모양이 아니면 저장하지 않는다.

    Returns:
        바꿨으면 ``True``. 링크가 없거나 모양이 아니면 ``False``.
    """

    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return False
    if logic.expiry_date_from_value(expires_at) is None:
        return False
    cursor = conn.execute(
        f"UPDATE {TABLE_SHARE_LINKS} SET expires_at = ? WHERE key_hash = ?",
        (str(expires_at).strip(), normalized),
    )
    return cursor.rowcount > 0


def record_link_adjustment(
    conn: sqlite3.Connection,
    *,
    key_hash: str,
    kind: str,
    old_value: str,
    new_value: str,
    reason: str,
    actor_id: str,
    created_at: str,
) -> None:
    """만료·한도 변경 한 건을 append-only 이력에 남긴다.

    ★ 열쇠 «원문»은 절대 넣지 않는다 — 지문(`key_hash`)만 받는다. 원문을 담으면
      이 표 하나가 링크 유출 경로가 된다.
    ★ 표의 CHECK가 모르는 종류·빈 사유를 거절한다. 잘못된 행을 조용히 남기느니
      변경 transaction 전체를 실패시키는 편이 낫다.
    """

    conn.execute(
        f"""
        INSERT INTO {TABLE_BUDGET_ADJUSTMENTS}
            (link_key_hash, kind, old_value, new_value, reason,
             actor_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(key_hash or "").strip().lower(),
            str(kind or ""),
            str(old_value or ""),
            str(new_value or ""),
            str(reason or ""),
            str(actor_id or ""),
            str(created_at or ""),
        ),
    )


def list_link_adjustments(
    conn: sqlite3.Connection, *, key_hash: str
) -> list[ShareLinkAdjustment]:
    """이 링크의 만료·한도 변경 이력. 최근 것부터."""

    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return []
    rows = conn.execute(
        f"""
        SELECT id, link_key_hash, kind, old_value, new_value, reason,
               actor_id, created_at
          FROM {TABLE_BUDGET_ADJUSTMENTS}
         WHERE link_key_hash = ?
         ORDER BY id DESC
        """,
        (normalized,),
    ).fetchall()
    return [
        ShareLinkAdjustment(
            id=int(row[0]),
            link_key_hash=str(row[1]),
            kind=str(row[2]),
            old_value=str(row[3]),
            new_value=str(row[4]),
            reason=str(row[5]),
            actor_id=str(row[6]),
            created_at=str(row[7]),
        )
        for row in rows
    ]


def load(conn: sqlite3.Connection, key: str) -> Optional[ShareLink]:
    """열쇠로 링크를 찾는다. 없으면 None."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE_SHARE_LINKS} WHERE key_hash = ?",
        (key_hash_of(key),),
    ).fetchone()
    return _row_to_link(row) if row else None


def load_by_hash(conn: sqlite3.Connection, key_hash: str) -> Optional[ShareLink]:
    """관리 화면의 비밀 아닌 식별자로 링크를 찾는다."""

    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE_SHARE_LINKS} WHERE key_hash = ?",
        (normalized,),
    ).fetchone()
    return _row_to_link(row) if row else None


def _open_window_start(now_iso: str) -> str:
    parsed = dt.datetime.fromisoformat(str(now_iso or "").strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=clock.KST)
    epoch = int(parsed.timestamp())
    start_epoch = epoch - (epoch % constants.ACCESS_WINDOW_SECONDS)
    return dt.datetime.fromtimestamp(start_epoch, dt.timezone.utc).isoformat(
        timespec="seconds"
    )


def _rollback_open_savepoint(conn: sqlite3.Connection, savepoint: str) -> None:
    try:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
    finally:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def mark_opened(
    conn: sqlite3.Connection,
    key: str,
    now_iso: str,
) -> bool:
    """active 링크 GET을 원자적 bounded 구간 집계에 남긴다.

    폐기·만료 상태를 writer lock 안에서 다시 확인하고, 같은 분 구간은 한 행만
    UPSERT한다. 사람을 식별하지 않고 capability 전체를 영속 cap으로 제한하며 최근
    구간만 보존한다. ``False``는 없음·비활성·cap 소진 중 하나다.
    """

    try:
        today = clock.business_date_from_iso(now_iso)
        window_started_at = _open_window_start(now_iso)
    except (OverflowError, TypeError, ValueError):
        return False

    key_hash = key_hash_of(key)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    savepoint = "share_link_mark_open"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        state = conn.execute(
            f"SELECT created_at, revoked_at, expires_at FROM {TABLE_SHARE_LINKS} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if (
            state is None
            or str(state[1] or "")
            # ★ 저장된 만료일을 «여기서» 본다 — 화면 쪽 검사만 믿으면 만료일을
            #   미룬(또는 옛 규칙으로 굳은) 링크가 문 앞에서 어긋난다.
            or logic.is_share_link_expired(
                str(state[0]), today=today, expires_at=str(state[2] or "")
            )
        ):
            _rollback_open_savepoint(conn, savepoint)
            return False

        recorded = conn.execute(
            f"""
            INSERT INTO {TABLE_OPEN_WINDOWS}
                (link_key_hash, window_started_at, opened_count,
                 first_opened_at, last_opened_at)
            SELECT key_hash, ?, 1, ?, ?
              FROM {TABLE_SHARE_LINKS}
             WHERE key_hash = ? AND revoked_at = ''
            ON CONFLICT(link_key_hash, window_started_at) DO UPDATE SET
                opened_count = opened_count + 1,
                last_opened_at = excluded.last_opened_at
            WHERE opened_count < ?
            RETURNING id, opened_count
            """,
            (
                window_started_at,
                now_iso,
                now_iso,
                key_hash,
                constants.OPEN_WINDOW_MAX_COUNT,
            ),
        ).fetchone()
        if recorded is None:
            _rollback_open_savepoint(conn, savepoint)
            return False

        cursor = conn.execute(
            f"""
            UPDATE {TABLE_SHARE_LINKS}
               SET opened_count    = opened_count + 1,
                   first_opened_at = CASE WHEN first_opened_at = ''
                                          THEN ? ELSE first_opened_at END,
                   last_opened_at  = ?
             WHERE key_hash = ? AND revoked_at = ''
            """,
            (now_iso, now_iso, key_hash),
        )
        if cursor.rowcount <= 0:
            raise RuntimeError("링크 요청 요약을 갱신하지 못했습니다")

        if int(recorded[1]) == 1:
            conn.execute(
                f"""
                DELETE FROM {TABLE_OPEN_WINDOWS}
                 WHERE link_key_hash = ?
                   AND id NOT IN (
                       SELECT id FROM {TABLE_OPEN_WINDOWS}
                        WHERE link_key_hash = ?
                        ORDER BY window_started_at DESC, id DESC
                        LIMIT ?
                   )
                """,
                (
                    key_hash,
                    key_hash,
                    constants.OPEN_WINDOW_ROWS_PER_LINK,
                ),
            )
    except BaseException:
        _rollback_open_savepoint(conn, savepoint)
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        return True


def list_open_events_by_hash(
    conn: sqlite3.Connection, key_hash: str
) -> list[ShareLinkOpenEvent]:
    """구형 개별 시각과 최근 bounded 집계 구간을 비식별 projection으로 돌려준다."""

    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return []
    legacy_rows = conn.execute(
        f"""
        SELECT id, link_key_hash, opened_at
          FROM {TABLE_OPEN_EVENTS}
         WHERE link_key_hash = ?
         ORDER BY id DESC
         LIMIT ?
        """,
        (normalized, constants.LEGACY_OPEN_EVENTS_DISPLAY_LIMIT),
    ).fetchall()
    legacy_events = [
        ShareLinkOpenEvent(
            id=int(row[0]), link_key_hash=row[1], opened_at=row[2]
        )
        for row in reversed(legacy_rows)
    ]
    legacy_max_row = conn.execute(
        f"SELECT COALESCE(MAX(id), 0) FROM {TABLE_OPEN_EVENTS}"
    ).fetchone()
    legacy_max_id = int(legacy_max_row[0])
    window_rows = conn.execute(
        f"""
        SELECT id, link_key_hash, window_started_at, opened_count, last_opened_at
          FROM {TABLE_OPEN_WINDOWS}
         WHERE link_key_hash = ?
         ORDER BY id ASC
        """,
        (normalized,),
    ).fetchall()
    window_events = [
        ShareLinkOpenEvent(
            id=(
                legacy_max_id
                + int(row[0]) * (constants.OPEN_WINDOW_MAX_COUNT + 1)
                + int(row[3])
            ),
            link_key_hash=str(row[1]),
            opened_at=str(row[4]),
            opened_count=int(row[3]),
            window_started_at=str(row[2]),
        )
        for row in window_rows
    ]
    return [*legacy_events, *window_events]


def record_report_view(
    conn: sqlite3.Connection,
    *,
    key: str,
    report_id: str,
    viewed_at: str,
) -> bool:
    """raw capability를 해시한 뒤 최초 정상 조회를 멱등 기록한다."""

    return record_report_view_by_hash(
        conn,
        key_hash=key_hash_of(key),
        report_id=report_id,
        viewed_at=viewed_at,
    )


def record_report_view_by_hash(
    conn: sqlite3.Connection,
    *,
    key_hash: str,
    report_id: str,
    viewed_at: str,
) -> bool:
    """active LINK의 최초 연결 보고서 첫 200 조회만 남긴다.

    같은 LINK로 새로 생성한 보고서는 run history가 정본이므로 이 표에
    중복 기록하지 않는다. 새로고침은 기존 사건의 최초 시각을 바꾸지
    않고 성공으로 본다. 원문 capability·IP·UA는 저장하지 않는다.
    """

    normalized_hash = str(key_hash or "").strip().lower()
    clean_report_id = str(report_id or "").strip()[:128]
    clean_viewed_at = str(viewed_at or "").strip()
    if not is_key_hash(normalized_hash):
        return False
    if not clean_report_id or not clean_viewed_at:
        raise ValueError("LINK 보고서 조회에는 보고서 ID와 시각이 필요합니다")
    try:
        dt.datetime.fromisoformat(clean_viewed_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("LINK 보고서 조회 시각 형식이 올바르지 않습니다") from exc
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_REPORT_VIEW_EVENTS}
            (link_key_hash, report_id, viewed_at)
        SELECT link.key_hash, ?, ?
          FROM {TABLE_SHARE_LINKS} AS link
         WHERE link.key_hash = ?
           AND link.revoked_at = ''
           AND link.report_id = ?
        ON CONFLICT(link_key_hash, report_id) DO NOTHING
        """,
        (
            clean_report_id,
            clean_viewed_at,
            normalized_hash,
            clean_report_id,
        ),
    )
    if cursor.rowcount > 0:
        return True
    authorized = conn.execute(
        f"""
        SELECT 1
          FROM {TABLE_SHARE_LINKS} AS link
          JOIN {TABLE_REPORT_VIEW_EVENTS} AS event
            ON event.link_key_hash = link.key_hash
           AND event.report_id = ?
         WHERE link.key_hash = ?
           AND link.revoked_at = ''
           AND link.report_id = ?
         LIMIT 1
        """,
        (
            clean_report_id,
            normalized_hash,
            clean_report_id,
        ),
    ).fetchone()
    return authorized is not None


def list_report_view_events_by_hash(
    conn: sqlite3.Connection, key_hash: str
) -> list[ShareLinkReportViewEvent]:
    """관리 화면용 최근 기존 보고서 조회 사건을 새 시각부터 돌려준다."""

    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return []
    rows = conn.execute(
        f"""
        SELECT id, link_key_hash, report_id, viewed_at
          FROM {TABLE_REPORT_VIEW_EVENTS}
         WHERE link_key_hash = ?
         ORDER BY viewed_at DESC, id DESC
         LIMIT ?
        """,
        (normalized, constants.REPORT_VIEW_EVENTS_DISPLAY_LIMIT),
    ).fetchall()
    return [
        ShareLinkReportViewEvent(
            id=int(row[0]),
            link_key_hash=str(row[1]),
            report_id=str(row[2]),
            viewed_at=str(row[3]),
        )
        for row in rows
    ]


def set_report(conn: sqlite3.Connection, key: str, report_id: str) -> bool:
    """기존 LINK에 시작 보고서를 연결하거나 빈 값으로 해제한다."""
    cursor = conn.execute(
        f"UPDATE {TABLE_SHARE_LINKS} SET report_id = ? WHERE key_hash = ?",
        (report_id, key_hash_of(key)),
    )
    return cursor.rowcount > 0


def set_report_by_hash(
    conn: sqlite3.Connection, key_hash: str, report_id: str
) -> bool:
    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return False
    cursor = conn.execute(
        f"UPDATE {TABLE_SHARE_LINKS} SET report_id = ? WHERE key_hash = ?",
        (report_id, normalized),
    )
    return cursor.rowcount > 0


def list_all(conn: sqlite3.Connection) -> list[ShareLink]:
    """발급한 링크 전부. 최근에 만든 것부터.

    ★ 관리 화면에서 «링크를 몇 개 뿌렸는지»를 보기 위한 것이다.
      전체 상한을 두지 않기로 했으므로(제품 결정), **링크 수가 곧 최악의 지출**이다.
      그 숫자를 볼 수 없으면 얼마가 나갈지 모르는 채로 두는 셈이 된다.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE_SHARE_LINKS} ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_link(row) for row in rows]


def delete(
    conn: sqlite3.Connection, key: str, *, revoked_at: str | None = None
) -> bool:
    """링크 권한만 닫고 행·접속·생성 이력은 보존한다.

    Returns:
        살아 있던 링크를 새로 철회했으면 ``True``.

    ★ 뿌린 링크를 «되돌릴 방법»이 있어야 한다 — 잘못 보냈거나
      지원이 끝났으면 닫을 수 있어야 한다. 없으면 두 달을 기다려야 한다.
    """
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SHARE_LINKS}
           SET revoked_at = ?
         WHERE key_hash = ? AND revoked_at = ''
        """,
        (revoked_at or clock.iso_now_kst(), key_hash_of(key)),
    )
    return cursor.rowcount > 0


def delete_by_hash(
    conn: sqlite3.Connection,
    key_hash: str,
    *,
    revoked_at: str | None = None,
) -> bool:
    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return False
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SHARE_LINKS}
           SET revoked_at = ?
         WHERE key_hash = ? AND revoked_at = ''
        """,
        (revoked_at or clock.iso_now_kst(), normalized),
    )
    return cursor.rowcount > 0


def start_run(
    conn: sqlite3.Connection,
    *,
    key: str,
    run_id: str,
    started_at: str,
    input_company: str,
    confirmed_company: str,
    company_id: str,
) -> bool:
    """LINK 권한으로 시작한 생성 한 건을 안전한 링크 지문에 결속한다."""

    clean_run_id = str(run_id or "").strip()
    clean_started_at = str(started_at or "").strip()
    clean_input_company = str(input_company or "").strip()
    clean_confirmed_company = str(confirmed_company or "").strip()
    clean_company_id = str(company_id or "").strip()
    if not all(
        (
            clean_run_id,
            clean_started_at,
            clean_input_company,
            clean_confirmed_company,
            clean_company_id,
        )
    ):
        raise ValueError("run ID·시작 시각·입력/확정 회사·회사 식별값이 필요합니다")
    # ★ 만료를 «돈이 나가기 직전»에 한 번 더 본다. 화면·요청 경로의 검사가
    #   저장된 만료일을 못 읽어도, 닫힌 링크로 유료 실행이 시작되지 않게 한다.
    try:
        started_day = clock.business_date_from_iso(clean_started_at).isoformat()
    except (OverflowError, TypeError, ValueError):
        return False
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_RUN_HISTORY} (
            run_id, link_key_hash, started_at, input_company,
            confirmed_company, company_id, status
        )
        SELECT ?, key_hash, ?, ?, ?, ?, ?
          FROM {TABLE_SHARE_LINKS}
         WHERE key_hash = ? AND revoked_at = ''
           AND (expires_at = '' OR expires_at > ?)
        ON CONFLICT(run_id) DO NOTHING
        """,
        (
            clean_run_id,
            clean_started_at,
            clean_input_company,
            clean_confirmed_company,
            clean_company_id,
            RUN_STATUS_RUNNING,
            key_hash_of(key),
            started_day,
        ),
    )
    return cursor.rowcount > 0


def finish_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    finished_at: str,
    stop_step: str = "",
    stop_reason: str = "",
    report_id: str = "",
    internal_ai_cost_krw: float = 0.0,
    customer_charge_krw: float = 0.0,
) -> bool:
    """생성 한 건의 현재 종결 상태를 갱신한다.

    자동출고 전 보고서는 ``awaiting_release``로 남고, 같은 report ID의 해시 결속
    출고가 성공한 뒤에만 ``completed``가 된다.
    """

    clean_status = str(status or "").strip()
    if clean_status not in {
        RUN_STATUS_AWAITING_RELEASE,
        RUN_STATUS_STOPPED,
        RUN_STATUS_INTERRUPTED,
    }:
        raise ValueError("올바른 LINK 생성 종료 상태가 필요합니다")
    clean_finished_at = str(finished_at or "").strip()
    clean_report_id = str(report_id or "").strip()
    clean_stop_step = str(stop_step or "").strip()
    clean_stop_reason = str(stop_reason or "").strip()
    if not clean_finished_at:
        raise ValueError("LINK 생성 완료 시각이 필요합니다")
    if clean_status == RUN_STATUS_AWAITING_RELEASE and not clean_report_id:
        raise ValueError("자동출고 대기에는 저장된 report ID가 필요합니다")
    if clean_status in {RUN_STATUS_STOPPED, RUN_STATUS_INTERRUPTED} and not (
        clean_stop_step and clean_stop_reason
    ):
        raise ValueError("중단 상태에는 단계와 이유가 필요합니다")
    internal_cost = float(internal_ai_cost_krw)
    customer_charge = float(customer_charge_krw)
    if internal_cost < 0 or customer_charge < 0:
        raise ValueError("LINK 생성 비용은 음수일 수 없습니다")
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_RUN_HISTORY}
           SET status = ?,
               stop_step = ?,
               stop_reason = ?,
               report_id = CASE WHEN ? <> '' THEN ? ELSE report_id END,
               finished_at = ?,
               internal_ai_cost_krw = ?,
               customer_charge_krw = ?
         WHERE run_id = ? AND status <> ?
        """,
        (
            clean_status,
            clean_stop_step,
            clean_stop_reason,
            clean_report_id,
            clean_report_id,
            clean_finished_at,
            internal_cost,
            customer_charge,
            str(run_id or "").strip(),
            RUN_STATUS_COMPLETED,
        ),
    )
    return cursor.rowcount > 0


def mark_released(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
    release_sha256: str,
    released_at: str,
    customer_charge_krw: float = 0.0,
) -> bool:
    """해시 결속 자동출고가 끝난 보고서만 LINK 생성 완료로 승격한다."""

    charge = float(customer_charge_krw)
    clean_report_id = str(report_id or "").strip()
    clean_pdf_sha256 = str(pdf_sha256 or "").strip().lower()
    clean_release_sha256 = str(release_sha256 or "").strip().lower()
    clean_released_at = str(released_at or "").strip()
    if charge < 0:
        raise ValueError("고객 청구액은 음수일 수 없습니다")
    if not (
        clean_report_id
        and is_key_hash(clean_pdf_sha256)
        and is_key_hash(clean_release_sha256)
        and clean_released_at
    ):
        raise ValueError("report ID·PDF/release 지문·출고 시각이 필요합니다")
    bound = load_run_by_report_id(conn, clean_report_id)
    if bound is None:
        return False
    if bound.status == RUN_STATUS_COMPLETED:
        return bool(
            bound.pdf_sha256 == clean_pdf_sha256
            and bound.release_sha256 == clean_release_sha256
            and bound.customer_charge_krw == charge
        )
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_RUN_HISTORY}
           SET status = ?,
               stop_step = '',
               stop_reason = '',
               report_id = ?,
               pdf_sha256 = ?,
               release_sha256 = ?,
               finished_at = CASE WHEN finished_at = '' THEN ? ELSE finished_at END,
               customer_charge_krw = ?
         WHERE report_id = ?
           AND status IN (?, ?, ?)
        """,
        (
            RUN_STATUS_COMPLETED,
            clean_report_id,
            clean_pdf_sha256,
            clean_release_sha256,
            clean_released_at,
            charge,
            clean_report_id,
            RUN_STATUS_RUNNING,
            RUN_STATUS_AWAITING_RELEASE,
            RUN_STATUS_STOPPED,
        ),
    )
    return cursor.rowcount > 0


def mark_release_stopped(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    stopped_at: str,
    stop_step: str,
    stop_reason: str,
) -> bool:
    """자동출고 검사 실패를 부분 완료가 아닌 중단으로 기록한다."""

    clean_report_id = str(report_id or "").strip()
    clean_stopped_at = str(stopped_at or "").strip()
    clean_stop_step = str(stop_step or "").strip()
    clean_stop_reason = str(stop_reason or "").strip()
    if not all(
        (clean_report_id, clean_stopped_at, clean_stop_step, clean_stop_reason)
    ):
        raise ValueError("자동출고 중단의 report ID·시각·단계·이유가 필요합니다")
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_RUN_HISTORY}
           SET status = ?, stop_step = ?, stop_reason = ?,
               finished_at = CASE WHEN finished_at = '' THEN ? ELSE finished_at END
         WHERE report_id = ?
           AND status <> ?
        """,
        (
            RUN_STATUS_STOPPED,
            clean_stop_step,
            clean_stop_reason,
            clean_stopped_at,
            clean_report_id,
            RUN_STATUS_COMPLETED,
        ),
    )
    return cursor.rowcount > 0


def load_run(conn: sqlite3.Connection, run_id: str) -> Optional[ShareLinkRun]:
    row = conn.execute(
        f"SELECT {_RUN_COLUMNS} FROM {TABLE_RUN_HISTORY} WHERE run_id = ?",
        (str(run_id or "").strip(),),
    ).fetchone()
    return _row_to_run(row) if row else None


def load_run_by_report_id(
    conn: sqlite3.Connection, report_id: str
) -> Optional[ShareLinkRun]:
    """public run ID와 별개인 정확한 report 결속으로 LINK 실행을 찾는다."""

    clean_report_id = str(report_id or "").strip()
    if not clean_report_id:
        return None
    row = conn.execute(
        f"SELECT {_RUN_COLUMNS} FROM {TABLE_RUN_HISTORY} WHERE report_id = ?",
        (clean_report_id,),
    ).fetchone()
    return _row_to_run(row) if row else None


def list_runs_by_hash(
    conn: sqlite3.Connection, key_hash: str
) -> list[ShareLinkRun]:
    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return []
    rows = conn.execute(
        f"""
        SELECT {_RUN_COLUMNS}
          FROM {TABLE_RUN_HISTORY}
         WHERE link_key_hash = ?
         ORDER BY started_at DESC, run_id DESC
        """,
        (normalized,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def list_running_runs(conn: sqlite3.Connection) -> list[ShareLinkRun]:
    """이 프로세스가 재시작 뒤 이어갈 수 없는 LINK 실행을 조회한다."""

    rows = conn.execute(
        f"""
        SELECT {_RUN_COLUMNS}
          FROM {TABLE_RUN_HISTORY}
         WHERE status = ?
         ORDER BY started_at ASC, run_id ASC
        """,
        (RUN_STATUS_RUNNING,),
    ).fetchall()
    return [_row_to_run(row) for row in rows]


def interrupt_running_runs(
    conn: sqlite3.Connection,
    *,
    interrupted_at: str,
    stop_step: str,
    stop_reason: str,
    known_internal_cost_krw_by_run: Mapping[str, float] | None = None,
) -> int:
    """hard restart 뒤 남은 ``running`` 행을 보수적으로 원자 마감한다.

    비용 원장에서 이미 확정된 단계 합계만 받아 기존 저장값과 큰 쪽을 보존한다.
    진행 중 provider의 미확정 금액은 여기서 추정하지 않는다.
    """

    clean_interrupted_at = str(interrupted_at or "").strip()
    clean_stop_step = str(stop_step or "").strip()
    clean_stop_reason = str(stop_reason or "").strip()
    if not all((clean_interrupted_at, clean_stop_step, clean_stop_reason)):
        raise ValueError("재시작 중단에는 시각·단계·이유가 필요합니다")

    costs = known_internal_cost_krw_by_run or {}
    running = list_running_runs(conn)
    updated = 0
    for run in running:
        known_cost = float(costs.get(run.run_id, 0.0))
        if not math.isfinite(known_cost) or known_cost < 0:
            raise ValueError("LINK 재시작 복구 비용은 유한한 음이 아닌 값이어야 합니다")
        preserved_cost = max(run.internal_ai_cost_krw, known_cost)
        cursor = conn.execute(
            f"""
            UPDATE {TABLE_RUN_HISTORY}
               SET status = ?,
                   stop_step = ?,
                   stop_reason = ?,
                   finished_at = ?,
                   internal_ai_cost_krw = ?
             WHERE run_id = ? AND status = ?
            """,
            (
                RUN_STATUS_INTERRUPTED,
                clean_stop_step,
                clean_stop_reason,
                clean_interrupted_at,
                preserved_cost,
                run.run_id,
                RUN_STATUS_RUNNING,
            ),
        )
        updated += max(0, cursor.rowcount)
    return updated


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """그 표가 이 DB에 있는가."""

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def link_run_cost_sum_krw(conn: sqlite3.Connection, *, key_hash: str) -> float:
    """이 링크로 «끝난» 실행들의 실측 AI 원가 합(원).

    Args:
        conn: 열린 DB 연결.
        key_hash: 링크의 비밀 아닌 지문.

    Returns:
        `internal_ai_cost_krw`의 합. 실행이 없으면 0.0.

    ★ 고객 청구액(`customer_charge_krw`)이 아니라 **내부 AI 원가**를 센다.
      청구는 자동출고 뒤 1회라 새 조사를 허용할지 판단하는 데 쓸 수 없다.
    ★ 진행 중인 실행은 아직 원가가 0으로 남아 있다 —
      그 몫은 `link_active_reservation_krw`가 따로 센다.
    """

    clean_hash = str(key_hash or "").strip()
    if not clean_hash:
        return 0.0
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(internal_ai_cost_krw), 0)
          FROM {TABLE_RUN_HISTORY}
         WHERE link_key_hash = ?
        """,
        (clean_hash,),
    ).fetchone()
    return float(row[0]) if row is not None else 0.0


def link_active_reservation_krw(
    conn: sqlite3.Connection, *, key_hash: str
) -> float:
    """이 링크의 «지금 진행 중인» 실행이 잡아 둔 예약액 합(원).

    Args:
        conn: 열린 DB 연결.
        key_hash: 링크의 비밀 아닌 지문.

    Returns:
        비용 원장에서 ACTIVE 상태인 단계의 예약액 합. 없으면 0.0.

    ★ 왜 이게 필요한가 — 진행 중 실행은 끝나기 전까지 실측 원가가 0이다.
      예약을 안 세면 900원짜리 조사가 도는 «동안» 새 조사가 계속 통과해
      누적 천장을 넘어 버린다.
    ★ 비용 원장 전환(cutover) 전 DB에는 이 표가 아직 없다. 그때는 0원으로
      본다 — 진행 중 예약이라는 개념 자체가 없던 시기다.
    """

    clean_hash = str(key_hash or "").strip()
    if not clean_hash:
        return 0.0
    phases = budget_spend_store.TABLE_BUDGET_PHASES
    if not _table_exists(conn, phases):
        return 0.0
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(phase.reservation_krw), 0)
          FROM {phases} AS phase
          JOIN {TABLE_RUN_HISTORY} AS run
            ON run.run_id = phase.run_id
         WHERE run.link_key_hash = ? AND phase.state = ?
        """,
        (clean_hash, budget_state_machine.PhaseState.ACTIVE.value),
    ).fetchone()
    return float(row[0]) if row is not None else 0.0


def _link_ledger_lifetime_krw(conn: sqlite3.Connection, *, key_hash: str) -> float:
    """비용 원장이 이 링크 통장 앞으로 «모든 날짜»에 걸쳐 잡아 둔 돈(원).

    Args:
        conn: 열린 DB 연결.
        key_hash: 링크의 비밀 아닌 지문.

    Returns:
        확정 원가 + 보수부채 + 진행 중 예약액. 원장이 없으면 0.0.

    ★ 왜 생성 이력만으로는 모자란가 — 회사 확인 단계는 생성 이력 행을 만들지
      않는다. 이력만 세면 확인 비용이 누적에서 통째로 빠진다.
    ★ 링크의 통장 지문은 열쇠 지문과 같은 값이다. 둘 다 열쇠 원문을 소문자로
      맞춰 SHA-256으로 접으므로, 지문 하나로 원장을 바로 볼 수 있다.
    ★ 비용 원장 전환 전 DB에는 원장 자체가 없다 — 그때는 0원으로 본다.
    """

    clean_hash = str(key_hash or "").strip().lower()
    if not is_key_hash(clean_hash):
        return 0.0
    if not budget_state_machine.cutover_applied(conn):
        return 0.0
    exposure = budget_state_machine.load_bucket_lifetime_exposure(
        conn, bucket_id=clean_hash
    )
    return (
        exposure.known_cost_krw
        + exposure.liability_krw
        + exposure.reservation_krw
    )


def link_total_spent_krw(conn: sqlite3.Connection, *, key_hash: str) -> float:
    """이 링크가 «수명 전체»에 쓴 돈(원).

    Args:
        conn: 열린 DB 연결.
        key_hash: 링크의 비밀 아닌 지문.

    Returns:
        생성 이력 기준 합과 비용 원장 기준 합 중 큰 쪽.

    ★ 생성 이력 쪽에서 두 값을 더해도 같은 돈을 두 번 세지 않는다 — 단계가
      끝나면 비용 원장의 예약액이 0이 되고(표의 CHECK가 강제한다), 그 실행의
      실측 원가는 그때 `finish_run`이 생성 이력에 적기 때문이다.
    ★ 이력 합과 원장 합은 «더하지 않고 큰 쪽»을 고른다 — 같은 조사가 양쪽에
      적히므로 더하면 두 번 센다. 확인 비용은 원장에만, 전환 전 옛 조사는
      이력에만 있어서, 큰 쪽을 고르면 어느 쪽도 놓치지 않는다.
    ★ 이 값은 화면 안내와 사전 검사가 함께 쓴다. 예약을 커밋하는 자리의
      최종 판단도 같은 바닥값을 보므로 안내 숫자와 실제 차단이 어긋나지 않는다.
    """

    from_history = link_run_cost_sum_krw(conn, key_hash=key_hash) + (
        link_active_reservation_krw(conn, key_hash=key_hash)
    )
    return max(from_history, _link_ledger_lifetime_krw(conn, key_hash=key_hash))


def is_linked_report(conn: sqlite3.Connection, report_id: str) -> bool:
    """보고서가 LINK의 시작 보고서 또는 생성 이력에 실제 결속됐는지 확인한다."""

    clean_report_id = str(report_id or "").strip()
    if not clean_report_id:
        return False
    row = conn.execute(
        f"""
        SELECT 1
          FROM {TABLE_SHARE_LINKS}
         WHERE report_id = ?
        UNION ALL
        SELECT 1
          FROM {TABLE_RUN_HISTORY}
         WHERE report_id = ?
         LIMIT 1
        """,
        (clean_report_id, clean_report_id),
    ).fetchone()
    return row is not None


def today_iso() -> str:
    """오늘 날짜 문자열. 시험에서 바꿔 끼울 수 있게 함수로 둔다."""
    return clock.today_kst().isoformat()
