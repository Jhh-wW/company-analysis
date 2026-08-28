"""구글 로그인의 «순수» 부분 — 네트워크도, 파일도 건드리지 않는다.

관리자 판정, CSRF state 만들기·검증, 구글이 준 사용자 정보에서 확인 이메일과
불변 ``sub`` 꺼내기, 그리고 로그인 세션의 정책을 여기 모은다.

★ 권한 검사는 화면이 아니라 «서버»에서 한다 (기획서 07_출력/4_근거/01_출력근거.md §4
  「버튼을 숨기는 것은 권한이 아니다」). 관리자 전용 경로를 만들 때는 매 요청마다
  `is_admin_session()`을 다시 불러야 한다 — 한 번 확인한 결과를 화면에 기억시키지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from src.core.constants import PIPELINE_ENV
from src.features.auth import constants


class StateMismatchError(Exception):
    """돌아온 state 값이 보낸 값과 다르다 — CSRF(다른 사이트가 대신 요청을 보내는 공격) 의심."""


class UnverifiedEmailError(Exception):
    """구글이 이 이메일을 확인해주지 않았다 (email_verified가 참이 아님)."""


class UnverifiedIdentityError(UnverifiedEmailError):
    """로그인 공급자의 불변 사용자 식별자(``sub``)가 없거나 손상됐다."""


_IDENTITY_SUBJECT_RE = re.compile(
    r"[a-z][a-z0-9_-]{1,31}:[A-Za-z0-9._~@+:-]{1,255}\Z",
    re.ASCII,
)
_PDF_PARTICIPANT_ROLES = ("author", "producer", "fact", "editorial", "visual")
_LEGACY_SUBJECT_PREFIX = "legacy-email:"


@dataclass(frozen=True)
class VerifiedIdentity:
    """확인된 이메일과 공급자의 불변 사람 식별자를 함께 보존한다."""

    email: str
    subject: str


def normalize_identity_subject(subject: str) -> str:
    """공급자 이름이 붙은 불변 subject의 제한된 wire 형식만 받는다."""

    clean = subject.strip() if isinstance(subject, str) else ""
    if clean != subject or _IDENTITY_SUBJECT_RE.fullmatch(clean) is None:
        raise UnverifiedIdentityError("로그인 계정 식별자를 확인할 수 없습니다")
    return clean


def person_id_for_subject(subject: str) -> str:
    """원문 subject를 노출하지 않는 동일인 비교용 80-bit opaque ID로 바꾼다."""

    clean = normalize_identity_subject(subject)
    return "user:" + hashlib.sha256(clean.encode("utf-8")).hexdigest()[:20]


def _legacy_subject(email: str) -> str:
    """기존 내부 호출의 세션 호환용 값. PDF 승인 신원으로는 인정하지 않는다."""

    digest = hashlib.sha256(normalize_email(email).encode("utf-8")).hexdigest()
    return _LEGACY_SUBJECT_PREFIX + digest


def is_approval_identity_subject(subject: object) -> bool:
    """이메일에서 만든 호환 subject를 제외한 공급자/설정 기반 신원인가."""

    if not isinstance(subject, str) or subject.startswith(_LEGACY_SUBJECT_PREFIX):
        return False
    try:
        normalize_identity_subject(subject)
    except UnverifiedIdentityError:
        return False
    return True


def pdf_release_participant_ids_from_env() -> dict[str, str]:
    """명시된 역할→불변 subject 설정을 opaque 사람 ID로 읽는다."""

    raw = os.environ.get(constants.ENV_PDF_RELEASE_PARTICIPANTS, "").strip()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UnverifiedIdentityError("PDF 출고 참여자 설정을 읽을 수 없습니다") from exc
    if not isinstance(payload, dict) or set(payload) != set(_PDF_PARTICIPANT_ROLES):
        raise UnverifiedIdentityError("PDF 출고 참여자 역할 설정이 완전하지 않습니다")
    subjects: dict[str, str] = {}
    for role in _PDF_PARTICIPANT_ROLES:
        value = payload.get(role)
        if not is_approval_identity_subject(value):
            raise UnverifiedIdentityError("PDF 출고 참여자에 불변 계정 식별자가 필요합니다")
        subjects[role] = str(value)
    person_ids = {role: person_id_for_subject(value) for role, value in subjects.items()}
    reviewers = tuple(person_ids[role] for role in ("fact", "editorial", "visual"))
    excluded = {person_ids["author"], person_ids["producer"]}
    if len(set(reviewers)) != 3 or any(reviewer in excluded for reviewer in reviewers):
        raise UnverifiedIdentityError(
            "작성자·생산자와 분리된 서로 다른 세 PDF 검수자가 필요합니다"
        )
    return person_ids


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

    공개 전 시험 배포의 안전장치다. 정확히 ``0``이라고 명시했을 때만 끈다.
    값이 없거나 오타가 나면 잠긴 상태를 유지해 실수로 전체 공개되지 않게 한다.
    """
    return os.environ.get(constants.ENV_BETA_ADMIN_ONLY, "").strip() != "0"


