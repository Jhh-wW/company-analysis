"""구글 로그인의 «순수» 부분 — 네트워크도, 파일도 건드리지 않는다.

관리자 판정, CSRF state 만들기·검증, 구글이 준 사용자 정보에서 이메일 꺼내기,
그리고 로그인 세션을 기억해두는 자리(메모리)까지 여기 모은다.

★ 권한 검사는 화면이 아니라 «서버»에서 한다 (기획서 07_출력/4_근거/01_출력근거.md §4
  「버튼을 숨기는 것은 권한이 아니다」). 관리자 전용 경로를 만들 때는 매 요청마다
  `is_admin_session()`을 다시 불러야 한다 — 한 번 확인한 결과를 화면에 기억시키지 않는다.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from src.features.auth import constants


class StateMismatchError(Exception):
    """돌아온 state 값이 보낸 값과 다르다 — CSRF(다른 사이트가 대신 요청을 보내는 공격) 의심."""


class UnverifiedEmailError(Exception):
    """구글이 이 이메일을 확인해주지 않았다 (email_verified가 참이 아님)."""


# ══════════════════════════════════════════════════════════
# 이메일 정규화 · 관리자 판정
# ══════════════════════════════════════════════════════════

def normalize_email(email: str) -> str:
    """이메일을 비교 가능한 형태로 다듬는다.

    ★ 하는 일은 딱 둘 — 대소문자 통일, 앞뒤 공백 제거.
      「+별칭」이나 「.점」을 지우는 «관대한» 정규화는 하지 않는다.
      그런 정규화를 넣으면 `admin+x@gmail.com`이 `admin@gmail.com`과
      «같다»고 통과하는 길이 생겨, 관리자가 아닌 사람이 뚫을 수 있다.
    """
    return email.strip().lower()


def admin_emails_from_env() -> tuple[str, ...]:
    """환경변수(ADMIN_EMAILS)에서 관리자 이메일 목록을 읽는다.

    비어 있으면 관리자를 한 명도 허용하지 않는다. 실제 개인 이메일은 코드가 아니라
    배포 환경변수에만 둔다.
    콤마로 구분해서 넣는다 (예: "a@x.com,b@y.com").
    """
    raw = os.environ.get(constants.ENV_ADMIN_EMAILS, "").strip()
    if not raw:
        return constants.DEFAULT_ADMIN_EMAILS
    return tuple(normalize_email(part) for part in raw.split(",") if part.strip())


def is_admin_email(email: str, admin_emails: Iterable[str]) -> bool:
    """이 이메일이 관리자 목록에 있는가. (순수 함수 — 목록을 직접 받는다)"""
    normalized_admins = {normalize_email(a) for a in admin_emails}
    return normalize_email(email) in normalized_admins


def check_admin(email: str) -> bool:
    """환경변수의 관리자 목록을 기준으로 이 이메일이 관리자인지 본다."""
    return is_admin_email(email, admin_emails_from_env())


def beta_admin_only_from_env() -> bool:
    """시험 배포 전체를 관리자 로그인 뒤에 둘지 읽는다.

    배포 환경에서 실수로 모호한 값을 넣어 공개 상태가 바뀌지 않도록 정확히 ``1``만
    켜짐으로 본다. 환경변수가 없으면 기존 공개 화면 동작을 그대로 유지한다.
    """
    return os.environ.get(constants.ENV_BETA_ADMIN_ONLY, "").strip() == "1"


# ══════════════════════════════════════════════════════════
# CSRF state
# ══════════════════════════════════════════════════════════

def make_state() -> str:
    """로그인 왕복 동안 대조할, 예측 불가능한 값을 만든다."""
    return secrets.token_urlsafe(constants.STATE_TOKEN_BYTES)


def state_matches(expected: str, received: str) -> bool:
    """보낸 state와 돌아온 state가 같은가.

    시간차 공격(문자를 한 글자씩 맞혀보는 공격)을 막기 위해 상수 시간 비교를 쓴다.
    둘 중 하나라도 비어 있으면 무조건 불일치로 본다.
    """
    if not expected or not received:
        return False
    return secrets.compare_digest(expected, received)


# ══════════════════════════════════════════════════════════
# 구글 사용자 정보에서 이메일 꺼내기
# ══════════════════════════════════════════════════════════

def extract_verified_email(userinfo: dict) -> str:
    """구글 사용자 정보 응답에서 «확인된» 이메일만 꺼낸다.

    Args:
        userinfo: 구글 userinfo 엔드포인트가 돌려준 응답(email, email_verified 등을 담은 dict).

    Returns:
        정규화한 이메일.

    Raises:
        UnverifiedEmailError: email이 없거나, email_verified가 참이 아닐 때.
    """
    email = userinfo.get("email")
    verified = userinfo.get("email_verified")
    # 구글 응답은 bool(True) 또는 문자열("true")로 올 수 있어 둘 다 받아준다.
    is_verified = verified is True or str(verified).strip().lower() == "true"
    if not email or not is_verified:
        raise UnverifiedEmailError("구글이 이 이메일을 확인해주지 않았습니다")
    return normalize_email(email)


# ══════════════════════════════════════════════════════════
# 로그인 세션 (메모리 저장)
# ══════════════════════════════════════════════════════════
# ★ 서버를 재시작하면 전부 사라진다 — main.py의 _JOBS와 같은 방식(메모리 dict)이다.
#   여러 프로세스로 띄우면(예: uvicorn --workers 2) 세션이 프로세스마다 따로 논다.
#   1단계라 별도 저장소(Redis 등)를 붙이지 않았다 — 이 파일 맨 위 주석 대상은 아니지만
#   실제 배포 전에 다시 볼 값이라 여기 남겨둔다.

@dataclass(frozen=True)
class Session:
    """로그인한 사람 한 명의 세션."""

    token: str
    email: str
    is_admin: bool
    expires_at: float  # time.time() 기준 초


#: 세션은 «파일 저장소»에 둔다 — 서버를 껐다 켜도 로그인이 유지된다.
#: ★ 토큰 만들기·유효시간 «정책»은 여기가 정하고, 저장은 storage가 한다.
#:   섞으면 정책을 바꿀 때 저장 코드까지 뜯어야 한다.


def _to_session(record) -> Session:
    return Session(
        token=record.token,
        email=record.email,
        is_admin=record.is_admin,
        expires_at=record.expires_at,
    )


def create_session(email: str, is_admin: bool, *, now: Optional[float] = None) -> Session:
    """새 세션을 만들고 저장한다."""
    from src.features.storage import db, sessions as store  # noqa: PLC0415

    started = now if now is not None else time.time()
    session = Session(
        token=secrets.token_urlsafe(constants.SESSION_TOKEN_BYTES),
        email=email,
        is_admin=is_admin,
        expires_at=started + constants.SESSION_MAX_AGE_SEC,
    )
    with db.connect() as conn:
        store.save_session(
            conn,
            store.SessionRecord(
                token=session.token,
                email=session.email,
                is_admin=session.is_admin,
                expires_at=session.expires_at,
            ),
        )
    return session


def get_session(token: Optional[str], *, now: Optional[float] = None) -> Optional[Session]:
    """토큰으로 세션을 찾는다. 없거나 만료됐으면 None.

    만료된 세션은 조회하는 김에 지운다 — 로그아웃 없이 방치돼도 메모리가 계속 커지지 않게.
    """
    from src.features.storage import db, sessions as store  # noqa: PLC0415

    if not token:
        return None
    # ★ 만료 판정은 저장소가 한다 — 만료된 세션은 아예 안 돌려준다.
    with db.connect() as conn:
        record = store.load_session(conn, token, now=now)
    return _to_session(record) if record is not None else None


def delete_session(token: Optional[str]) -> None:
    """로그아웃 — 세션을 지운다. 없는 토큰이어도 조용히 넘어간다."""
    from src.features.storage import db, sessions as store  # noqa: PLC0415

    if token:
        with db.connect() as conn:
            store.delete_session(conn, token)


def is_admin_session(token: Optional[str], *, now: Optional[float] = None) -> bool:
    """이 세션 토큰이 «지금» 관리자 권한을 갖고 있는가.

    ★ 관리자 전용 경로(라우트)는 이 함수를 «매 요청마다» 다시 불러야 한다.
      로그인 시점에 한 번 판정한 결과를 브라우저가 스스로 우겨서(예: 쿠키 조작)
      다시 쓸 수 없게, 세션이 살아 있는지·관리자가 맞는지를 매번 서버에서 새로 본다.
    """
    session = get_session(token, now=now)
    return session is not None and session.is_admin


def current_email(token: Optional[str], *, now: Optional[float] = None) -> Optional[str]:
    """이 세션 토큰으로 로그인한 이메일. 세션이 없거나 만료됐으면 None."""
    session = get_session(token, now=now)
    return session.email if session else None
