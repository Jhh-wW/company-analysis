"""«초대한 사람»만 유료 기능을 쓰게 한다.

★ 왜 필요한가 — **로그인만으로는 아무 권한도 주면 안 된다.**
  구글 로그인은 「이 사람이 누구인가」만 알려준다. 「이 사람이 써도 되는가」는 별개다.
  이걸 안 나누면 **인터넷의 아무나 구글 로그인만 하면 MEMBER 성공 보고서를 쓴다.**
  포트폴리오 링크로 들어온 인사팀이 로그인해도 마찬가지다.
  ⚠️ 사용자가 직접 지적해 잡힌 구멍이다.

★ 왜 환경변수가 아니라 DB인가 — 친구를 한 명 추가할 때마다
  **서버를 껐다 켜야 한다면** 실제로는 아무도 추가 안 하게 된다.
  관리 화면에서 바로 넣고 뺄 수 있어야 쓰인다.

★ 관리자(`ADMIN_EMAILS`)는 이 명단과 «별개»다 — 관리자는 항상 통과한다.
  둘을 합치면 「친구를 지웠는데 관리자까지 날아가는」 사고가 난다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final, Optional

#: 초대한 사람을 담는 표.
TABLE_ALLOWED_USERS = "allowed_users"

#: 관리자가 한 사람에게 줄 수 있는 하루 성공 보고서 건수의 범위.
#: 아래쪽 1 — 0을 허용하면 「명단에는 있는데 아무것도 못 하는」 상태가 되어
#: 「빼기(revoke)」와 뜻이 겹치고, 화면에서 두 상태를 구분할 수 없다.
#: 위쪽 20 — 하루 성공 20건이면 본조사 예상액만 18,000원이라 사고 규모가
#: 관리자 몫(5,000원)의 세 배를 넘는다. 그 위는 실수로 눌렀을 가능성이 더 크다.
MEMBER_SUCCESS_LIMIT_MIN: Final[int] = 1
MEMBER_SUCCESS_LIMIT_MAX: Final[int] = 20

#: 관리자가 한 사람에게 줄 수 있는 하루 비용 상한의 범위(원).
#: 0원 — 「명단에는 두되 유료 조사는 잠시 멈춘다」를 표현할 수 있어야 한다.
#: 20,000원 — 그 위는 사람 하나가 링크 여러 개보다 크게 쓰는 값이라 오타로 본다.
MEMBER_BUDGET_MIN_KRW: Final[float] = 0.0
MEMBER_BUDGET_MAX_KRW: Final[float] = 20_000.0

#: 한도를 바꾼 이유의 최대 길이. 화면 표시용이라 문단이 아니라 한 줄이면 된다.
LIMIT_REASON_MAX_CHARS: Final[int] = 200

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_ALLOWED_USERS} (
    email      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    invited_at TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    revoked_at TEXT NOT NULL DEFAULT '',
    daily_success_limit INTEGER,
    daily_budget_krw    REAL,
    limit_reason     TEXT NOT NULL DEFAULT '',
    limit_updated_at TEXT NOT NULL DEFAULT ''
)
"""

@dataclass(frozen=True)
class AllowedUser:
    """초대한 사람 한 명.

    ★ `daily_success_limit`·`daily_budget_krw`는 **관리자가 덮어쓴 값**이다.
      `None`이면 「기본값을 쓰라」는 뜻이며, 기본값이 얼마인지는 여기서 정하지
      않는다 — 성공 건수 기본값은 `admin_dashboard/store.py`가, 비용 기본값은
      `sharelink/constants.py`가 각각 정본이다. 두 상수를 이 파일이 가져와
      해석하면 같은 정의가 두 곳이 되어 한쪽만 고쳐진다 (P-83과 같은 함정).
    """

    email: str
    display_name: str
    note: str
    invited_at: str
    daily_success_limit: Optional[int] = None
    daily_budget_krw: Optional[float] = None
    limit_reason: str = ""
    limit_updated_at: str = ""


