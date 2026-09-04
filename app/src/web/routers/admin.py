"""관리자 대시보드와 사용자·공유 링크 관리 경로."""

import base64
import datetime as dt
import logging
import os
import re
import secrets
import string
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.feedback_report import constants as feedback_constants
from src.features.feedback_report import logic as feedback_logic
from src.features.observability import admin_audit, admin_audit_store
from src.features.budget import constants as budget_constants
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.budget import spend_store
from src.features.budget import state_machine as budget_state_machine
from src.features.pipeline.demo import DemoPipeline
from src.features.report_delivery import constants as delivery_constants
from src.features.report_delivery import store as delivery_store
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import constants as share_constants
from src.features.sharelink import issue as share_issue
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.features.storage import sessions as session_store
from src.web import (
    deployment_mode,
    job_runtime,
    paid_runtime,
    report_delivery_adapter,
    report_publication,
    request_helpers,
    runtime,
)
from src.web.routers import feedback as feedback_router
from src.web.security import (
    COMPANY_MAX_CHARS,
    CSRF_TOKEN_MAX_CHARS,
    EMAIL_MAX_CHARS,
    JOB_MAX_CHARS,
    NOTE_MAX_CHARS,
    PHASE_MAX_CHARS,
    REFERENCE_MAX_CHARS,
    RUN_ID_MAX_CHARS,
)


router = APIRouter()
logger = logging.getLogger(__name__)
_KEY_ISSUE_ATTEMPTS = 5
_ADMIN_AUDIT_TABLE = admin_audit_store.TABLE_ADMIN_AUDIT_EVENTS
_AUDIT_SAFE_CHARS = frozenset(string.ascii_letters + string.digits + "_.:-")
#: 발급 화면에서 QR 그림을 내려받을 때 쓰는 파일 이름.
_ISSUED_QR_FILENAME = "company-analysis-link-qr.svg"
#: 총 수명 상한에 닿아 더 미룰 수 없을 때 보여줄 말. 내부 용어를 쓰지 않는다.
_LINK_EXTENSION_CAP_MESSAGE = (
    "이 링크는 더 미룰 수 없습니다. 새 링크를 발급해 주세요."
)
#: 링크 변경 이력에 적힌 종류를 관리자 화면 말로 바꾼다.
_ADJUSTMENT_KIND_LABELS = {
    share_store.ADJUSTMENT_KIND_EXPIRES: "만료일",
    share_store.ADJUSTMENT_KIND_DAILY_BUDGET: "하루 한도",
    share_store.ADJUSTMENT_KIND_TOTAL_BUDGET: "누적 한도",
}
#: 회사 이름 비교에서 떼어 내는 법인격 표기. 같은 회사를 다르게 적은 것뿐이다.
_COMPANY_LEGAL_FORMS = (
    "주식회사",
    "유한책임회사",
    "유한회사",
    "합자회사",
    "합명회사",
    "(주)",
    "(유)",
    # 한 글자로 합쳐 쓴 표기(U+321C·U+3232). casefold로는 안 풀려 따로 적는다.
    "㈜",
    "㈲",
)
_COMPANY_LEGAL_FORM_PATTERN = "|".join(
    re.escape(legal_form) for legal_form in _COMPANY_LEGAL_FORMS
)
#: ★ 법인격 표기는 «이름의 맨 앞이나 맨 뒤»에 붙을 때만 떼어 낸다.
#: 경계 없이 지우면 「질주식회사원」이 「질원」이 되어 고유번호가 다른 별개
#: 회사가 같은 이름으로 통과한다. 첫 결속에는 대조할 고유번호가 없어서
#: 이 이름 검사가 유일한 방어선이다.
_COMPANY_LEGAL_PREFIX_RE = re.compile(rf"^(?:{_COMPANY_LEGAL_FORM_PATTERN})+")
_COMPANY_LEGAL_SUFFIX_RE = re.compile(rf"(?:{_COMPANY_LEGAL_FORM_PATTERN})+$")


class _AccessDataUnavailable(RuntimeError):
    """접근 목록이나 비용 원장의 정본을 확인할 수 없음."""


class _AdminStateUnchanged(RuntimeError):
    """관리자 변경 요청 뒤 저장 행이 기대한 상태로 바뀌지 않음."""


def _admin_response(request: Request, response):
    """관리자 응답은 캐시하지 않고 감사 상관관계 ID를 함께 보낸다."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Request-ID"] = admin_audit.request_id(request)
    return response


def _audit_change(
    request: Request,
    *,
    action: str,
    target: str,
    outcome: str,
    reason: str,
) -> None:
    """상태 변경 감사 이벤트. 호출 실패는 쓰기 transaction을 실패시켜야 한다."""
    admin_audit.emit(
        request,
        action=action,
        target=target,
        outcome=outcome,
        reason=reason,
    )


def _safe_audit_field(value: str, *, max_chars: int) -> str:
    """이미 비식별화된 감사 필드만 DB 정본에 넣는다."""
    clean = str(value or "")
    if (
        not clean
        or len(clean) > max_chars
        or any(char not in _AUDIT_SAFE_CHARS for char in clean)
    ):
        raise ValueError("invalid sanitized audit field")
    return clean


def _kst_timestamp_label(value: str) -> str:
    """저장 시각을 관리자 화면용 KST 분 단위 라벨로 바꾼다."""
    if not isinstance(value, str) or not value.strip():
        return "—"
    try:
        raw = value.strip()
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=clock.KST)
        local = parsed.astimezone(clock.KST)
    except (OverflowError, TypeError, ValueError):
        return "확인 불가"
    return f"{local:%Y-%m-%d %H:%M} (한국시간)"


def _svg_data_url(svg_text: str) -> str:
    """SVG 글자를 «내려받기 단추»에 바로 걸 수 있는 data 주소로 바꾼다.

    ★ 서버에 따로 내려받기 경로를 두지 않는 이유 — 그 경로는 원문 열쇠를 한 번
      더 받아야 하고, 그만큼 원문이 지나가는 자리가 늘어난다. 이미 이 화면에
      그려 둔 그림을 그대로 파일로 저장하면 새로 노출되는 곳이 없다.
    """
    encoded = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _link_expiry_date_label(created_at: str, *, expires_at: str = "") -> str:
    """이 링크가 닫히는 날을 관리자 화면용으로 표시한다.

    Args:
        created_at: 발급 시각.
        expires_at: 저장된 만료일. **있으면 이 값이 우선**이다 — 관리자가
            미룬 날짜를 화면이 안 보여 주면 연장이 됐는지 알 수 없다.
    """

    expires = share_logic.expiry_date_of(created_at, expires_at=expires_at)
    if expires is None:
        return "확인 불가"
    return f"{expires:%Y-%m-%d} (한국시간 00:00부터 닫힘)"


def _link_days_left(created_at: str, *, expires_at: str = "") -> int | None:
    """오늘 기준 남은 날 수. 이미 닫혔으면 0, 계산 불가면 ``None``.

    ★ 「며칠 남았나」를 안 보여 주면 관리자는 만료일 문자열을 보고 매번 날짜를
      세야 한다. 연장 판단에 필요한 값이라 화면에 같이 낸다.
    """

    expires = share_logic.expiry_date_of(created_at, expires_at=expires_at)
    if expires is None:
        return None
    return max(0, (expires - clock.today_kst()).days)


def _queue_committed_change(
    conn,
    request: Request,
    *,
    action: str,
    target: str,
    reason: str,
) -> None:
    """상태 변경과 같은 SQLite transaction에 append-only 성공 사건을 넣는다.

    외부 logger는 transaction과 원자화할 수 없다. 따라서 이 행이 성공 감사의
    정본이며, commit 뒤 logger로 보내는 것은 best-effort mirror일 뿐이다.
    """
    admin_audit_store.append_success(
        conn,
        event_time=clock.iso_now_kst(),
        request_id=_safe_audit_field(admin_audit.request_id(request), max_chars=64),
        actor_id=_safe_audit_field(admin_audit.actor_id(request), max_chars=80),
        action=_safe_audit_field(action, max_chars=64),
        target_id=_safe_audit_field(target, max_chars=80),
        reason_code=_safe_audit_field(reason, max_chars=48),
    )


def _mirror_committed_change(
    request: Request, *, action: str, target: str, reason: str
) -> None:
    """commit된 정본 사건을 외부 logger로 복제한다. 실패해도 DB 정본은 남는다."""
    try:
        _audit_change(
            request,
            action=action,
            target=target,
            outcome="success",
            reason=reason,
        )
    except Exception:  # noqa: BLE001 — 정본은 이미 원자 commit됐고 원문은 남기지 않는다
        logger.error("관리자 변경 감사 미러를 남기지 못했습니다")


def _audit_failed_change(
    request: Request, *, action: str, target: str, reason: str
) -> None:
    """이미 fail-closed된 요청의 실패 이벤트를 민감 원문 없이 남긴다."""
    try:
        _audit_change(
            request,
            action=action,
            target=target,
            outcome="failed",
            reason=reason,
        )
    except Exception:  # noqa: BLE001 — 실패 응답은 유지하고 원문 예외는 기록하지 않는다
        logger.error("관리자 변경 실패 감사기록을 남기지 못했습니다")


# ══════════════════════════════════════════════════════════
# 위험 동작의 확인 단계 — 지우거나 바꾸는 일은 두 걸음으로 나눈다
# ══════════════════════════════════════════════════════════

#: 확인 화면이 준 «1회용 확인 표»가 살아 있는 시간(분).
#: ★ 짧게 두는 이유 — 확인 화면을 열어 둔 채 잊었다가 한참 뒤에 누르면 그 사이에
#:   대상이 달라졌을 수 있다. 표가 만료되면 화면을 다시 열어 대상을 다시 본다.
CONFIRM_TOKEN_TTL_MINUTES = 10
#: 이유를 받는 위험 동작에서 요구하는 최소 글자 수. 이유는 필수이고 20자 이상이다.
DANGEROUS_ACTION_REASON_MIN_CHARS = 20
#: 서버가 동시에 들고 있는 확인 표 상한. 넘치면 오래된 것부터 버려 메모리가
#: 무한히 늘지 않게 한다. 비상 화면은 목록 줄마다 한 장씩 발급해 여유를 둔다.
_CONFIRM_TOKEN_MAX_PENDING = 512
#: 확인 표의 바이트 수. 16바이트면 hex로 32자리다.
_CONFIRM_TOKEN_BYTES = 16
#: 확인을 안 거친 요청을 감사에 남길 때 쓰는 사유 코드.
#: ★ 감사행 `reason_code`는 ASCII만 받는다. 한국어를 넣으면 그 요청의
#:   감사 기록 자체가 실패한다.
_CONFIRM_MISSING_REASON = "confirm_missing"
#: 확인을 안 거친 요청에 돌려주는 화면 문구. 내부 용어를 쓰지 않는다.
_CONFIRM_REQUIRED_MESSAGE = (
    "확인 화면을 거친 뒤에만 실행할 수 있습니다. "
    "무엇을 바꾸는지 다시 확인하도록 화면을 새로 열어 주세요. "
    "이번 요청으로 바뀐 것은 없습니다."
)


@dataclass(frozen=True)
class _PendingConfirmation:
    """확인 화면이 발급한 표 한 장. 누가·무엇을·언제까지인지만 담는다."""

    actor_id: str
    action: str
    target: str
    expires_at: dt.datetime


#: 발급했지만 아직 안 쓴 확인 표. 프로세스가 다시 뜨면 사라지는데, 그때는
#: «막는» 쪽으로 틀린다(확인 화면을 다시 열게 된다) — 안전한 방향이다.
#: 배포 계약이 worker 1·instance 1을 못 박아 표가 갈라지지 않는다.
_CONFIRM_TOKENS: dict[str, _PendingConfirmation] = {}


def _prune_confirm_tokens(now: dt.datetime) -> None:
    """기한이 지난 표를 버리고, 그래도 많으면 오래 묵은 것부터 버린다."""

    for token, pending in list(_CONFIRM_TOKENS.items()):
        if pending.expires_at <= now:
            del _CONFIRM_TOKENS[token]
    while len(_CONFIRM_TOKENS) > _CONFIRM_TOKEN_MAX_PENDING:
        _CONFIRM_TOKENS.pop(next(iter(_CONFIRM_TOKENS)))


def issue_confirm_token(request: Request, *, action: str, target: str) -> str:
    """확인 화면이 «이 사람·이 동작·이 대상»에만 쓸 수 있는 표를 발급한다."""

    now = clock.now_kst()
    _prune_confirm_tokens(now)
    token = secrets.token_hex(_CONFIRM_TOKEN_BYTES)
    _CONFIRM_TOKENS[token] = _PendingConfirmation(
        actor_id=admin_audit.actor_id(request),
        action=action,
        target=target,
        expires_at=now + dt.timedelta(minutes=CONFIRM_TOKEN_TTL_MINUTES),
    )
    return token


def _confirmation_accepted(
    request: Request, token: str, *, action: str, target: str
) -> bool:
    """확인 표를 «쓰고 버린다». 대상·동작·사람이 모두 맞을 때만 참이다.

    ★ 맞지 않아도 표는 버린다 — 한 장으로 여러 대상을 시험해 볼 수 없게 한다.
    """

    now = clock.now_kst()
    _prune_confirm_tokens(now)
    pending = _CONFIRM_TOKENS.pop(str(token or "").strip(), None)
    if pending is None:
        return False
    return (
        pending.action == action
        and pending.target == target
        and pending.actor_id == admin_audit.actor_id(request)
        and pending.expires_at > now
    )


def _audit_denied_change(
    request: Request, *, action: str, target: str, reason: str
) -> None:
    """실행하지 «않은» 요청도 감사에 남긴다.

    ★ 성공 정본 표는 `outcome='success'`만 받는다(`admin_audit_store.py`).
      그래서 거절은 정해진 대로 로그 미러로만 남는다.
    """

    try:
        _audit_change(
            request,
            action=action,
            target=target,
            outcome="denied",
            reason=reason,
        )
    except Exception:  # noqa: BLE001 — 거절 응답은 유지하고 원문 예외는 안 남긴다
        logger.error("관리자 변경 거절 감사기록을 남기지 못했습니다")


def _confirmation_required(
    request: Request, *, action: str, target: str
) -> HTMLResponse:
    """확인을 안 거친 요청을 «실행 0»으로 되돌리고 그 사실을 감사에 남긴다."""

    _audit_denied_change(
        request, action=action, target=target, reason=_CONFIRM_MISSING_REASON
    )
    return _admin_response(
        request, HTMLResponse(_CONFIRM_REQUIRED_MESSAGE, status_code=400)
    )


def _validated_action_reason(raw: str) -> tuple[str, str]:
    """위험 동작의 이유를 다듬고, 너무 짧으면 왜 거절인지 함께 돌려준다.

    Returns:
        (다듬은 이유, 오류 문구). 오류가 있으면 이유는 빈 글자다.
    """

    reason = str(raw or "").strip()
    if not reason:
        return "", "왜 바꾸는지 적어주세요."
    if len(reason) < DANGEROUS_ACTION_REASON_MIN_CHARS:
        return "", (
            f"왜 바꾸는지 {DANGEROUS_ACTION_REASON_MIN_CHARS}자 이상으로 적어주세요. "
            "나중에 같은 판단을 다시 하려면 이유가 남아 있어야 합니다."
        )
    return reason, ""


def _member_default_success_limit() -> int:
    """한도를 따로 안 정한 친구가 쓰는 하루 성공 건수."""

    return dashboard_store.MEMBER_DAILY_SUCCESS_LIMIT


def _member_default_budget_krw() -> float:
    """한도를 따로 안 정한 친구가 쓰는 하루 비용 상한(원)."""

    return share_tracks.budget_of(share_tracks.Track.MEMBER)


def _krw_label(amount_krw: float) -> str:
    """확인 화면에 쓰는 금액 표기. 회원 화면과 같은 모양이어야 한다."""

    return f"{amount_krw:,.0f}원"


def _linked_report_state(conn, link: share_store.ShareLink) -> str:
    """연결 보고서의 현재 공개 가능 상태를 링크 표시·진입 시점에 다시 확인한다."""
    if not link.report_id:
        return "none"
    report = report_store.load(conn, link.report_id)
    if report is None:
        return "missing"
    if not report_publication.report_is_published_or_legacy(conn, link.report_id):
        return "unavailable"
    return "expired" if job_runtime._link_expired(report) else "active"


def _assert_budget_store_healthy() -> None:
    with paid_runtime._PAID_PHASE_LOCK:
        if not paid_runtime._BUDGET_STORE_HEALTHY:
            raise _AccessDataUnavailable("budget_health")


def _paid_research_status() -> tuple[bool, str]:
    """관리자가 유료 조사 차단 여부와 사람이 풀어야 하는 이유를 확인하게 한다.

    판정 자체는 `paid_runtime` 이 갖는다 — 관리자 첫 화면(`/admin`)도
    «같은» 판정을 써야 두 화면이 어긋나지 않는다.
    """

    return paid_runtime.paid_research_block()


def _assert_access_write_ready(conn) -> None:
    """예산 노출을 바꾸기 전 목록·원장 정본을 모두 읽을 수 있어야 한다."""
    _assert_budget_store_healthy()
    share_store.list_all(conn)
    share_allow.list_all(conn)
    spend_store.ensure_schema(conn)
    if budget_state_machine.cutover_applied(conn):
        # 전환 뒤 입장 판단의 정본은 attempt 원장이다. 폐기 예정인 legacy 표를
        # 읽을 수 있다는 사실로 새 원장이 정상이라고 오판하지 않는다.
        budget_state_machine.load_day_exposures(conn, day=clock.today_kst())
    else:
        spend_store.load_day(conn, clock.today_kst())
    spend_store.load_overrun_day(conn, clock.today_kst())
    _assert_budget_store_healthy()


def _share_base_url(request: Request) -> str:
    """Host 헤더 대신 설정된 공개 origin, 또는 엄격한 로컬 origin만 쓴다."""
    local_origin = ""
    if isinstance(runtime._PIPELINE, DemoPipeline):
        local_origin = share_issue.safe_local_base_url(str(request.url))
        if local_origin:
            # 로컬 실행기가 부모 셸의 배포 설정을 상속했더라도, 격리 데모 링크가
            # 실제 공개 서비스 주소인 것처럼 보이지 않게 현재 loopback을 우선한다.
            return local_origin
    configured = os.environ.get(
        share_constants.ENV_PUBLIC_BASE_URL, ""
    ).strip()
    render_origin = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if configured or render_origin:
        return share_issue.canonical_public_base_url(configured or render_origin)
    return local_origin


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    """관리자 첫 화면. 권한은 서버가 매번 다시 판정한다."""
    # ``/admin``은 승인된 운영 대시보드의 정식 진입점이다. 기존 초대·LINK 관리
    # 기능은 `/members`, `/links`에서 같은 저장소를 계속 사용한다.
    from src.web.routers import dashboard  # noqa: PLC0415

    return await dashboard.render_admin_home(request)


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """이전 품질 대시보드 주소의 안전한 호환 리다이렉트."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _admin_response(request, RedirectResponse("/admin", status_code=303))


