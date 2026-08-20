"""관리자 권한·CSRF·상태 변경의 구조화 감사 로그."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from typing import Final

from fastapi import Request

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic


logger = logging.getLogger("security.admin_audit")
_SAFE_FIELD_RE: Final[re.Pattern[str]] = re.compile(r"[^a-zA-Z0-9_.:-]+")


def _safe_field(value: str, *, fallback: str, limit: int = 80) -> str:
    clean = _SAFE_FIELD_RE.sub("_", (value or "").strip())[:limit]
    return clean or fallback


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").strip().lower().encode("utf-8")).hexdigest()[:20]


def actor_id(request: Request) -> str:
    """원문 이메일·세션 토큰 없이 공급자 불변 subject의 actor 지문을 만든다."""
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    subject = auth_logic.current_subject(token) or ""
    return auth_logic.person_id_for_subject(subject) if subject else "anonymous"


def reviewer_id(request: Request) -> str:
    """PDF 승인에는 이메일 호환 세션이 아닌 불변 공급자 신원만 허용한다."""

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    subject = auth_logic.current_subject(token)
    if not auth_logic.is_approval_identity_subject(subject):
        return ""
    return auth_logic.person_id_for_subject(str(subject))


def target_id(kind: str, value: str = "") -> str:
    """비밀·원문 대상 대신 종류와 지문만 남긴다."""
    safe_kind = _safe_field(kind, fallback="none", limit=32)
    return f"{safe_kind}:{_digest(value)}" if value else safe_kind


def request_id(request: Request) -> str:
    """상관관계 ID는 bounded 문자만 허용해 줄바꿈 로그 주입을 막는다."""
    existing = getattr(request.state, "admin_audit_request_id", "")
    if existing:
        return str(existing)
    supplied = _safe_field(
        request.headers.get("x-request-id", ""), fallback="", limit=64
    )
    identifier = supplied or uuid.uuid4().hex
    request.state.admin_audit_request_id = identifier
    return identifier


def emit(
    request: Request,
    *,
    action: str,
    target: str,
    outcome: str,
    reason: str,
) -> dict[str, str]:
    """민감 원문 없이 한 줄 JSON 감사 이벤트를 기록하고 시험 가능한 값을 반환한다."""
    event = {
        "event_time": clock.iso_now_kst(),
        "request_id": request_id(request),
        "actor_id": actor_id(request),
        "action": _safe_field(action, fallback="unknown_action", limit=64),
        "target_id": _safe_field(target, fallback="none", limit=80),
        "outcome": _safe_field(outcome, fallback="unknown", limit=24),
        "reason_code": _safe_field(reason, fallback="none", limit=48),
    }
    logger.info("admin_audit %s", json.dumps(event, ensure_ascii=True, sort_keys=True))
    return event
