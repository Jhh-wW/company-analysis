"""«초대한 사람»만 유료 기능을 쓰게 한다 (문제로그 P-95).

★ 왜 필요한가 — **로그인만으로는 아무 권한도 주면 안 된다.**
  구글 로그인은 「이 사람이 누구인가」만 알려준다. 「이 사람이 써도 되는가」는 별개다.
  이걸 안 나누면 **인터넷의 아무나 구글 로그인만 하면 MEMBER 성공 보고서를 쓴다.**
  포트폴리오 링크로 들어온 인사팀이 로그인해도 마찬가지다.
  ⚠️ 사용자가 직접 지적해 잡힌 구멍이다 (2026-08-16).

★ 왜 환경변수가 아니라 DB인가 — 친구를 한 명 추가할 때마다
  **서버를 껐다 켜야 한다면** 실제로는 아무도 추가 안 하게 된다.
  관리 화면에서 바로 넣고 뺄 수 있어야 쓰인다.

★ 관리자(`ADMIN_EMAILS`)는 이 명단과 «별개»다 — 관리자는 항상 통과한다.
  둘을 합치면 「친구를 지웠는데 관리자까지 날아가는」 사고가 난다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

#: 초대한 사람을 담는 표.
TABLE_ALLOWED_USERS = "allowed_users"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_ALLOWED_USERS} (
    email      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    invited_at TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    revoked_at TEXT NOT NULL DEFAULT ''
)
"""


@dataclass(frozen=True)
class AllowedUser:
    """초대한 사람 한 명."""

    email: str
    display_name: str
    note: str
    invited_at: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """신규·기존 초대 명단에 친구 이름 열을 멱등으로 준비한다."""
    conn.execute(CREATE_SQL)
    columns = {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({TABLE_ALLOWED_USERS})")
    }
    if "display_name" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} "
            "ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
        )
    if "is_active" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} "
            "ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
        )
    if "revoked_at" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} "
            "ADD COLUMN revoked_at TEXT NOT NULL DEFAULT ''"
        )


def normalize(email: str) -> str:
    """이메일을 대조용으로 다듬는다.

    Args:
        email: 사람이 입력했거나 구글이 준 이메일.

    Returns:
        앞뒤 공백을 떼고 소문자로 바꾼 것.

    ★ 대소문자를 안 맞추면 「Hong@Gmail.com」으로 초대해 놓고
      「hong@gmail.com」으로 로그인했을 때 «명단에 없는 사람»이 된다.
    """
    return (email or "").strip().lower()


def invite(
    conn: sqlite3.Connection, *, email: str, note: str, now_iso: str,
    display_name: str = "",
) -> bool:
    """사람을 명단에 넣는다.

    Returns:
        새로 넣었으면 True, 이미 있었으면 False.

    ★ 이미 있으면 «덮어쓰지 않는다» — 초대한 날짜가 사라지면
      「언제부터 쓰던 사람인가」를 못 알게 된다.
    """
    clean = normalize(email)
    if not clean or "@" not in clean:
        return False
    existing = conn.execute(
        f"SELECT is_active FROM {TABLE_ALLOWED_USERS} WHERE email = ?", (clean,)
    ).fetchone()
    if existing is not None and bool(existing[0]):
        return False
    if existing is not None:
        cursor = conn.execute(
            f"""UPDATE {TABLE_ALLOWED_USERS}
            SET display_name = ?, note = ?, invited_at = ?, is_active = 1, revoked_at = ''
            WHERE email = ? AND is_active = 0""",
            (display_name.strip(), note.strip(), now_iso, clean),
        )
    else:
        cursor = conn.execute(
            f"INSERT INTO {TABLE_ALLOWED_USERS} "
            "(email, display_name, note, invited_at, is_active, revoked_at) "
            "VALUES (?, ?, ?, ?, 1, '')",
            (clean, display_name.strip(), note.strip(), now_iso),
        )
    return cursor.rowcount > 0


def revoke(conn: sqlite3.Connection, email: str, *, now_iso: str = "") -> bool:
    """명단에서 뺀다.

    Returns:
        실제로 빠졌으면 True.

    ★ 뺄 수 있어야 한다 — 친구가 다 썼거나, 실수로 넣었거나,
      계정이 남에게 넘어갔을 때 되돌릴 방법이 없으면 안 된다.
    """
    cursor = conn.execute(
        f"""UPDATE {TABLE_ALLOWED_USERS}
        SET is_active = 0, revoked_at = ? WHERE email = ? AND is_active = 1""",
        (str(now_iso or "").strip(), normalize(email)),
    )
    return cursor.rowcount > 0


def is_allowed(conn: sqlite3.Connection, email: str) -> bool:
    """이 사람이 «유료 기능»을 써도 되는가.

    Args:
        conn: 열린 DB 연결.
        email: 로그인한 사람의 이메일.

    Returns:
        명단에 있으면 True.

    ⚠️ **관리자 여부는 여기서 안 본다.** 부르는 쪽이 「관리자거나 명단에 있거나」로 합친다 —
      두 가지를 여기 섞으면 이 함수의 뜻이 흐려지고, 시험도 무엇을 보는지 모호해진다.
    """
    clean = normalize(email)
    if not clean:
        return False
    row = conn.execute(
        f"SELECT 1 FROM {TABLE_ALLOWED_USERS} WHERE email = ? AND is_active = 1", (clean,)
    ).fetchone()
    return row is not None


def load(conn: sqlite3.Connection, email: str) -> Optional[AllowedUser]:
    """명단에서 한 명을 찾는다."""
    row = conn.execute(
        f"SELECT email, display_name, note, invited_at "
        f"FROM {TABLE_ALLOWED_USERS} WHERE email = ? AND is_active = 1",
        (normalize(email),),
    ).fetchone()
    return (
        AllowedUser(email=row[0], display_name=row[1], note=row[2], invited_at=row[3])
        if row else None
    )


def list_all(conn: sqlite3.Connection) -> list[AllowedUser]:
    """초대한 사람 전부. 최근에 넣은 사람부터.

    ★ 관리 화면에서 «몇 명을 초대했는지»를 보기 위한 것이다.
      호출 전 비용 입장 기준 합계 = 1인 기준 × 명단 인원이다. 실제 청구의
      절대 상한은 아니지만, 이 숫자가 비용 노출 규모를 정한다.
    """
    rows = conn.execute(
        f"SELECT email, display_name, note, invited_at FROM {TABLE_ALLOWED_USERS} "
        "WHERE is_active = 1 "
        "ORDER BY invited_at DESC"
    ).fetchall()
    return [
        AllowedUser(email=r[0], display_name=r[1], note=r[2], invited_at=r[3])
        for r in rows
    ]


def list_profiles(conn: sqlite3.Connection) -> list[AllowedUser]:
    """철회 뒤에도 과거 피드백의 이름·이메일 연결을 보존해 읽는다."""
    rows = conn.execute(
        f"SELECT email, display_name, note, invited_at FROM {TABLE_ALLOWED_USERS} "
        "ORDER BY invited_at DESC"
    ).fetchall()
    return [
        AllowedUser(email=r[0], display_name=r[1], note=r[2], invited_at=r[3])
        for r in rows
    ]