@router.get("/admin/frame", response_class=HTMLResponse)
async def admin_frame(request: Request):
    """승인 전 프레임 주소의 호환 리다이렉트."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _admin_response(request, RedirectResponse("/admin", status_code=303))


def _access_context(request: Request, *, today: dt.date, **kwargs) -> dict:
    """초대·링크 관리 화면이 쓸 값을 모은다."""
    report_states: dict[str, str] = {}
    # ★ 원장 검사보다 «먼저» 읽는다 — 원장이 나빠도 무엇이 걸렸는지는 보여야 한다.
    unresolved_spend, unresolved_spend_available = paid_runtime.list_unresolved_spend(today)
    _assert_budget_store_healthy()

    try:
        with storage_db.connect() as conn:
            links = share_store.list_all(conn)
            members = share_allow.list_all(conn)
            spend_store.ensure_schema(conn)
            if budget_state_machine.cutover_applied(conn):
                exposure = budget_state_machine.load_day_exposures(conn, day=today)
                spent_today = exposure.total.known_cost_krw
                liability_today = exposure.total.liability_krw
                reservation_today = exposure.total.reservation_krw
            else:
                spend = spend_store.load_day(conn, today)
                spent_today = spend.total_krw
                liability_today = 0.0
                reservation_today = 0.0
            overrun = spend_store.load_overrun_day(conn, today)
            for link in links:
                try:
                    report_states[link.key] = _linked_report_state(conn, link)
                except Exception:  # noqa: BLE001 — 한 보고서 손상으로 링크 목록을 숨기지 않는다
                    logger.error("연결 보고서 상태를 읽지 못했습니다")
                    report_states[link.key] = "unavailable"
    except Exception as error:  # noqa: BLE001 — 알 수 없는 값을 0·빈 목록으로 만들지 않는다
        logger.error("초대·링크 또는 비용 원장을 읽지 못했습니다")
        raise _AccessDataUnavailable("access_store") from error

    _assert_budget_store_healthy()

    expired_link_keys = {
        link.key
        for link in links
        if share_logic.link_expired(link)
    }
    revoked_link_keys = {link.key for link in links if link.is_revoked}
    active_link_count = len(links) - len(expired_link_keys | revoked_link_keys)
    link_created_at_labels = {
        link.key: _kst_timestamp_label(link.created_at) for link in links
    }
    link_first_opened_at_labels = {
        link.key: _kst_timestamp_label(link.first_opened_at) for link in links
    }
    link_last_opened_at_labels = {
        link.key: _kst_timestamp_label(link.last_opened_at) for link in links
    }
    link_expiry_date_labels = {
        link.key: _link_expiry_date_label(
            link.created_at, expires_at=link.expires_at
        )
        for link in links
    }
    member_invited_at_labels = {
        member.email: _kst_timestamp_label(member.invited_at)
        for member in members
    }
    # LINK·MEMBER는 각각 독립 통장이므로 활성 개수만큼 입장 상한이 생긴다.
    # MEMBER는 이 금액 제한에 더해 KST 성공 보고서 건수 제한도 함께 적용한다.
    # ★ 친구마다 하루 한도가 다를 수 있다. 「인원 × 기본값」으로
    #   세면 한 명만 올려도 이 합계가 실제 비용 노출보다 «작게» 나온다. 이 숫자를
    #   보고 링크·친구를 더 늘려도 되는지 판단하므로, 작게 보이는 쪽이 위험하다.
    member_default_budget_krw = (
        share_tracks.budget_of(share_tracks.Track.MEMBER) or 0.0
    )
    member_budget_total_krw = sum(
        (
            member_default_budget_krw
            if member.daily_budget_krw is None
            else float(member.daily_budget_krw)
        )
        for member in members
    )
    # 아무도 안 바꿨으면 「N명 × 기본값」이 여전히 참이고 읽기도 쉽다.
    # 한 명이라도 다르면 그 곱셈은 거짓이 되므로 합계로만 말한다.
    member_budget_customized = any(
        member.daily_budget_krw is not None for member in members
    )
    configured_stop_threshold = (
        active_link_count * (share_tracks.budget_of(share_tracks.Track.LINK) or 0.0)
        + member_budget_total_krw
        + (share_tracks.budget_of(share_tracks.Track.ADMIN) or 0.0)
    )
    paid_research_closed, paid_research_closed_reason = _paid_research_status()
    return request_helpers._ctx(
        request,
        links=links,
        active_link_count=active_link_count,
        expired_link_keys=expired_link_keys,
        revoked_link_keys=revoked_link_keys,
        report_states=report_states,
        link_created_at_labels=link_created_at_labels,
        link_first_opened_at_labels=link_first_opened_at_labels,
        link_last_opened_at_labels=link_last_opened_at_labels,
        link_expiry_date_labels=link_expiry_date_labels,
        members=members,
        member_invited_at_labels=member_invited_at_labels,
        member_budget_total_krw=member_budget_total_krw,
        member_budget_customized=member_budget_customized,
        member_default_budget_label=f"{member_default_budget_krw:,.0f}원",
        member_default_success_limit=dashboard_store.MEMBER_DAILY_SUCCESS_LIMIT,
        spent_today=spent_today,
        liability_today=liability_today,
        reservation_today=reservation_today,
        configured_stop_threshold_krw=configured_stop_threshold,
        actual_over_threshold_krw=max(
            0.0, spent_today - configured_stop_threshold
        ),
        estimate_overrun_count=overrun.count,
        estimate_overrun_krw=overrun.excess_krw,
        business_day_label=clock.business_day_label(today),
        access_data_available=True,
        paid_research_closed=paid_research_closed,
        paid_research_closed_reason=paid_research_closed_reason,
        unresolved_spend=unresolved_spend,
        unresolved_spend_available=unresolved_spend_available,
        spend_phase_labels=budget_constants.SPEND_PHASE_LABELS,
        job_max_chars=JOB_MAX_CHARS,
        note_max_chars=NOTE_MAX_CHARS,
        reference_max_chars=REFERENCE_MAX_CHARS,
        **kwargs,
    )


#: 정보 구조 재배치 뒤 초대·링크·회원·비용은 화면 셋으로 나뉜다.
#: 같은 `_access_context`를 세 화면이 나눠 쓰고, 자료를 못 읽으면 셋 다 같은
#: 축소 화면으로 떨어진다.
ACCESS_LINKS_TEMPLATE = "admin_links.html"
ACCESS_MEMBERS_TEMPLATE = "admin_members.html"
ACCESS_COSTS_TEMPLATE = "admin_costs.html"
ACCESS_UNAVAILABLE_TEMPLATE = "admin_access_unavailable.html"


def _frame_base_context(request: Request, template: str) -> dict:
    """화면 틀(메뉴·목록·통계)이 쓰는 대시보드 쪽 값.

    ★ 비용 화면은 일부러 비운다 — 그 화면의 계약은 「원장 기준일을 요청당 한 번만
      읽는다」이고(`test_비용카드는_요청에서_한번_캡처한_KST_원장기준일을_명시한다`),
      대시보드 문맥은 `clock.today_kst()`를 더 부른다. 그래서 비용 화면은 이
      실패축 자체가 없다.

    ★ 못 읽으면 «조용히 빈 값으로» 넘기지 않고 축소 화면으로 보낸다
      (`_AccessDataUnavailable`). 회귀 정정 —
      앞 판은 `except Exception: return {}`로 삼켰는데,
      ① 회원 화면은 `dashboard_company_labels` 같은 값을 `is defined` 가드 없이
         써서 렌더가 `jinja2.exceptions.UndefinedError`로 **500**이 났고,
      ② 링크 화면은 그려지긴 해서 못 읽은 「새 접속」이 0건처럼 보였다.
      base(0acf798)의 옛 `members_page`·`links_page`는 둘 다 503으로 닫았다.
      템플릿에 가드를 다는 것은 고침이 아니다 — 실패를 빈 화면으로 숨긴다.
    """

    from src.web.routers import dashboard  # noqa: PLC0415

    try:
        if template == ACCESS_LINKS_TEMPLATE:
            return dashboard.link_page_context(request)
        if template == ACCESS_MEMBERS_TEMPLATE:
            return dashboard.member_page_context(request)
    except Exception as error:  # noqa: BLE001 — 저장소 속사정은 화면에 내지 않는다
        logger.error("관리자 화면 틀 정보를 읽지 못했습니다")
        raise _AccessDataUnavailable("frame_context") from error
    return {}


def _access_page(
    request: Request,
    *,
    status_code: int = 200,
    template: str = ACCESS_LINKS_TEMPLATE,
    **context,
) -> HTMLResponse:
    today = clock.today_kst()
    try:
        page_context = _access_context(request, today=today, **context)
        # ★ 틀 읽기도 «같은 try 안»에 둔다. 밖에 두면 여기서 난 실패가
        #   축소 화면을 못 거치고 그대로 렌더로 흘러가 500이 된다.
        merged = _frame_base_context(request, template)
    except _AccessDataUnavailable:
        status_code = 503
        template = ACCESS_UNAVAILABLE_TEMPLATE
        # 축소 화면은 대시보드 틀 값을 하나도 쓰지 않는다.
        merged = {}
        # ★ 원장을 못 읽어도 «무엇이 걸렸는지»는 따로 읽어 보여 준다 —
        #   대사하라면서 대사할 대상을 안 보여 주면 관리자는 아무것도 못 한다.
        축소_미확정, 축소_미확정_읽었나 = paid_runtime.list_unresolved_spend(today)
        revocation_links = []
        revocation_members = []
        revocation_data_available = False
        try:
            # 비용 수치가 불명이어도 권한을 줄이는 비상 철회 목록은 별도로 읽는다.
            # 둘 중 하나라도 불명확하면 잘못된 목록을 정상처럼 보이지 않는다.
            with storage_db.connect() as conn:
                revocation_links = [
                    link for link in share_store.list_all(conn) if not link.is_revoked
                ]
                revocation_members = share_allow.list_all(conn)
            revocation_data_available = True
        except Exception:  # noqa: BLE001 — 축소 화면도 추정값을 표시하지 않는다
            logger.error("비상 철회 목록을 확인하지 못했습니다")
        # ★ 이 화면만 «한 단계»로 둔다 — 비용 원장이 막힌 상태에서 확인 화면을
        #   한 번 더 요구하면, 새고 있는 링크를 못 닫는 쪽으로 틀린다. 대신 이
        #   화면이 대상 목록을 그대로 다시 보여 주므로 확인 화면 역할을 겸한다.
        revocation_link_tokens = {
            link.key_hash: issue_confirm_token(
                request,
                action="admin.link.revoke",
                target=admin_audit.target_id("link", link.key_hash),
            )
            for link in revocation_links
        }
        revocation_member_tokens = {
            member.email: issue_confirm_token(
                request,
                action="admin.member.revoke",
                target=admin_audit.target_id("member", member.email),
            )
            for member in revocation_members
        }
        page_context = request_helpers._ctx(
            request,
            links=[],
            active_link_count=None,
            expired_link_keys=set(),
            revoked_link_keys=set(),
            report_states={},
            link_created_at_labels={},
            link_first_opened_at_labels={},
            link_last_opened_at_labels={},
            link_expiry_date_labels={},
            members=[],
            member_invited_at_labels={},
            spent_today=None,
            configured_stop_threshold_krw=None,
            actual_over_threshold_krw=None,
            estimate_overrun_count=None,
            estimate_overrun_krw=None,
            business_day_label=clock.business_day_label(today),
            access_data_available=False,
            revocation_data_available=revocation_data_available,
            revocation_links=revocation_links,
            revocation_members=revocation_members,
            revocation_link_tokens=revocation_link_tokens,
            revocation_member_tokens=revocation_member_tokens,
            unresolved_spend=축소_미확정,
            unresolved_spend_available=축소_미확정_읽었나,
            spend_phase_labels=budget_constants.SPEND_PHASE_LABELS,
            paid_research_closed=True,
            paid_research_closed_reason=(
                "비용 기록을 확인할 수 없어 유료 조사를 닫았습니다. "
                "관리자가 원장을 확인해야 다시 열립니다."
            ),
            job_max_chars=JOB_MAX_CHARS,
            note_max_chars=NOTE_MAX_CHARS,
            reference_max_chars=REFERENCE_MAX_CHARS,
            access_error_title=context.get(
                "access_error_title", "관리 정보를 확인할 수 없습니다."
            ),
            access_error=context.get(
                "access_error",
                "접근 목록과 비용 원장을 확인한 뒤 다시 시도해주세요.",
            ),
        )
    merged.update(page_context)
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name=template,
        context=merged,
        status_code=status_code,
    )
    return _admin_response(request, response)


def render_link_admin_page(request: Request) -> HTMLResponse:
    """초대 링크 화면(정보 구조 ②). `/admin/links` 라우트가 부른다."""

    return _access_page(request, template=ACCESS_LINKS_TEMPLATE)


def render_member_admin_page(request: Request) -> HTMLResponse:
    """회원 화면(정보 구조 ③). `/admin/members` 라우트가 부른다."""

    return _access_page(request, template=ACCESS_MEMBERS_TEMPLATE)


def _normalized_company_name(value: str) -> str:
    """회사 표시명을 비교용으로 다듬는다.

    ★ 「(주)진영」과 「진영」은 같은 회사다. 폼에 손으로 적는 이름은 법인격
      표기·띄어쓰기가 매번 달라서 글자 그대로 비교하면 «같은 회사인데 막히는»
      쪽으로 자주 틀린다. 이름이 정말 같은 «동명 회사»는 고유번호로 갈라낸다.

    ★ 법인격 표기는 **맨 앞·맨 뒤에서만** 뗀다. 이름 가운데 우연히 같은 글자가
      들어간 상호(예: 「질주식회사원」)를 다른 이름(「질원」)으로 바꿔 버리면
      별개 회사가 서로 통과한다.
    """
    raw = "".join(str(value or "").split()).casefold()
    if not raw:
        return ""
    stripped = _COMPANY_LEGAL_PREFIX_RE.sub("", raw)
    stripped = _COMPANY_LEGAL_SUFFIX_RE.sub("", stripped)
    # 법인격 표기만으로 된 이름은 통째로 사라지므로 그때는 원래 글자를 쓴다.
    return stripped or raw


def _report_company_id(conn, report_id: str, report=None) -> str | None:
    """이 보고서의 회사 고유번호. **저장 표의 열을 먼저** 읽는다.

    Args:
        conn: 열린 DB 연결.
        report_id: 볼 보고서 번호.
        report: 이미 되살려 둔 본문이 있으면 다시 안 읽으려고 받는다.

    Returns:
        고유번호. 열도 본문도 비었으면 빈 문자열.
        **읽지 못했으면 `None`** — 「확인 못 했다」와 「없다」는 다른 값이다.

    ★ 열을 먼저 보는 이유 — 본문(`payload_json`)의 `company_id`는 출고 상태가
      FULL일 때만 채워진다(`pipeline/real.py:3519`). 안전 확인 중에 나간 옛
      저장본은 본문이 비어 있어, 본문만 보면 **이름이 같은 다른 법인을 못 가른다**.
      저장 표의 `corp_id` 열은 출고 상태와 무관하게 저장 경로가
      항상 채운다(`storage/cache.py:398`).
    ★ 본문 값은 폴백으로 남긴다 — 열이 없던 시절에 다른 길로 저장된 행이 있을 수
      있고, 값이 있는 쪽을 쓰는 편이 대조를 더 많이 해 준다.
    ★ 열에서 값을 얻었더라도 **본문 읽기를 건너뛰지 않는다.** 「결속 보고서를 읽지
      못하면 연결하지 않는다」는 기존 계약(`test_admin_access.py`
      `test_결속_보고서를_읽지_못하면_연결을_거부한다`)을 이 변경으로 느슨하게
      만들지 않기 위해서다. 바뀌는 것은 **대조에 쓰는 값**뿐이고, 「확인 못 하면
      거부」라는 경계는 그대로다.
    """
    try:
        return report_store.resolve_company_id(conn, report_id, report)
    except Exception:  # noqa: BLE001 — 확인 실패는 통과가 아니라 거부로 넘긴다
        logger.error("보고서의 회사 고유번호를 읽지 못했습니다")
        return None


def _link_company_id(conn, link) -> str | None:
    """이 링크가 지금 가리키는 회사의 고유번호. 연결된 보고서에서만 읽는다.

    ★ `share_links` 표에는 고유번호 열이 없다 — 링크와 보고서 두 곳에 같은 값을
      두면 결속을 바꿀 때 어긋나기 때문이다. 그래서 이미 묶여 있는 보고서의
      값을 그 링크의 회사 신원으로 삼는다.

    Returns:
        고유번호. 묶인 보고서가 없거나 그 보고서에 고유번호가 없으면 빈 문자열.
        **읽지 못했으면 `None`** — 「확인 못 했다」와 「없다」는 다른 값이다.
        같은 값으로 뭉개면 읽기 실패가 조용히 «검사 통과»가 된다(fail-open).
    """
    return _report_company_id(conn, str(getattr(link, "report_id", "") or ""))


def _report_company_mismatch(
    report,
    *,
    expected_company: str,
    expected_company_id: str | None,
    report_company_id: str | None = None,
) -> str:
    """링크의 회사와 보고서의 회사가 다르면 화면에 보여줄 이유를 만든다.

    빈 문자열이면 같은 회사라는 뜻이다. 링크에 회사 꼬리표가 없으면(빈 값)
    비교할 기준이 없으므로 막지 않는다.

    Args:
        report_company_id: 묶으려는 보고서의 고유번호. 본문이 아니라 저장 표의
            열을 먼저 읽은 값이다(`_report_company_id`). 생략하면 본문 값을 쓴다 —
            **옛 저장본은 본문이 비어 있어 대조가 헐거워지므로 되도록 넘긴다.**
    """
    if expected_company_id is None or report_company_id is None:
        # ★ 대조에 쓸 값을 못 읽었으면 «통과»가 아니라 거부다. 여기서 이름
        #   검사로만 되돌아가면 동명 다른 법인이 그대로 들어온다.
        return "보고서 정보를 확인할 수 없어 연결하지 않았습니다."
    link_company = str(expected_company or "").strip()
    if not link_company:
        return ""
    report_company = str(getattr(report, "company", "") or "").strip()
    if _normalized_company_name(report_company) != _normalized_company_name(
        link_company
    ):
        return (
            f"이 보고서는 다른 회사({report_company})의 것입니다. "
            f"이 링크는 {link_company} 지원용으로 발급됐습니다."
        )
    candidate_company_id = str(
        report_company_id
        if report_company_id is not None
        else getattr(report, "company_id", "")
        or ""
    ).strip()
    link_company_id = str(expected_company_id or "").strip()
    if (
        link_company_id
        and candidate_company_id
        and link_company_id != candidate_company_id
    ):
        return (
            f"이름은 같지만 다른 법인의 보고서입니다"
            f"({report_company}, 고유번호 {candidate_company_id}). "
            f"이 링크에 연결된 회사의 고유번호는 {link_company_id}입니다."
        )
    return ""


def _validated_report_id(
    conn,
    reference: str,
    *,
    expected_company: str = "",
    expected_company_id: str | None = "",
) -> tuple[str, str]:
    """결과 참조가 저장돼 있고 아직 열리는 «이 회사의» 보고서인지 확인한다.

    ★ 회사 대조를 서버에서 하는 이유 — 관리자가 화면의 회사명을 눈으로 거르는
      것은 방어가 아니다. 한 번만 틀려도 받은 사람은 엉뚱한 회사 보고서를 본다.

    ★ 참조가 비어 있으면(연결 해제) 대조 없이 통과시킨다. 연결을 «푸는» 쪽은
      안전한 방향이라 확인 실패로 막을 이유가 없다.
    """
    if not reference.strip():
        return "", ""
    report_id = share_logic.report_id_from_reference(reference)
    if not report_id:
        return "", "결과 화면 주소 또는 32자리 보고서 ID를 확인해주세요."
    report = report_store.load(conn, report_id)
    if report is None:
        return "", "이 데모 저장소에서 해당 보고서를 찾을 수 없습니다."
    if not report_publication.report_is_published_or_legacy(conn, report_id):
        return "", "자동출고가 완료되지 않은 임시 보고서는 연결할 수 없습니다."
    if job_runtime._link_expired(report):
        return "", "공유 기간이 지난 보고서입니다. 새 보고서를 만든 뒤 연결해주세요."
    company_mismatch = _report_company_mismatch(
        report,
        expected_company=expected_company,
        expected_company_id=expected_company_id,
        report_company_id=_report_company_id(conn, report_id, report),
    )
    if company_mismatch:
        return "", company_mismatch
    return report_id, ""


@router.get("/admin/access", response_class=HTMLResponse)
async def admin_access(request: Request):
    """초대·링크·비용이 한 화면에 섞여 있던 옛 주소의 호환 리다이렉트.

    ★ 이 화면은 링크(`/admin/links`)·회원(`/admin/members`)·
      비용(`/admin/costs`) 셋으로 나뉘었다. **주소는 지우지 않는다** —
      이미 뿌린 주소가 깨지지 않아야 한다. 도착지는 링크 화면이다.
    ★ 권한 판정을 건너뛰고 리다이렉트하지 않는다. 거절도 감사에 남아야 한다.
    """
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _admin_response(
        request, RedirectResponse("/admin/links", status_code=303)
    )


@router.get("/admin/costs", response_class=HTMLResponse)
async def admin_costs(request: Request):
    """비용·예산 화면(정보 구조 ⑤). 오늘 지출·차단 기준·미확정 대사."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _access_page(request, template=ACCESS_COSTS_TEMPLATE)


