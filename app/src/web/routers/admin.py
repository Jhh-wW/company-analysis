"""관리자 대시보드와 사용자·공유 링크 관리 경로."""

import datetime as dt
import logging
import os
import string

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock
from src.features.observability import admin_audit, admin_audit_store
from src.features.budget import spend_store
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import constants as share_constants
from src.features.sharelink import issue as share_issue
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.features.storage import sessions as session_store
from src.web import deployment_mode, job_runtime, paid_runtime, request_helpers, runtime
from src.web.security import (
    COMPANY_MAX_CHARS,
    CSRF_TOKEN_MAX_CHARS,
    EMAIL_MAX_CHARS,
    JOB_MAX_CHARS,
    NOTE_MAX_CHARS,
    REFERENCE_MAX_CHARS,
)


router = APIRouter()
logger = logging.getLogger(__name__)
_KEY_ISSUE_ATTEMPTS = 5
_ADMIN_AUDIT_TABLE = admin_audit_store.TABLE_ADMIN_AUDIT_EVENTS
_AUDIT_SAFE_CHARS = frozenset(string.ascii_letters + string.digits + "_.:-")


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


def _link_expiry_date_label(created_at: str) -> str:
    """발급 KST 날짜와 현재 LINK 수명 정책으로 만료 날짜를 표시한다."""

    try:
        issued = clock.business_date_from_iso(created_at)
        expires = issued + dt.timedelta(
            days=share_logic.link_max_age_days_from_env()
        )
    except (OverflowError, TypeError, ValueError):
        return "확인 불가"
    return f"{expires:%Y-%m-%d} (한국시간 00:00부터 닫힘)"


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


def _linked_report_state(conn, link: share_store.ShareLink) -> str:
    """연결 보고서의 현재 공개 가능 상태를 링크 표시·진입 시점에 다시 확인한다."""
    if not link.report_id:
        return "none"
    report = report_store.load(conn, link.report_id)
    if report is None:
        return "missing"
    return "expired" if job_runtime._link_expired(report) else "active"


def _assert_budget_store_healthy() -> None:
    with paid_runtime._PAID_PHASE_LOCK:
        if not paid_runtime._BUDGET_STORE_HEALTHY:
            raise _AccessDataUnavailable("budget_health")


def _paid_research_status() -> tuple[bool, str]:
    """관리자가 유료 조사 차단 여부와 사람이 풀어야 하는 이유를 확인하게 한다."""

    with paid_runtime._PAID_PHASE_LOCK:
        if not paid_runtime._BUDGET_STORE_HEALTHY:
            return (
                True,
                "비용 기록을 복원할 수 없어 모든 유료 조사를 닫았습니다. "
                "관리자가 원장과 손상 기록을 확인해야 다시 열립니다.",
            )
    with paid_runtime._SLOT_LOCK:
        if paid_runtime._UNRESOLVED_BUCKETS:
            return (
                True,
                "provider 과금 여부를 확정하지 못한 통장의 유료 조사를 닫았습니다. "
                "관리자가 미확정 비용을 대사해야 해당 통장이 다시 열립니다.",
            )
    return False, ""


def _assert_access_write_ready(conn) -> None:
    """예산 노출을 바꾸기 전 목록·원장 정본을 모두 읽을 수 있어야 한다."""
    _assert_budget_store_healthy()
    share_store.list_all(conn)
    share_allow.list_all(conn)
    spend_store.ensure_schema(conn)
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
    _assert_budget_store_healthy()

    try:
        with storage_db.connect() as conn:
            links = share_store.list_all(conn)
            members = share_allow.list_all(conn)
            spend_store.ensure_schema(conn)
            spend = spend_store.load_day(conn, today)
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
        if share_logic.is_share_link_expired(link.created_at)
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
        link.key: _link_expiry_date_label(link.created_at) for link in links
    }
    member_invited_at_labels = {
        member.email: _kst_timestamp_label(member.invited_at)
        for member in members
    }
    # MEMBER는 금액 통장이 아니라 KST 성공 보고서 3건으로 제한한다. 여기에 임의
    # 금액을 넣으면 폐기한 1,000원 정책이 다시 운영 수치로 살아난다.
    configured_stop_threshold = (
        active_link_count * (share_tracks.budget_of(share_tracks.Track.LINK) or 0.0)
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
        spent_today=spend.total_krw,
        configured_stop_threshold_krw=configured_stop_threshold,
        actual_over_threshold_krw=max(
            0.0, spend.total_krw - configured_stop_threshold
        ),
        estimate_overrun_count=overrun.count,
        estimate_overrun_krw=overrun.excess_krw,
        business_day_label=clock.business_day_label(today),
        access_data_available=True,
        paid_research_closed=paid_research_closed,
        paid_research_closed_reason=paid_research_closed_reason,
        job_max_chars=JOB_MAX_CHARS,
        note_max_chars=NOTE_MAX_CHARS,
        reference_max_chars=REFERENCE_MAX_CHARS,
        **kwargs,
    )


