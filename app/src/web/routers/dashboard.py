"""승인된 관리자 운영 대시보드와 MEMBER 피드백 입구.

이 라우터는 공개 JSON을 만들지 않는다. 새로고침도 인증된 HTML 조각만 돌려주며,
모든 POST는 기존 세션 CSRF 방어를 지난다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, replace
from datetime import timedelta
import logging
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock
from src.features.admin_dashboard import kpi as dashboard_kpi
from src.features.admin_dashboard import maintenance as dashboard_maintenance
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.report_access import logic as report_access_logic
from src.features.backup import status as backup_status
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.features.report_standard import PublishBlockedError, build_published_report
from src.web import job_runtime, paid_runtime, report_retention_adapter, request_helpers
from src.web.security import CSRF_TOKEN_MAX_CHARS, REFERENCE_MAX_CHARS


router = APIRouter()
logger = logging.getLogger(__name__)
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


def _link_timestamp_label(value: str) -> str:
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


def _link_expiry_label(created_at: str, *, expires_at: str = "") -> str:
    """이 링크가 닫히는 날을 관리자 화면용으로 표시한다.

    ★ 저장된 ``expires_at``이 있으면 그 값이 우선이다. 발급일 + 현재 수명으로만
      계산하면 관리자가 미룬 날짜와 옛 규칙으로 굳은 날짜를 둘 다 못 보여 준다.
    """

    expires = share_logic.expiry_date_of(created_at, expires_at=expires_at)
    if expires is None:
        return "확인 불가"
    return f"{expires:%Y-%m-%d} (한국시간 00:00부터 닫힘)"


def _dashboard_link_report_state(conn, link: share_store.ShareLink) -> str:
    if not link.report_id:
        return "none"
    report = report_store.load(conn, link.report_id)
    if report is None:
        return "missing"
    return "expired" if job_runtime._link_expired(report) else "active"


def _dashboard_link_company_id_state(
    conn, link: share_store.ShareLink, report_state: str
) -> str:
    """결속 보고서의 회사 고유번호 상태 (발견 F-GS2p1b · 티켓 G-S4c).

    Returns:
        ``"none"``(결속 없음) · ``"present"``(있음) · ``"missing"``(없음) ·
        ``"unknown"``(확인 못 함).

    ★ 고유번호가 없으면 이후 재결속이 «이름»만 대조하게 되어, 이름이 같은 다른
      법인이 그대로 들어온다. 관리자가 그 사실을 알아야 보고서를 다시 만든다.
    ★ 「없다」와 「못 읽었다」를 나눈다 — 같은 침묵으로 두면 읽기가 깨진 동안
      관리자는 아무 문제가 없다고 믿는다.
    ★ 저장 표의 열과 본문이 둘 다 비었을 때만 ``"missing"``이다. 열만 차 있어도
      대조는 된다.
    """

    if report_state not in ("active", "expired"):
        return "none"
    try:
        resolved = report_store.resolve_company_id(conn, link.report_id)
    except Exception:  # noqa: BLE001 — 안내 한 줄 때문에 상세 화면을 깨지 않는다
        logger.error("결속 보고서의 회사 고유번호를 읽지 못했습니다")
        return "unknown"
    return "present" if resolved else "missing"


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


def _member_report_owner_action(
    request: Request, report_id: str, csrf_token: str
) -> tuple[Response | None, str]:
    """CSRF 뒤, 쓰기 연결 전에 현재 MEMBER의 정확한 report 소유권을 확인한다."""

    denied, email = _member_action(request, csrf_token)
    if denied is not None:
        return denied, ""
    decision = report_access_logic.authorize_report_access(request, report_id)
    if decision.allowed and decision.role is report_access_logic.AccessRole.MEMBER:
        return None, email
    if decision.reason == "store_unavailable":
        return HTMLResponse("권한 상태를 확인할 수 없습니다.", status_code=503), ""
    return HTMLResponse("자신이 만든 보고서만 변경할 수 있습니다.", status_code=403), ""


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
            _unseen_open_count(event, last_seen)
            for event in share_store.list_open_events_by_hash(conn, link.key_hash)
        )
    return links, unseen


def _unseen_open_count(
    event: share_store.ShareLinkOpenEvent,
    last_seen: int,
) -> int:
    """집계 구간을 확인한 뒤 같은 구간에 늘어난 횟수만 새 요청으로 센다."""

    if event.id <= last_seen:
        return 0
    if not event.window_started_at:
        return event.opened_count
    event_base_id = event.id - event.opened_count
    already_seen = max(0, min(event.opened_count, last_seen - event_base_id))
    return event.opened_count - already_seen


def _unseen_open_events(
    events: list[share_store.ShareLinkOpenEvent],
    last_seen: int,
) -> list[share_store.ShareLinkOpenEvent]:
    return [
        replace(event, opened_count=count)
        for event in events
        if (count := _unseen_open_count(event, last_seen)) > 0
    ]


def _member_period(request: Request) -> tuple[str, str]:
    requested = request.query_params.get("period", "all")
    if requested == "7d":
        return "7d", (clock.today_kst() - timedelta(days=6)).isoformat()
    if requested == "30d":
        return "30d", (clock.today_kst() - timedelta(days=29)).isoformat()
    return "all", ""


def _settings_context(request: Request, *, edit: bool) -> dict:
    providers = (
        ("Google", bool(os.environ.get("GOOGLE_CLIENT_ID", "").strip())),
        ("DART", bool(os.environ.get("DART_API_KEY", "").strip())),
        ("Naver", bool(os.environ.get("NAVER_CLIENT_ID", "").strip())),
        ("Anthropic", bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())),
        ("Places", bool(os.environ.get("GOOGLE_PLACES_API_KEY", "").strip())),
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
    weekly_reports, weekly_available = _dashboard_read(
        "주간 파일",
        [],
        lambda conn: [asdict(item) for item in dashboard_store.list_weekly_reports(conn)],
    )
    trash_reports, trash_available = _dashboard_read(
        "휴지통",
        [],
        lambda conn: [asdict(item) for item in dashboard_store.list_trashed_reports(conn)],
    )
    operation_claims, operations_available = _dashboard_read(
        "정기 작업 이력", [], dashboard_store.list_operation_claims
    )
    current_backup_status, backup_available = _dashboard_read(
        "백업 상태",
        {
            "status": "unavailable", "last_attempt_at": "", "last_success_at": "",
            "last_failure_at": "", "last_failure_summary": "",
        },
        lambda conn: asdict(backup_status.status_view(conn, now_iso=clock.iso_now_kst())),
    )
    return request_helpers._ctx(
        request,
        dashboard_service=service,
        dashboard_external_statuses=external_statuses,
        dashboard_settings_edit=edit,
        dashboard_weekly_reports=weekly_reports,
        dashboard_weekly_available=weekly_available,
        dashboard_trash_reports=trash_reports,
        dashboard_trash_available=trash_available,
        dashboard_operation_claims=operation_claims,
        dashboard_operations_available=operations_available,
        dashboard_backup_status=current_backup_status,
        dashboard_backup_available=backup_available,
        dashboard_default_week_start=dashboard_maintenance.last_completed_week_start(
            clock.today_kst()
        ),
    )


def _dashboard_read(label: str, fallback, reader):
    """한 대시보드 조각의 실패가 다른 조각까지 503으로 번지지 않게 읽는다."""
    try:
        with storage_db.connect() as conn:
            return reader(conn), True
    except Exception:
        logger.exception("대시보드 %s 데이터를 읽지 못했습니다", label)
        return fallback, False


def _member_default_success_limit() -> int:
    """한도를 따로 안 정한 친구가 쓰는 하루 성공 보고서 건수."""
    return dashboard_store.MEMBER_DAILY_SUCCESS_LIMIT


def _member_default_budget_krw() -> float:
    """한도를 따로 안 정한 친구가 쓰는 하루 비용 상한(원)."""
    return share_tracks.budget_of(share_tracks.Track.MEMBER)


def _budget_label(amount_krw: float) -> str:
    """화면에 쓰는 금액 표기. 천 단위 쉼표 + 「원」."""
    return f"{amount_krw:,.0f}원"


def _member_limit_rows(
    members, *, available: bool
) -> tuple[dict[str, dict[str, object]], bool]:
    """친구별 «실제로 적용되는» 한도를 화면용으로 편다.

    명단을 못 읽었으면 빈 표와 False를 돌려준다 — 못 읽은 명단을 「0명·0원」으로
    보여주면 관리자가 노출이 없다고 오해한다.
    """
    if not available:
        return {}, False
    default_success = _member_default_success_limit()
    default_budget = _member_default_budget_krw()
    rows: dict[str, dict[str, object]] = {}
    for member in members:
        success = (
            default_success
            if member.daily_success_limit is None
            else int(member.daily_success_limit)
        )
        budget_krw = (
            default_budget
            if member.daily_budget_krw is None
            else float(member.daily_budget_krw)
        )
        rows[member.email] = {
            "success": success,
            "budget_krw": budget_krw,
            "budget_label": _budget_label(budget_krw),
            "reason": member.limit_reason,
            "updated_at": member.limit_updated_at,
            "customized": (
                member.daily_success_limit is not None
                or member.daily_budget_krw is not None
            ),
        }
    return rows, True


def _dashboard_context(request: Request) -> dict:
    """오늘 화면과 조각 새로고침이 같은 정본을 사용한다."""
    service, service_available = _dashboard_read(
        "서비스 상태",
        None,
        dashboard_store.get_service_state,
    )
    errors, errors_available = _dashboard_read(
        "보고서 문제", [], lambda conn: dashboard_store.list_open_errors(conn, limit=25)
    )
    incidents, incidents_available = _dashboard_read(
        "운영 사고", [], lambda conn: dashboard_store.list_active_incidents(conn, limit=25)
    )
    operation_issues, operation_issues_available = _dashboard_read(
        "정기 작업 문제",
        [],
        lambda conn: dashboard_store.list_failed_operation_issues(conn, limit=25),
    )
    survey_summary, survey_available = _dashboard_read(
        "만족도", (0, 0), dashboard_store.survey_summary
    )
    surveys_total, helpful = survey_summary
    kpi_summary, kpi_available = _dashboard_read(
        "응답 시간",
        dashboard_kpi.KpiSummary(measured_responses=0, within_target=0),
        dashboard_kpi.summary,
    )
    members, members_available = _dashboard_read(
        "친구 명단", [], share_allow.list_all
    )
    link_data, links_available = _dashboard_read(
        "지원 LINK", ([], {}), _link_rows_for_dashboard
    )
    links, link_unseen = link_data
    reports, reports_available = _dashboard_read(
        "보고서 목록", [], lambda conn: _report_rows(conn, limit=12)
    )
    resolved_issues, resolved_available = _dashboard_read(
        "최근 해결 문제",
        [],
        lambda conn: dashboard_store.list_recent_resolved_issues(conn, limit=5),
    )
    member_company_counts, member_company_counts_available = _dashboard_read(
        "친구 회사 유형", {company_type: 0 for company_type in dashboard_store.COMPANY_TYPES},
        _member_company_counts,
    )
    member_usage, member_usage_available = _dashboard_read(
        "친구 오늘 이용",
        {},
        lambda conn: {
            member.email: dashboard_store.member_usage_today(
                conn, actor_email=member.email, day=clock.today_kst().isoformat()
            )
            for member in members
        },
    )
    # ★ 회원별 하루 한도 (결정 D-G4 (a)) — 화면이 「전원 3건·3,000원」이라고 단정하면
    #   한 명만 올렸을 때 관리자가 틀린 숫자를 보고 또 올린다. 합계도 인원 곱셈이
    #   아니라 사람마다 다른 값의 «합»이어야 최악의 하루 지출을 바로 읽는다.
    #   ★ 기본값 해석을 «여기서» 하는 이유 — 성공 건수 기본값은 admin_dashboard가,
    #     비용 기본값은 sharelink가 각자 정본이다. 두 feature를 모두 아는 곳은
    #     화면을 만드는 이 자리뿐이고, 명단 표는 「덮어쓴 값이 있나」만 기억한다.
    member_limits, member_limits_available = _member_limit_rows(
        members, available=members_available
    )
    member_budget_total_krw = sum(
        float(item["budget_krw"]) for item in member_limits.values()
    )
    member_success_total = sum(int(item["success"]) for item in member_limits.values())
    incidents = sorted(incidents, key=lambda item: (str(item["created_at"]), int(item["id"])))
    operation_issues = sorted(
        operation_issues, key=lambda item: (str(item["created_at"]), str(item["operation_key"]))
    )
    # ★ 유료 조사가 통째로 막혔는지 — 2026-08-28 까지 첫 화면이 이걸 «안 읽었다».
    #   모든 유료 조사가 닫힌 날에도 관리자 첫 화면은 「문제 없음」이었다.
    유료차단, 유료차단_사유 = paid_runtime.paid_research_block()
    service_dict = (
        asdict(service)
        if service is not None
        else {
            "status": "unavailable", "cause": "", "impact": "",
            "next_action": "", "updated_at": "",
        }
    )
    if not service_available:
        primary_issue = {
            "kind": "unavailable", "title": "미해결 문제 상태를 확인할 수 없습니다",
            "status": "확인 불가", "detail": "서비스 상태 저장소를 다시 확인해 주세요.",
            "href": "/admin/issues", "action": "문제 화면 보기",
        }
    elif service_dict["status"] == dashboard_store.SERVICE_MAINTENANCE:
        primary_issue = {
            "kind": "service", "title": service_dict["cause"] or "전역 점검 중",
            "status": "전체 점검 우선", "detail": service_dict["impact"],
            "href": "/admin/settings", "action": "원인·다음 행동 보기",
        }
    elif 유료차단:
        # 개별 보고서 문제보다 앞에 둔다 — 이건 «모든» 새 조사가 멈춘 상태다.
        primary_issue = {
            "kind": "paid_research", "title": "유료 조사가 막혀 있습니다",
            "status": "새 조사 불가", "detail": 유료차단_사유,
            "href": "/admin/access", "action": "원장 다시 읽기",
        }
    elif errors:
        issue = errors[0]
        primary_issue = {
            "kind": "report", "title": issue.area,
            "status": _status_labels().get(issue.status, "확인 대기"),
            "detail": issue.reason,
            "href": f"/admin/reports/{issue.report_id}", "action": "문제 자세히 보기",
        }
    elif operation_issues:
        issue = operation_issues[0]
        primary_issue = {
            "kind": "operation", "title": str(issue["operation"]),
            "status": "정기 작업 실패", "detail": str(issue["detail"]),
            "href": "/admin/settings", "action": "설정 확인",
        }
    elif incidents:
        issue = incidents[0]
        primary_issue = {
            "kind": "incident", "title": str(issue["summary"]),
            "status": "지연·비용 주의", "detail": str(issue["kind"]),
            "href": "/admin/issues", "action": "운영 기록 보기",
        }
    elif all((errors_available, incidents_available, operation_issues_available)):
        primary_issue = None
    else:
        primary_issue = {
            "kind": "unavailable", "title": "일부 문제 목록을 확인할 수 없습니다",
            "status": "확인 불가", "detail": "읽지 못한 항목만 다시 확인해 주세요.",
            "href": "/admin/issues", "action": "문제 화면 보기",
        }
    satisfaction = (
        "확인 불가"
        if not survey_available
        else
        "자료 모으는 중"
        if surveys_total < 5
        else f"{round(helpful * 100 / surveys_total)}% ({helpful}/{surveys_total})"
    )
    three_minute_response = (
        "확인 불가"
        if not kpi_available
        else
        "자료 모으는 중"
        if kpi_summary.measured_responses < 5
        else (
            f"{round(kpi_summary.within_target * 100 / kpi_summary.measured_responses)}% "
            f"({kpi_summary.within_target}/{kpi_summary.measured_responses})"
        )
    )
    return request_helpers._ctx(
        request,
        dashboard_service=service_dict,
        dashboard_service_available=service_available,
        dashboard_errors=[asdict(item) for item in errors],
        dashboard_errors_available=errors_available,
        dashboard_incidents=incidents,
        dashboard_incidents_available=incidents_available,
        dashboard_operation_issues=operation_issues,
        dashboard_operation_issues_available=operation_issues_available,
        dashboard_primary_issue=primary_issue,
        dashboard_reports=reports,
        dashboard_reports_available=reports_available,
        dashboard_resolved_issues=[asdict(item) for item in resolved_issues],
        dashboard_resolved_available=resolved_available,
        dashboard_members=members,
        dashboard_members_available=members_available,
        dashboard_links=links,
        dashboard_links_available=links_available,
        dashboard_link_unseen=link_unseen,
        dashboard_member_usage=member_usage,
        dashboard_member_usage_available=member_usage_available,
        dashboard_member_limits=member_limits,
        dashboard_member_limits_available=member_limits_available,
        dashboard_member_budget_total_label=_budget_label(member_budget_total_krw),
        dashboard_member_success_total_label=f"{member_success_total}건",
        dashboard_member_default_success=_member_default_success_limit(),
        dashboard_member_default_budget_label=_budget_label(
            _member_default_budget_krw()
        ),
        dashboard_company_counts=member_company_counts,
        dashboard_company_counts_available=member_company_counts_available,
        dashboard_satisfaction=satisfaction,
        dashboard_survey_total=surveys_total,
        dashboard_survey_available=survey_available,
        dashboard_three_minute_response=three_minute_response,
        dashboard_kpi_measured=kpi_summary.measured_responses,
        dashboard_kpi_available=kpi_available,
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
            snapshot_row = conn.execute(
                f"""SELECT payload_json FROM {dashboard_store.TABLE_REPORT_VERSIONS}
                WHERE report_id = ? AND version = ?""",
                (report_id, state.version),
            ).fetchone()
            if report is not None and snapshot_row is not None:
                try:
                    report = report_store.report_from_json(
                        str(snapshot_row["payload_json"])
                    )
                except (KeyError, TypeError, ValueError):
                    # 옛 운영 자료에 불완전한 스냅샷이 있어도 원본 상세는 계속 연다.
                    pass
            trashed = dashboard_store.trash_record(conn, report_id)
            service = dashboard_store.get_service_state(conn)
            errors = [
                asdict(error)
                for error in dashboard_store.list_report_error_history(
                    conn, report_id=report_id
                )
            ]
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


@router.get(
    "/admin/reports/{report_id}/versions/{version}",
    response_class=HTMLResponse,
)
async def admin_report_snapshot(
    request: Request, report_id: str, version: int
):
    """MEMBER 설문에 결속된 과거 보고서 버전을 읽기 전용으로 보여준다."""

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    if version < 1:
        return _admin_response(
            request,
            HTMLResponse("올바른 보고서 버전이 아닙니다.", status_code=404),
        )
    try:
        with storage_db.connect() as conn:
            snapshot = dashboard_store.get_report_snapshot(
                conn, report_id=report_id, version=version
            )
        if snapshot is None:
            response = HTMLResponse(
                "설문 당시 보고서 스냅샷을 찾을 수 없습니다.", status_code=404
            )
        else:
            report = report_store.report_from_json(snapshot.payload_json)
            response = request_helpers.templates.TemplateResponse(
                request=request,
                name="admin_report_snapshot.html",
                context=request_helpers._ctx(
                    request,
                    report=report,
                    report_snapshot=asdict(snapshot),
                ),
            )
    except dashboard_store.ReportSnapshotIntegrityError:
        logger.error("보고서 스냅샷 SHA-256 무결성 확인에 실패했습니다")
        response = HTMLResponse(
            "설문 당시 보고서 스냅샷의 무결성을 확인할 수 없습니다.",
            status_code=503,
        )
    except (KeyError, TypeError, ValueError):
        response = HTMLResponse(
            "설문 당시 보고서 스냅샷을 안전하게 해석할 수 없습니다.",
            status_code=503,
        )
    except Exception:
        logger.error("보고서 스냅샷을 안전하게 읽지 못했습니다")
        response = HTMLResponse(
            "설문 당시 보고서 스냅샷을 안전하게 읽지 못했습니다.",
            status_code=503,
        )
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
    denied, email = _member_report_owner_action(request, report_id, csrf_token)
    if denied is not None:
        return denied
    try:
        with storage_db.connect() as conn:
            report = report_store.load(conn, report_id)
            if report is None:
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            report_state = dashboard_store.get_report_state(conn, report_id)
            if not report_state.updated_at:
                report_state = dashboard_store.register_report(
                    conn,
                    report_id=report_id,
                    corp_type=report.corp_type,
                    payload_json=report_store.report_to_json(report),
                    now_iso=clock.iso_now_kst(),
                )
            report_version = report_state.version
            if dashboard_store.get_report_snapshot(
                conn, report_id=report_id, version=report_version
            ) is None:
                return HTMLResponse(
                    "설문 당시 보고서 원본을 안전하게 연결할 수 없습니다.",
                    status_code=503,
                )
            now_iso = clock.iso_now_kst()
            revision = dashboard_store.save_survey(
                conn, report_id=report_id, actor_email=email, rating=rating,
                overall_feedback=overall_feedback, business_distinction=business_distinction,
                add_information=add_information, delete_information=delete_information,
                now_iso=now_iso,
                report_version=report_version,
            )
            if revision == 1:
                try:
                    dashboard_kpi.record_first_survey(
                        conn,
                        report_id=report_id,
                        report_version=report_version,
                        actor_email=email,
                        now_iso=now_iso,
                    )
                except Exception:
                    logger.exception("첫 설문 KPI를 기록하지 못했습니다")
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("설문을 저장하지 못했습니다.", status_code=503)
    return RedirectResponse(f"/result/{report_id}", status_code=303)


@router.post("/reports/{report_id}/errors")
async def submit_error(
    request: Request, report_id: str,
    area: str = Form(..., max_length=1000), reason: str = Form(..., max_length=_FEEDBACK_MAX_CHARS),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    denied, email = _member_report_owner_action(request, report_id, csrf_token)
    if denied is not None:
        return denied
    try:
        with storage_db.connect() as conn:
            if not report_store.exists(conn, report_id):
                return HTMLResponse("존재하지 않는 보고서입니다.", status_code=404)
            dashboard_store.record_error(
                conn, report_id=report_id, actor_email=email, area=area, reason=reason,
                now_iso=clock.iso_now_kst(),
            )
    except ValueError as error:
        return HTMLResponse(str(error), status_code=400)
    except Exception:
        return HTMLResponse("오류 신고를 저장하지 못했습니다.", status_code=503)
    return RedirectResponse(f"/result/{report_id}", status_code=303)


def link_page_context(request: Request) -> dict:
    """초대 링크 화면(정보 구조 ②)이 쓰는 대시보드 쪽 값.

    ★ 2026-09-02 G-S8 — 링크 목록 «본문»은 이제 `routers/admin.py`의 접근 문맥이
      정본이다. 여기서는 새 접속 건수처럼 대시보드만 아는 값을 얹는다.
    """

    return _dashboard_context(request)


def member_page_context(request: Request) -> dict:
    """회원 화면(정보 구조 ③)이 쓰는 대시보드 쪽 값 — 기간 통계·한도·설문."""

    context = _dashboard_context(request)
    period, start_day = _member_period(request)
    with storage_db.connect() as conn:
        member_statistics = dashboard_store.member_run_statistics(
            conn, start_day=start_day
        )
        member_feedback = dashboard_store.list_member_feedback(
            conn, start_day=start_day
        )
        period_survey_total, period_helpful = dashboard_store.survey_summary(
            conn, start_day=start_day
        )
        period_kpi = dashboard_kpi.summary(conn, start_day=start_day)
        member_profiles = share_allow.list_profiles(conn)
    member_names = {
        member.email: (member.display_name.strip() or "이름 미등록")
        for member in member_profiles
    }
    feedback_rows = []
    for item in member_feedback:
        row = asdict(item)
        row["display_name"] = member_names.get(item.actor_email, "이름 미등록")
        feedback_rows.append(row)
    context.update(
        dashboard_member_period=period,
        dashboard_member_statistics=member_statistics,
        dashboard_member_feedback=feedback_rows,
        dashboard_survey_total=period_survey_total,
        dashboard_satisfaction=(
            "자료 모으는 중"
            if period_survey_total < 5
            else f"{round(period_helpful * 100 / period_survey_total)}% "
            f"({period_helpful}/{period_survey_total})"
        ),
        dashboard_kpi_measured=period_kpi.measured_responses,
        dashboard_three_minute_response=(
            "자료 모으는 중"
            if period_kpi.measured_responses < 5
            else f"{round(period_kpi.within_target * 100 / period_kpi.measured_responses)}% "
            f"({period_kpi.within_target}/{period_kpi.measured_responses})"
        ),
    )
    return context


@router.get("/admin/members", response_class=HTMLResponse)
async def members_page(request: Request):
    """회원 화면 — 초대·명단·한도·이용·설문을 한곳에서 본다(정보 구조 ③).

    ★ 2026-09-02 G-S8 — 초대 폼과 명단은 `/admin/access`에 따로 있었다. 화면
      본문은 `routers/admin.py`가 그리고, 이 라우트는 권한만 판정한다.
    """

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    from src.web.routers import admin as admin_router  # noqa: PLC0415

    return admin_router.render_member_admin_page(request)


@router.get("/admin/links", response_class=HTMLResponse)
async def links_page(request: Request):
    """초대 링크 화면 — 발급·목록·상태·철회를 한곳에서 본다(정보 구조 ②).

    ★ 2026-09-02 G-S8 — 발급 폼은 `/admin/access`에, 목록은 여기에 나뉘어 있었다.
      화면 본문은 `routers/admin.py`가 그리고, 이 라우트는 권한만 판정한다.
    """

    blocked = request_helpers.require_admin(request)
    if blocked is not None:
        return blocked
    from src.web.routers import admin as admin_router  # noqa: PLC0415

    return admin_router.render_link_admin_page(request)


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
            report_view_events = share_store.list_report_view_events_by_hash(
                conn, key_hash
            )
            runs = share_store.list_runs_by_hash(conn, key_hash)
            report_state = (
                "none" if link is None else _dashboard_link_report_state(conn, link)
            )
            link_company_id_state = (
                "none"
                if link is None
                else _dashboard_link_company_id_state(conn, link, report_state)
            )
            run_report_states = {
                run.run_id: (
                    "none"
                    if not run.report_id
                    else (
                        "available"
                        if report_store.load(conn, run.report_id) is not None
                        else "missing"
                    )
                )
                for run in runs
            }
            seen_open_id = dashboard_store.link_open_seen_id(conn, key_hash=key_hash)
            new_open_events = _unseen_open_events(open_events, seen_open_id)
        if link is None:
            return _admin_response(request, RedirectResponse("/admin/links", status_code=303))
        response = request_helpers.templates.TemplateResponse(
            request=request, name="admin_link_detail.html",
            context=request_helpers._ctx(
                request,
                dashboard_link=link,
                dashboard_service=asdict(service),
                dashboard_open_events=open_events,
                dashboard_new_open_events=new_open_events,
                dashboard_new_open_counts={
                    event.id: event.opened_count for event in new_open_events
                },
                dashboard_report_view_events=report_view_events,
                dashboard_runs=runs,
                dashboard_link_report_state=report_state,
                dashboard_run_report_states=run_report_states,
                dashboard_link_expired=share_logic.link_expired(link),
                dashboard_link_company_id_state=link_company_id_state,
                # ★ 이 값은 «보고서 공개 기간»이지 LINK 수명이 아니다. 두 값이
                #   60일로 같던 시절에는 LINK 수명을 넣어도 맞아 보였다.
                dashboard_result_share_days=REPORT_LINK_MAX_AGE_DAYS,
                dashboard_link_created_at_label=_link_timestamp_label(
                    link.created_at
                ),
                dashboard_link_expiry_label=_link_expiry_label(
                    link.created_at, expires_at=link.expires_at
                ),
                dashboard_link_first_opened_at_label=_link_timestamp_label(
                    link.first_opened_at
                ),
                dashboard_link_last_opened_at_label=_link_timestamp_label(
                    link.last_opened_at
                ),
                dashboard_link_revoked_at_label=_link_timestamp_label(
                    link.revoked_at
                ),
                dashboard_open_event_labels={
                    event.id: _link_timestamp_label(event.opened_at)
                    for event in open_events
                },
                dashboard_run_status_labels={
                    share_store.RUN_STATUS_RUNNING: "생성 중",
                    share_store.RUN_STATUS_AWAITING_RELEASE: "자동출고 검사 대기",
                    share_store.RUN_STATUS_COMPLETED: "완료",
                    share_store.RUN_STATUS_STOPPED: "중단",
                    share_store.RUN_STATUS_INTERRUPTED: "서버 종료로 중단",
                },
                dashboard_run_stop_reason_labels={
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
                    # admin.py 사본과 «같이» 고쳐야 한다. 한쪽만 고치면 그 화면만
                    # 조용히 원문 코드로 보인다(test_link_stop_reason_label_parity).
                    "link_revoked": "초대 링크의 사용이 중단됨",
                    "link_expired": "초대 링크의 기간이 지남",
                    "link_state_unknown": "초대 링크 상태를 확인하지 못함",
                },
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
    try:
        dashboard_maintenance.run_current_operation(
            storage_db.connect,
            operation=dashboard_maintenance.OPERATION_WEEKLY,
            actor_email=_admin_email(request),
        )
    except dashboard_maintenance.MaintenanceRunError:
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
    try:
        dashboard_maintenance.run_current_operation(
            storage_db.connect,
            operation=dashboard_maintenance.OPERATION_CLEANUP,
            actor_email=_admin_email(request),
            cleanup_runner=report_retention_adapter.purge_expired_reports,
        )
    except dashboard_maintenance.MaintenanceRunError:
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