def local_demo_auth_enabled_from_env() -> bool:
    """로컬 데모 관리자 입구에 필요한 명시적 환경 조건을 모두 확인한다.

    이 함수가 참이어도 웹 층에서 실행 중 파이프라인, OAuth 미설정, 요청 Host와
    실제 소켓 클라이언트가 모두 로컬인지 다시 확인한다. 여기서는 설정 누락·오타가
    언제나 닫힌 쪽으로 가도록 정확한 값만 받는다.
    """
    return bool(
        os.environ.get(constants.ENV_LOCAL_DEMO_AUTH, "").strip() == "1"
        and os.environ.get(PIPELINE_ENV, "").strip().lower() == "demo"
        and os.environ.get(constants.ENV_BETA_ADMIN_ONLY, "").strip() == "0"
        and os.environ.get(constants.ENV_COOKIE_INSECURE, "").strip() == "1"
        and local_demo_auth_token_from_env() is not None
        and admin_emails_from_env()
        and not any(
            os.environ.get(name, "").strip()
            for name in (
                constants.ENV_CLIENT_ID,
                constants.ENV_CLIENT_SECRET,
                constants.ENV_REDIRECT_URI,
            )
        )
    )


def local_demo_auth_token_from_env() -> Optional[str]:
    """실행기가 만든 32바이트 로컬 capability만 돌려준다.

    값이 없거나 형식이 다르면 설정 실수로 보고 입구 전체를 닫는다. 실제 값은
    로그·화면·예외에 넣지 않는다.
    """
    raw = os.environ.get(constants.ENV_LOCAL_DEMO_AUTH_TOKEN, "").strip()
    if (
        len(raw) != constants.LOCAL_DEMO_AUTH_TOKEN_HEX_CHARS
        or any(char not in "0123456789abcdef" for char in raw)
    ):
        return None
    return raw


def local_demo_auth_token_matches(received: object) -> bool:
    """요청 capability를 환경의 값과 고정 길이 digest로 비교한다."""
    expected = local_demo_auth_token_from_env()
    candidate = received if isinstance(received, str) else ""
    expected_bytes = (expected or "").encode("utf-8")
    candidate_bytes = candidate.encode("utf-8", errors="replace")
    # 원문 길이가 달라도 같은 길이 digest끼리 반드시 비교한다. 형식 판정은 비교 뒤에
    # 결합해, 공격자가 문자별 비교 시간으로 capability를 좁히지 못하게 한다.
    matches = hmac.compare_digest(
        hashlib.sha256(expected_bytes).digest(),
        hashlib.sha256(candidate_bytes).digest(),
    )
    candidate_has_expected_shape = (
        len(candidate) == constants.LOCAL_DEMO_AUTH_TOKEN_HEX_CHARS
        and all(char in "0123456789abcdef" for char in candidate)
    )
    return bool(expected is not None and candidate_has_expected_shape and matches)


