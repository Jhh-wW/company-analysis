"""승인된 관리자 운영 대시보드와 MEMBER 피드백 입구.

이 라우터는 공개 JSON을 만들지 않는다. 새로고침도 인증된 HTML 조각만 돌려주며,
모든 POST는 기존 세션 CSRF 방어를 지난다.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.admin_dashboard import weekly as dashboard_weekly
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.features.report_standard import PublishBlockedError, build_published_report
from src.web import request_helpers
from src.web.security import CSRF_TOKEN_MAX_CHARS, REFERENCE_MAX_CHARS


router = APIRouter()
_FEEDBACK_MAX_CHARS = 3000
_CORRECTED_PAYLOAD_MAX_CHARS = 250_000
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _status_labels() -> dict[str, str]:
    return {
        dashboard_store.REPORT_STATUS_PENDING: "확인 대기",
        dashboard_store.REPORT_STATUS_FIXING: "수정 중",
        dashboard_store.REPORT_STATUS_RECHECKING: "재검사 중",
        dashboard_store.REPORT_STATUS_RECHECK_FAILED: "재검사 실패",
        dashboard_store.REPORT_STATUS_NORMAL: "정상",
    }


def _company_labels() -> dict[str, str]:
    return {
        dashboard_store.COMPANY_LISTED: "상장사",
        dashboard_store.COMPANY_AUDITED: "비상장 외감",
        dashboard_store.COMPANY_UNDECIDED: "판정 전 종료",
    }


def _admin_response(request: Request, response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _admin_email(request: Request) -> str:
    session = auth_logic.get_session(
        request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    )
    if session is None or not session.is_admin:
        raise PermissionError("관리자 세션이 필요합니다")
    return session.email


def _member_action(request: Request, csrf_token: str) -> tuple[Response | None, str]:
    """링크/익명 보고가 아니라 초대 MEMBER의 설문·신고만 받는다."""
    csrf_blocked = request_helpers.require_csrf(request, csrf_token)
    if csrf_blocked is not None:
        return csrf_blocked, ""
    session = auth_logic.get_session(
        request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    )
    if session is None or session.is_admin:
        return HTMLResponse("초대된 MEMBER만 이 작업을 할 수 있습니다.", status_code=403), ""
    try:
        with storage_db.connect() as conn:
            allowed = share_allow.is_allowed(conn, session.email)
    except Exception:
        return HTMLResponse("권한 상태를 확인할 수 없습니다.", status_code=503), ""
    if not allowed:
        return HTMLResponse("초대된 MEMBER만 이 작업을 할 수 있습니다.", status_code=403), ""
    return None, session.email


def _report_rows(conn, *, limit: int = 100) -> list[dict[str, object]]:
    rows = conn.execute(
        f"""SELECT r.report_id, r.corp_id, r.job, r.created_at,
                   s.status, s.blocked, s.company_type, s.version, s.updated_at
            FROM {storage_constants.TABLE_REPORTS} AS r
            LEFT JOIN {dashboard_store.TABLE_REPORT_STATES} AS s
              ON s.report_id = r.report_id
            LEFT JOIN {dashboard_store.TABLE_REPORT_TRASH} AS t
              ON t.report_id = r.report_id AND t.status <> 'active'
            WHERE t.report_id IS NULL
            ORDER BY r.created_at DESC LIMIT ?""",
        (max(1, min(limit, 500)),),
    ).fetchall()
    out: list[dict[str, object]] = []
    for row in rows:
        state = _report_state_for_dashboard(conn, str(row["report_id"]))
        out.append(
            {
                "report_id": str(row["report_id"]),
                "corp_id": str(row["corp_id"]),
                "job": str(row["job"]),
                "created_at": str(row["created_at"]),
                "status": state.status,
                "blocked": state.blocked,
                "company_type": state.company_type,
                "version": state.version,
                "updated_at": state.updated_at,
            }
        )
    return out


def _report_state_for_dashboard(conn, report_id: str, *, report=None):
    """새 projection 전의 저장 보고서도 원본 회사 유형을 정직하게 표시한다."""
    state = dashboard_store.get_report_state(conn, report_id)
    if state.updated_at:
        return state
    loaded = report if report is not None else report_store.load(conn, report_id)
    if loaded is None:
        return state
    return replace(
        state,
        company_type=dashboard_store.company_type_from_report(loaded.corp_type),
    )


def _member_company_counts(conn) -> dict[str, int]:
    """친구 이용 화면에는 MEMBER 성공 보고서만 회사 유형별로 센다."""
    counts = {company_type: 0 for company_type in dashboard_store.COMPANY_TYPES}
    rows = conn.execute(
        f"""SELECT report_id FROM {dashboard_store.TABLE_MEMBER_USAGE}
        WHERE state = ? AND report_id <> '' ORDER BY settled_at DESC""",
        (dashboard_store.MEMBER_USAGE_USED,),
    ).fetchall()
    for row in rows:
        report_id = str(row["report_id"])
        if dashboard_store.report_is_trashed(conn, report_id):
            continue
        report = report_store.load(conn, report_id)
        if report is None:
            continue
        state = _report_state_for_dashboard(conn, report_id, report=report)
        counts[state.company_type] += 1
    return counts


def _link_rows_for_dashboard(conn):
    """최근 접속 순서와 관리자 미확인 접속 수만 목록 projection으로 만든다."""
    links = sorted(
        share_store.list_all(conn),
        key=lambda link: (link.last_opened_at or link.created_at, link.created_at, link.key_hash),
        reverse=True,
    )
    unseen: dict[str, int] = {}
    for link in links:
        last_seen = dashboard_store.link_open_seen_id(conn, key_hash=link.key_hash)
        unseen[link.key_hash] = sum(
            event.id > last_seen
            for event in share_store.list_open_events_by_hash(conn, link.key_hash)
        )
    return links, unseen


def _member_period(request: Request) -> tuple[str, str]:
    requested = request.query_params.get("period", "all")
    if requested == "7d":
        return "7d", (clock.today_kst() - timedelta(days=6)).isoformat()
    if requested == "30d":
        return "30d", (clock.today_kst() - timedelta(days=29)).isoformat()
    return "all", ""


def _last_completed_week_start() -> str:
    """수동 실행도 정본과 같은 직전 월요일~일요일만 대상으로 삼는다."""
    today = clock.today_kst()
    this_monday = today - timedelta(days=today.weekday())
    return (this_monday - timedelta(days=7)).isoformat()


def _settings_context(request: Request, *, edit: bool) -> dict:
    providers = (
        ("Google", bool(os.environ.get("GOOGLE_API_KEY", "").strip())),
        ("DART", bool(os.environ.get("DART_API_KEY", "").strip())),
        ("Naver", bool(os.environ.get("NAVER_CLIENT_ID", "").strip())),
        ("Anthropic", bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())),
        ("Places", bool(os.environ.get("GOOGLE_PLACES_API_KEY", "").strip())),
        ("백업 저장소", False),
    )
    try:
        with storage_db.connect() as conn:
            service = asdict(dashboard_store.get_service_state(conn))
    except Exception:
        service = {
            "status": "unavailable", "cause": "", "impact": "", "next_action": "", "updated_at": "",
        }
    try:
        with storage_db.connect() as conn:
            external_statuses = dashboard_store.external_status_cards(conn, providers=providers)
    except Exception:
        external_statuses = [
            {
                "provider": provider,
                "status": "unavailable",
                "last_success_at": "",
                "error_at": "",
                "error_summary": "확인 불가",
            }
            for provider, _configured in providers
        ]
    try:
        with storage_db.connect() as conn:
            weekly_reports = [asdict(item) for item in dashboard_store.list_weekly_reports(conn)]
            trash_reports = [asdict(item) for item in dashboard_store.list_trashed_reports(conn)]
            operation_claims = dashboard_store.list_operation_claims(conn)
    except Exception:
        weekly_reports, trash_reports, operation_claims = [], [], []
    return request_helpers._ctx(
        request,
        dashboard_service=service,
        dashboard_external_statuses=external_statuses,
        dashboard_settings_edit=edit,
        dashboard_weekly_reports=weekly_reports,
        dashboard_trash_reports=trash_reports,
        dashboard_operation_claims=operation_claims,
        dashboard_default_week_start=_last_completed_week_start(),
    )


def _dashboard_context(request: Request) -> dict:
    """오늘 화면과 조각 새로고침이 같은 정본을 사용한다."""
    with storage_db.connect() as conn:
        service = dashboard_store.get_service_state(conn)
        errors = dashboard_store.list_open_errors(conn, limit=25)
        incidents = dashboard_store.list_incidents(conn, limit=25)
        operation_issues = dashboard_store.list_failed_operation_issues(conn, limit=25)
        surveys_total, helpful = dashboard_store.survey_summary(conn)
        members = share_allow.list_all(conn)
        links, link_unseen = _link_rows_for_dashboard(conn)
        reports = _report_rows(conn, limit=12)
        member_company_counts = _member_company_counts(conn)
        member_usage = {
            member.email: dashboard_store.member_usage_today(
                conn, actor_email=member.email, day=clock.today_kst().isoformat()
            )
            for member in members
        }
    satisfaction = (
        "자료 모으는 중"
        if surveys_total < 5
        else f"{round(helpful * 100 / surveys_total)}% ({helpful}/{surveys_total})"
    )
    return request_helpers._ctx(
        request,
        dashboard_service=asdict(service),
        dashboard_errors=[asdict(item) for item in errors],
        dashboard_incidents=incidents,
        dashboard_operation_issues=operation_issues,
        dashboard_reports=reports,
        dashboard_members=members,
        dashboard_links=links,
        dashboard_link_unseen=link_unseen,
        dashboard_member_usage=member_usage,
        dashboard_company_counts=member_company_counts,
        dashboard_satisfaction=satisfaction,
        dashboard_survey_total=surveys_total,
        dashboard_last_updated=clock.iso_now_kst(),
        dashboard_status_labels=_status_labels(),
        dashboard_company_labels=_company_labels(),
    )


async def render_admin_home(request: Request) -> Response:
    """기존 ``/admin``의 승인 후 정식 진입점."""
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        response = request_helpers.templates.TemplateResponse(
            request=request,
            name="admin_dashboard.html",
            context=_dashboard_context(request),
        )
    except Exception:
        response = HTMLResponse("운영 데이터를 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/refresh/today", response_class=HTMLResponse, include_in_schema=False)
async def refresh_today(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        response = request_helpers.templates.TemplateResponse(
            request=request, name="_admin_today.html", context=_dashboard_context(request)
        )
    except Exception:
        response = HTMLResponse("오늘 운영 데이터를 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/issues", response_class=HTMLResponse)
async def issues_page(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        context = _dashboard_context(request)
        response = request_helpers.templates.TemplateResponse(
            request=request, name="admin_issues.html", context=context
        )
    except Exception:
        response = HTMLResponse("문제 목록을 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/refresh/issues", response_class=HTMLResponse, include_in_schema=False)
async def refresh_issues(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        context = _dashboard_context(request)
        response = request_helpers.templates.TemplateResponse(
            request=request, name="_admin_issues.html", context=context
        )
    except Exception:
        response = HTMLResponse("문제 목록을 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/reports/{report_id}", response_class=HTMLResponse)
async def admin_report_detail(request: Request, report_id: str):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            report = report_store.load(conn, report_id)
            state = _report_state_for_dashboard(conn, report_id, report=report)
            trashed = dashboard_store.trash_record(conn, report_id)
            service = dashboard_store.get_service_state(conn)
            errors = [asdict(error) for error in dashboard_store.list_open_errors(conn, limit=500) if error.report_id == report_id]
            events = conn.execute(
                f"""SELECT action, from_status, to_status, blocked, reason, created_at
                FROM {dashboard_store.TABLE_REPORT_EVENTS} WHERE report_id = ?
                ORDER BY id DESC""", (report_id,)
            ).fetchall()
            surveys = conn.execute(
                f"""SELECT actor_email, rating, overall_feedback, business_distinction,
                    add_information, delete_information, revision, updated_at
                FROM {dashboard_store.TABLE_SURVEYS} WHERE report_id = ? ORDER BY updated_at DESC""",
                (report_id,),
            ).fetchall()
        if report is None:
            return _admin_response(request, RedirectResponse("/admin/issues", status_code=303))
        response = request_helpers.templates.TemplateResponse(
            request=request,
            name="admin_report_detail.html",
            context=request_helpers._ctx(
                request, report=report, report_id=report_id, report_state=asdict(state),
                report_trash=asdict(trashed) if trashed is not None else None,
                dashboard_service=asdict(service),
                report_errors=errors, report_events=events, report_surveys=surveys,
                dashboard_status_labels=_status_labels(),
                dashboard_company_labels=_company_labels(),
                report_statuses=(dashboard_store.REPORT_STATUS_PENDING, dashboard_store.REPORT_STATUS_FIXING,
                                 dashboard_store.REPORT_STATUS_RECHECKING, dashboard_store.REPORT_STATUS_RECHECK_FAILED,
                                 dashboard_store.REPORT_STATUS_NORMAL),
                company_types=(dashboard_store.COMPANY_LISTED, dashboard_store.COMPANY_AUDITED,
                               dashboard_store.COMPANY_UNDECIDED),
            ),
        )
    except Exception:
        response = HTMLResponse("보고서 원본과 운영 상태를 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.post("/admin/reports/{report_id}/state")
async def change_report_state(
    request: Request, report_id: str,
    status: str = Form(..., max_length=40), company_type: str = Form("", max_length=40),
    reason: str = Form("", max_length=_FEEDBACK_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(request, csrf_token, action="dashboard.report.state")
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            if not report_store.exists(conn, report_id):
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            current = dashboard_store.get_report_state(conn, report_id)
            if (
                status == dashboard_store.REPORT_STATUS_NORMAL
                and not dashboard_store.report_snapshot_exists(
                    conn, report_id=report_id, version=current.version + 1
                )
            ):
                raise ValueError("재공개 전에는 검증된 수정본 원본을 먼저 등록해야 합니다.")
            state = dashboard_store.change_report_state(
                conn, report_id=report_id, next_status=status, actor_email=_admin_email(request),
                reason=reason, company_type=company_type, now_iso=clock.iso_now_kst(),
            )
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("상태 변경을 저장하지 못했습니다.", status_code=503)
    return _admin_response(request, RedirectResponse(f"/admin/reports/{report_id}", status_code=303))


@router.post("/admin/reports/{report_id}/corrected-payload")
async def register_corrected_report_payload(
    request: Request,
    report_id: str,
    corrected_payload_json: str = Form(..., max_length=_CORRECTED_PAYLOAD_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """재검사 완료한 수정 원본만 다음 공개 버전으로 append-only 보존한다."""
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action="dashboard.report.corrected_payload"
    )
    if blocked is not None:
        return blocked
    try:
        corrected_report = report_store.report_from_json(corrected_payload_json)
        build_published_report(corrected_report)
    except (TypeError, ValueError, PublishBlockedError):
        return HTMLResponse("수정 보고서 원본이 유효한 재검사 결과가 아닙니다.", status_code=400)
    try:
        with storage_db.connect() as conn:
            if not report_store.exists(conn, report_id):
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            state = dashboard_store.get_report_state(conn, report_id)
            if state.status != dashboard_store.REPORT_STATUS_RECHECKING:
                raise ValueError("수정본은 재검사 중인 보고서에만 등록할 수 있습니다.")
            captured = dashboard_store.capture_report_snapshot(
                conn,
                report_id=report_id,
                version=state.version + 1,
                payload_json=corrected_payload_json,
                actor=_admin_email(request),
                now_iso=clock.iso_now_kst(),
            )
            if not captured:
                raise ValueError("동일한 원본이나 버전이 이미 등록되어 있습니다.")
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("수정본을 안전하게 등록하지 못했습니다.", status_code=503)
    return _admin_response(request, RedirectResponse(f"/admin/reports/{report_id}", status_code=303))


@router.post("/admin/reports/{report_id}/trash")
async def move_report_to_trash(
    request: Request,
    report_id: str,
    reason: str = Form(..., max_length=_FEEDBACK_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action="dashboard.report.trash"
    )
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            if not report_store.exists(conn, report_id):
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            if not dashboard_store.trash_report(
                conn, report_id=report_id, actor_email=_admin_email(request),
                reason=reason, now_iso=clock.iso_now_kst(),
            ):
                raise ValueError("이미 휴지통에 있거나 삭제가 끝난 보고서입니다.")
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("보고서를 휴지통으로 옮기지 못했습니다.", status_code=503)
    return _admin_response(request, RedirectResponse("/admin/issues", status_code=303))


@router.post("/admin/reports/{report_id}/restore")
async def restore_report_from_trash(
    request: Request,
    report_id: str,
    reason: str = Form(..., max_length=_FEEDBACK_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action="dashboard.report.restore"
    )
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            if not dashboard_store.restore_report_from_trash(
                conn, report_id=report_id, actor_email=_admin_email(request),
                reason=reason, now_iso=clock.iso_now_kst(),
            ):
                raise ValueError("복구할 수 있는 휴지통 보고서가 아닙니다.")
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("휴지통 보고서를 복구하지 못했습니다.", status_code=503)
    return _admin_response(request, RedirectResponse(f"/admin/reports/{report_id}", status_code=303))


@router.post("/reports/{report_id}/survey")
async def submit_survey(
    request: Request, report_id: str, rating: int = Form(...),
    overall_feedback: str = Form(..., max_length=_FEEDBACK_MAX_CHARS),
    business_distinction: str = Form(..., max_length=_FEEDBACK_MAX_CHARS),
    add_information: str = Form("", max_length=_FEEDBACK_MAX_CHARS),
    delete_information: str = Form("", max_length=_FEEDBACK_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    denied, email = _member_action(request, csrf_token)
    if denied is not None:
        return denied
    try:
        with storage_db.connect() as conn:
            if not report_store.exists(conn, report_id):
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            dashboard_store.save_survey(
                conn, report_id=report_id, actor_email=email, rating=rating,
                overall_feedback=overall_feedback, business_distinction=business_distinction,
                add_information=add_information, delete_information=delete_information,
                now_iso=clock.iso_now_kst(),
                report_version=dashboard_store.get_report_state(conn, report_id).version,
            )
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("설문을 저장하지 못했습니다.", status_code=503)
    return RedirectResponse(f"/result/{report_id}", status_code=303)


@router.post("/reports/{report_id}/errors")
async def submit_error(
    request: Request, report_id: str,
    area: str = Form(..., max_length=1000), reason: str = Form(..., max_length=_FEEDBACK_MAX_CHARS),
    incident_kind: str = Form("", max_length=40),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    denied, email = _member_action(request, csrf_token)
    if denied is not None:
        return denied
    try:
        with storage_db.connect() as conn:
            if not report_store.exists(conn, report_id):
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            dashboard_store.record_error(
                conn, report_id=report_id, actor_email=email, area=area, reason=reason,
                now_iso=clock.iso_now_kst(), incident_kind=incident_kind,
            )
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("오류 신고를 저장하지 못했습니다.", status_code=503)
    return RedirectResponse(f"/result/{report_id}", status_code=303)


@router.get("/admin/members", response_class=HTMLResponse)
async def members_page(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        context = _dashboard_context(request)
        period, start_day = _member_period(request)
        with storage_db.connect() as conn:
            member_statistics = dashboard_store.member_run_statistics(conn, start_day=start_day)
        context.update(
            dashboard_member_period=period,
            dashboard_member_statistics=member_statistics,
        )
        response = request_helpers.templates.TemplateResponse(request=request, name="admin_members.html", context=context)
    except Exception:
        response = HTMLResponse("친구 이용 정보를 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/links", response_class=HTMLResponse)
async def links_page(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        context = _dashboard_context(request)
        response = request_helpers.templates.TemplateResponse(request=request, name="admin_links.html", context=context)
    except Exception:
        response = HTMLResponse("LINK 목록을 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/links/{key_hash}", response_class=HTMLResponse)
async def link_detail(request: Request, key_hash: str):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    if not share_store.is_key_hash(key_hash):
        return HTMLResponse("올바르지 않은 LINK 식별자입니다.", status_code=404)
    try:
        with storage_db.connect() as conn:
            link = share_store.load_by_hash(conn, key_hash)
            service = dashboard_store.get_service_state(conn)
            open_events = share_store.list_open_events_by_hash(conn, key_hash)
            runs = share_store.list_runs_by_hash(conn, key_hash)
            seen_open_id = dashboard_store.link_open_seen_id(conn, key_hash=key_hash)
            new_open_events = [event for event in open_events if event.id > seen_open_id]
        if link is None:
            return _admin_response(request, RedirectResponse("/admin/links", status_code=303))
        response = request_helpers.templates.TemplateResponse(
            request=request, name="admin_link_detail.html",
            context=request_helpers._ctx(
                request,
                dashboard_link=link,
                dashboard_service=asdict(service),
                dashboard_new_open_events=new_open_events,
                dashboard_runs=runs,
            ),
        )
    except Exception:
        response = HTMLResponse("LINK 이력을 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.post("/admin/links/{key_hash}/opens/confirm")
async def confirm_link_opens(
    request: Request,
    key_hash: str,
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(
        request,
        csrf_token,
        action="dashboard.link.opens.confirm",
    )
    if blocked is not None:
        return blocked
    if not share_store.is_key_hash(key_hash):
        return HTMLResponse("올바른 LINK 식별자가 아닙니다.", status_code=404)
    try:
        with storage_db.connect() as conn:
            if share_store.load_by_hash(conn, key_hash) is None:
                return HTMLResponse("존재하지 않는 LINK입니다.", status_code=404)
            events = share_store.list_open_events_by_hash(conn, key_hash)
            if events:
                dashboard_store.mark_link_opens_seen(
                    conn,
                    key_hash=key_hash,
                    last_seen_open_id=events[-1].id,
                    actor_email=_admin_email(request),
                    now_iso=clock.iso_now_kst(),
                )
    except Exception:
        return HTMLResponse("새 접속 확인을 저장하지 못했습니다.", status_code=503)
    return _admin_response(request, RedirectResponse(f"/admin/links/{key_hash}", status_code=303))


@router.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        response = request_helpers.templates.TemplateResponse(
            request=request, name="admin_settings.html",
            context=_settings_context(request, edit=False),
        )
    except Exception:
        response = HTMLResponse("설정 상태를 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/settings/change", response_class=HTMLResponse)
async def settings_change_page(request: Request):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        response = request_helpers.templates.TemplateResponse(
            request=request, name="admin_settings.html",
            context=_settings_context(request, edit=True),
        )
    except Exception:
        response = HTMLResponse("설정 상태를 안전하게 읽지 못했습니다.", status_code=503)
    return _admin_response(request, response)


@router.get("/admin/settings/weekly-reports/{week_start}/download")
async def download_weekly_report(request: Request, week_start: str):
    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            workbook = dashboard_store.load_weekly_report_blob(conn, week_start=week_start)
    except ValueError:
        return HTMLResponse("올바른 주간 파일 기준일이 아닙니다.", status_code=404)
    except Exception:
        return HTMLResponse("주간 파일을 안전하게 읽지 못했습니다.", status_code=503)
    if workbook is None:
        return HTMLResponse("아직 생성되지 않은 주간 파일입니다.", status_code=404)
    response = Response(
        content=workbook,
        media_type=_XLSX_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="weekly-{week_start}.xlsx"',
        },
    )
    return _admin_response(request, response)


@router.post("/admin/settings/weekly-reports/run")
async def create_weekly_report(
    request: Request,
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action="dashboard.weekly_xlsx.run"
    )
    if blocked is not None:
        return blocked
    week_start = _last_completed_week_start()
    now_iso = clock.iso_now_kst()
    try:
        with storage_db.connect() as conn:
            claim = dashboard_store.claim_operation(
                conn,
                operation=dashboard_store.OPERATION_WEEKLY_XLSX,
                period_key=week_start,
                actor_email=_admin_email(request),
                now_iso=now_iso,
            )
    except Exception:
        return HTMLResponse("주간 파일 작업을 시작하지 못했습니다.", status_code=503)
    if claim is None:
        return _admin_response(request, RedirectResponse("/admin/settings", status_code=303))
    try:
        with storage_db.connect() as conn:
            workbook = dashboard_weekly.build_weekly_workbook(conn, week_start=week_start)
        with storage_db.connect() as conn:
            saved = dashboard_store.save_weekly_report(
                conn,
                week_start=week_start,
                workbook_blob=workbook,
                actor_email=_admin_email(request),
                now_iso=clock.iso_now_kst(),
            )
            if not saved:
                raise RuntimeError("동일 주간 파일이 이미 저장되어 있습니다")
            if not dashboard_store.complete_operation(
                conn, key=claim, status="succeeded", detail="관리자 전용 XLSX 생성 완료", now_iso=clock.iso_now_kst()
            ):
                raise RuntimeError("주간 파일 작업 상태를 마감하지 못했습니다")
    except Exception:
        try:
            with storage_db.connect() as conn:
                dashboard_store.complete_operation(
                    conn, key=claim, status="failed", detail="주간 XLSX 생성에 실패했습니다.", now_iso=clock.iso_now_kst()
                )
        except Exception:
            pass
        return HTMLResponse("주간 파일 생성에 실패했습니다. 문제 목록에서 기록을 확인해 주세요.", status_code=503)
    return _admin_response(request, RedirectResponse("/admin/settings", status_code=303))


@router.post("/admin/settings/trash-cleanup/run")
async def run_trash_cleanup(
    request: Request,
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(
        request, csrf_token, action="dashboard.trash_cleanup.run"
    )
    if blocked is not None:
        return blocked
    now_iso = clock.iso_now_kst()
    day = clock.today_kst().isoformat()
    day_start_iso = f"{day}T00:00:00+09:00"
    try:
        with storage_db.connect() as conn:
            claim = dashboard_store.claim_operation(
                conn,
                operation=dashboard_store.OPERATION_TRASH_CLEANUP,
                period_key=day,
                actor_email=_admin_email(request),
                now_iso=now_iso,
            )
    except Exception:
        return HTMLResponse("휴지통 정리 작업을 시작하지 못했습니다.", status_code=503)
    if claim is None:
        return _admin_response(request, RedirectResponse("/admin/settings", status_code=303))
    try:
        with storage_db.connect() as conn:
            stopped = dashboard_store.fail_stalled_operations(
                conn, before_iso=day_start_iso, now_iso=clock.iso_now_kst()
            )
            purged = dashboard_store.purge_expired_trash(conn, now_iso=clock.iso_now_kst())
            if not dashboard_store.complete_operation(
                conn,
                key=claim,
                status="succeeded",
                detail=f"30일 경과 휴지통 {purged}건 정리 완료, 이전 날짜 멈춘 작업 {stopped}건 실패 처리",
                now_iso=clock.iso_now_kst(),
            ):
                raise RuntimeError("휴지통 정리 작업 상태를 마감하지 못했습니다")
    except Exception:
        try:
            with storage_db.connect() as conn:
                dashboard_store.complete_operation(
                    conn, key=claim, status="failed", detail="휴지통 30일 정리에 실패했습니다.", now_iso=clock.iso_now_kst()
                )
        except Exception:
            pass
        return HTMLResponse("휴지통 정리에 실패했습니다. 문제 목록에서 기록을 확인해 주세요.", status_code=503)
    return _admin_response(request, RedirectResponse("/admin/settings", status_code=303))


@router.post("/admin/settings/service")
async def set_service(
    request: Request, status: str = Form(..., max_length=30),
    cause: str = Form("", max_length=_FEEDBACK_MAX_CHARS),
    impact: str = Form("", max_length=_FEEDBACK_MAX_CHARS),
    next_action: str = Form("", max_length=_FEEDBACK_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    blocked = request_helpers.require_admin_action(request, csrf_token, action="dashboard.service.state")
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            dashboard_store.set_service_state(
                conn, status=status, cause=cause, impact=impact, next_action=next_action,
                actor_email=_admin_email(request), now_iso=clock.iso_now_kst(),
            )
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("서비스 상태를 저장하지 못했습니다.", status_code=503)
    return _admin_response(request, RedirectResponse("/admin/settings", status_code=303))
