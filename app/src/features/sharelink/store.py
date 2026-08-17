"""발급한 열쇠 링크를 저장하고 찾는다 (문제로그 P-94).

★ 저장이 필요한 이유 두 가지
  ① **서버를 껐다 켜도 링크가 살아 있어야 한다.** 인사팀은 며칠 뒤에 열어본다.
  ② **누가 언제 열어봤는지**를 알아야 한다 — 그게 이 방식을 고른 이유 중 하나다.

★ 표를 «새로» 만든다. 기존 `reports` 표는 안 건드린다 —
  이미 들어 있는 자료에 열을 더하면 옛 줄을 어떻게 채울지가 문제가 된다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Optional

#: 발급한 링크를 담는 표.
TABLE_SHARE_LINKS = "share_links"

#: 표 만들기. `db.py`가 서버 시작 때 부른다.
CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SHARE_LINKS} (
    key             TEXT PRIMARY KEY,
    company         TEXT NOT NULL,
    job             TEXT NOT NULL,
    -- ★ 미리 구워 둔 보고서 번호. 비어 있으면 「아직 안 구웠다」는 뜻이다.
    report_id       TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    -- ★ 열어본 기록. 「인사팀이 내 포폴을 봤는지」를 이걸로 안다.
    opened_count    INTEGER NOT NULL DEFAULT 0,
    first_opened_at TEXT NOT NULL DEFAULT '',
    last_opened_at  TEXT NOT NULL DEFAULT ''
)
"""


@dataclass(frozen=True)
class ShareLink:
    """발급한 링크 하나."""

    key: str
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


def _row_to_link(row: sqlite3.Row | tuple) -> ShareLink:
    return ShareLink(
        key=row[0],
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
    "key, company, job, report_id, note, created_at, "
    "opened_count, first_opened_at, last_opened_at"
)


def save(
    conn: sqlite3.Connection,
    *,
    key: str,
    company: str,
    job: str,
    report_id: str = "",
    note: str = "",
    now_iso: str,
) -> None:
    """링크를 발급해 저장한다. 같은 열쇠가 있으면 회사·직무·보고서만 갱신한다.

    ★ 열어본 기록(`opened_count` 등)은 **덮어쓰지 않는다** —
      보고서를 다시 구웠다고 「누가 봤는지」가 사라지면 안 된다.
    """
    conn.execute(
        f"""
        INSERT INTO {TABLE_SHARE_LINKS}
            (key, company, job, report_id, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            company   = excluded.company,
            job       = excluded.job,
            report_id = excluded.report_id,
            note      = excluded.note
        """,
        (key, company, job, report_id, note, now_iso),
    )


def load(conn: sqlite3.Connection, key: str) -> Optional[ShareLink]:
    """열쇠로 링크를 찾는다. 없으면 None."""
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE_SHARE_LINKS} WHERE key = ?", (key,)
    ).fetchone()
    return _row_to_link(row) if row else None


def mark_opened(conn: sqlite3.Connection, key: str, now_iso: str) -> None:
    """열어본 기록을 남긴다.

    ★ 처음 열어본 시각은 «한 번만» 적는다 — 나중 방문이 그걸 덮으면
      「언제 처음 봤나」를 영영 못 알게 된다. 그게 알고 싶은 값이다.
    """
    conn.execute(
        f"""
        UPDATE {TABLE_SHARE_LINKS}
           SET opened_count    = opened_count + 1,
               first_opened_at = CASE WHEN first_opened_at = ''
                                      THEN ? ELSE first_opened_at END,
               last_opened_at  = ?
         WHERE key = ?
        """,
        (now_iso, now_iso, key),
    )


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
        f"DELETE FROM {TABLE_SHARE_LINKS} WHERE key = ?", (key,)
    )
    return cursor.rowcount > 0


def today_iso() -> str:
    """오늘 날짜 문자열. 시험에서 바꿔 끼울 수 있게 함수로 둔다."""
    return dt.date.today().isoformat()