@router.post("/admin/links/new")
async def admin_link_new(
    request: Request,
    company: str = Form(..., max_length=COMPANY_MAX_CHARS),
    job: str = Form("", max_length=JOB_MAX_CHARS),
    note: str = Form("", max_length=NOTE_MAX_CHARS),
    audience_label: str = Form("", max_length=COMPANY_MAX_CHARS),
    report_reference: str = Form("", max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """지원 회사·직무 꼬리표가 붙은 LINK를 새로 발급한다.

    Args:
        audience_label: 관리 화면에서 이 링크를 알아보려고 붙이는 표시 이름
            (예: 「하이브 인사팀」). **받는 사람 화면에는 쓰지 않는다** —
            내부 메모(`note`)와 마찬가지로 우리 쪽 편의를 위한 값이다.
    """
    if deployment_mode.render_admin_no_forwarded():
        return _admin_response(
            request,
            HTMLResponse("찾을 수 없습니다.", status_code=404),
        )
    company_clean = company.strip()
    # 회사·직무는 지원 맥락 꼬리표일 뿐 검색·생성 권한 범위가 아니다.
    job_clean = job.strip()
    action = "admin.link.create"
    authorization_target = admin_audit.target_id("company", company_clean)
    blocked = request_helpers.require_admin_action(
        request,
        csrf_token,
        action=action,
        target=authorization_target,
    )
    if blocked is not None:
        return blocked
    audience_label_clean = audience_label.strip()
    form_values = {
        "company": company_clean,
        "job": job_clean,
        "note": note.strip(),
        "audience_label": audience_label_clean,
        "report_reference": report_reference.strip(),
    }
    if not company_clean:
        try:
            _audit_change(
                request,
                action=action,
                target=authorization_target,
                outcome="rejected",
                reason="validation_failed",
            )
        except Exception:  # noqa: BLE001 — 감사 실패 시 변경 기능을 열지 않는다
            return _access_page(
                request,
                status_code=503,
                access_error="요청 기록을 남기지 못했습니다. 잠시 후 다시 시도해주세요.",
                link_form=form_values,
            )
        return _access_page(
            request,
            status_code=400,
            access_error="회사 이름을 입력해주세요.",
            link_form=form_values,
        )

    key = ""
    validation_error = ""
    issued_expires_at = ""
    try:
        with storage_db.connect() as conn:
            _assert_access_write_ready(conn)
            report_id, validation_error = _validated_report_id(
                conn, report_reference, expected_company=company_clean
            )
            if not validation_error:
                now_iso = clock.iso_now_kst()
                for _attempt in range(_KEY_ISSUE_ATTEMPTS):
                    candidate = share_issue.new_key()
                    if not share_logic.is_valid_key(candidate):
                        continue
                    if share_store.insert_new(
                        conn,
                        key=candidate,
                        company=company_clean,
                        job=job_clean,
                        note=note.strip(),
                        audience_label=audience_label_clean,
                        report_id=report_id,
                        now_iso=now_iso,
                    ):
                        key = candidate
                        break
                if not key:
                    raise RuntimeError("고유한 LINK 열쇠를 발급하지 못했습니다")
                inserted = share_store.load(conn, key)
                if (
                    inserted is None
                    or inserted.company != company_clean
                    or inserted.job != job_clean
                    or inserted.audience_label != audience_label_clean
                    or inserted.report_id != report_id
                ):
                    raise _AdminStateUnchanged("link_insert_unconfirmed")
                # 만료일은 저장 순간에 굳는다. 화면 라벨도 «그 값»을 그대로
                # 읽어야 자정을 넘겨 발급했을 때 하루 어긋나지 않는다.
                issued_expires_at = inserted.expires_at
                _queue_committed_change(
                    conn,
                    request,
                    action=action,
                    target=admin_audit.target_id("link", inserted.key_hash),
                    reason="created",
                )
                _assert_budget_store_healthy()
    except Exception:  # noqa: BLE001
        logger.error("링크 발급 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request,
            action=action,
            target=authorization_target,
            reason="storage_unavailable",
        )
        return _access_page(
            request,
            status_code=503,
            access_error="링크를 저장하지 못했습니다. 잠시 후 다시 시도해주세요.",
            link_form=form_values,
        )
    if validation_error:
        try:
            _audit_change(
                request,
                action=action,
                target=authorization_target,
                outcome="rejected",
                reason="report_validation_failed",
            )
        except Exception:  # noqa: BLE001 — 감사 실패 시 관리자 작업을 계속하지 않는다
            return _access_page(
                request,
                status_code=503,
                access_error="요청 기록을 남기지 못했습니다. 잠시 후 다시 시도해주세요.",
                link_form=form_values,
            )
        return _access_page(
            request,
            status_code=400,
            access_error=validation_error,
            link_form=form_values,
        )
    _mirror_committed_change(
        request,
        action=action,
        target=admin_audit.target_id("link", share_store.key_hash_of(key)),
        reason="created",
    )
    base = _share_base_url(request)
    issued_url = share_issue.link_url(base, key) if base else share_issue.link_url("", key)
    # 저장소에는 지문만 남으므로 QR은 «지금» 그리지 않으면 영영 만들 수 없다.
    issued_qr_svg = share_issue.qr_svg(issued_url)
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_link_issued.html",
        context=request_helpers._ctx(
            request,
            issued_url=issued_url,
            issued_qr_svg=issued_qr_svg,
            issued_qr_data_url=_svg_data_url(issued_qr_svg),
            issued_qr_filename=_ISSUED_QR_FILENAME,
            # 설정된 공개 HTTPS origin이 없으면 이 주소는 남에게 안 열린다.
            issued_url_is_local=not issued_url.lower().startswith("https://"),
            link_company=company_clean,
            link_job=job_clean,
            link_expiry_date_label=_link_expiry_date_label(
                clock.iso_now_kst(), expires_at=issued_expires_at
            ),
        ),
        status_code=200,
    )
    # 관리 화면에서 링크를 되짚을 때 쓰는 안전한 식별자(지문)만 헤더로 내보낸다.
    response.headers["X-Link-Identifier"] = share_store.key_hash_of(key)
    # 원문 capability는 이 일회성 화면에만 실리고 DB·HTML·로그·관리 URL에는 없다.
    # ★ Referrer-Policy는 여기서 정하지 않는다 — HTML 응답은 공용 미들웨어가
    #   `same-origin`으로 고정한다(`response_security.py`). 이 문서의 주소
    #   (`/admin/links/new`)에는 비밀이 없으므로 referer로 원문이 새지 않는다.
    return _admin_response(request, response)


def _link_detail_page(
    request: Request, key: str, *, link_error: str = "", status_code: int = 200
):
    key_hash = key.strip().lower() if share_store.is_key_hash(key) else ""
    if not key_hash:
        return _admin_response(
            request, HTMLResponse("올바르지 않은 LINK 식별자입니다.", status_code=404)
        )
    report_state = "none"
    open_events: list[share_store.ShareLinkOpenEvent] = []
    generated_runs: list[share_store.ShareLinkRun] = []
    run_report_states: dict[str, str] = {}
    adjustments: list[share_store.ShareLinkAdjustment] = []
    link_company_id_state = "none"
    try:
        with storage_db.connect() as conn:
            link = share_store.load_by_hash(conn, key_hash)
            if link is not None:
                report_state = _linked_report_state(conn, link)
                # ★ 결속 보고서에 회사 고유번호가 없으면 이후 재결속은 «이름»만
                #   대조하게 된다 — 같은 이름의 다른 회사가 통과한다.
                # ★ 「없다」와 「못 읽었다」를 나눈다. 같은 침묵으로 두면 읽기가
                #   깨진 동안 관리자는 아무 문제가 없다고 믿는다.
                if report_state in ("active", "expired"):
                    resolved_company_id = _link_company_id(conn, link)
                    if resolved_company_id is None:
                        link_company_id_state = "unknown"
                    elif resolved_company_id:
                        link_company_id_state = "present"
                    else:
                        link_company_id_state = "missing"
                adjustments = share_store.list_link_adjustments(
                    conn, key_hash=link.key_hash
                )
                open_events = share_store.list_open_events_by_hash(conn, link.key_hash)
                generated_runs = share_store.list_runs_by_hash(conn, link.key_hash)
                for run in generated_runs:
                    if not run.report_id:
                        run_report_states[run.run_id] = "none"
                    else:
                        report = report_store.load(conn, run.report_id)
                        run_report_states[run.run_id] = (
                            "missing"
                            if report is None
                            else (
                                "available"
                                if report_publication.report_is_published_or_legacy(
                                    conn, run.report_id
                                )
                                else "unavailable"
                            )
                        )
    except Exception:  # noqa: BLE001
        logger.error("링크를 읽지 못했습니다")
        return _access_page(
            request,
            status_code=503,
            access_error_title="LINK를 불러오지 못했습니다.",
            access_error="잠시 후 다시 시도해주세요.",
        )
    if link is None:
        return _admin_response(
            request, RedirectResponse("/admin/access", status_code=303)
        )

    extension_bounds = _extension_bounds(
        share_logic.expiry_date_of(link.created_at, expires_at=link.expires_at),
        created_at=link.created_at,
    )
    open_event_labels = [
        _kst_timestamp_label(event.opened_at) for event in open_events
    ]
    run_started_at_labels = {
        run.run_id: _kst_timestamp_label(run.started_at) for run in generated_runs
    }
    run_finished_at_labels = {
        run.run_id: _kst_timestamp_label(run.finished_at) for run in generated_runs
    }
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_link.html",
        context=request_helpers._ctx(
            request,
            link=link,
            link_error=link_error,
            url="",
            path="",
            base_url="",
            secret_available=False,
            link_expired=share_logic.link_expired(link),
            link_revoked=link.is_revoked,
            link_revoked_at_label=_kst_timestamp_label(link.revoked_at),
            link_created_at_label=_kst_timestamp_label(link.created_at),
            link_expiry_date_label=_link_expiry_date_label(
                link.created_at, expires_at=link.expires_at
            ),
            link_days_left=_link_days_left(
                link.created_at, expires_at=link.expires_at
            ),
            link_first_opened_at_label=_kst_timestamp_label(
                link.first_opened_at
            ),
            link_last_opened_at_label=_kst_timestamp_label(link.last_opened_at),
            open_events=open_events,
            open_event_labels=open_event_labels,
            open_window_rows_per_link=share_constants.OPEN_WINDOW_ROWS_PER_LINK,
            historical_open_count_gap=max(
                0,
                link.opened_count
                - sum(event.opened_count for event in open_events),
            ),
            generated_runs=generated_runs,
            run_started_at_labels=run_started_at_labels,
            run_finished_at_labels=run_finished_at_labels,
            run_report_states=run_report_states,
            run_status_labels={
                share_store.RUN_STATUS_RUNNING: "생성 중",
                share_store.RUN_STATUS_AWAITING_RELEASE: "자동출고 검사 대기",
                share_store.RUN_STATUS_COMPLETED: "완료",
                share_store.RUN_STATUS_STOPPED: "중단",
                share_store.RUN_STATUS_INTERRUPTED: "서버 종료로 중단",
            },
            run_stop_reason_labels={
                "company_not_found": "회사를 확정하지 못함",
                "unsupported_public_entity": "분석 대상이 아닌 공공기관",
                "disclosure_not_available": "공시 자료를 확인할 수 없음",
                "posting_discarded": "채용공고로 확인되지 않음",
                "evidence_gate_stopped": "보고서 근거가 부족함",
                "generation_failed": "생성 중 기술 오류",
                "posting_image_consent_required": "이미지 전송 동의가 필요함",
                "posting_image_read_failed": "공고 이미지를 읽지 못함",
                "posting_image_demo_unsupported": "데모에서 이미지 입력을 지원하지 않음",
                "posting_image_extraction_failed": "이미지 글자 추출에 실패함",
                "daily_budget_unavailable": "LINK 일일 비용 한도를 사용할 수 없음",
                "daily_budget_exhausted": "LINK 일일 비용 한도에 도달함",
                "job_registration_failed": "생성 작업을 저장하지 못함",
                "server_shutdown": "서버 종료로 시작하지 못함",
                "shutdown_timeout": "서버 종료 제한시간을 넘김",
                "server_restart": "서버 재시작으로 작업을 이어갈 수 없음",
                "server_restart_delivery_incomplete": "서버 재시작으로 자동출고 확인 전 중단됨",
                "admin_manual_settled": "관리자가 수동으로 대사해 중단됨",
                "generation_start_failed": "생성 시작 중 기술 오류",
                "generation_not_started": "생성을 시작하지 못함",
                "automatic_release_gate_stopped": "자동출고 검사를 통과하지 못함",
                # 조사 도중 초대 링크가 닫혀 멈춘 갈래. 사람 말로 적는다 —
                # 「revoked」로는 무엇이 멈췄는지 읽는 사람이 알 수 없다.
                "link_revoked": "초대 링크의 사용이 중단됨",
                "link_expired": "초대 링크의 기간이 지남",
                "link_state_unknown": "초대 링크 상태를 확인하지 못함",
            },
            reference_max_chars=REFERENCE_MAX_CHARS,
            is_deployed=False,
            qr_svg="",
            report_state=report_state,
            link_company_id_state=link_company_id_state,
            link_adjustments=adjustments,
            link_adjustment_kind_labels=_ADJUSTMENT_KIND_LABELS,
            link_adjustment_at_labels={
                item.id: _kst_timestamp_label(item.created_at)
                for item in adjustments
            },
            link_extend_min_date=extension_bounds[0].isoformat(),
            link_extend_max_date=(
                "" if extension_bounds[1] is None
                else extension_bounds[1].isoformat()
            ),
            link_extension_capped=extension_bounds[1] is None,
            link_extension_cap_message=_LINK_EXTENSION_CAP_MESSAGE,
            link_total_age_days=share_constants.MAX_LINK_TOTAL_AGE_DAYS,
            link_extend_max_days=share_constants.MAX_LINK_EXTENSION_DAYS,
            link_reason_max_chars=(
                share_constants.LINK_ADJUSTMENT_REASON_MAX_CHARS
            ),
            link_extension_disabled=deployment_mode.render_admin_no_forwarded(),
            report_share_days=REPORT_LINK_MAX_AGE_DAYS,
            # 이 화면이 곧 만료 연장의 «확인 화면»이다 — 지금
            # 만료일과 남은 날짜를 보여 준 뒤 1회용 표를 함께 실어 보낸다.
            confirm_token=issue_confirm_token(
                request,
                action="admin.link.extend",
                target=admin_audit.target_id("link", link.key_hash),
            ),
            link_reason_min_chars=DANGEROUS_ACTION_REASON_MIN_CHARS,
        ),
        status_code=status_code,
    )
    return _admin_response(request, response)


