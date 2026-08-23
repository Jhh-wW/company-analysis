"""발급한 열쇠 링크를 저장하고 찾는다 (문제로그 P-94).

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
from src.features.sharelink import constants, logic

#: 발급한 링크를 담는 표.
TABLE_SHARE_LINKS = "share_links"
TABLE_OPEN_EVENTS = "share_link_open_events"
TABLE_OPEN_WINDOWS = "share_link_open_windows"
TABLE_ACCESS_SUBJECTS = "share_link_access_subjects"
TABLE_RUN_HISTORY = "share_link_run_history"
TABLE_REPORT_VIEW_EVENTS = "share_link_report_view_events"
INDEX_RUN_REPORT_ID = f"idx_{TABLE_RUN_HISTORY}_report_id_unique"
INDEX_OPEN_WINDOWS_LINK_WINDOW = f"idx_{TABLE_OPEN_WINDOWS}_link_window_unique"
INDEX_ACCESS_SUBJECT_WINDOW = f"idx_{TABLE_ACCESS_SUBJECTS}_window_subject_unique"
INDEX_REPORT_VIEW_EVENTS_LINK_TIME = (
    f"idx_{TABLE_REPORT_VIEW_EVENTS}_link_time"
)
INDEX_REPORT_VIEW_EVENTS_LINK_REPORT = (
    f"idx_{TABLE_REPORT_VIEW_EVENTS}_link_report_unique"
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
    revoked_at      TEXT NOT NULL DEFAULT ''
)
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
    CHECK (opened_count BETWEEN 1 AND {constants.ACCESS_PER_REQUESTER_LIMIT})
)
"""
CREATE_ACCESS_SUBJECTS_UNIQUE_INDEX_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_ACCESS_SUBJECT_WINDOW}
ON {TABLE_ACCESS_SUBJECTS}(window_id, requester_hash)
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
    )


_COLUMNS = (
    "key_hash, company, job, report_id, note, created_at, "
    "opened_count, first_opened_at, last_opened_at, revoked_at"
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
        }
        if not required_link_columns.issubset(columns):
            raise RuntimeError("share_links 필수 열이 없습니다")

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
        conn.execute(CREATE_RUN_HISTORY_SQL)
        conn.execute(CREATE_RUN_HISTORY_NO_DELETE_SQL)
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
        if not {"id", "window_id", "requester_hash", "opened_count"}.issubset(
            _table_columns(conn, TABLE_ACCESS_SUBJECTS)
        ):
            raise RuntimeError("share_link_access_subjects 스키마가 올바르지 않습니다")
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
    now_iso: str,
) -> bool:
    """새 열쇠만 삽입한다. 충돌한 기존 링크는 절대 덮어쓰지 않는다.

    Returns:
        삽입했으면 ``True``, 같은 열쇠가 이미 있으면 ``False``.
    """
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_SHARE_LINKS}
            (key_hash, company, job, report_id, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(key_hash) DO NOTHING
        """,
        (key_hash_of(key), company, job, report_id, note, now_iso),
    )
    return cursor.rowcount > 0


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
    *,
    requester_hash: str = "",
) -> bool:
    """active 링크 GET을 원자적 bounded 구간 집계에 남긴다.

    폐기·만료 상태를 writer lock 안에서 다시 확인하고, 같은 분 구간은 한 행만
    UPSERT한다. capability 전체와 비가역 요청자 통장을 모두 영속 cap으로 제한하며
    최근 구간만 보존한다. ``False``는 없음·비활성·cap 소진 중 하나다.
    """

    normalized_requester = str(requester_hash or "").strip().lower()
    if normalized_requester and not is_key_hash(normalized_requester):
        return False
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
            f"SELECT created_at, revoked_at FROM {TABLE_SHARE_LINKS} "
            "WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if (
            state is None
            or str(state[1] or "")
            or logic.is_share_link_expired(str(state[0]), today=today)
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

        if normalized_requester:
            subject = conn.execute(
                f"""
                INSERT INTO {TABLE_ACCESS_SUBJECTS}
                    (window_id, requester_hash, opened_count)
                VALUES (?, ?, 1)
                ON CONFLICT(window_id, requester_hash) DO UPDATE SET
                    opened_count = opened_count + 1
                WHERE opened_count < ?
                RETURNING opened_count
                """,
                (
                    int(recorded[0]),
                    normalized_requester,
                    constants.ACCESS_PER_REQUESTER_LIMIT,
                ),
            ).fetchone()
            if subject is None:
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
            # SQLite connection의 foreign_keys 설정과 무관하게 요청자 통장도
            # 같은 bounded 보존 범위로 명시 정리한다.
            conn.execute(
                f"""
                DELETE FROM {TABLE_ACCESS_SUBJECTS}
                 WHERE window_id IN (
                       SELECT id FROM {TABLE_OPEN_WINDOWS}
                        WHERE link_key_hash = ?
                          AND id NOT IN (
                              SELECT id FROM {TABLE_OPEN_WINDOWS}
                               WHERE link_key_hash = ?
                               ORDER BY window_started_at DESC, id DESC
                               LIMIT ?
                          )
                 )
                """,
                (
                    key_hash,
                    key_hash,
                    constants.OPEN_WINDOW_ROWS_PER_LINK,
                ),
            )
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
      전체 상한을 두지 않기로 했으므로(사용자 결정), **링크 수가 곧 최악의 지출**이다.
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
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_RUN_HISTORY} (
            run_id, link_key_hash, started_at, input_company,
            confirmed_company, company_id, status
        )
        SELECT ?, key_hash, ?, ?, ?, ?, ?
          FROM {TABLE_SHARE_LINKS}
         WHERE key_hash = ? AND revoked_at = ''
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