def _access_page(
    request: Request, *, status_code: int = 200, **context
) -> HTMLResponse:
    today = clock.today_kst()
    try:
        page_context = _access_context(request, today=today, **context)
    except _AccessDataUnavailable:
        status_code = 503
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
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_access.html",
        context=page_context,
        status_code=status_code,
    )
    return _admin_response(request, response)


def _validated_report_id(
    conn, reference: str, *, expected_company: str = ""
) -> tuple[str, str]:
    """공개 폼의 결과 참조가 실제로 저장돼 아직 열리는 보고서인지 확인한다."""
    del expected_company
    if not reference.strip():
        return "", ""
    report_id = share_logic.report_id_from_reference(reference)
    if not report_id:
        return "", "결과 화면 주소 또는 32자리 보고서 ID를 확인해주세요."
    report = report_store.load(conn, report_id)
    if report is None:
        return "", "이 데모 저장소에서 해당 보고서를 찾을 수 없습니다."
    if job_runtime._link_expired(report):
        return "", "공유 기간이 지난 보고서입니다. 새 보고서를 만든 뒤 연결해주세요."
    return report_id, ""


@router.get("/admin/access", response_class=HTMLResponse)
async def admin_access(request: Request):
    """초대한 친구와 지원 맥락 LINK를 관리하는 화면."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _access_page(request)


@router.post("/admin/link/new", include_in_schema=False)
@router.post("/admin/links/new")
async def admin_link_new(
    request: Request,
    company: str = Form(..., max_length=COMPANY_MAX_CHARS),
    job: str = Form("", max_length=JOB_MAX_CHARS),
    note: str = Form("", max_length=NOTE_MAX_CHARS),
    report_reference: str = Form("", max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """지원 회사·직무 꼬리표가 붙은 LINK를 새로 발급한다."""
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
    form_values = {
        "company": company_clean,
        "job": job_clean,
        "note": note.strip(),
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
                    or inserted.report_id != report_id
                ):
                    raise _AdminStateUnchanged("link_insert_unconfirmed")
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
    response = Response(
        content=f"{issued_url}\n",
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="company-analysis-link.txt"',
            "X-Link-Identifier": share_store.key_hash_of(key),
            "Referrer-Policy": "no-referrer",
        },
    )
    # 원문 capability는 이 일회성 다운로드에만 실리고 DB·HTML·로그·관리 URL에는 없다.
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
    try:
        with storage_db.connect() as conn:
            link = share_store.load_by_hash(conn, key_hash)
            if link is not None:
                report_state = _linked_report_state(conn, link)
                open_events = share_store.list_open_events_by_hash(conn, link.key_hash)
                generated_runs = share_store.list_runs_by_hash(conn, link.key_hash)
                for run in generated_runs:
                    if not run.report_id:
                        run_report_states[run.run_id] = "none"
                    else:
                        report = report_store.load(conn, run.report_id)
                        run_report_states[run.run_id] = (
                            "missing" if report is None else "available"
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
            link_expired=share_logic.is_share_link_expired(link.created_at),
            link_revoked=link.is_revoked,
            link_revoked_at_label=_kst_timestamp_label(link.revoked_at),
            link_created_at_label=_kst_timestamp_label(link.created_at),
            link_expiry_date_label=_link_expiry_date_label(link.created_at),
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
                "generation_start_failed": "생성 시작 중 기술 오류",
                "generation_not_started": "생성을 시작하지 못함",
                "automatic_release_gate_stopped": "자동출고 검사를 통과하지 못함",
            },
            reference_max_chars=REFERENCE_MAX_CHARS,
            is_deployed=False,
            qr_svg="",
            report_state=report_state,
        ),
        status_code=status_code,
    )
    return _admin_response(request, response)


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
            request, RedirectResponse("/admin/access", status_code=303)
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
            request, RedirectResponse("/admin/access", status_code=303)
        )
    return _admin_response(
        request,
        RedirectResponse(f"/admin/reports/{clean_report_id}", status_code=303),
    )


@router.post("/admin/link/report", include_in_schema=False)
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
                    conn, report_reference, expected_company=link.company
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


@router.post("/admin/link/delete", include_in_schema=False)
@router.post("/admin/links/revoke")
async def admin_link_delete(
    request: Request,
    key: str = Form(..., max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """링크를 닫는다."""
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
            HTMLResponse("관리자 전용 운영판에서는 친구 MEMBER 초대를 보류했습니다.", status_code=409),
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
        request, RedirectResponse("/admin/access", status_code=303)
    )


@router.post("/admin/revoke")
async def admin_revoke(
    request: Request,
    email: str = Form(..., max_length=EMAIL_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """친구를 초대 명단에서 뺀다."""
    email_clean = share_allow.normalize(email)
    action = "admin.member.revoke"
    target = admin_audit.target_id("member", email_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
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
        request, RedirectResponse("/admin/access", status_code=303)
    )