def _link_total_age_limit(created_at: str) -> dt.date | None:
    """이 링크가 «발급일 기준»으로 살 수 있는 마지막 날. 못 읽으면 ``None``.

    ★ 미루기를 반복하면 링크는 영원히 산다. 1회 상한만으로는 그것을 못 막는다.
      회수할 수 없는 QR을 뿌리는 일이므로 발급일에서 세는 천장을 따로 둔다.
    """

    try:
        issued = clock.business_date_from_iso(created_at)
    except (OverflowError, TypeError, ValueError):
        return None
    try:
        return issued + dt.timedelta(
            days=share_constants.MAX_LINK_TOTAL_AGE_DAYS
        )
    except (OverflowError, ValueError):
        return None


def _extension_bounds(
    previous: dt.date | None, *, created_at: str
) -> tuple[dt.date, dt.date | None]:
    """새 만료일로 받을 수 있는 «가장 이른 날»과 «가장 늦은 날».

    Args:
        previous: 지금 만료일. 읽을 수 없으면 ``None``.
        created_at: 발급 시각. 총 수명 상한을 여기서 센다.

    Returns:
        (최소, 최대). 둘 다 그날을 포함한다. **더 미룰 여지가 없으면 최대가
        ``None``**이다 — 발급일을 못 읽는 경우도 여기에 들어간다.

    ★ 1회 폭은 «오늘»이 아니라 «지금 만료일»에서 잰다. 오늘 기준으로 재면 방금
      발급한 90일짜리 링크는 상한과 현재 만료일이 같은 날이 되어 **한 번도
      못 미룬다**. 관리자가 원하는 것은 「지금보다 얼마나 더」이므로 그 폭을
      묶는 편이 맞다.
    ★ 이미 닫힌 링크는 오늘을 기준으로 잰다 — 과거 날짜에서 재면 미뤄도
      여전히 닫힌 날이 나온다.
    ★ 두 상한 중 **먼저 걸리는 쪽**이 최대다. 총 상한이 더 가까우면 1회 90일을
      다 못 쓴다.
    """

    today = clock.today_kst()
    base = previous if previous is not None and previous > today else today
    action_limit = base + dt.timedelta(
        days=share_constants.MAX_LINK_EXTENSION_DAYS
    )
    total_limit = _link_total_age_limit(created_at)
    if total_limit is None:
        return base + dt.timedelta(days=1), None
    maximum = min(action_limit, total_limit)
    if maximum <= base:
        return base + dt.timedelta(days=1), None
    return base + dt.timedelta(days=1), maximum


