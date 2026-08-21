"""발급한 열쇠 링크를 저장하고 찾는다 (문제로그 P-94).

★ 저장이 필요한 이유 두 가지
  ① **서버를 껐다 켜도 링크가 살아 있어야 한다.** 인사팀은 며칠 뒤에 열어본다.
  ② 링크 요청 횟수와 최초·최근 요청 시각을 남긴다. 메신저 미리보기·봇도 GET을
     보낼 수 있으므로 사람이나 개인을 식별하는 기록이라고 주장하지 않는다.

★ 표를 «새로» 만든다. 기존 `reports` 표는 안 건드린다 —
  이미 들어 있는 자료에 열을 더하면 옛 줄을 어떻게 채울지가 문제가 된다.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from src.core import clock

#: 발급한 링크를 담는 표.
TABLE_SHARE_LINKS = "share_links"

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
    last_opened_at  TEXT NOT NULL DEFAULT ''
)
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

    @property
    def is_baked(self) -> bool:
        """미리 구워 둔 보고서가 있는가. 있으면 인사팀이 0원으로 바로 본다."""
        return bool(self.report_id)

    @property
    def key(self) -> str:
        """관리 화면에서 쓰는 비밀이 아닌 영속 식별자."""

        return self.key_hash


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
    )


_COLUMNS = (
    "key_hash, company, job, report_id, note, created_at, "
    "opened_count, first_opened_at, last_opened_at"
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
    """원문 열쇠 표를 지문 표로 원자 전환하고 삭제 페이지도 덮어쓴다."""

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
        if (
            _table_state(conn, TABLE_SHARE_LINKS) != "hashed"
            or _table_state(conn, _LEGACY_TABLE) != "missing"
        ):
            raise RuntimeError("share_links 해시 스키마 전환을 완결하지 못했습니다")
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


def mark_opened(conn: sqlite3.Connection, key: str, now_iso: str) -> bool:
    """링크 GET 요청 기록을 남긴다.

    ★ 최초 요청 시각은 «한 번만» 적는다. 나중 GET은 최근 요청 시각만 갱신한다.
      이 값은 사람 식별이나 보안 감사 증거가 아니라 요청 관찰 지표다.
    """
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_SHARE_LINKS}
           SET opened_count    = opened_count + 1,
               first_opened_at = CASE WHEN first_opened_at = ''
                                      THEN ? ELSE first_opened_at END,
               last_opened_at  = ?
         WHERE key_hash = ?
        """,
        (now_iso, now_iso, key_hash_of(key)),
    )
    return cursor.rowcount > 0


def set_report(conn: sqlite3.Connection, key: str, report_id: str) -> bool:
    """기존 회사 링크에 미리 만든 보고서를 연결하거나 빈 값으로 해제한다."""
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


def delete(conn: sqlite3.Connection, key: str) -> bool:
    """링크를 닫는다 (지운다).

    Returns:
        실제로 지워졌으면 True.

    ★ 뿌린 링크를 «되돌릴 방법»이 있어야 한다 — 잘못 보냈거나
      지원이 끝났으면 닫을 수 있어야 한다. 없으면 두 달을 기다려야 한다.
    """
    cursor = conn.execute(
        f"DELETE FROM {TABLE_SHARE_LINKS} WHERE key_hash = ?", (key_hash_of(key),)
    )
    return cursor.rowcount > 0


def delete_by_hash(conn: sqlite3.Connection, key_hash: str) -> bool:
    normalized = str(key_hash or "").strip().lower()
    if not is_key_hash(normalized):
        return False
    cursor = conn.execute(
        f"DELETE FROM {TABLE_SHARE_LINKS} WHERE key_hash = ?", (normalized,)
    )
    return cursor.rowcount > 0


def today_iso() -> str:
    """오늘 날짜 문자열. 시험에서 바꿔 끼울 수 있게 함수로 둔다."""
    return clock.today_kst().isoformat()