def _row_to_user(row: sqlite3.Row | tuple) -> AllowedUser:
    """`SELECT` 결과 한 줄을 초대된 사람으로 옮긴다. 열 순서는 `_SELECT_COLUMNS`."""
    return AllowedUser(
        email=str(row[0]),
        display_name=str(row[1]),
        note=str(row[2]),
        invited_at=str(row[3]),
        daily_success_limit=None if row[4] is None else int(row[4]),
        daily_budget_krw=None if row[5] is None else float(row[5]),
        limit_reason=str(row[6] or ""),
        limit_updated_at=str(row[7] or ""),
    )


_SELECT_COLUMNS: Final[str] = (
    "email, display_name, note, invited_at, "
    "daily_success_limit, daily_budget_krw, limit_reason, limit_updated_at"
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """신규·기존 초대 명단에 친구 이름·회원별 한도 열을 멱등으로 준비한다."""
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
    # ★ 한도 두 열은 «NULL을 허용»한다 — 기존 행에 3·3000을 채워 넣으면
    #   나중에 기본값을 바꿀 때 「한 번도 안 건드린 사람」과 「일부러 3으로 맞춘
    #   사람」을 구분할 수 없다. 값이 없음 = 그때그때의 기본값을 따른다.
    #   범위 검사는 CHECK가 아니라 `set_limits()`에서 한다 — CREATE로 만든 표와
    #   ALTER로 늘린 표의 제약이 서로 달라지면 같은 입력이 DB마다 다르게 걸린다.
    if "daily_success_limit" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} ADD COLUMN daily_success_limit INTEGER"
        )
    if "daily_budget_krw" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} ADD COLUMN daily_budget_krw REAL"
        )
    if "limit_reason" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} "
            "ADD COLUMN limit_reason TEXT NOT NULL DEFAULT ''"
        )
    if "limit_updated_at" not in columns:
        conn.execute(
            f"ALTER TABLE {TABLE_ALLOWED_USERS} "
            "ADD COLUMN limit_updated_at TEXT NOT NULL DEFAULT ''"
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
        # ★ 되살릴 때 한도는 «기본값으로 되돌린다» — 「한 번 빼 놓은 사람」을
        #   다시 넣을 때 예전에 올려 둔 몫이 조용히 따라오면, 넣은 사람은
        #   기본 한도인 줄 알고 있는데 실제 비용 노출만 커진다.
        cursor = conn.execute(
            f"""UPDATE {TABLE_ALLOWED_USERS}
            SET display_name = ?, note = ?, invited_at = ?, is_active = 1, revoked_at = '',
                daily_success_limit = NULL, daily_budget_krw = NULL,
                limit_reason = '', limit_updated_at = ''
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


def set_limits(
    conn: sqlite3.Connection,
    *,
    email: str,
    daily_success_limit: Optional[int],
    daily_budget_krw: Optional[float],
    reason: str,
    now_iso: str,
) -> bool:
    """이 친구 «한 명»의 하루 한도를 바꾼다. 다음 날에도 그대로 유지되는 값이다.

    Args:
        daily_success_limit: 하루 성공 보고서 건수. `None`이면 기본값으로 되돌린다.
        daily_budget_krw: 하루 비용 상한(원). `None`이면 기본값으로 되돌린다.
        reason: 왜 바꿨는지. **빈 값이면 거절한다.**
        now_iso: 바꾼 시각.

    Returns:
        명단에 살아 있는 사람의 한도를 실제로 바꿨으면 True.
        명단에 없거나 이미 뺀 사람이면 False.

    Raises:
        ValueError: 값이 허용 범위 밖이거나 이유가 비었을 때.

    ★ 왜 사람마다 두나 — 상수 하나를 올리면 **명단 전원**의 몫이 같이 올라간다.
      최악의 하루 지출은 「1인 상한 × 명단 인원」이므로, 한 명을 위한 상수 변경은
      나머지 전원에게도 청구될 수 있는 문을 연다.
    ★ 왜 「오늘만」이 아닌가 — 이번 결정은 **영구 값 변경**이다.
      당일 1회성 보너스는 별도 표가 필요해 이번 범위 밖이다.
    ★ 왜 이유가 필수인가 — 몇 달 뒤 「이 사람은 왜 7건이지」를 답할 수 있어야
      되돌릴지 판단할 수 있다. 관리자 감사 원장은 ASCII 사유코드만 담으므로
      사람이 쓴 한국어 이유는 이 행에 함께 남긴다.
    """
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("한도를 바꾼 이유를 적어야 합니다")
    if len(clean_reason) > LIMIT_REASON_MAX_CHARS:
        raise ValueError("한도를 바꾼 이유가 너무 깁니다")

    clean_success: Optional[int] = None
    if daily_success_limit is not None:
        if isinstance(daily_success_limit, bool):
            raise ValueError("하루 성공 건수 한도는 숫자여야 합니다")
        try:
            clean_success = int(daily_success_limit)
        except (TypeError, ValueError) as error:
            raise ValueError("하루 성공 건수 한도는 숫자여야 합니다") from error
        if not (
            MEMBER_SUCCESS_LIMIT_MIN <= clean_success <= MEMBER_SUCCESS_LIMIT_MAX
        ):
            raise ValueError(
                f"하루 성공 건수 한도는 {MEMBER_SUCCESS_LIMIT_MIN}~"
                f"{MEMBER_SUCCESS_LIMIT_MAX}건 사이여야 합니다"
            )

    clean_budget: Optional[float] = None
    if daily_budget_krw is not None:
        if isinstance(daily_budget_krw, bool):
            raise ValueError("하루 비용 한도는 숫자여야 합니다")
        try:
            clean_budget = float(daily_budget_krw)
        except (TypeError, ValueError) as error:
            raise ValueError("하루 비용 한도는 숫자여야 합니다") from error
        if clean_budget != clean_budget:  # NaN은 어떤 비교에도 걸리지 않는다
            raise ValueError("하루 비용 한도는 숫자여야 합니다")
        if not (MEMBER_BUDGET_MIN_KRW <= clean_budget <= MEMBER_BUDGET_MAX_KRW):
            raise ValueError(
                f"하루 비용 한도는 {MEMBER_BUDGET_MIN_KRW:.0f}~"
                f"{MEMBER_BUDGET_MAX_KRW:.0f}원 사이여야 합니다"
            )

    cursor = conn.execute(
        f"""UPDATE {TABLE_ALLOWED_USERS}
        SET daily_success_limit = ?, daily_budget_krw = ?,
            limit_reason = ?, limit_updated_at = ?
        WHERE email = ? AND is_active = 1""",
        (
            clean_success,
            clean_budget,
            clean_reason,
            str(now_iso or "").strip(),
            normalize(email),
        ),
    )
    return cursor.rowcount > 0


def load(conn: sqlite3.Connection, email: str) -> Optional[AllowedUser]:
    """명단에서 한 명을 찾는다."""
    row = conn.execute(
        f"SELECT {_SELECT_COLUMNS} "
        f"FROM {TABLE_ALLOWED_USERS} WHERE email = ? AND is_active = 1",
        (normalize(email),),
    ).fetchone()
    return _row_to_user(row) if row else None


def list_all(conn: sqlite3.Connection) -> list[AllowedUser]:
    """초대한 사람 전부. 최근에 넣은 사람부터.

    ★ 관리 화면에서 «몇 명을 초대했는지»를 보기 위한 것이다.
      호출 전 비용 입장 기준 합계 = 1인 기준 × 명단 인원이다. 실제 청구의
      절대 상한은 아니지만, 이 숫자가 비용 노출 규모를 정한다.
    """
    rows = conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM {TABLE_ALLOWED_USERS} "
        "WHERE is_active = 1 "
        "ORDER BY invited_at DESC"
    ).fetchall()
    return [_row_to_user(r) for r in rows]


def list_profiles(conn: sqlite3.Connection) -> list[AllowedUser]:
    """철회 뒤에도 과거 피드백의 이름·이메일 연결을 보존해 읽는다."""
    rows = conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM {TABLE_ALLOWED_USERS} "
        "ORDER BY invited_at DESC"
    ).fetchall()
    return [_row_to_user(r) for r in rows]