def _validated_new_expiry(
    raw: str, *, previous: dt.date | None, created_at: str
) -> tuple[dt.date | None, str]:
    """관리자가 적은 새 만료일이 «미루는» 값인지 확인한다.

    Args:
        raw: 폼에 적힌 ``YYYY-MM-DD``.
        previous: 지금 만료일. 읽을 수 없으면 ``None``.
        created_at: 발급 시각. 총 수명 상한을 여기서 센다.

    Returns:
        (새 만료일, 오류 문구). 오류가 있으면 날짜는 ``None``이다.

    ★ 상한이 둘인 이유 — 1회 상한은 「실수로 2099년」을, 총 상한은 「조금씩 계속
      미뤄 사실상 영구 링크가 되는 것」을 막는다. 서로 다른 위험이라 둘 다 둔다.
    """

    parsed = share_logic.expiry_date_from_value(raw)
    if parsed is None:
        return None, "새 만료일을 2026-12-31 같은 형식으로 입력해주세요."
    today = clock.today_kst()
    if parsed <= today:
        return None, "오늘보다 뒤의 날짜만 넣을 수 있습니다."
    if previous is not None and parsed <= previous:
        return None, (
            f"지금 만료일({previous:%Y-%m-%d})보다 뒤의 날짜만 넣을 수 있습니다. "
            "기간을 줄이려면 링크를 철회하세요."
        )
    _minimum, maximum = _extension_bounds(previous, created_at=created_at)
    if maximum is None:
        return None, _LINK_EXTENSION_CAP_MESSAGE
    if parsed > maximum:
        total_limit = _link_total_age_limit(created_at)
        if total_limit is not None and maximum == total_limit:
            return None, (
                f"이 링크는 {maximum:%Y-%m-%d}까지만 미룰 수 있습니다. "
                f"발급 후 {share_constants.MAX_LINK_TOTAL_AGE_DAYS}일이 한 링크의 "
                "최대 기간입니다. 더 필요하면 새 링크를 발급해 주세요."
            )
        return None, (
            f"한 번에 {share_constants.MAX_LINK_EXTENSION_DAYS}일까지만 미룰 수 "
            f"있습니다. {maximum:%Y-%m-%d} 이내로 넣어주세요."
        )
    return parsed, ""


@router.get("/admin/link/{key_hash}/extend", response_class=HTMLResponse)
async def admin_link_extend_page(request: Request, key_hash: str):
    """만료일·남은 날짜·연장 폼·변경 이력이 함께 있는 링크 상세 화면.

    ★ 목록 쪽 상세(`/admin/links/…`)는 접속·생성 이력을 본다. 만료를 «바꾸는»
      일은 이유와 이력이 함께 남아야 하므로 폼과 이력표를 한 화면에 둔다.
    """

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _link_detail_page(request, key_hash)


