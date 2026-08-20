"""로그인 세션을 SQLite에 저장한다 — 서버를 껐다 켜도 로그인이 유지되게.

★ 여기는 «저장»만 한다. 토큰을 어떻게 만들지·얼마나 오래 살지(관리자
  판정 포함)는 auth 기능의 정책이다(`features/auth/constants.py`·
  `features/auth/logic.py`). storage가 그 값을 알 필요는 없다 — 알면
  auth가 정책을 하나 바꿀 때마다(예: 세션 유효시간) storage도 같이 손대야
  하는 결합이 생긴다. 그래서 `expires_at`도 호출부가 이미 계산해서 넘긴다.

연결 지점 예시 → 최종 보고 §연결 지점 ⓑ.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Optional

from src.features.storage.constants import TABLE_SESSIONS


@dataclass(frozen=True)
class SessionRecord:
    """저장소에 있는 세션 1행. `auth/logic.py`의 `Session`과 필드가 같다 —
    storage가 auth를 import하지 않으려고(feature 간 직접 import 회피) 여기서
    독립적으로 정의했다. 값의 모양만 맞으면 되므로 문제 없다.
    """

    token: str
    email: str
    subject: str
    is_admin: bool
    expires_at: float  # time.time() 기준 초


def save_session(conn: sqlite3.Connection, record: SessionRecord) -> None:
    """세션을 저장한다(같은 토큰이면 덮어쓴다)."""
    conn.execute(
        f"""
        INSERT INTO {TABLE_SESSIONS} (token, email, subject, is_admin, expires_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(token) DO UPDATE SET
            email=excluded.email, subject=excluded.subject,
            is_admin=excluded.is_admin, expires_at=excluded.expires_at
        """,
        (
            record.token,
            record.email,
            record.subject,
            int(record.is_admin),
            record.expires_at,
        ),
    )


def load_session(
    conn: sqlite3.Connection, token: Optional[str], *, now: Optional[float] = None
) -> Optional[SessionRecord]:
    """토큰으로 세션을 찾는다. 없거나 만료됐으면 `None`(만료분은 조회하는 김에 지운다)."""
    if not token:
        return None
    row = conn.execute(
        f"SELECT token, email, subject, is_admin, expires_at FROM {TABLE_SESSIONS} WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    # 마이그레이션 전 세션은 이메일만 갖고 있어 동일 인물의 이메일 별칭을 구별할
    # 수 없다. 승인자 신원으로 승격하지 않고 폐기해 재로그인시킨다.
    if not isinstance(row["subject"], str) or not row["subject"].strip():
        conn.execute(f"DELETE FROM {TABLE_SESSIONS} WHERE token = ?", (token,))
        return None
    checked = now if now is not None else dt.datetime.now().timestamp()
    if row["expires_at"] < checked:
        conn.execute(f"DELETE FROM {TABLE_SESSIONS} WHERE token = ?", (token,))
        return None
    return SessionRecord(
        token=row["token"],
        email=row["email"],
        subject=row["subject"],
        is_admin=bool(row["is_admin"]),
        expires_at=row["expires_at"],
    )


def delete_session(conn: sqlite3.Connection, token: Optional[str]) -> None:
    """로그아웃 — 세션을 지운다. 없는 토큰이어도 조용히 넘어간다."""
    if token:
        conn.execute(f"DELETE FROM {TABLE_SESSIONS} WHERE token = ?", (token,))