def local_demo_admin_email_from_env() -> Optional[str]:
    """로컬 데모 세션에 쓸 첫 관리자 이메일. 목록이 비었으면 ``None``."""
    emails = admin_emails_from_env()
    return emails[0] if emails else None


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


def csrf_token_for_session(session_token: Optional[str]) -> str:
    """세션마다 고정된 폼용 CSRF 토큰을 만든다.

    세션 토큰 자체는 브라우저의 HttpOnly 쿠키 밖으로 내보내지 않는다. 대신 세션
    난수를 HMAC 키로 써 파생한 값만 숨은 폼 입력에 싣는다. 별도 서버 상태가 없어도
    다른 사이트는 이 값을 알 수 없다.
    """
    if not session_token:
        return ""
    return hmac.new(
        session_token.encode("utf-8"),
        b"enterprise-analysis-form-csrf-v1",
        hashlib.sha256,
    ).hexdigest()


def csrf_token_matches(session_token: Optional[str], received: str) -> bool:
    """폼 토큰이 현재 세션에서 파생된 값과 같은지 상수 시간으로 비교한다."""
    expected = csrf_token_for_session(session_token)
    # ``compare_digest(str, str)``는 비ASCII 문자열을 받으면 TypeError를 낸다.
    # 폼 값은 공격자가 마음대로 만들 수 있으므로 비교 전에 정확한 wire 형식부터
    # 고정한다. SHA-256 hexdigest는 소문자 ASCII 16진수 64자뿐이다.
    if (
        not expected
        or not isinstance(received, str)
        or len(received) != 64
        or any(char not in "0123456789abcdef" for char in received)
    ):
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


def extract_verified_identity(userinfo: dict) -> VerifiedIdentity:
    """검증된 이메일과 Google OIDC의 불변 ``sub``를 한 번에 꺼낸다."""

    email = extract_verified_email(userinfo)
    raw_subject = userinfo.get("sub")
    if not isinstance(raw_subject, str):
        raise UnverifiedIdentityError("구글 계정 식별자를 확인할 수 없습니다")
    return VerifiedIdentity(
        email=email,
        subject=normalize_identity_subject(f"google:{raw_subject}"),
    )


# ══════════════════════════════════════════════════════════
# 로그인 세션 (SQLite 저장)
# ══════════════════════════════════════════════════════════
# 토큰·이메일·불변 subject는 공용 SQLite에 저장해 재시작과 여러 worker에서도 같은
# 신원을 읽는다. 권한 판단은 저장 당시 플래그가 아니라 현재 ADMIN_EMAILS로 매번 한다.

@dataclass(frozen=True)
class Session:
    """로그인한 사람 한 명의 세션."""

    token: str
    email: str
    subject: str
    is_admin: bool
    expires_at: float  # time.time() 기준 초


#: 세션은 «파일 저장소»에 둔다 — 서버를 껐다 켜도 로그인이 유지된다.
#: ★ 토큰 만들기·유효시간 «정책»은 여기가 정하고, 저장은 storage가 한다.
#:   섞으면 정책을 바꿀 때 저장 코드까지 뜯어야 한다.


def _to_session(record) -> Session:
    return Session(
        token=record.token,
        email=record.email,
        subject=record.subject,
        is_admin=record.is_admin,
        expires_at=record.expires_at,
    )


def create_session(
    email: str,
    is_admin: bool,
    *,
    subject: Optional[str] = None,
    now: Optional[float] = None,
) -> Session:
    """새 세션을 만들고 저장한다."""
    from src.features.storage import db, sessions as store  # noqa: PLC0415

    started = now if now is not None else time.time()
    identity_subject = (
        normalize_identity_subject(subject) if subject is not None else _legacy_subject(email)
    )
    session = Session(
        token=secrets.token_urlsafe(constants.SESSION_TOKEN_BYTES),
        email=email,
        subject=identity_subject,
        is_admin=is_admin,
        expires_at=started + constants.SESSION_MAX_AGE_SEC,
    )
    with db.connect() as conn:
        store.save_session(
            conn,
            store.SessionRecord(
                token=session.token,
                email=session.email,
                subject=session.subject,
                is_admin=session.is_admin,
                expires_at=session.expires_at,
            ),
            now=started,
        )
    return session