@router.post("/admin/link/{key_hash}/extend")
async def admin_link_extend(
    request: Request,
    key_hash: str,
    expires_on: str = Form("", max_length=REFERENCE_MAX_CHARS),
    reason: str = Form("", max_length=NOTE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
    confirm_token: str = Form("", max_length=REFERENCE_MAX_CHARS),
):
    """링크의 만료일을 뒤로 미룬다. 이유·이력행·감사행을 함께 남긴다.

    ★ 「미루기」만 한다 — 앞당기기는 철회(`/admin/links/revoke`)가 이미 즉시
      막아 준다. 두 길을 한 폼에 두면 실수로 링크를 조기에 닫는다.
    """

    if deployment_mode.render_admin_no_forwarded():
        return _admin_response(
            request,
            HTMLResponse("찾을 수 없습니다.", status_code=404),
        )
    key_clean = str(key_hash or "").strip().lower()
    action = "admin.link.extend"
    target = admin_audit.target_id("link", key_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    if not share_store.is_key_hash(key_clean):
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_target"
        )
        return _admin_response(
            request,
            HTMLResponse("올바르지 않은 LINK 식별자입니다.", status_code=404),
        )
    reason_clean, reason_error = _validated_action_reason(reason)
    reason_clean = reason_clean[
        : share_constants.LINK_ADJUSTMENT_REASON_MAX_CHARS
    ]
    validation_error = ""
    confirmation_missing = False
    try:
        with storage_db.connect() as conn:
            link = share_store.load_by_hash(conn, key_clean)
            if link is None:
                raise _AdminStateUnchanged("link_missing")
            previous = share_logic.expiry_date_of(
                link.created_at, expires_at=link.expires_at
            )
            new_expiry, validation_error = _validated_new_expiry(
                str(expires_on or ""),
                previous=previous,
                created_at=link.created_at,
            )
            if not validation_error and reason_error:
                # ★ 이유를 «필수»로 두는 이유 — 나중의 내가 왜 미뤘는지 알아야
                #   같은 판단을 다시 할 수 있다. 이력만 남고 이유가 없으면
                #   기록이 아니라 흔적이다. 길이 하한은 위험 동작에 공통으로 걸리는
                #   규칙이다.
                # ★ 날짜가 이미 틀렸으면 그쪽을 먼저 말한다 — 폼 순서대로 한 번에
                #   하나씩 알려야 관리자가 무엇을 고칠지 헷갈리지 않는다.
                validation_error = (
                    "연장하는 이유를 적어주세요."
                    if not str(reason or "").strip()
                    else reason_error
                )
            # ★ 확인 표는 «날짜·이유를 다 통과한 뒤»에 본다. 앞에 두면 총 수명
            #   상한에 닿아 애초에 못 미루는 링크까지 「확인 화면을 거치세요」로
            #   대답해, 진짜 막힌 이유(상한)를 가린다. 폼 오류로 표를
            #   태우지 않는 이점도 있다 — 날짜를 고쳐 다시 보내면 그대로 통과한다.
            confirmation_missing = (
                not validation_error
                and not _confirmation_accepted(
                    request, confirm_token, action=action, target=target
                )
            )
            if not validation_error and not confirmation_missing and (
                new_expiry is not None
            ):
                if not share_store.set_expires_at(
                    conn,
                    key_hash=link.key_hash,
                    expires_at=new_expiry.isoformat(),
                ):
                    raise _AdminStateUnchanged("link_expiry_unconfirmed")
                share_store.record_link_adjustment(
                    conn,
                    key_hash=link.key_hash,
                    kind=share_store.ADJUSTMENT_KIND_EXPIRES,
                    old_value="" if previous is None else previous.isoformat(),
                    new_value=new_expiry.isoformat(),
                    reason=reason_clean,
                    actor_id=admin_audit.actor_id(request),
                    created_at=clock.iso_now_kst(),
                )
                updated = share_store.load_by_hash(conn, link.key_hash)
                if updated is None or updated.expires_at != new_expiry.isoformat():
                    raise _AdminStateUnchanged("link_expiry_unconfirmed")
                _queue_committed_change(
                    conn,
                    request,
                    action=action,
                    target=target,
                    reason="expiry_extended",
                )
    except Exception:  # noqa: BLE001 — 확인 못 한 변경을 성공으로 보지 않는다
        logger.error("링크 만료 연장 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request, action=action, target=target, reason="storage_unavailable"
        )
        return _link_detail_page(
            request,
            key_clean,
            link_error=(
                "만료일을 저장하지 못했습니다. 바뀌었는지 확인할 수 없으니 "
                "성공으로 보지 말고 잠시 후 다시 시도해주세요."
            ),
            status_code=503,
        )
    if confirmation_missing:
        return _confirmation_required(request, action=action, target=target)
    if validation_error:
        try:
            _audit_change(
                request,
                action=action,
                target=target,
                outcome="rejected",
                reason="validation_failed",
            )
        except Exception:  # noqa: BLE001 — 감사 실패 시 관리자 작업을 계속하지 않는다
            return _link_detail_page(
                request,
                key_clean,
                link_error="요청 기록을 남기지 못했습니다. 잠시 후 다시 시도해주세요.",
                status_code=503,
            )
        return _link_detail_page(
            request, key_clean, link_error=validation_error, status_code=400
        )
    _mirror_committed_change(
        request, action=action, target=target, reason="expiry_extended"
    )
    return _admin_response(
        request,
        RedirectResponse(f"/admin/link/{key_clean}/extend", status_code=303),
    )


@router.get("/admin/link/{key}", response_class=HTMLResponse)
async def admin_link_detail(request: Request, key: str):
    """구형 singular 주소에서 해시만 신형 상세로 정규화한다."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    key_hash = key.strip().lower()
    if not share_store.is_key_hash(key_hash):
        return _admin_response(
            request, HTMLResponse("올바르지 않은 LINK 식별자입니다.", status_code=404)
        )
    return _admin_response(
        request, RedirectResponse(f"/admin/links/{key_hash}", status_code=303)
    )


@router.get("/admin/link/report/{report_id}", response_class=HTMLResponse)
async def admin_link_generated_report(request: Request, report_id: str):
    """만료·철회 뒤에도 관리자가 LINK에 결속된 과거 보고서를 검수한다."""

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    clean_report_id = share_logic.report_id_from_reference(report_id)
    if not clean_report_id:
        return _admin_response(
            request, RedirectResponse("/admin/links", status_code=303)
        )
    try:
        with storage_db.connect() as conn:
            linked = share_store.is_linked_report(conn, clean_report_id)
    except Exception:  # noqa: BLE001 — 결속을 확인하지 못하면 만료 우회를 열지 않는다
        return _access_page(
            request,
            status_code=503,
            access_error="LINK 보고서 결속을 확인하지 못했습니다.",
        )
    if not linked:
        return _admin_response(
            request, RedirectResponse("/admin/links", status_code=303)
        )
    return _admin_response(
        request,
        RedirectResponse(f"/admin/reports/{clean_report_id}", status_code=303),
    )


@router.post("/admin/links/report")
async def admin_link_report(
    request: Request,
    key: str = Form(..., max_length=REFERENCE_MAX_CHARS),
    report_reference: str = Form("", max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """기존 링크에 저장 보고서를 연결·교체하거나 빈 값으로 연결을 푼다."""
    key_clean = key.strip().lower()
    action = "admin.link.report"
    target = admin_audit.target_id("link", key_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    key_is_hash = share_store.is_key_hash(key_clean)
    detail_key_hash = key_clean if key_is_hash else ""
    if not key_is_hash:
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_target"
        )
        return _access_page(
            request,
            status_code=400,
            access_error_title="LINK를 확인해주세요.",
            access_error="올바른 LINK 식별자가 아닙니다.",
        )

    validation_error = ""
    try:
        with storage_db.connect() as conn:
            _assert_access_write_ready(conn)
            link = share_store.load_by_hash(conn, key_clean)
            if link is None:
                raise _AdminStateUnchanged("link_missing")
            else:
                detail_key_hash = link.key_hash
                report_id, validation_error = _validated_report_id(
                    conn,
                    report_reference,
                    expected_company=link.company,
                    expected_company_id=_link_company_id(conn, link),
                )
                if not validation_error:
                    changed = share_store.set_report_by_hash(
                        conn, key_clean, report_id
                    )
                    updated = share_store.load_by_hash(conn, key_clean)
                    if (
                        not changed
                        or updated is None
                        or updated.report_id != report_id
                    ):
                        raise _AdminStateUnchanged("link_report_unconfirmed")
                    _queue_committed_change(
                        conn,
                        request,
                        action=action,
                        target=target,
                        reason="report_updated",
                    )
                    _assert_budget_store_healthy()
    except Exception:  # noqa: BLE001
        logger.error("링크 보고서 연결 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request,
            action=action,
            target=target,
            reason="storage_unavailable",
        )
        return _access_page(
            request,
            status_code=503,
            access_error_title="보고서 연결을 저장하지 못했습니다.",
            access_error=(
                "저장 뒤 상태를 확인할 수 없어 성공으로 처리하지 않았습니다. "
                "잠시 후 다시 시도해주세요."
            ),
        )
    if validation_error:
        try:
            _audit_change(
                request,
                action=action,
                target=target,
                outcome="rejected",
                reason="report_validation_failed",
            )
        except Exception:  # noqa: BLE001 — 감사 실패 시 관리자 작업을 계속하지 않는다
            return _access_page(
                request,
                status_code=503,
                access_error="요청 기록을 남기지 못했습니다. 잠시 후 다시 시도해주세요.",
            )
        return _link_detail_page(
            request,
            detail_key_hash,
            link_error=validation_error,
            status_code=400,
        )
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="report_updated",
    )
    return _admin_response(
        request,
        RedirectResponse(f"/admin/links/{detail_key_hash}", status_code=303),
    )


@router.get("/admin/links/{key_hash}/revoke", response_class=HTMLResponse)
async def admin_link_revoke_confirm(request: Request, key_hash: str):
    """초대 링크 사용 중단 «확인 화면».

    ★ 목록에서 단추 하나로 바로 닫히면 잘못 눌렀을 때 되돌릴 길이 없다 —
      이미 뿌린 주소가 죽고 새로 발급해 다시 나눠 줘야 한다. 그래서 무엇을
      닫는지 다시 보여 주고, 이 화면에서 받은 1회용 표가 있어야 실행한다.
    """

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    key_clean = str(key_hash or "").strip().lower()
    if not share_store.is_key_hash(key_clean):
        return _admin_response(
            request,
            HTMLResponse("찾을 수 없는 초대 링크입니다.", status_code=404),
        )
    try:
        with storage_db.connect() as conn:
            link = share_store.load_by_hash(conn, key_clean)
    except Exception:  # noqa: BLE001 — 못 읽은 대상을 «있는 것»으로 보이지 않는다
        logger.error("확인 화면에서 초대 링크를 읽지 못했습니다")
        return _admin_response(
            request,
            HTMLResponse(
                "지금은 이 초대 링크를 확인할 수 없습니다. 잠시 후 다시 "
                "시도해주세요.",
                status_code=503,
            ),
        )
    if link is None:
        return _admin_response(
            request,
            HTMLResponse("찾을 수 없는 초대 링크입니다.", status_code=404),
        )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_confirm_link_revoke.html",
        context=request_helpers._ctx(
            request,
            link=link,
            link_key_hash=link.key_hash,
            link_created_at_label=_kst_timestamp_label(link.created_at),
            link_expiry_date_label=_link_expiry_date_label(
                link.created_at, expires_at=link.expires_at
            ),
            link_days_left=_link_days_left(
                link.created_at, expires_at=link.expires_at
            ),
            link_revoked=link.is_revoked,
            report_share_days=REPORT_LINK_MAX_AGE_DAYS,
            confirm_token=issue_confirm_token(
                request,
                action="admin.link.revoke",
                target=admin_audit.target_id("link", link.key_hash),
            ),
        ),
    )
    return _admin_response(request, response)


@router.post("/admin/links/revoke")
async def admin_link_delete(
    request: Request,
    key: str = Form(..., max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
    confirm_token: str = Form("", max_length=REFERENCE_MAX_CHARS),
):
    """링크를 닫는다. 확인 화면에서 받은 1회용 표가 있어야 실행한다."""
    key_clean = key.strip().lower()
    key_is_hash = share_store.is_key_hash(key_clean)
    action = "admin.link.revoke"
    target = admin_audit.target_id("link", key_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    if not key_is_hash:
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_target"
        )
        return _admin_response(
            request,
            HTMLResponse("올바르지 않은 LINK 식별자입니다.", status_code=400),
        )
    if not _confirmation_accepted(
        request, confirm_token, action=action, target=target
    ):
        return _confirmation_required(request, action=action, target=target)
    detail_key_hash = key_clean if key_is_hash else ""
    try:
        with storage_db.connect() as conn:
            # 철회는 비용 노출을 늘리지 않는 비상 안전 동작이다. 비용 원장이
            # 닫혀 있어도 LINK 정본·감사 정본을 쓸 수 있으면 즉시 닫아야 한다.
            deleted = share_store.delete_by_hash(conn, key_clean)
            revoked = share_store.load_by_hash(conn, key_clean)
            if not deleted or revoked is None or not revoked.is_revoked:
                raise _AdminStateUnchanged("link_delete_unconfirmed")
            detail_key_hash = revoked.key_hash
            _queue_committed_change(
                conn,
                request,
                action=action,
                target=target,
                reason="revoked",
            )
    except Exception:  # noqa: BLE001
        logger.error("링크 철회 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request,
            action=action,
            target=target,
            reason="storage_unavailable",
        )
        return _access_page(
            request,
            status_code=503,
            access_error_title="LINK를 철회하지 못했습니다.",
            access_error=(
                "저장소에서 철회 여부를 확인하지 못했습니다. 기존 링크가 아직 살아 "
                "있을 수 있으니 성공으로 보지 말고 잠시 후 다시 시도해주세요."
            ),
        )
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="revoked",
    )
    return _admin_response(
        request,
        RedirectResponse(f"/admin/links/{detail_key_hash}", status_code=303),
    )


@router.post("/admin/budget/recheck")
async def admin_budget_recheck(
    request: Request,
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """비용 원장을 «다시 읽어» 유료 조사를 열 수 있는지 확인한다.

    ★ 왜 이 경로가 생겼나 — 사용자 화면은
      「비용 기록을 확인할 수 없어 새 조사를 잠시 멈췄습니다. **관리자 확인이 끝나야
      다시 열립니다.**」라고 말하는데, 정작 **관리자가 「확인」을 실행할 방법이 없었다.**
      `_BUDGET_STORE_HEALTHY` 를 True 로 되돌리는 곳이 기동 시 한 곳뿐이라,
      운영 중 한 번 꺼지면 **서버를 재시작하기 전까지 모든 유료 조사가 막혔다.**

    ★ **강제로 열지 않는다.** 기동 때와 «같은 검사»를 다시 돌릴 뿐이고,
      자료가 여전히 나쁘면 닫힌 채로 남는다 (`paid_runtime.recheck_budget_store`).
    """
    action = "admin.budget.recheck"
    target = admin_audit.target_id("budget", "store")
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked

    opened, notice = paid_runtime.recheck_budget_store()

    # 돈이 걸린 문을 여닫는 일이라 «성공도 실패도» 기록에 남긴다.
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="opened" if opened else "still_closed",
    )
    # ★ 「원장은 살아났지만 미확정 통장이 남아 계속 막혀 있다」는 «부분 성공»이다.
    #   전에는 이때도 그냥 리다이렉트해서, 관리자 눈에는 **버튼이 아무 일도 안 한
    #   것처럼** 보였다 (사용자 신고: 「그냥 버튼만 있는 거 아니냐」).
    막힌채, _사유 = paid_runtime.paid_research_block()
    if opened and not 막힌채:
        return _admin_response(
            request, RedirectResponse("/admin/costs", status_code=303)
        )
    return _access_page(
        request,
        template=ACCESS_COSTS_TEMPLATE,
        status_code=503,
        access_error_title=(
            "아직 유료 조사가 닫혀 있습니다."
            if opened
            else "유료 조사를 다시 열지 못했습니다."
        ),
        access_error=notice,
    )


@router.post("/admin/budget/settle")
async def admin_budget_settle(
    request: Request,
    run_id: str = Form("", max_length=RUN_ID_MAX_CHARS),
    phase: str = Form("", max_length=PHASE_MAX_CHARS),
    attempt_id: str = Form("", max_length=RUN_ID_MAX_CHARS),
    resolution_action: str = Form("", max_length=48),
    actual_cost_krw: str = Form("", max_length=32),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """미확정 유료 단계 하나를 «대사 완료»로 마감한다.

    ★ 왜 이 경로가 생겼나 — 화면은 「관리자가 미확정 비용을 대사해야
      해당 통장이 다시 열립니다」라고 말하는데, **대사할 방법이 코드에 없었다.**
      `finish_inflight` 는 내부 정산에서만 불렸고 관리자 경로가 0개였다.
      그래서 「원장 다시 읽기」를 눌러도 미확정은 그대로 남아 계속 막혔다 —
      재시작해도 DB에서 다시 읽히므로 **영원히 안 풀렸다.**

    ★ 예약액을 «쓴 것으로» 확정한다. 실제 청구액을 모를 때는 많이 썼다고 가정해야
      하루 상한이 느슨해지지 않는다 (`paid_runtime.settle_unresolved_spend`).
    """
    action = "admin.budget.settle"
    target = admin_audit.target_id(
        "budget-attempt" if attempt_id else "budget",
        attempt_id or "inflight",
    )
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked

    reason = "settle_failed"
    if attempt_id:
        try:
            resolution = budget_state_machine.ResolutionAction(resolution_action)
            if resolution is budget_state_machine.ResolutionAction.CONFIRM_ACTUAL:
                actual = float(actual_cost_krw)
            else:
                actual = None
        except (TypeError, ValueError):
            마감했나, notice = (
                False,
                "확인한 결과와 실제 비용 값을 다시 확인해 주세요.",
            )
        else:
            마감했나, notice = paid_runtime.resolve_budget_liability(
                attempt_id=attempt_id,
                action=resolution,
                actual_cost_krw=actual,
                actor_id=admin_audit.actor_id(request),
                reason_code={
                    budget_state_machine.ResolutionAction.CONFIRM_ACTUAL:
                        "provider-actual-confirmed",
                    budget_state_machine.ResolutionAction.CONFIRM_ZERO:
                        "provider-zero-confirmed",
                    budget_state_machine.ResolutionAction.CONFIRM_CONSERVATIVE_LIABILITY:
                        "liability-retained",
                }[resolution],
            )
            if 마감했나:
                reason = {
                    budget_state_machine.ResolutionAction.CONFIRM_ACTUAL:
                        "actual_confirmed",
                    budget_state_machine.ResolutionAction.CONFIRM_ZERO:
                        "zero_confirmed",
                    budget_state_machine.ResolutionAction.CONFIRM_CONSERVATIVE_LIABILITY:
                        "liability_retained",
                }[resolution]
    else:
        마감했나, notice = paid_runtime.settle_unresolved_spend(run_id, phase)
        if 마감했나:
            reason = "legacy_settled"

    # 돈을 «썼다고 확정»하는 일이라 성공도 실패도 기록에 남긴다.
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason=reason,
    )
    막힌채, _사유 = paid_runtime.paid_research_block()
    if 마감했나 and not 막힌채:
        return _admin_response(
            request, RedirectResponse("/admin/costs", status_code=303)
        )
    return _access_page(
        request,
        template=ACCESS_COSTS_TEMPLATE,
        status_code=503,
        access_error_title=(
            "한 건을 마감했지만 아직 닫혀 있습니다."
            if 마감했나
            else "미확정 비용을 마감하지 못했습니다."
        ),
        access_error=notice,
    )


def _delivery_settle_context(request: Request, *, error: str = "") -> dict:
    """대사 화면과 대사 실패 재표시가 공유하는 목록 조회."""
    with storage_db.connect() as conn:
        pending = delivery_store.list_stale_required_delivery_intents(
            conn, older_than=clock.now_kst()
        )
    return request_helpers._ctx(
        request,
        pending_intents=pending,
        pending_intent_labels={
            intent.public_id: {
                "required_at": _kst_timestamp_label(intent.required_at.isoformat()),
                "updated_at": _kst_timestamp_label(intent.updated_at.isoformat()),
            }
            for intent in pending
        },
        settle_error=error,
    )


@router.get("/admin/delivery/settle", response_class=HTMLResponse)
async def admin_delivery_settle_list(request: Request):
    """저장은 됐지만 출고가 확정되지 못한 delivery 의무 중 스윕이 못 잡은 나머지를 보여준다.

    ``runtime._recover_stale_report_deliveries``가 서버 시작 때 N분 넘게
    정체된 의무를 자동으로 닫지만, 서버를 재시작하지 않았거나 아직 그
    기준에 못 미친 건은 이 화면에서 관리자가 직접 확인하고 닫는다.
    """
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_delivery_settle.html",
        context=_delivery_settle_context(request),
    )
    return _admin_response(request, response)


@router.post("/admin/delivery/settle")
async def admin_delivery_settle(
    request: Request,
    report_id: str = Form("", max_length=RUN_ID_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """스윕이 못 잡은 required delivery 의무 한 건을 관리자가 직접 닫는다.

    ★ 왜 이 경로가 생겼나(``admin_budget_settle``의 앞선
      선례를 그대로 본뜬다) — 부팅 스윕은 일정 시간 이상 정체된 의무만
      자동으로 닫는다. 그보다 최근에 멈췄거나 스윕 자체가 실패한 건은
      관리자가 직접 닫을 방법이 있어야 한다(재시작해도 DB에서 다시
      읽히므로 자동 스윕 기준을 못 넘는 한 영원히 안 풀린다).

    ★ **진짜 출고가 있는 보고서는 절대 실패로 뒤집지 않는다** —
      ``report_delivery_adapter.delivery_exists``로 먼저 확인한다.
    """
    action = "admin.delivery.settle"
    clean_report_id = str(report_id or "").strip()
    target = admin_audit.target_id("delivery", clean_report_id or "missing")
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked

    error = ""
    settled = False
    if not clean_report_id:
        error = "대사할 보고서 ID를 입력해 주세요."
    else:
        intent = report_delivery_adapter.load_public_delivery_intent(clean_report_id)
        if intent is None:
            error = "이 보고서에는 대사할 delivery 의무가 없습니다."
        elif report_delivery_adapter.delivery_exists(clean_report_id):
            error = "이미 출고가 끝난 보고서는 대사 대상이 아닙니다."
        elif intent.state == delivery_store.DELIVERY_INTENT_COMPLETE:
            error = "이미 완료로 표시된 보고서는 대사 대상이 아닙니다."
        elif intent.state == delivery_store.DELIVERY_INTENT_FAILED:
            # 이미 닫혀 있음 — 두 번째 제출을 오류로 막지 않고 그대로 성공 취급한다.
            settled = True
        else:
            try:
                report_delivery_adapter.fail_public_delivery(
                    clean_report_id,
                    failure_code=delivery_constants.MANUAL_SETTLEMENT_FAILURE_CODE,
                    failed_at=clock.now_kst(),
                )
                with storage_db.connect() as conn:
                    share_store.mark_release_stopped(
                        conn,
                        report_id=clean_report_id,
                        stopped_at=clock.iso_now_kst(),
                        stop_step="admin_manual_settle",
                        stop_reason=delivery_constants.MANUAL_SETTLEMENT_FAILURE_CODE,
                    )
                settled = True
            except Exception:  # noqa: BLE001 — 실패도 성공도 감사에 남긴다
                logger.exception(
                    "delivery 의무 수동 대사를 마치지 못했습니다 report_id=%s",
                    clean_report_id,
                )
                error = "대사 처리 중 오류가 발생했습니다. 다시 시도해 주세요."

    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="settled" if settled else "rejected",
    )
    if settled:
        return _admin_response(
            request, RedirectResponse("/admin/delivery/settle", status_code=303)
        )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_delivery_settle.html",
        context=_delivery_settle_context(request, error=error),
        status_code=409,
    )
    return _admin_response(request, response)


@router.post("/admin/invite")
async def admin_invite(
    request: Request,
    email: str = Form(..., max_length=EMAIL_MAX_CHARS),
    display_name: str = Form("", max_length=NOTE_MAX_CHARS),
    note: str = Form("", max_length=NOTE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """친구를 초대 명단에 넣는다."""
    if deployment_mode.render_admin_no_forwarded():
        return _admin_response(
            request,
            HTMLResponse("이 운영판에서는 친구를 초대할 수 없습니다.", status_code=409),
        )
    email_clean = share_allow.normalize(email)
    action = "admin.member.invite"
    target = admin_audit.target_id("member", email_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            _assert_access_write_ready(conn)
            changed = share_allow.invite(
                conn,
                email=email_clean,
                display_name=display_name,
                note=note,
                now_iso=clock.iso_now_kst(),
            )
            confirmed = share_allow.load(conn, email_clean) is not None
            if not changed or not confirmed:
                raise _AdminStateUnchanged("invite_unconfirmed")
            _queue_committed_change(
                conn,
                request,
                action=action,
                target=target,
                reason="invited",
            )
            _assert_budget_store_healthy()
    except Exception:  # noqa: BLE001
        logger.error("초대 저장 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request,
            action=action,
            target=target,
            reason="storage_unavailable",
        )
        return _access_page(
            request,
            template=ACCESS_MEMBERS_TEMPLATE,
            status_code=503,
            access_error_title="친구를 초대하지 못했습니다.",
            access_error=(
                "초대 명단이 실제로 바뀌었는지 확인할 수 없어 성공으로 처리하지 "
                "않았습니다. 잠시 후 다시 시도해주세요."
            ),
        )
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="invited",
    )
    return _admin_response(
        request, RedirectResponse("/admin/members", status_code=303)
    )


@router.get("/admin/members/{email}/remove", response_class=HTMLResponse)
async def admin_member_revoke_confirm(request: Request, email: str):
    """친구를 명단에서 빼기 전 «확인 화면».

    ★ 표를 «명단을 못 읽어도» 발급한다 — 여기서 404로 끊으면 뒤의 POST가
      「확인을 안 거쳤다」로 뭉개져, 명단에 없는 사람인지 확인 단계를 건너뛴
      것인지 구별할 수 없다. 못 읽었다는 사실은 화면이 그대로 말한다.
    """

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    email_clean = share_allow.normalize(email)
    member = None
    member_available = False
    try:
        with storage_db.connect() as conn:
            member = share_allow.load(conn, email_clean)
        member_available = True
    except Exception:  # noqa: BLE001 — 못 읽은 명단을 «없음»으로 보이지 않는다
        logger.error("확인 화면에서 초대 명단을 읽지 못했습니다")
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_confirm_member_remove.html",
        context=request_helpers._ctx(
            request,
            member=member,
            member_email=email_clean,
            member_available=member_available,
            member_invited_at_label=(
                _kst_timestamp_label(member.invited_at) if member else "—"
            ),
            confirm_token=issue_confirm_token(
                request,
                action="admin.member.revoke",
                target=admin_audit.target_id("member", email_clean),
            ),
        ),
    )
    return _admin_response(request, response)


@router.post("/admin/revoke")
async def admin_revoke(
    request: Request,
    email: str = Form(..., max_length=EMAIL_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
    confirm_token: str = Form("", max_length=REFERENCE_MAX_CHARS),
):
    """친구를 초대 명단에서 뺀다. 확인 화면의 1회용 표가 있어야 한다."""
    email_clean = share_allow.normalize(email)
    action = "admin.member.revoke"
    target = admin_audit.target_id("member", email_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    if not _confirmation_accepted(
        request, confirm_token, action=action, target=target
    ):
        return _confirmation_required(request, action=action, target=target)
    try:
        with storage_db.connect() as conn:
            # MEMBER 철회도 비용 원장과 무관한 비상 안전 동작이다. 권한 변경,
            # 기존 세션 폐기와 성공 감사행은 아래 한 transaction에 계속 묶는다.
            changed = share_allow.revoke(
                conn, email_clean, now_iso=clock.iso_now_kst()
            )
            session_store.delete_member_sessions_by_email(conn, email_clean)
            confirmed = share_allow.load(conn, email_clean) is None
            if not changed or not confirmed:
                raise _AdminStateUnchanged("revoke_unconfirmed")
            _queue_committed_change(
                conn,
                request,
                action=action,
                target=target,
                reason="revoked",
            )
    except Exception:  # noqa: BLE001
        logger.error("초대 철회 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request,
            action=action,
            target=target,
            reason="storage_unavailable",
        )
        return _access_page(
            request,
            template=ACCESS_MEMBERS_TEMPLATE,
            status_code=503,
            access_error_title="친구 초대를 철회하지 못했습니다.",
            access_error=(
                "초대 명단이 실제로 바뀌었는지 확인할 수 없어 성공으로 처리하지 "
                "않았습니다. 잠시 후 다시 시도해주세요."
            ),
        )
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="revoked",
    )
    return _admin_response(
        request, RedirectResponse("/admin/members", status_code=303)
    )


#: 한도 입력칸의 최대 글자 수. 숫자 하나가 들어오는 칸이라 길 이유가 없다.
_MEMBER_LIMIT_FIELD_MAX_CHARS = 16


def _optional_member_limit_int(raw: str, *, label: str) -> int | None:
    """빈 칸은 «기본값으로 되돌린다»는 뜻이다. 숫자가 아니면 거절한다."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as error:
        raise ValueError(f"{label}은(는) 숫자로 적어 주세요.") from error


def _optional_member_limit_float(raw: str, *, label: str) -> float | None:
    """빈 칸은 «기본값으로 되돌린다»는 뜻이다. 숫자가 아니면 거절한다."""
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"{label}은(는) 숫자로 적어 주세요.") from error


