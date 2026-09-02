"""모든 결과 채널이 함께 쓰는 보고서 접근 판정표."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum

from fastapi import Request

from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.report_access import constants, store
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db


logger = logging.getLogger(__name__)

_LOCATOR_RE = re.compile(
    rf"^[0-9a-f]{{{constants.REPORT_ID_HEX_CHARS}}}$"
)


class AccessRole(str, Enum):
    ADMIN = "admin"
    MEMBER = "member"
    LINK = "link"
    PUBLIC = "public"


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    role: AccessRole | None
    reason: str


def _member_owns(
    conn: sqlite3.Connection, *, identity_subject: str, locator: str
) -> bool:
    return store.member_subject_allows(
        conn,
        identity_subject=identity_subject,
        locator=locator,
    )


def _link_owns(
    conn: sqlite3.Connection, *, raw_key: str, locator: str
) -> bool:
    if not share_logic.is_valid_key(raw_key):
        return False
    link = share_store.load(conn, raw_key)
    if (
        link is None
        or link.is_revoked
        # ★ 저장된 만료일(``expires_at``)까지 함께 본다 — 발급일만 보면
        #   관리자가 미룬 만료일과 옛 규칙으로 굳은 만료일을 둘 다 놓친다.
        or share_logic.link_expired(link)
    ):
        return False
    if link.report_id == locator:
        return True
    linked_run = share_store.load_run(conn, locator)
    if linked_run is None:
        linked_run = share_store.load_run_by_report_id(conn, locator)
    return linked_run is not None and linked_run.link_key_hash == link.key_hash


def _warn_legacy_access(*, locator: str, audience: str) -> None:
    """raw 공개 ID·IP 없이 유한 호환 경로 사용을 보안 경고로 남긴다."""

    resource_digest = hashlib.sha256(locator.encode("ascii")).hexdigest()
    logger.warning(
        "legacy report access compatibility used audience=%s resource_digest=%s",
        audience,
        resource_digest,
    )


def _store_failure_reason(error: Exception) -> str:
    """원문을 내보내지 않고 운영자가 복구 범위만 구분할 수 있게 한다."""

    message = str(error).lower()
    if isinstance(error, sqlite3.OperationalError) and (
        "no such table" in message
        or "no such column" in message
        or "readonly database" in message
        or "read-only database" in message
    ):
        return "store_incomplete"
    if isinstance(error, sqlite3.DatabaseError):
        return "store_unreadable"
    return "store_unavailable"


def authorize_report_access(
    request: Request,
    locator: str,
    *,
    now: float | None = None,
) -> AccessDecision:
    """ID 자체에는 권한을 주지 않고 네 갈래의 현재 증명만 읽어 판정한다.

    이 함수는 읽기 전용 SQLite 연결만 사용한다. 결과·PDF·progress/API가 모두
    이 한 판정표를 호출해야 하며, 저장소가 없거나 손상되면 닫힌 쪽으로 끝난다.
    """

    clean = str(locator or "").strip().lower()
    checked_at = float(time.time() if now is None else now)
    try:
        with storage_db.connect_readonly_existing() as conn:
            if conn is None:
                return AccessDecision(False, None, "store_missing")

            token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
            session = auth_logic.get_session(
                token,
                now=checked_at,
                readonly_existing=True,
            )
            # 현재 관리자는 cutover 전 임의 길이 legacy locator도 계속 검토할 수
            # 있다. 일반 손님에게 허용하는 새 locator 모양은 아래에서 32hex로
            # 엄격히 좁힌다. 단 공개 채널의 휴지통·차단은 관리자도 우회하지 않는다.
            if session is not None and session.is_admin:
                if dashboard_store.report_is_trashed(
                    conn, clean
                ) or dashboard_store.report_is_blocked(conn, clean):
                    return AccessDecision(False, None, "resource_revoked")
                return AccessDecision(True, AccessRole.ADMIN, "current_admin")

            if _LOCATOR_RE.fullmatch(clean) is None:
                return AccessDecision(False, None, "invalid_locator")

            if dashboard_store.report_is_trashed(
                conn, clean
            ) or dashboard_store.report_is_blocked(conn, clean):
                return AccessDecision(False, None, "resource_revoked")

            legacy = store.legacy_access_for(conn, locator=clean, now=checked_at)

            if session is not None and not session.is_admin:
                owns = _member_owns(
                    conn, identity_subject=session.subject, locator=clean
                )
                if owns:
                    if share_allow.is_allowed(conn, session.email):
                        return AccessDecision(
                            True, AccessRole.MEMBER, "member_owner"
                        )
                    return AccessDecision(False, None, "member_revoked")
                if (
                    legacy is not None
                    and legacy.audience == store.LEGACY_AUDIENCE_MEMBER
                    and legacy.actor_email_hash == store.email_hash(session.email)
                ):
                    if share_allow.is_allowed(conn, session.email):
                        _warn_legacy_access(locator=clean, audience="member")
                        return AccessDecision(
                            True, AccessRole.MEMBER, "legacy_member_email"
                        )
                    return AccessDecision(False, None, "member_revoked")

            raw_key = (
                request.cookies.get(KEY_COOKIE_NAME) or ""
            ).strip().lower()
            if _link_owns(conn, raw_key=raw_key, locator=clean):
                return AccessDecision(True, AccessRole.LINK, "link_owner")

            if store.public_grant_allows(
                conn,
                raw_token=request.cookies.get(constants.PUBLIC_GRANT_COOKIE_NAME),
                locator=clean,
                now=checked_at,
            ):
                return AccessDecision(True, AccessRole.PUBLIC, "public_grant")
            if (
                legacy is not None
                and legacy.audience == store.LEGACY_AUDIENCE_PUBLIC
            ):
                _warn_legacy_access(locator=clean, audience="public")
                return AccessDecision(
                    True, AccessRole.PUBLIC, "legacy_public_bearer"
                )
    # 권한 저장소의 새 실패 형태가 생겨도 공개 ID가 우연히 통과하거나 500으로
    # 내부 사정을 드러내면 안 된다. 이 경계에서는 모든 저장소 실패를 닫는다.
    except Exception as error:
        return AccessDecision(False, None, _store_failure_reason(error))
    return AccessDecision(False, None, "not_owner")
