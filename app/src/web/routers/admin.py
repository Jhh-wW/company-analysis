"""관리자 대시보드와 사용자·공유 링크 관리 경로."""

import datetime as dt
import logging
import os
import string
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core import clock
from src.features.observability import lifecycle
from src.features.observability import admin_audit
from src.features.observability.metrics import build_dashboard
from src.features.observability.records import read_records
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
from src.web import job_runtime, paid_runtime, request_helpers, runtime
from src.web.recording import records_path
from src.web.security import (
    COMPANY_MAX_CHARS,
    CSRF_TOKEN_MAX_CHARS,
    EMAIL_MAX_CHARS,
    NOTE_MAX_CHARS,
    REFERENCE_MAX_CHARS,
)


router = APIRouter()
logger = logging.getLogger(__name__)
_KEY_ISSUE_ATTEMPTS = 5
_ADMIN_AUDIT_TABLE = "admin_audit_events"
_AUDIT_SAFE_CHARS = frozenset(string.ascii_letters + string.digits + "_.:-")
_CREATE_ADMIN_AUDIT_SQL = f"""
CREATE TABLE IF NOT EXISTS {_ADMIN_AUDIT_TABLE} (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time  TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    actor_id    TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    reason_code TEXT NOT NULL
)
"""
_CREATE_ADMIN_AUDIT_NO_UPDATE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {_ADMIN_AUDIT_TABLE}_no_update
BEFORE UPDATE ON {_ADMIN_AUDIT_TABLE}
BEGIN SELECT RAISE(ABORT, 'admin audit events are append-only'); END
"""
_CREATE_ADMIN_AUDIT_NO_DELETE_SQL = f"""
CREATE TRIGGER IF NOT EXISTS {_ADMIN_AUDIT_TABLE}_no_delete
BEFORE DELETE ON {_ADMIN_AUDIT_TABLE}
BEGIN SELECT RAISE(ABORT, 'admin audit events are append-only'); END
"""


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
    conn.execute(_CREATE_ADMIN_AUDIT_SQL)
    conn.execute(_CREATE_ADMIN_AUDIT_NO_UPDATE_SQL)
    conn.execute(_CREATE_ADMIN_AUDIT_NO_DELETE_SQL)
    conn.execute(
        f"""
        INSERT INTO {_ADMIN_AUDIT_TABLE}
            (event_time, request_id, actor_id, action, target_id, outcome, reason_code)
        VALUES (?, ?, ?, ?, ?, 'success', ?)
        """,
        (
            clock.iso_now_kst(),
            _safe_audit_field(admin_audit.request_id(request), max_chars=64),
            _safe_audit_field(admin_audit.actor_id(request), max_chars=80),
            _safe_audit_field(action, max_chars=64),
            _safe_audit_field(target, max_chars=80),
            _safe_audit_field(reason, max_chars=48),
        ),
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
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return request_helpers.templates.TemplateResponse(
        request=request, name="admin_home.html", context=request_helpers._ctx(request)
    )


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """운영자 품질 대시보드."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    today = clock.today_kst()
    job_runtime._sweep_jobs(time.monotonic())
    read = read_records(records_path())
    final_records = []
    try:
        with storage_db.connect() as conn:
            lifecycle.ensure_schema(conn)
            final_records = lifecycle.list_final(conn)
    except Exception:  # noqa: BLE001 — JSONL 옛 이력은 계속 보여 주되 손상을 알린다
        logger.exception("SQLite 최종 관측값을 읽지 못했습니다")

    month_spend = None
    try:
        with paid_runtime._PAID_PHASE_LOCK:
            _assert_budget_store_healthy()
            with paid_runtime._SLOT_LOCK:
                known_active = frozenset(paid_runtime._ACTIVE_PAID_PHASES)
            with storage_db.connect() as conn:
                spend_store.ensure_schema(conn)
                month_spend = spend_store.load_month(
                    conn,
                    today,
                    known_active=known_active,
                )
            _assert_budget_store_healthy()
    except Exception:  # noqa: BLE001 — 0원으로 꾸미지 않고 화면에 확인 불가로 표시한다
        month_spend = None
        logger.exception("월 비용 원장을 읽지 못했습니다")

    dashboard = build_dashboard(
        [*read.records, *final_records],
        today=today,
        model=runtime._current_model(),
        cost_month_krw_override=(
            month_spend.total_krw if month_spend is not None else None
        ),
        cost_ledger_error=month_spend is None,
        unresolved_cost_runs=(
            month_spend.unresolved_runs if month_spend is not None else 0
        ),
        cost_ledger_since=(
            month_spend.ledger_since if month_spend is not None else ""
        ),
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=request_helpers._ctx(
            request,
            dashboard=dashboard,
            skipped=read.skipped,
            business_day_label=clock.business_day_label(today),
        ),
        status_code=503 if month_spend is None else 200,
    )
    return _admin_response(request, response)


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
    active_link_count = len(links) - len(expired_link_keys)
    configured_stop_threshold = (
        active_link_count * share_tracks.budget_of(share_tracks.Track.LINK)
        + len(members) * share_tracks.budget_of(share_tracks.Track.MEMBER)
        + share_tracks.budget_of(share_tracks.Track.ADMIN)
    )
    return request_helpers._ctx(
        request,
        links=links,
        active_link_count=active_link_count,
        expired_link_keys=expired_link_keys,
        report_states=report_states,
        members=members,
        spent_today=spend.total_krw,
        configured_stop_threshold_krw=configured_stop_threshold,
        actual_over_threshold_krw=max(
            0.0, spend.total_krw - configured_stop_threshold
        ),
        estimate_overrun_count=overrun.count,
        estimate_overrun_krw=overrun.excess_krw,
        business_day_label=clock.business_day_label(today),
        access_data_available=True,
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
        page_context = request_helpers._ctx(
            request,
            links=[],
            active_link_count=None,
            expired_link_keys=set(),
            report_states={},
            members=[],
            spent_today=None,
            configured_stop_threshold_krw=None,
            actual_over_threshold_krw=None,
            estimate_overrun_count=None,
            estimate_overrun_krw=None,
            business_day_label=clock.business_day_label(today),
            access_data_available=False,
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
    if expected_company and not share_logic.scope_matches(
        link_company=expected_company,
        company=report.company,
    ):
        return "", "링크 회사와 보고서 회사가 다릅니다. 같은 회사의 보고서만 연결해주세요."
    return report_id, ""


@router.get("/admin/access", response_class=HTMLResponse)
async def admin_access(request: Request):
    """초대한 친구와 회사별 링크를 관리하는 화면."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _access_page(request)


@router.post("/admin/link/new")
async def admin_link_new(
    request: Request,
    company: str = Form(..., max_length=COMPANY_MAX_CHARS),
    note: str = Form("", max_length=NOTE_MAX_CHARS),
    report_reference: str = Form("", max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """회사별 링크를 새로 발급한다."""
    company_clean = company.strip()
    # DB 열은 옛 링크 호환을 위해 유지하되, 회사 분석 전용 링크에는 빈 값만 저장한다.
    job_clean = ""
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
                now_iso = dt.datetime.now().isoformat()
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
                    raise RuntimeError("고유한 회사 링크 열쇠를 발급하지 못했습니다")
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
                    target=admin_audit.target_id("link", key),
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
        target=admin_audit.target_id("link", key),
        reason="created",
    )
    return _admin_response(
        request, RedirectResponse(f"/admin/link/{key}", status_code=303)
    )


def _link_detail_page(
    request: Request, key: str, *, link_error: str = "", status_code: int = 200
):
    if not share_logic.is_valid_key(key):
        return _admin_response(
            request, RedirectResponse("/admin/access", status_code=303)
        )
    report_state = "none"
    try:
        with storage_db.connect() as conn:
            link = share_store.load(conn, key.lower())
            if link is not None:
                report_state = _linked_report_state(conn, link)
    except Exception:  # noqa: BLE001
        logger.error("링크를 읽지 못했습니다")
        return _access_page(
            request,
            status_code=503,
            access_error_title="회사 링크를 불러오지 못했습니다.",
            access_error="잠시 후 다시 시도해주세요.",
        )
    if link is None:
        return _admin_response(
            request, RedirectResponse("/admin/access", status_code=303)
        )

    base = _share_base_url(request)
    path = share_issue.link_url("", link.key)
    url = share_issue.link_url(base, link.key) if base else path
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="admin_link.html",
        context=request_helpers._ctx(
            request,
            link=link,
            link_error=link_error,
            url=url,
            path=path,
            base_url=base,
            link_expired=share_logic.is_share_link_expired(link.created_at),
            is_deployed=share_issue.looks_deployed(url),
            qr_svg=share_issue.qr_svg(url) if base else "",
            report_state=report_state,
        ),
        status_code=status_code,
    )
    return _admin_response(request, response)


@router.get("/admin/link/{key}", response_class=HTMLResponse)
async def admin_link_detail(request: Request, key: str):
    """링크 하나의 주소와 QR을 보여준다."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    return _link_detail_page(request, key)


@router.post("/admin/link/report")
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
    if not share_logic.is_valid_key(key_clean):
        _audit_failed_change(
            request, action=action, target=target, reason="invalid_target"
        )
        return _access_page(
            request,
            status_code=400,
            access_error_title="회사 링크를 확인해주세요.",
            access_error="올바른 회사 링크 식별자가 아닙니다.",
        )

    validation_error = ""
    try:
        with storage_db.connect() as conn:
            _assert_access_write_ready(conn)
            link = share_store.load(conn, key_clean)
            if link is None:
                raise _AdminStateUnchanged("link_missing")
            else:
                report_id, validation_error = _validated_report_id(
                    conn, report_reference, expected_company=link.company
                )
                if not validation_error:
                    changed = share_store.set_report(conn, key_clean, report_id)
                    updated = share_store.load(conn, key_clean)
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
            key_clean,
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
        request, RedirectResponse(f"/admin/link/{key_clean}", status_code=303)
    )


@router.post("/admin/link/delete")
async def admin_link_delete(
    request: Request,
    key: str = Form(..., max_length=REFERENCE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """링크를 닫는다."""
    key_clean = key.strip().lower()
    action = "admin.link.revoke"
    target = admin_audit.target_id("link", key_clean)
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action=action, target=target
    )
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            _assert_access_write_ready(conn)
            deleted = share_store.delete(conn, key_clean)
            delete_confirmed = share_store.load(conn, key_clean) is None
            if not deleted or not delete_confirmed:
                raise _AdminStateUnchanged("link_delete_unconfirmed")
            _queue_committed_change(
                conn,
                request,
                action=action,
                target=target,
                reason="revoked",
            )
            _assert_budget_store_healthy()
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
            access_error_title="회사 링크를 닫지 못했습니다.",
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
        request, RedirectResponse("/admin/access", status_code=303)
    )


@router.post("/admin/invite")
async def admin_invite(
    request: Request,
    email: str = Form(..., max_length=EMAIL_MAX_CHARS),
    note: str = Form("", max_length=NOTE_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """친구를 초대 명단에 넣는다."""
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
                note=note,
                now_iso=dt.datetime.now().isoformat(),
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
            _assert_access_write_ready(conn)
            changed = share_allow.revoke(conn, email_clean)
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
            _assert_budget_store_healthy()
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