@router.get("/admin/members/{email}/limit", response_class=HTMLResponse)
async def admin_member_limit_confirm(
    request: Request,
    email: str,
    daily_success_limit: str = "",
    daily_budget_krw: str = "",
    reason: str = "",
):
    """친구 한 명의 하루 한도를 바꾸기 전 «확인 화면».

    회원 화면에서 적은 값을 여기서 **지금 값과 나란히** 다시 보여 준다. 숫자
    하나를 잘못 적으면 그 친구가 하루에 쓸 수 있는 돈이 달라지므로, 바꾸기
    전에 「무엇에서 무엇으로」를 눈으로 확인하게 한다.

    ★ 빈 칸은 「기본값으로 되돌린다」는 뜻이라 그대로 기본값을 «후» 값으로 보인다.
    """

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    email_clean = share_allow.normalize(email)
    member = None
    member_available = False
    try:
        with storage_db.connect() as conn:
            member = share_allow.load(conn, email_clean)
        member_available = True
    except Exception:  # noqa: BLE001 — 못 읽은 값을 기본값처럼 보이지 않는다
        logger.error("확인 화면에서 친구 한도를 읽지 못했습니다")

    default_success = _member_default_success_limit()
    default_budget = _member_default_budget_krw()
    current_success = default_success
    current_budget = default_budget
    if member is not None:
        if member.daily_success_limit is not None:
            current_success = int(member.daily_success_limit)
        if member.daily_budget_krw is not None:
            current_budget = float(member.daily_budget_krw)

    next_success, success_error = _optional_member_limit_preview(
        daily_success_limit, label="하루 성공 건수", default=default_success
    )
    next_budget, budget_error = _optional_member_limit_preview(
        daily_budget_krw, label="하루 비용 한도", default=default_budget, decimal=True
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_confirm_member_limit.html",
        context=request_helpers._ctx(
            request,
            member=member,
            member_email=email_clean,
            member_available=member_available,
            member_current_success_label=f"{current_success}건",
            member_current_budget_label=_krw_label(current_budget),
            member_next_success_label=(
                "" if next_success is None else f"{int(next_success)}건"
            ),
            member_next_budget_label=(
                "" if next_budget is None else _krw_label(float(next_budget))
            ),
            member_default_success_label=f"{default_success}건",
            member_default_budget_label=_krw_label(default_budget),
            member_limit_preview_error=success_error or budget_error,
            member_limit_success_input=str(daily_success_limit or "").strip(),
            member_limit_budget_input=str(daily_budget_krw or "").strip(),
            member_limit_reason_input=str(reason or "").strip(),
            member_limit_reason_min_chars=DANGEROUS_ACTION_REASON_MIN_CHARS,
            member_limit_reason_max_chars=share_allow.LIMIT_REASON_MAX_CHARS,
            confirm_token=issue_confirm_token(
                request,
                action="admin.member.limit",
                target=admin_audit.target_id("member", email_clean),
            ),
        ),
    )
    return _admin_response(request, response)


def _optional_member_limit_preview(
    raw: str, *, label: str, default: float, decimal: bool = False
) -> tuple[float | None, str]:
    """확인 화면에 보일 «바꿀 값». 빈 칸은 기본값, 숫자가 아니면 오류 문구.

    ★ 여기서는 «보여 주기»만 한다. 실제 범위 검사는 저장 경로가 정본이다 —
      확인 화면이 통과시킨 값이라고 저장이 통과시키는 것은 아니다.
    """

    try:
        parsed = (
            _optional_member_limit_float(raw, label=label)
            if decimal
            else _optional_member_limit_int(raw, label=label)
        )
    except ValueError as error:
        return None, str(error)
    return (default if parsed is None else float(parsed)), ""