def rotate_session(
    email: str,
    is_admin: bool,
    *,
    subject: Optional[str] = None,
    previous_token: Optional[str],
    now: Optional[float] = None,
) -> Session:
    """기존 브라우저 세션을 폐기하면서 새 로그인 세션으로 원자 교체한다.

    인증이 모두 성공한 뒤 이 함수를 호출해야 한다. 삭제와 저장을 한 트랜잭션에
    묶어 새 세션 저장이 실패하면 기존 세션도 그대로 남도록 한다.
    """
    from src.features.storage import db, sessions as store  # noqa: PLC0415

    started = now if now is not None else time.time()
    identity_subject = (
        normalize_identity_subject(subject) if subject is not None else _legacy_subject(email)
    )
    session = Session(
        token=secrets.token_urlsafe(constants.SESSION_TOKEN_BYTES),
        email=email,
        subject=identity_subject,
        is_admin=is_admin,
        expires_at=started + constants.SESSION_MAX_AGE_SEC,
    )
    with db.connect() as conn:
        store.delete_session(conn, previous_token)
        store.save_session(
            conn,
            store.SessionRecord(
                token=session.token,
                email=session.email,
                subject=session.subject,
                is_admin=session.is_admin,
                expires_at=session.expires_at,
            ),
            now=started,
        )
    return session


def get_session(
    token: Optional[str],
    *,
    now: Optional[float] = None,
    readonly_existing: bool = False,
) -> Optional[Session]:
    """토큰으로 세션을 찾는다. 없거나 만료됐으면 None.

    만료된 세션은 조회하는 김에 지운다 — 로그아웃 없이 방치돼도 메모리가 계속 커지지 않게.
    """
    from src.features.storage import db, sessions as store  # noqa: PLC0415

    if not token:
        return None
    # ★ 만료 판정은 저장소가 한다 — 만료된 세션은 아예 안 돌려준다.
    # 공개 보고서 GET은 schema·행을 만들거나 정리하지 않는 별도 경계다.
    if readonly_existing:
        try:
            with db.connect_readonly_existing() as conn:
                if conn is None:
                    return None
                record = store.load_session(
                    conn,
                    token,
                    now=now,
                    delete_invalid=False,
                )
        except sqlite3.DatabaseError:
            # 이 모드는 공개 GET의 안내 화면을 그리기 위한 보조 조회다. 본 접근
            # 게이트가 저장소 장애를 503으로 닫으므로 여기서는 세션 없음으로만
            # 처리해 그 503 화면 자체가 다시 500으로 깨지지 않게 한다.
            return None
    else:
        with db.connect() as conn:
            record = store.load_session(conn, token, now=now)
    if record is None:
        return None
    try:
        normalize_identity_subject(record.subject)
    except UnverifiedIdentityError:
        # DB 변조·부분 마이그레이션 값을 이메일 신원으로 대체하지 않는다.
        if not readonly_existing:
            with db.connect() as conn:
                store.delete_session(conn, token)
        return None

    # 관리자 권한은 로그인 당시의 스냅샷을 믿지 않는다. 운영 중 ADMIN_EMAILS에서
    # 계정을 빼거나 더한 즉시 beta gate·관리 화면·비용 갈래 등 모든 소비자에게
    # 반영돼야 한다. DB의 is_admin은 옛 기록 호환용일 뿐 현재 권한이 아니다.
    session = _to_session(record)
    return Session(
        token=session.token,
        email=session.email,
        subject=session.subject,
        is_admin=check_admin(session.email),
        expires_at=session.expires_at,
    )


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


def current_subject(token: Optional[str], *, now: Optional[float] = None) -> Optional[str]:
    """현재 세션의 공급자 불변 subject. 세션이 없거나 만료됐으면 None."""

    session = get_session(token, now=now)
    return session.subject if session else None