@router.post("/admin/members/{email}/limit")
async def admin_member_limit(
    request: Request,
    email: str,
    daily_success_limit: str = Form("", max_length=_MEMBER_LIMIT_FIELD_MAX_CHARS),
    daily_budget_krw: str = Form("", max_length=_MEMBER_LIMIT_FIELD_MAX_CHARS),
    reason: str = Form("", max_length=share_allow.LIMIT_REASON_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
    confirm_token: str = Form("", max_length=REFERENCE_MAX_CHARS),
):
    """친구 «한 명»의 하루 한도를 바꾼다.

    바꾼 값은 **다음 날에도 그대로**인 영구 값이다. 두 칸을 비우면 그 친구는
    다시 공통 기본값을 쓴다. 「오늘만 더」는 별도 표가 필요해 이번 범위 밖이다.

    ★ 왜 상수를 안 고치나 — 공통 상수를 올리면 명단 전원의 몫이 같이 올라가고
      최악의 하루 지출이 「1인 상한 × 인원」으로 곱해진다.
    ★ 초대·철회와 같은 자리에 둔다 — 명단을 바꾸는 일이라 같은 권한·CSRF·감사
      경로를 그대로 쓴다. 링크 관련 라우트는 이 요청과 아무 관계가 없다.
    """
    if deployment_mode.render_admin_no_forwarded():
        return _admin_response(
            request,
            HTMLResponse(
                "이 운영판에서는 친구 한도를 바꿀 수 없습니다.", status_code=409
            ),
        )
    email_clean = share_allow.normalize(email)
    action = "admin.member.limit"
    target = admin_audit.target_id("member", email_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    if not _confirmation_accepted(
        request, confirm_token, action=action, target=target
    ):
        return _confirmation_required(request, action=action, target=target)

    reason_clean, reason_error = _validated_action_reason(reason)
    if reason_error:
        # ★ 이유 검사를 «저장 앞»으로 당긴다 — 명단 feature는 「비어 있지 않음」만
        #   보고, 최소 길이는 위험 동작 공통 규칙이라 여기서 본다.
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_input"
        )
        return _admin_response(
            request, HTMLResponse(reason_error, status_code=400)
        )

    try:
        success_limit = _optional_member_limit_int(
            daily_success_limit, label="하루 성공 건수"
        )
        budget_krw = _optional_member_limit_float(
            daily_budget_krw, label="하루 비용 한도"
        )
    except ValueError as error:
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_input"
        )
        return _admin_response(
            request, HTMLResponse(str(error), status_code=400)
        )

    try:
        with storage_db.connect() as conn:
            _assert_access_write_ready(conn)
            # 범위·이유 검사는 명단 feature가 정본이다. 화면에서만 막으면
            # 폼을 우회한 요청이 그대로 통과한다.
            changed = share_allow.set_limits(
                conn,
                email=email_clean,
                daily_success_limit=success_limit,
                daily_budget_krw=budget_krw,
                reason=reason_clean,
                now_iso=clock.iso_now_kst(),
            )
            saved = share_allow.load(conn, email_clean)
            if (
                not changed
                or saved is None
                or saved.daily_success_limit != success_limit
                or saved.daily_budget_krw != budget_krw
            ):
                raise _AdminStateUnchanged("limit_unconfirmed")
            _queue_committed_change(
                conn,
                request,
                action=action,
                target=target,
                reason="limit_changed",
            )
            _assert_budget_store_healthy()
    except ValueError as error:
        # 범위 밖 값·빈 이유는 사람이 고칠 수 있는 입력 문제다. 저장소 장애와
        # 같은 응답으로 뭉개면 관리자가 무엇을 고쳐야 할지 알 수 없다.
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_input"
        )
        return _admin_response(
            request, HTMLResponse(str(error), status_code=400)
        )
    except Exception:  # noqa: BLE001
        logger.error("친구 한도 저장 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request,
            action=action,
            target=target,
            reason="storage_unavailable",
        )
        return _admin_response(
            request,
            HTMLResponse(
                "한도가 실제로 바뀌었는지 확인할 수 없어 성공으로 처리하지 "
                "않았습니다. 잠시 후 다시 시도해주세요.",
                status_code=503,
            ),
        )
    _mirror_committed_change(
        request,
        action=action,
        target=target,
        reason="limit_changed",
    )
    return _admin_response(
        request, RedirectResponse("/admin/members", status_code=303)
    )


# ══════════════════════════════════════════════════════════
# 신고 관리 — feedback_report 원장의 관리자 화면
# ══════════════════════════════════════════════════════════

_FEEDBACK_LIST_PATH = "/admin/feedback-reports"

#: 목록·상세 화면의 상태 배지 색상 구분. 값 목록은 feedback_constants가 정본이다.
_FEEDBACK_STATUS_TONES = {
    feedback_constants.STATUS_OPEN: "tone-open",
    feedback_constants.STATUS_REVIEWING: "tone-reviewing",
    feedback_constants.STATUS_RESOLVED: "tone-resolved",
    feedback_constants.STATUS_REJECTED: "tone-rejected",
}

#: 현재 페이지 앞뒤로 보여줄 페이지 번호 개수.
_FEEDBACK_PAGE_WINDOW = 2


def _admin_actor_digest(request: Request) -> str:
    """상태 변경 기록용 처리자 식별자. 원문 이메일은 저장하지 않는다."""
    session = auth_logic.get_session(
        request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    )
    if session is None or not session.is_admin:
        raise PermissionError("관리자 세션이 필요합니다")
    return dashboard_store.actor_digest(session.email)


def _feedback_page_numbers(page: int, page_count: int) -> list[int]:
    """현재 페이지 주변의 번호만 잘라 페이지네이션에 보여준다."""
    if page_count <= 0:
        return []
    current = min(max(1, page), page_count)
    start = max(1, current - _FEEDBACK_PAGE_WINDOW)
    end = min(page_count, current + _FEEDBACK_PAGE_WINDOW)
    return list(range(start, end + 1))


@router.get("/admin/feedback-reports", response_class=HTMLResponse)
async def admin_feedback_reports(
    request: Request,
    status: str = "",
    category: str = "",
    stage: str = "",
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    page: str = "1",
):
    """신고 목록 화면 — 상태 집계·필터·페이지네이션."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        page_number = max(1, int(str(page).strip() or "1"))
    except ValueError:
        page_number = 1
    filters = {
        "status": str(status or "").strip(),
        "category": str(category or "").strip(),
        "stage": str(stage or "").strip(),
        "date_from": str(date_from or "").strip(),
        "date_to": str(date_to or "").strip(),
        "keyword": str(keyword or "").strip(),
    }
    filter_error = ""
    page_data = None
    try:
        with storage_db.connect() as conn:
            counts = feedback_logic.count_by_status(conn)
            try:
                page_data = feedback_logic.list_reports(
                    conn, page=page_number, **filters
                )
            except feedback_logic.FeedbackReportError as error:
                # 필터 값이 계약을 벗어났다 — 집계는 유지하고 메시지를 그대로 보여준다.
                filter_error = str(error)
    except Exception:  # noqa: BLE001 — 읽지 못한 목록을 0건 정상처럼 보이지 않는다
        logger.error("신고 목록 또는 집계를 읽지 못했습니다")
        return _admin_response(
            request,
            HTMLResponse("신고 목록을 안전하게 읽지 못했습니다.", status_code=503),
        )
    active_filters = {key: value for key, value in filters.items() if value}
    filter_query = urlencode(active_filters)
    page_url_prefix = (
        f"{_FEEDBACK_LIST_PATH}?{filter_query}&page="
        if filter_query
        else f"{_FEEDBACK_LIST_PATH}?page="
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_feedback_reports.html",
        context=request_helpers._ctx(
            request,
            feedback_counts=counts,
            feedback_total=sum(counts.values()),
            feedback_page=page_data,
            feedback_filters=filters,
            feedback_filter_error=filter_error,
            feedback_status_options=feedback_constants.REPORT_STATUSES,
            feedback_category_options=feedback_constants.REPORT_CATEGORIES,
            feedback_stage_options=feedback_constants.REPORT_STAGES,
            feedback_status_tones=_FEEDBACK_STATUS_TONES,
            feedback_page_numbers=_feedback_page_numbers(
                page_data.page if page_data is not None else 1,
                page_data.page_count if page_data is not None else 0,
            ),
            feedback_page_url_prefix=page_url_prefix,
            feedback_keyword_max_chars=feedback_constants.MAX_KEYWORD_CHARS,
            # ★ 신고자 «갈래»(회원/링크 손님/비회원/관리자)만 화면에 올린다.
            #   reporter_key의 지문(해시) 부분은 이 함수가 아예 반환하지
            #   않는다 — 화면·캡처로 새어 나갈 값을 만들지 않기 위해서다.
            reporter_track_label=feedback_router.reporter_track_label,
        ),
        status_code=400 if filter_error else 200,
    )
    return _admin_response(request, response)


def _feedback_report_detail_page(
    request: Request,
    report_id: str,
    *,
    status_error: str = "",
    note_value: str | None = None,
    status_code: int = 200,
):
    """신고 상세 화면. 상태 변경 실패 시 오류 메시지와 함께 다시 그린다."""
    try:
        with storage_db.connect() as conn:
            found = feedback_logic.get_report(conn, report_id)
    except Exception:  # noqa: BLE001 — 못 읽은 신고를 없는 신고로 단정하지 않는다
        logger.error("신고 상세를 읽지 못했습니다")
        return _admin_response(
            request,
            HTMLResponse("신고 내용을 안전하게 읽지 못했습니다.", status_code=503),
        )
    if found is None:
        return _admin_response(
            request,
            HTMLResponse("해당 신고를 찾을 수 없습니다.", status_code=404),
        )
    allowed = feedback_constants.ALLOWED_STATUS_TRANSITIONS.get(
        found.status, frozenset()
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_feedback_report.html",
        context=request_helpers._ctx(
            request,
            feedback_report=found,
            feedback_status_error=status_error,
            feedback_transition_targets=[
                target
                for target in feedback_constants.REPORT_STATUSES
                if target in allowed
            ],
            feedback_status_tones=_FEEDBACK_STATUS_TONES,
            feedback_created_at_label=_kst_timestamp_label(found.created_at),
            feedback_updated_at_label=_kst_timestamp_label(found.updated_at),
            feedback_admin_note_value=(
                found.admin_note if note_value is None else note_value
            ),
            feedback_admin_note_max_chars=feedback_constants.MAX_ADMIN_NOTE_CHARS,
            # ★ 목록과 같은 함수 — 갈래만 노출, 지문은 절대 넘기지 않는다.
            reporter_track_label=feedback_router.reporter_track_label,
        ),
        status_code=status_code,
    )
    return _admin_response(request, response)


@router.get("/admin/feedback-reports/{report_id}", response_class=HTMLResponse)
async def admin_feedback_report_detail(request: Request, report_id: str):
    """신고 한 건의 전체 필드와 상태 변경 폼."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _feedback_report_detail_page(request, report_id)


@router.post("/admin/feedback-reports/{report_id}/status")
async def admin_feedback_report_status(
    request: Request,
    report_id: str,
    to_status: str = Form(..., max_length=20),
    admin_note: str = Form("", max_length=feedback_constants.MAX_ADMIN_NOTE_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """허용된 전이만 실행한다. 위반 메시지는 상세 화면에 그대로 보여준다."""
    action = "admin.feedback_report.status"
    target = admin_audit.target_id("feedback", report_id)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    try:
        actor = _admin_actor_digest(request)
    except Exception:  # noqa: BLE001 — 처리자를 확정하지 못한 변경은 시작하지 않는다
        logger.error("신고 처리자 식별자를 만들지 못했습니다")
        _audit_failed_change(
            request, action=action, target=target, reason="actor_unavailable"
        )
        return _admin_response(
            request,
            HTMLResponse(
                "처리자 정보를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.",
                status_code=503,
            ),
        )
    validation_error = ""
    try:
        with storage_db.connect() as conn:
            try:
                changed = feedback_logic.change_status(
                    conn,
                    report_id=report_id,
                    to_status=to_status,
                    admin_note=admin_note,
                    actor=actor,
                    now_iso=clock.iso_now_kst(),
                )
            except feedback_logic.FeedbackReportError as error:
                validation_error = str(error)
            else:
                _queue_committed_change(
                    conn,
                    request,
                    action=action,
                    target=admin_audit.target_id("feedback", changed.report_id),
                    reason="status_changed",
                )
    except Exception:  # noqa: BLE001 — 변경 확인 실패를 성공으로 처리하지 않는다
        logger.error("신고 상태 변경 또는 변경 확인에 실패했습니다")
        _audit_failed_change(
            request, action=action, target=target, reason="storage_unavailable"
        )
        return _admin_response(
            request,
            HTMLResponse(
                "신고 상태를 저장하지 못했습니다. 잠시 후 다시 시도해주세요.",
                status_code=503,
            ),
        )
    if validation_error:
        try:
            _audit_change(
                request,
                action=action,
                target=target,
                outcome="rejected",
                reason="transition_rejected",
            )
        except Exception:  # noqa: BLE001 — 감사 실패 시 관리자 작업을 계속하지 않는다
            return _admin_response(
                request,
                HTMLResponse(
                    "요청 기록을 남기지 못했습니다. 잠시 후 다시 시도해주세요.",
                    status_code=503,
                ),
            )
        return _feedback_report_detail_page(
            request,
            report_id,
            status_error=validation_error,
            note_value=admin_note,
            status_code=400,
        )
    _mirror_committed_change(
        request, action=action, target=target, reason="status_changed"
    )
    return _admin_response(
        request,
        RedirectResponse(
            f"{_FEEDBACK_LIST_PATH}/{quote(str(report_id).strip())}",
            status_code=303,
        ),
    )
