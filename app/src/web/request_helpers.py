"""화면 공통 값, 공유 권한, 요청 제한과 CSRF 검사."""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Final, Optional
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.features.report_standard.period_summary import (
    PeriodSummaryItem,
    period_summary_from_table,
)
from src.features.report_standard.visualization import composition_tone
from src.features.report_access import logic as report_access_logic

from src.core import clock, paths
from src.core.citations import (
    INTERPRETATION_LABEL,
    citation_number,
    split_citation_markers,
    split_interpretation_marker,
)
from src.core.constants import (
    CELL_LABELS,
    COMPANY_LOOKUP_FAILED_MESSAGE,
    MAX_RETRY_INPUT,
    RAW_SOURCE_LABEL,
    RAW_SOURCE_NOTE,
    section_display_parts,
)
from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.admin_dashboard import store as dashboard_store
from src.features.budget import logic as budget_logic
from src.features.budget import spend_store
from src.features.budget.constants import (
    BUDGET_STORE_BLOCKED_MESSAGE,
    BUSY_MESSAGE,
    RATE_LIMITED_MESSAGE,
    RATE_MAX_RUNS,
    RATE_WINDOW_SEC,
)
from src.features.business_candidate.logic import CandidateResolution
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.final_gate_diagnostic.presentation import (
    guidance_for_final_gate_reason,
)
from src.features.observability import admin_audit
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import CompanyLookupResult, Outcome, RunResult, UserInput
from src.features.provenance.sources import visible_citations
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    LANDING_REPORT_BUTTON_TEMPLATE,
    LINK_BUDGET_EXHAUSTED_MESSAGE,
    LINK_TOTAL_BUDGET_EXHAUSTED_CONTACT,
    LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
    LINK_TOTAL_BUDGET_EXHAUSTED_TITLE,
    LINK_TOTAL_BUDGET_KRW,
    PUBLIC_NOT_ALLOWED_MESSAGE,
)
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.report_standard.cover_metrics import cover_metrics
from src.features.report_standard.visualization import table_visualization
from src.features.report_standard.section_content import (
    masthead_lines,
    section_content_blocks,
    source_verification_label,
    summary_topic,
)
from src.features.storage import db as storage_db
from src.web import deployment_mode, evaluation_mode, paid_runtime, runtime
from src.web.security import (
    COMPANY_MAX_CHARS,
    REGION_MAX_CHARS,
)


logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(paths.TEMPLATES_DIR))
_READONLY_EXISTING_REQUEST_STATE = "public_get_readonly_existing"


def _request_uses_readonly_existing(request: Request) -> bool:
    return bool(getattr(request.state, _READONLY_EXISTING_REQUEST_STATE, False))

# ★ 구성 도식의 «색 고르는 규칙»을 화면 틀에서도 쓴다.
#   PDF(export_pdf/logic.py)와 «같은 함수»여야 화면과 인쇄물의 색이 안 어긋난다.
#   틀 안에서 `loop.index0 % 5` 식으로 따로 계산하면 항목 수가 바뀔 때
#   두 곳이 조용히 달라진다 (하이브 6부문에서 실제로 그랬다).
templates.env.globals["composition_tone"] = composition_tone

# ★ 4장 «3개년 변화 요약» — 표 안의 두 값만으로 증감을 만든다.
#   PDF와 «같은 함수»를 써야 화면과 인쇄물의 숫자가 안 어긋난다.
#   ⚠️ 이 함수는 «봉인 없는» v1·옛 v2 저장본 갈래 전용이다. 봉인이 있는
#      v2 화면은 이 함수를 부르지 않는다 — 띠는 이미 블록 안에 들어 있다.
templates.env.globals["period_summary_from_table"] = period_summary_from_table


def sealed_period_basis_text(item: tuple[str, ...]) -> str:
    """봉인된 3개년 띠 한 칸의 «계산 근거 한 줄»을 만든다.

    봉인 블록은 띠 한 칸을 열 개의 표시 문자열로만 담는다
    (``PublicPeriodSummaryBlock``). 근거 줄(「2023년 5,665 → 2025년 5,940」)은
    그 열 개에서 파생되는 값이라 블록에 따로 없다.

    ★ 그 «모양»을 화면이 새로 지어내면 PDF와 갈라진다. 그래서 같은 열 개로
      ``PeriodSummaryItem``을 되살려 이미 있는 ``basis_text`` 한 곳에서만
      만든다 — 웹에 새 문구 규칙을 두지 않는다.
    """

    return PeriodSummaryItem(*item).basis_text


templates.env.globals["sealed_period_basis_text"] = sealed_period_basis_text


def company_analysis_input(*, company: str, region: str) -> UserInput:
    """회사 분석 전용 웹 요청을 기존 파이프라인 계약으로 옮긴다.

    ``job``과 ``posting_text``는 저장 데이터·파이프라인의 하위 호환 필드라 구조는
    유지하지만, 새 웹 요청에서는 사용자에게 받지도 않고 가짜 값으로 채우지도 않는다.
    """
    return UserInput(
        company=company.strip(),
        job="",
        region=region.strip(),
        posting_text="",
    )


def _sweep_jobs(now: float) -> None:
    """순환 import 없이 작업 메모리 청소를 호출한다."""
    from src.web import job_runtime  # noqa: PLC0415

    job_runtime._sweep_jobs(now)

def _ctx(request: Request, **kwargs) -> dict:
    """모든 화면이 공통으로 쓰는 값.

    ★ 로그인 상태를 «여기서» 싣는다. 화면마다 따로 넣으면 하나쯤 빠뜨리고,
      그 화면만 「로그인 안 한 것처럼」 보이게 된다.
    """
    report = kwargs.get("report")
    if report is not None:
        kwargs["public_citations"] = visible_citations(
            getattr(report, "citations", ())
        )
    result = kwargs.get("result")
    if (
        result is not None
        and getattr(result, "outcome", None) is Outcome.GATE_STOPPED
    ):
        # 자유 문장 message가 닫힌 최종 사유와 어긋나도 화면은 한 권위만
        # 따른다. 오래된 빈 사유는 presentation이 보수적인 기타 안내로 닫는다.
        kwargs["gate_guidance"] = guidance_for_final_gate_reason(
            getattr(result, "final_gate_reason", "")
        )
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    session = auth_logic.get_session(
        token,
        readonly_existing=_request_uses_readonly_existing(request),
    )
    csrf_secret = _request_csrf_secret(request)
    evaluation = evaluation_mode.settings()
    base = {
        "cell_labels": CELL_LABELS,
        "section_display_parts": section_display_parts,
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "engine_v2_schema_version": ENGINE_V2_SCHEMA_VERSION,
        # 내부 ``조각 N·종류``를 템플릿에서 직접 자르면 다른 출력과 다시 갈린다.
        "citation_number": citation_number,
        # 본문에 박힌 [n]을 작은 위첨자 링크로 바꿔 인쇄하기 위한 분해기.
        # v1은 이미 .ref 위첨자를 쓰는데 v2만 평문 대괄호였다(사용자 신고).
        "split_citation_markers": split_citation_markers,
        # 문장 끝 «— 해석»을 떼어 둥근 배지로 보여 주기 위한 분리기.
        "split_interpretation_marker": split_interpretation_marker,
        "interpretation_label": INTERPRETATION_LABEL,
        # 표의 사실을 반복하지 않고 구성비·추세·흐름 표현으로 바꾸는 순수 함수다.
        "table_visualization": table_visualization,
        # 표지 띠에 올릴 값을 «고르는» 순수 함수. PDF(export_pdf/logic.py)와
        # 같은 함수여야 화면과 인쇄물의 표지 숫자가 안 어긋난다.
        "cover_metrics": cover_metrics,
        "section_content_blocks": section_content_blocks,
        "source_verification_label": source_verification_label,
        "summary_topic": summary_topic,
        # 표지 다음 첫 본문 페이지 마스트헤드 두 줄. PDF·Notion과
        # «같은 함수»여야 회사명·생성일 표기가 세 채널에서 안 어긋난다.
        "masthead_lines": masthead_lines,
        # 검증된 본문 아래의 근거 원문 문구 — 세 출력 형태가 core 값을 같이 쓴다.
        "raw_source_label": RAW_SOURCE_LABEL,
        "raw_source_note": RAW_SOURCE_NOTE,
        "max_retry": MAX_RETRY_INPUT,
        "company_max_chars": COMPANY_MAX_CHARS,
        "region_max_chars": REGION_MAX_CHARS,
        # 화면 머리의 배지가 «지금 진짜로 도는지»를 정직하게 말하게 한다.
        "is_real": not isinstance(runtime._PIPELINE, DemoPipeline),
        "evaluation_mode": evaluation.enabled,
        "evaluation_paid_providers": evaluation.paid_providers_enabled,
        "evaluation_per_run_cap_krw": evaluation.per_run_cap_krw,
        "evaluation_daily_cap_krw": evaluation.daily_cap_krw,
        "evaluation_business_day_label": evaluation.business_day_label,
        # ⚠️ 이 값은 «보여주기»용이다. 진짜 차단은 require_admin()이 매 요청마다 한다.
        "auth_email": session.email if session is not None else None,
        "auth_is_admin": session.is_admin if session is not None else False,
        "csrf_token": auth_logic.csrf_token_for_session(csrf_secret),
        # 설정값 자체는 절대 싣지 않는다. 로그인 버튼이 지금 동작 가능한지만 알린다.
        "auth_login_available": auth_google.credentials_configured(),
        "narrow_admin_demo": deployment_mode.render_admin_demo_no_forwarded(),
        "narrow_admin_real": deployment_mode.render_admin_real_no_forwarded(),
        "narrow_admin_no_forwarded": deployment_mode.render_admin_no_forwarded(),
        "beta_admin_only": auth_logic.beta_admin_only_from_env(),
    }
    base.update(kwargs)
    return base


def mark_public_get_readonly_existing(request: Request) -> None:
    """이 공개 GET의 공통 화면 조회도 기존 DB를 읽기만 하도록 표시한다."""

    setattr(request.state, _READONLY_EXISTING_REQUEST_STATE, True)


#: 권한이 없어 못 여는 보고서 화면의 제목. 세 갈래(404·403·409)가 같은 제목을 쓴다 —
#: 왜 못 여는지는 갈래마다 다르지만, 손님이 겪은 일은 「이 보고서가 안 열린다」 하나다.
REPORT_ACCESS_DENIED_TITLE: Final[str] = "보고서를 열 수 없습니다"

#: 그 화면에서 손님이 «지금 할 수 있는 일». 결과 주소는 가장 자주 공유되는 주소라,
#: 남에게 열릴 때 여기서 길을 못 주면 그대로 막다른 길이 된다.
REPORT_ACCESS_REOPEN_HINT: Final[str] = (
    "받으신 초대 링크(QR)를 다시 열면 됩니다. "
    "잘 안 되면 링크를 보내 주신 담당자에게 문의해 주세요."
)

#: 신고로 잠시 멈춘 보고서의 안내. 다시 열어도 결과가 같으므로 «다시 시도»가 아니라
#: 언제 열리는지와 물어볼 곳을 알려 준다.
REPORT_ACCESS_REVOKED_HINT: Final[str] = (
    "확인이 끝나면 같은 주소로 다시 열립니다. "
    "급하시면 링크를 보내 주신 담당자에게 문의해 주세요."
)


def _report_access_denied_screen(
    request: Request, *, revoked: bool, status_code: int
) -> Response:
    """보고서 접근을 거절할 때 «틀을 갖춘» 안내 화면을 그린다.

    Args:
        request: 들어온 요청.
        revoked: 신고 접수로 잠시 멈춘 보고서인가.
        status_code: 그대로 내보낼 상태 코드(404·403·409).

    ★ 앞서는 ``<h1><p>`` 두 줄짜리 조각이었다. 서체·상단바·돌아갈 길·물어볼 곳이
      한 개도 없어, 남에게 공유된 결과 주소를 연 사람은 «고장 난 페이지»를 봤다.
      같은 파일의 다른 거절 화면과 **같은 틀**을 쓴다.
    ★ ↻(다시 하면 된다)를 쓰지 않는다 — 같은 주소를 다시 열어도 결과가 같다.
    """

    from src.web import job_runtime  # noqa: PLC0415

    if revoked:
        message = (
            "오류 신고가 접수되어 결과·다운로드·공유를 잠시 멈췄습니다. "
            "관리자가 원본과 출처를 확인한 뒤 직접 다시 공개합니다."
        )
        hint = REPORT_ACCESS_REVOKED_HINT
        icon = "⛔"
    else:
        message = (
            "보고서 번호만으로는 열람 권한이 되지 않습니다. "
            "조사를 시작한 브라우저에서 열면 그대로 보실 수 있습니다."
        )
        hint = REPORT_ACCESS_REOPEN_HINT
        icon = "ℹ️"
    return templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=_ctx(
            request,
            interruption_icon=icon,
            interruption_title=REPORT_ACCESS_DENIED_TITLE,
            interruption_message=message,
            interruption_hint=hint,
            retry_url=job_runtime.DEFAULT_EXIT_URL,
            retry_label=job_runtime.DEFAULT_EXIT_LABEL,
            retry_same_page=False,
        ),
        status_code=status_code,
    )


def require_report_access(
    request: Request, locator: str, *, api: bool = False
) -> Response | None:
    """결과·PDF·두 progress 채널이 함께 쓰는 한곳짜리 권한 경계."""

    decision = report_access_logic.authorize_report_access(request, locator)
    if decision.allowed:
        return None
    unavailable = decision.reason in {
        "store_unavailable",
        "store_missing",
        "store_incomplete",
        "store_unreadable",
    }
    status_code = (
        503
        if unavailable
        else 409
        if decision.reason == "resource_revoked"
        else 403
        if decision.reason == "member_revoked"
        else 404
    )
    if api:
        from fastapi.responses import JSONResponse  # noqa: PLC0415

        response: Response = JSONResponse(
            {
                "error": (
                    "보고서 접근 권한을 잠시 확인할 수 없습니다."
                    if unavailable
                    else "이 보고서를 열 수 없습니다."
                ),
                "code": (
                    "report_access_store_unavailable"
                    if unavailable
                    else "report_access_denied"
                ),
                "retryable": unavailable,
            },
            status_code=status_code,
        )
    else:
        response = _report_access_denied_screen(
            request,
            revoked=decision.reason == "resource_revoked",
            status_code=status_code,
        )
    response.headers["Cache-Control"] = "private, no-store"
    store_status = {
        "store_missing": "missing",
        "store_incomplete": "incomplete",
        "store_unreadable": "unreadable",
    }.get(decision.reason, "")
    if store_status:
        response.headers["X-Report-Store-Status"] = store_status
    return response


def public_get_uses_readonly_existing(request: Request) -> bool:
    """공개 GET의 하위 화면 조립기가 같은 읽기 전용 계약을 이어받게 한다."""

    return _request_uses_readonly_existing(request)

def _retry_screen(
    request: Request,
    user_input: UserInput,
    retry: int,
    rejected: bool,
    candidate_resolution: CandidateResolution | None = None,
    candidate_was_selected: bool = False,
    candidate_search_available: bool = False,
    candidate_search_grant: str = "",
    candidate_search_cost_krw: float = 0.0,
    evaluation_consent_grant: str = "",
) -> HTMLResponse:
    """회사명을 다시 받는 화면.

    「못 찾음」과 「[아닙니다]」 둘 다 같은 화면으로 보낸다 — 사용자가 할 일이 같기 때문이다.
    ★ 여기서 「대상 아님」이라고 단정하지 않는다. 이름을 아직 못 맞춘 것뿐이다.
    """
    candidate_technical_retry = bool(
        candidate_resolution is not None
        and candidate_resolution.provider_name == "DART"
        and candidate_resolution.status.value
        in {"unconfigured", "rate_limited", "timed_out", "failed"}
    )
    return templates.TemplateResponse(
        request=request,
        name="not_found.html",
        context=_ctx(
            request,
            user_input=user_input,
            retry=retry,
            rejected=rejected,
            # retry는 「지금까지 몇 번 찾아봤나」. 다음 시도가 상한을 넘으면 폼을 닫는다.
            exhausted=(
                not candidate_technical_retry and retry + 1 >= MAX_RETRY_INPUT
            ),
            candidate_technical_retry=candidate_technical_retry,
            next_retry=(retry if candidate_technical_retry else retry + 1),
            demo_companies=(
                available_companies()
                if isinstance(runtime._PIPELINE, DemoPipeline)
                else []
            ),
            candidate_resolution=candidate_resolution,
            candidate_was_selected=candidate_was_selected,
            candidate_search_available=candidate_search_available,
            candidate_search_grant=candidate_search_grant,
            candidate_search_cost_krw=candidate_search_cost_krw,
            evaluation_consent_grant=evaluation_consent_grant,
            evaluation_workflow_id=evaluation_mode.issue_workflow_id(),
        ),
    )

def _lookup_failed_screen(request: Request) -> HTMLResponse:
    """기술 실패를 「회사를 못 찾음」으로 단정하지 않는 기존 중단 화면."""
    return templates.TemplateResponse(
        request=request,
        name="stopped.html",
        context=_ctx(
            request,
            result=RunResult(
                outcome=Outcome.FAILED,
                message=COMPANY_LOOKUP_FAILED_MESSAGE,
            ),
            show_quota_note=False,
        ),
    )

def _load_active_share_link(
    conn: sqlite3.Connection, key: str
) -> Optional[share_store.ShareLink]:
    """철회되지 않았고 자동 수명도 남은 링크만 권한으로 인정한다."""
    link = share_store.load(conn, key)
    if (
        link is None
        or link.is_revoked
        # 발급일만 보면 저장된 만료일(옛 규칙으로 굳은 값·관리자가 미룬 값)을
        # 둘 다 놓친다. 행을 통째로 넘겨 그 값을 빠뜨릴 수 없게 한다.
        or share_logic.link_expired(link)
    ):
        return None
    return link

def _current_share_link(request: Request):
    """이 손님이 «어느 지원 맥락 LINK»로 들어왔는지.

    Args:
        request: 들어온 요청.

    Returns:
        발급해 둔 링크 정보. 열쇠가 없거나 못 찾으면 None.

    ★ 첫 화면에서 지원 회사·직무 꼬리표와 편집 가능한 기본값을 보여주기 위한 것이다.
    ★ 못 찾아도 **조용히 None**을 돌려준다. 링크가 닫혔다고 첫 화면까지
      막으면, 인사팀에게는 그냥 「안 되는 사이트」가 된다.
    """
    key = (request.cookies.get(KEY_COOKIE_NAME) or "").strip().lower()
    if not share_logic.is_valid_key(key):
        return None
    try:
        if _request_uses_readonly_existing(request):
            with storage_db.connect_readonly_existing() as conn:
                return None if conn is None else _load_active_share_link(conn, key)
        with storage_db.connect() as conn:
            return _load_active_share_link(conn, key)
    except Exception:  # noqa: BLE001 — 링크를 못 읽어도 화면은 떠야 한다
        logger.exception("열쇠 링크를 못 읽었습니다")
        return None

def _find_company_metered(user_input: UserInput) -> CompanyLookupResult:
    """유료 알맹이는 계량 약속이 없으면 호출 자체를 하지 않는다."""
    metered = getattr(runtime._PIPELINE, "find_company_metered", None)
    if callable(metered):
        result = metered(user_input)
        if isinstance(result, CompanyLookupResult):
            return result
        raise TypeError("find_company_metered()가 약속한 결과 모양을 돌려주지 않았습니다")
    if isinstance(runtime._PIPELINE, DemoPipeline):
        return CompanyLookupResult(card=runtime._PIPELINE.find_company(user_input))
    logger.error("유료 알맹이에 find_company_metered()가 없어 회사 식별을 막았습니다")
    return CompanyLookupResult(card=None, failed=True)


def _find_company_by_ref_metered(
    user_input: UserInput, candidate_ref: str
) -> CompanyLookupResult:
    """서명 검증을 마친 DART-local 후보를 고유번호 그대로 다시 확인한다."""
    metered = getattr(runtime._PIPELINE, "find_company_by_ref_metered", None)
    if not callable(metered):
        logger.error("유료 알맹이에 find_company_by_ref_metered()가 없어 후보 선택을 막았습니다")
        return CompanyLookupResult(card=None, failed=True)
    result = metered(user_input, candidate_ref)
    if isinstance(result, CompanyLookupResult):
        return result
    raise TypeError("find_company_by_ref_metered()가 약속한 결과 모양을 돌려주지 않았습니다")

def _raw_share_key(request: Request) -> str:
    """쿠키 열쇠가 실제 발급돼 지금도 살아 있을 때만 돌려준다.

    ★ 16진수 모양만 보면 공격자가 쿠키를 바꿀 때마다 새 3,000원 통장을 만들 수
      있다. 발급 저장소에 없거나 이미 삭제된 키는 PUBLIC(0원)으로 되돌린다.
    """
    key = (request.cookies.get(KEY_COOKIE_NAME) or "").strip().lower()
    if not share_logic.is_valid_key(key):
        return ""
    try:
        if _request_uses_readonly_existing(request):
            with storage_db.connect_readonly_existing() as conn:
                return (
                    key
                    if conn is not None
                    and _load_active_share_link(conn, key) is not None
                    else ""
                )
        with storage_db.connect() as conn:
            return key if _load_active_share_link(conn, key) is not None else ""
    except Exception:  # noqa: BLE001 — 링크 확인 실패는 권한을 주지 않는 쪽으로
        logger.exception("열쇠 링크를 확인하지 못해 공개 손님으로 봅니다")
        return ""

def _track_of(request: Request) -> tuple[share_tracks.Track, str, float | None]:
    """이 손님이 «어느 갈래»이고, «어느 통장»에서, «얼마»까지 쓸 수 있는가.

    Args:
        request: 들어온 요청.

    Returns:
        (갈래, 통장 이름, 하루 상한).

    ★ **로그인만으로는 아무 권한도 안 준다**.
      구글 로그인은 「누구인가」만 알려준다. 「써도 되는가」는 **초대 명단**이 정한다.
      이걸 안 나누면 인터넷의 아무나 로그인해서 돈을 쓴다.
    ★ 명단을 못 읽으면 «초대 안 된 사람»으로 본다 — 안전한 쪽으로 틀린다.
    """
    # 이 전용 통장은 실행기·환경 플래그만으로 열리지 않는다. 요청 URL, 실제 client와
    # server socket이 모두 loopback이고 프록시 흔적도 없는 경우에만 쓴다.
    if evaluation_mode.enabled() and _strict_loopback_http_request(request):
        return (
            share_tracks.Track.ADMIN,
            evaluation_mode.LOCAL_BUCKET,
            evaluation_mode.settings().daily_cap_krw,
        )

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    session = auth_logic.get_session(
        token,
        readonly_existing=_request_uses_readonly_existing(request),
    )
    email = session.email if session is not None else ""
    is_admin = bool(session is not None and session.is_admin)

    is_member = False
    # ★ 관리자가 이 친구 «한 명»에게만 따로 정해 둔 하루 비용 상한.
    #   None이면 갈래 기본값(3,000원)을 쓴다. 명단을 못 읽었을 때도 None으로 남아
    #   기본값 쪽으로 떨어진다 — 못 읽은 것이 상한을 «푸는» 근거가 되면 안 된다.
    member_daily_budget_krw: float | None = None
    if email and not is_admin:
        try:
            if _request_uses_readonly_existing(request):
                with storage_db.connect_readonly_existing() as conn:
                    profile = (
                        None if conn is None else share_allow.load(conn, email)
                    )
            else:
                with storage_db.connect() as conn:
                    profile = share_allow.load(conn, email)
            is_member = profile is not None
            if profile is not None:
                member_daily_budget_krw = profile.daily_budget_krw
        except Exception:  # noqa: BLE001 — 못 읽으면 «안 된 사람»으로 본다
            logger.exception("초대 명단을 못 읽었습니다 — 초대 안 된 것으로 봅니다")

    key = _raw_share_key(request)
    track = share_tracks.decide_track(
        email=email, is_admin=is_admin, is_member=is_member, share_key=key
    )
    bucket = share_tracks.bucket_of(track, email=email, share_key=key)
    return track, bucket, share_tracks.budget_of(
        track, member_daily_budget_krw=member_daily_budget_krw
    )


def require_active_share_link(
    request: Request,
    *,
    resolved_track: Optional[tuple[share_tracks.Track, str, float | None]] = None,
) -> Optional[HTMLResponse]:
    """LINK 자체가 아직 유효한지만 실행 경계에서 다시 확인한다.

    링크에 저장된 지원 회사와 직무는 전달 맥락을 설명하는 꼬리표이지 권한 범위가
    아니므로 이 함수는 제출 회사나 직무를 받지도, 비교하지도 않는다. 관리자·회원·
    공개 경로의 기존 판정은 그대로 둔다.
    """
    track, bucket, _cap = resolved_track or _track_of(request)
    if track is not share_tracks.Track.LINK:
        return None
    try:
        with storage_db.connect() as conn:
            link = _load_active_share_link(conn, bucket)
    except Exception:  # noqa: BLE001 — 범위를 확인하지 못하면 링크 권한을 쓰지 못한다
        logger.exception("LINK 권한 상태를 확인하지 못했습니다")
        return templates.TemplateResponse(
            request=request,
            name="share_scope_error.html",
            context=_ctx(
                request,
                # ★ 「못 읽었다」와 「닫혔다」는 다르다 — 닫혔다고 단정하면
                #   거짓말이 될 수 있다. 같은 화면을 쓰는 403 갈래와 어휘만
                #   맞추고(「초대 링크」), 사실은 다르게 말한다.
                scope_error=(
                    "초대 링크 상태를 지금 확인할 수 없습니다. "
                    "잠시 뒤 같은 링크에서 다시 시도해 주세요."
                ),
            ),
            status_code=503,
        )
    if link is None:
        return templates.TemplateResponse(
            request=request,
            name="share_scope_error.html",
            context=_ctx(
                request,
                # ★ 이 문구는 인사팀이 읽는다 — 내부 용어(LINK)와 「철회」를
                #   쓰지 않는다. 받는 사람에게 만료와 철회는 같은 뜻이라
                #   첫 화면 안내와 **같은 말**을 쓴다.
                scope_error=(
                    "이 초대 링크는 사용이 중단되어 더 이상 열리지 않습니다. "
                    "포트폴리오에 적힌 연락처로 알려 주시면 새 링크를 보내 드립니다."
                ),
            ),
            status_code=403,
        )
    return None


def require_share_scope(
    request: Request,
    *,
    company: str,
    job: str = "",
    resolved_track: Optional[tuple[share_tracks.Track, str, float | None]] = None,
) -> Optional[HTMLResponse]:
    """이전 내부 호출과 확장 코드의 호환을 위한 별칭이다.

    회사·직무는 LINK 권한 범위가 아니며 일부러 버린다. 새 코드는 의미가 분명한
    :func:`require_active_share_link`를 사용해야 한다.
    """
    del company, job
    return require_active_share_link(request, resolved_track=resolved_track)

def _guard_run(
    request: Request,
    *,
    count_start: bool = True,
    owns_slot: bool = False,
    resolved_track: Optional[tuple[share_tracks.Track, str, float | None]] = None,
    now: Optional[float] = None,
) -> Optional[HTMLResponse]:
    """조사를 시작해도 되는지 보고, 안 되면 «막는 화면»을 돌려준다.

    Args:
        request: 들어온 요청.
        now: 앞선 일회용 attempt 검증과 공유할 monotonic 시각. 일반 호출은 생략한다.

    Returns:
        막아야 하면 보여줄 화면. 시작해도 되면 None.

    ★ 통과할 때만 횟수를 적는다 — 거절당한 요청까지 세면
      「돈도 안 썼는데 차단」이 된다 (budget/logic.py 참고).
    """
    admission_now = time.monotonic() if now is None else now
    _sweep_jobs(admission_now)

    # 전역 점검은 새 생성을 막되, 이미 저장된 결과의 열람은 reports 경로에서
    # 계속 별도로 판정한다. 상태를 읽지 못할 때도 새 외부 호출을 열지 않는다.
    try:
        with storage_db.connect() as conn:
            service_state = dashboard_store.get_service_state(conn)
    except Exception:
        logger.exception("전역 운영 상태를 읽지 못했습니다")
        return _throttled(
            request,
            "현재 운영 상태를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.",
            "service-state-store",
        )
    if service_state.status == dashboard_store.SERVICE_MAINTENANCE:
        return _throttled(
            request,
            "현재 점검 중입니다. 원인 확인과 재검사가 끝난 뒤 운영자가 직접 재시작합니다.",
            "service-maintenance",
        )

    # 미리보기는 화면만 확인하는 모드다. 후보 검색을 포함한 외부 provider 진입 전에
    # 서버에서도 닫아 HTML의 disabled 속성을 우회해도 비용이 나가지 않게 한다.
    if evaluation_mode.enabled() and not evaluation_mode.paid_providers_enabled():
        return _throttled(
            request,
            evaluation_mode.PREVIEW_BLOCKED_MESSAGE,
            "evaluation-preview",
        )

    # ★ 예산은 «링크마다» 센다 (제품 결정).
    #   전체 상한은 두지 않는다 — 대신 링크 하나가 하루에 쓸 수 있는 몫을 정했다.
    #   ⚠️ 그러므로 **최악의 하루 지출 = 링크당 상한 × 살아 있는 링크 수**다.
    #     링크를 몇 개 뿌렸는지가 곧 예산이다 (관리 화면에서 확인).
    costs_money = not isinstance(runtime._PIPELINE, DemoPipeline)
    track, bucket, cap = resolved_track or _track_of(request)
    if costs_money and not paid_runtime.reap_expired_paid_phases():
        return _throttled(request, BUDGET_STORE_BLOCKED_MESSAGE, "budget-store")
    # 횟수 제한도 권한 통장의 지문을 쓴다. 프록시가 전달한 IP는 신뢰 경계가
    # 아니므로 X-Forwarded-For를 바꿔도 새 횟수 통장을 만들 수 없다.
    rate_key = spend_store.bucket_id(bucket)
    stored_bucket = rate_key
    with paid_runtime._SLOT_LOCK:
        unresolved = (
            clock.today_kst().isoformat(), stored_bucket
        ) in paid_runtime._UNRESOLVED_BUCKETS
    # 메모리 장부 모양을 바꾸기 전부터 있던 호출부·운영 중 갱신을 안전하게 잇는다.
    # 새 원장 복원값은 지문 키만 쓰고, 원문 키가 실제로 있을 때만 옛 값을 읽는다.
    if (
        share_logic.spent_for(
            paid_runtime._LINK_SPEND, stored_bucket, clock.today_kst()
        ) <= 0
        and share_logic.spent_for(
            paid_runtime._LINK_SPEND, bucket, clock.today_kst()
        ) > 0
    ):
        stored_bucket = bucket
    # ★ LINK만 «수명 전체» 누적 상한을 하나 더 본다.
    #   하루 상한은 자정마다 되살아난다. 링크는 기본 60일을 사니까 하루 상한만
    #   두면 링크 하나의 최악 노출이 상한 × 60이었다. 누적 상한이 그 곱셈을 끊는다.
    #   MEMBER·ADMIN·PUBLIC은 사람·전체 통장이라 「수명」 개념이 없어 보지 않는다.
    link_total_spent: Optional[float] = None
    link_total_cap: Optional[float] = None
    if costs_money and track is share_tracks.Track.LINK:
        try:
            with storage_db.connect() as conn:
                link_key_hash = share_store.key_hash_of(bucket)
                link_row = share_store.load_by_hash(conn, link_key_hash)
                link_total_spent = share_store.link_total_spent_krw(
                    conn, key_hash=link_key_hash
                )
        except Exception:
            # 누적을 못 읽는데 열어 주면 저장소 장애가 곧 무제한 지출이 된다.
            logger.exception("링크 누적 사용액을 읽지 못했습니다")
            return _throttled(
                request, BUDGET_STORE_BLOCKED_MESSAGE, "budget-store"
            )
        # 링크가 사라졌으면 기본 상한으로 본다. 못 찾았다고 상한을 푸는 쪽으로
        # 떨어지면 안 된다 (링크 자체의 유효성은 다른 검사가 따로 본다).
        link_total_cap = (
            link_row.effective_total_budget_krw
            if link_row is not None
            else LINK_TOTAL_BUDGET_KRW
        )
    link_total_exhausted = (
        link_total_spent is not None
        and link_total_cap is not None
        and not share_logic.can_start_within_total_budget(
            link_total_spent, link_total_cap
        )
    )
    budget_exhausted = (
        costs_money
        and cap is not None
        and not share_logic.can_start_new_run(
            paid_runtime._LINK_SPEND,
            stored_bucket,
            clock.today_kst(),
            cap,
            total_spent_krw=link_total_spent,
            total_cap_krw=link_total_cap,
        )
    )
    if track is share_tracks.Track.MEMBER:
        # MEMBER는 위에서 실패까지 포함한 비용 상한을 먼저 확인하고, 여기서는
        # 성공 보고서 건수도 따로 확인한다. 빠른 사전 확인 뒤 Job 등록 직전의
        # SQLite reservation이 성공 건수의 동시 경쟁을 닫는다.
        # ★ 건수는 «이 친구의 한도»다 — 관리자가 따로 안 정했으면 기존 3건이다.
        #   화면 문구도 같은 숫자를 써야 「3건이라더니 왜 막지」가
        #   안 생긴다.
        try:
            with storage_db.connect() as conn:
                member_email = bucket.removeprefix("user:")
                member_success_limit = dashboard_store.member_success_limit(
                    conn, actor_email=member_email
                )
                member_available = dashboard_store.member_can_start(
                    conn,
                    actor_email=member_email,
                    day=clock.today_kst().isoformat(),
                    success_limit=member_success_limit,
                )
        except Exception:
            logger.exception("MEMBER 성공 보고서 사용량을 읽지 못했습니다")
            return _throttled(request, BUDGET_STORE_BLOCKED_MESSAGE, "member-usage-store")
        if not member_available:
            return _throttled(
                request,
                member_success_limit_message(member_success_limit),
                "member-success-limit",
            )
    if count_start and not budget_logic.rate_ok(
        paid_runtime._RATE_HISTORY,
        rate_key,
        admission_now,
        window_sec=RATE_WINDOW_SEC,
        max_runs=RATE_MAX_RUNS,
    ):
        return _throttled(request, RATE_LIMITED_MESSAGE, "rate")
    if paid_runtime._slot_is_full(track, bucket, owns_slot=owns_slot):
        return _throttled(request, BUSY_MESSAGE, "busy")
    if costs_money and not paid_runtime._BUDGET_STORE_HEALTHY:
        # 원장이 고장 난 채 열어 두면 재시작 뒤 상한을 보장할 수 없다.
        return _throttled(
            request, BUDGET_STORE_BLOCKED_MESSAGE, "budget-store"
        )
    if costs_money and unresolved:
        # provider 응답 전에 서버가 죽었거나 API 예외로 과금 여부가 불명확하다.
        # 다른 통장은 살리고 이 통장만 사람이 원장을 확인할 때까지 닫는다.
        return _throttled(
            request, BUDGET_STORE_BLOCKED_MESSAGE, "budget-unresolved"
        )
    if budget_exhausted:
        # ★ 예산이 다 돼도 **이미 만들어 둔 보고서는 계속 열린다** —
        #   그건 파이프라인을 안 거치고 저장소에서 바로 꺼내므로 0원이다.
        #   막는 것은 «새로 AI를 부르는 일»뿐이다 (제품 결정).
        # ★ 모르는 손님(상한 0원)에게는 «다른 말»을 한다 — 「다 썼다」가 아니라
        #   「이 기능은 초대받은 분만」이다. 사실이 다르면 안내도 달라야 한다.
        # ★ 누적 소진도 «다른 말»이다 — 하루 소진은 「내일 다시 열립니다」가 사실이지만
        #   누적 소진은 내일도 안 열린다. 같은 말을 하면 헛되이 기다리게 한다.
        if track is share_tracks.Track.PUBLIC:
            return _throttled(
                request, PUBLIC_NOT_ALLOWED_MESSAGE, f"budget:{track.value}"
            )
        if link_total_exhausted:
            return _throttled(
                request,
                LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
                f"budget-total:{track.value}",
            )
        return _throttled(
            request, LINK_BUDGET_EXHAUSTED_MESSAGE, f"budget:{track.value}"
        )

    # ★ 통과할 때만 횟수를 적는다 — 거절당한 요청까지 세면
    #   「돈도 안 썼는데 차단」이 된다 (budget/logic.py 참고).
    if count_start:
        budget_logic.record_start(
            paid_runtime._RATE_HISTORY, rate_key, admission_now
        )
    return None

#: 이 이유로 막힌 것은 «정상 동작이 아니라 고장»이다 — 화면이 그렇게 말해야 한다.
#: 사람이 비용 기록을 확인해야 풀리므로, 사용자에게 문의 번호를 줘야 신고가 닿는다.
THROTTLE_FAULT_KINDS: Final[frozenset[str]] = frozenset(
    {"budget-store", "budget-unresolved", "member-usage-store"}
)

#: 기다리면 풀리는 차단의 제목. 「잠시 기다려 주세요」는 **정말 기다리면 열릴 때만**
#: 참이다.
THROTTLE_WAIT_TITLE: Final[str] = "잠시 기다려 주세요"

#: 고장으로 막았을 때의 제목.
THROTTLE_FAULT_TITLE: Final[str] = "새 조사가 멈췄습니다"

#: 초대 없이 들어온 손님을 막았을 때의 제목. 기다린다고 열리는 것이 아니므로
#: 「잠시 기다려 주세요」를 쓰지 않는다.
THROTTLE_NOT_INVITED_TITLE: Final[str] = "새 조사를 시작할 수 없습니다"

#: 누적 소진 갈래의 ``kind`` 앞머리. ``_guard_run``이 붙이는 값과 같아야 한다.
THROTTLE_TOTAL_EXHAUSTED_PREFIX: Final[str] = "budget-total:"

#: 하루 상한 갈래의 ``kind`` 앞머리.
THROTTLE_DAILY_BUDGET_PREFIX: Final[str] = "budget:"

#: 초대 없는 손님 갈래의 ``kind``.
THROTTLE_NOT_INVITED_KIND: Final[str] = (
    f"{THROTTLE_DAILY_BUDGET_PREFIX}{share_tracks.Track.PUBLIC.value}"
)


@dataclass(frozen=True)
class _ThrottleScreen:
    """왜 막았는지에 따라 달라지는 화면 조각.

    ★ 왜 필요한가 — 막는 이유가 넷인데 화면은 하나였다. 그래서 「내일이면
      열린다」가 참인 하루 상한의 틀에 «내일도 안 열리는» 누적 소진과
      «초대가 있어야 열리는» 차단까지 실려, 화면이 사실과 다른 말을 했다.
    """

    #: 화면 제목.
    title: str
    #: 제목 옆 그림 글자. ⏳는 «기다리면 된다»는 뜻이라 아무 데나 쓰지 않는다.
    icon: str = "⏳"
    #: 「하루에 돌릴 수 있는 양을 미리 정해 두었습니다」를 붙일지.
    #: 하루 상한에 걸렸을 때만 참이다.
    explains_daily_cap: bool = False
    #: 기다려도 안 열리는 갈래에 주는 «사람에게 닿는 길».
    contact_note: str = ""


def _throttle_screen(kind: str) -> _ThrottleScreen:
    """막은 이유(``kind``)에 맞는 제목·설명을 고른다."""

    if kind in THROTTLE_FAULT_KINDS:
        return _ThrottleScreen(title=THROTTLE_FAULT_TITLE, icon="⛔")
    if kind.startswith(THROTTLE_TOTAL_EXHAUSTED_PREFIX):
        # 제목은 본문 첫 문장을 그대로 앞세운다 — 「기다려도 열리지 않는다」는 사실이
        # 제목에서 먼저 보여야 하고, 본문은 「그래도 볼 수 있는 것」까지 한 번에
        # 말해야 손님이 두 사실을 따로 찾지 않는다.
        return _ThrottleScreen(
            title=LINK_TOTAL_BUDGET_EXHAUSTED_TITLE,
            icon="ℹ️",
            contact_note=LINK_TOTAL_BUDGET_EXHAUSTED_CONTACT,
        )
    if kind == THROTTLE_NOT_INVITED_KIND:
        return _ThrottleScreen(title=THROTTLE_NOT_INVITED_TITLE, icon="ℹ️")
    if kind.startswith(THROTTLE_DAILY_BUDGET_PREFIX):
        # 남은 예산 갈래는 하루 상한뿐이다 — 여기서만 그 설명이 사실이다.
        return _ThrottleScreen(title=THROTTLE_WAIT_TITLE, explains_daily_cap=True)
    return _ThrottleScreen(title=THROTTLE_WAIT_TITLE)


def _throttle_bound_report(request: Request) -> tuple[str, str]:
    """이 손님의 초대 링크에 묶인 회사 보고서를 «지금» 열 수 있으면 그 길을 준다.

    Args:
        request: 들어온 요청.

    Returns:
        (버튼 글자, 보고서 주소). 열 수 없으면 빈 글자 두 개.

    ★ 「열 수 있는가」를 여기서 새로 판단하지 않는다 — 첫 화면 랜딩과 **같은
      함수**를 부른다. 두 곳이 따로 판단하면 한 화면에서만 버튼이 사라진다.
    ★ 못 읽어도 조용히 빈 값을 준다. 부르는 쪽은 이미 «막는 중»이라 여기서 또
      실패하면 화면 자체가 안 뜬다.
    """

    link = _current_share_link(request)
    company = str(getattr(link, "company", "") or "").strip() if link else ""
    if not company:
        return "", ""
    from src.web.routers import analysis as analysis_router  # noqa: PLC0415

    report_url, _made_on = analysis_router._bound_report_view(link)
    if not report_url:
        return "", ""
    return LANDING_REPORT_BUTTON_TEMPLATE.format(company=company), report_url


#: 한도 안내에 반드시 함께 나가는 «저장본도 오늘 몫을 쓴다»는 사실.
#: ★ 왜 미리 말하는가 — 같은 회사를 다시 조사하면 새로 만들지 않고 저장본을 그대로
#:   보여 주는데, 그래도 오늘 몫 1건은 줄어든다. 말하지 않으면 손님은 「보여만
#:   줬는데 왜 줄지」로 읽고 남은 건수를 실제보다 많게 센 채 하루를 쓴다.
#: ★ 순서를 지어낼 수 없다 — 자리는 조사를 시작할 때 잡고, 저장본을 보여 줄지는
#:   그 뒤에 정해진다. 그래서 저장본이라고 몫을 돌려줄 수 없다.
MEMBER_SUCCESS_REUSE_NOTICE: Final[str] = (
    "같은 회사를 다시 조사하면 저장본을 보여 드리지만 오늘 몫 1건은 사용됩니다."
)


def member_success_limit_message(limit: int) -> str:
    """오늘 성공 보고서 건수를 다 쓴 친구에게 보여줄 말.

    Args:
        limit: **그 친구의** 하루 성공 보고서 한도.

    ★ 왜 함수로 빼는가 — 이 문장은 «막는 자리»가 두 곳이라 두 번 나온다.
      실행 시작 전 사전 확인(`_guard_run`)과 Job 등록 직전의 예약 커밋
      (`job_runtime._start_with_reserved_slot`)이다. 같은 문장을 두 곳에 따로
      적어 두면 한쪽만 고쳐져서, 한도를 7건으로 올린 친구가 경쟁에서 밀렸을 때
      「3건 다 썼다」는 틀린 말을 본다 — 같은 정의가 두 곳이 되는 함정이다.

    ★ 저장본 안내를 여기 함께 붙이는 이유 — 회원이 자기 하루 한도를 보는 화면은
      이 문장이 실리는 차단 화면뿐이다. 남은 건수를 미리 보여 주는 화면이 없으니,
      「저장본도 몫을 쓴다」를 말할 자리도 여기밖에 없다.
    """
    return (
        f"오늘 성공한 보고서 {int(limit)}건을 모두 사용했습니다. "
        "내일 다시 시도해 주세요. "
        f"{MEMBER_SUCCESS_REUSE_NOTICE}"
    )


def _throttled(request: Request, message: str, kind: str) -> HTMLResponse:
    """조사를 막았을 때 보여줄 화면.

    Args:
        request: 들어온 요청.
        message: 사용자에게 보여줄 말.
        kind: 왜 막았는지 (`rate` | `busy` | `budget`). 로그·화면 구분용.

    ★ 429는 대개 「지금은 안 된다」이지 「고장」이 아니다. 화면이 그걸 분명히 말한다.

    ⚠️ 단, ``THROTTLE_FAULT_KINDS`` 는 진짜 고장이다.
      그때까지 이 화면은 **모든** 경우에 「고장이 아닙니다」라고 단언했고,
      비용 기록이 깨져 막힌 사용자도 그 말을 봤다 — 사실이 아니고,
      신고할 번호도 없어 관리자에게 알릴 길이 없었다.

    ⚠️ 제목·설명도 갈래마다 다르다(``_throttle_screen``). 하나로 두면 내일도
      안 열리는 차단이 「잠시 기다려 주세요」 밑에 실려 손님을 헛되이 기다리게 한다.
    """
    logger.info("조사를 막았습니다: %s", kind)
    고장이다 = kind in THROTTLE_FAULT_KINDS
    screen = _throttle_screen(kind)
    bound_report_label, bound_report_url = _throttle_bound_report(request)
    response = templates.TemplateResponse(
        request=request,
        name="throttled.html",
        context=_ctx(
            request,
            throttle_message=message,
            throttle_kind=kind,
            throttle_is_fault=고장이다,
            throttle_title=screen.title,
            throttle_icon=screen.icon,
            throttle_explains_daily_cap=screen.explains_daily_cap,
            throttle_contact_note=screen.contact_note,
            throttle_bound_report_label=bound_report_label,
            throttle_bound_report_url=bound_report_url,
            # 고장일 때만 문의 번호를 준다 — 정상 차단에는 필요 없는 잡음이다.
            support_reference=admin_audit.request_id(request) if 고장이다 else "",
        ),
        status_code=429,
    )
    # 유료 파일럿 실행기만 429의 안전한 재시도 가능 여부를 증명할 수 있게 한다.
    # 금액·잔여 한도·공급자 원문은 노출하지 않는다.
    response.headers["X-Company-Analysis-Block"] = kind
    return response

def _cookie_secure(request: Request) -> bool:
    """요청별 쿠키의 ``Secure`` 여부를 fail-closed로 정한다.

    ``AUTH_COOKIE_INSECURE=1``은 그 자체로 쿠키를 약하게 만들지 않는다. 실제 HTTP
    요청의 URL·서버 소켓·클라이언트 소켓이 모두 loopback이고 프록시 전달 흔적이
    없는 로컬 개발 요청에서만 예외를 허용한다. 요청 정보가 없거나 모호하면 언제나
    ``Secure``를 유지한다.
    """
    client_host = request.client.host if request.client is not None else ""
    server = request.scope.get("server")
    server_host = (
        str(server[0])
        if isinstance(server, (tuple, list)) and len(server) >= 1
        else ""
    )
    forwarded = any(
        request.headers.get(name, "").strip()
        for name in (
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
        )
    )
    allow_local_http = bool(
        os.environ.get(auth_constants.ENV_COOKIE_INSECURE, "").strip() == "1"
        and str(request.scope.get("scheme", "")).lower() == "http"
        and _request_targets_loopback(request)
        and _is_loopback_hostname(server_host)
        and _is_loopback_hostname(client_host)
        and not forwarded
    )
    return not allow_local_http


def _strict_loopback_http_request(request: Request) -> bool:
    """프록시 해석 없이 실제 로컬 HTTP 소켓이라고 확인되는 요청만 허용한다."""
    client_host = request.client.host if request.client is not None else ""
    server = request.scope.get("server")
    server_host = (
        str(server[0])
        if isinstance(server, (tuple, list)) and len(server) >= 1
        else ""
    )
    forwarded = any(
        request.headers.get(name, "").strip()
        for name in (
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
        )
    )
    return bool(
        str(request.scope.get("scheme", "")).lower() == "http"
        and _request_targets_loopback(request)
        and _is_loopback_hostname(server_host)
        and _is_loopback_hostname(client_host)
        and not forwarded
    )

def _admin_audit_unavailable(request: Request) -> HTMLResponse:
    """감사기록 자체를 만들 수 없으면 관리자 기능을 안전하게 닫는다."""
    response = HTMLResponse(
        '<div role="alert"><strong>관리자 요청을 기록할 수 없습니다.</strong> '
        "잠시 후 다시 시도해주세요.</div>",
        status_code=503,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Request-ID"] = admin_audit.request_id(request)
    return response


def _audit_admin_authorization(
    request: Request, *, action: str, target: str, allowed: bool
) -> Optional[HTMLResponse]:
    try:
        admin_audit.emit(
            request,
            action=action,
            target=target,
            outcome="allowed" if allowed else "denied",
            reason="authorization_ok" if allowed else "authorization_denied",
        )
    except Exception:  # noqa: BLE001 — 감사 sink 실패는 관리자 기능을 열지 않는다
        logger.error("관리자 권한 감사기록을 남기지 못했습니다")
        return _admin_audit_unavailable(request)
    return None


def require_admin(
    request: Request,
    *,
    action: str = "admin.authorize",
    target: str = "",
) -> Optional[Response]:
    """관리자가 아니면 되돌릴 응답, 관리자면 None.

    ★ **매 요청마다 서버에서 다시 판정한다.** 버튼을 숨기는 것은 권한이 아니다
      (성공기준 P4 「0건 고정」).
    """
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    allowed = auth_logic.is_admin_session(token)
    audit_target = target or admin_audit.target_id("route", request.url.path)
    audit_blocked = _audit_admin_authorization(
        request,
        action=action,
        target=audit_target,
        allowed=allowed,
    )
    if audit_blocked is not None:
        return audit_blocked
    if not allowed:
        response = RedirectResponse("/auth/not-admin", status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = admin_audit.request_id(request)
        return response
    return None

def _request_csrf_secret(request: Request) -> str:
    """검증된 권한 쿠키 중 CSRF 토큰의 비밀로 쓸 값을 하나 고른다.

    로그인 세션을 먼저 고정한다. 로그인과 링크 쿠키가 함께 있을 때 화면과 검사가
    서로 다른 비밀을 쓰면 정상 폼이 깨지고, 공격자가 더 약한 쪽을 고를 수도 있다.
    쿠키 문자열만 그럴듯한 것은 비밀로 인정하지 않는다. 공격자가 자기가 정한 쿠키와
    공개된 HMAC 식으로 토큰까지 계산해 유료 입구를 통과하는 우회를 막기 위해서다.
    """
    session_secret = request.cookies.get(auth_constants.SESSION_COOKIE_NAME) or ""
    if session_secret:
        try:
            if auth_logic.get_session(session_secret) is not None:
                return session_secret
        except Exception:  # noqa: BLE001 — 검증 저장소 장애는 권한을 주지 않는 쪽으로
            logger.exception("CSRF 세션을 확인하지 못했습니다")
    if evaluation_mode.enabled() and _strict_loopback_http_request(request):
        # 로그인 없는 로컬 평가도 same-origin만으로 끝내지 않고, 화면을 실제로 연
        # 브라우저만 알 수 있는 프로세스 난수 파생 폼 토큰까지 요구한다.
        return evaluation_mode.csrf_secret()
    # 모양만 맞는 링크도 안 된다. 현재 DB에 있고 만료·삭제되지 않은 링크만 쓴다.
    return _raw_share_key(request)

def _effective_http_origin(
    scheme: str, hostname: Optional[str], port: Optional[int]
) -> tuple[str, str, int] | None:
    """HTTP(S) 출처를 (scheme, host, effective port)로 정규화한다."""
    normalized_scheme = scheme.lower()
    if normalized_scheme not in {"http", "https"} or not hostname:
        return None
    effective_port = port
    if effective_port is None:
        effective_port = 443 if normalized_scheme == "https" else 80
    return normalized_scheme, hostname.lower(), effective_port

def _csrf_origin_matches(request: Request) -> bool:
    """브라우저가 Origin을 보냈다면 요청의 전체 HTTP(S) 출처와 같아야 한다."""
    if (
        # ★ 포트폴리오 링크 계약도 옛 관리자 계약과 같은 «forwarded 헤더 불신 +
        #   고정 PUBLIC_ORIGIN 하나만 신뢰» 실행 모델이므로 render_admin_no_forwarded()
        #   (친구 입구 차단 여부)가 아니라 이 더 넓은 판정으로 CSRF Origin을 고정한다.
        deployment_mode.render_pinned_origin_no_forwarded()
        and request.method.upper() == "POST"
    ):
        # Render edge가 붙인 X-Forwarded-*는 읽지 않는다. 외부 HTTPS 출처는
        # 보호된 배포 설정 하나만 신뢰하고, 중복·누락 Origin도 fail-closed한다.
        origin_values = request.headers.getlist("origin")
        if len(origin_values) != 1:
            return False
        supplied = deployment_mode.http_origin(origin_values[0])
        expected = deployment_mode.configured_public_origin()
        return supplied is not None and expected is not None and supplied == expected

    origin = request.headers.get("origin", "").strip()
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        # Origin 헤더는 경로·인증정보·쿼리·fragment가 없는 직렬화된 출처여야 한다.
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        supplied = _effective_http_origin(parsed.scheme, parsed.hostname, parsed.port)
        current = _effective_http_origin(
            request.url.scheme, request.url.hostname, request.url.port
        )
    except (TypeError, ValueError):
        return False
    return supplied is not None and supplied == current


def _is_loopback_hostname(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _request_targets_loopback(request: Request) -> bool:
    """로컬 미리보기 주소인지 확인한다.

    이 값은 익명 ``DemoPipeline``의 무료 화면 흐름에만 쓰인다. Render 같은 공개
    호스트, 유효한 로그인 세션·초대 링크, 실제 조사에는 예외를 주지 않는다.
    """
    return _is_loopback_hostname(request.url.hostname or "")


def _local_demo_origin_matches(request: Request) -> bool:
    """로컬 데모에서 허용할 브라우저 Origin 차이만 좁게 판정한다."""
    if not _request_targets_loopback(request):
        return False

    origin = request.headers.get("origin", "").strip()
    if origin == "null":
        return True
    # Origin 표준에는 끝 슬래시가 없지만 일부 로컬 도구가 한 개를 붙인다.
    if origin.endswith("/"):
        origin = origin[:-1]
    try:
        parsed = urlsplit(origin)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
        supplied = _effective_http_origin(parsed.scheme, parsed.hostname, parsed.port)
        current = _effective_http_origin(
            request.url.scheme, request.url.hostname, request.url.port
        )
    except (TypeError, ValueError):
        return False
    return bool(
        supplied is not None
        and current is not None
        and supplied[0] == current[0]
        and supplied[2] == current[2]
        and _is_loopback_hostname(supplied[1])
    )

def _csrf_rejected() -> HTMLResponse:
    response = HTMLResponse(
        "요청을 확인할 수 없습니다. 화면을 새로고침해 주세요.",
        status_code=403,
    )
    response.headers["Cache-Control"] = "no-store"
    return response

def require_csrf(request: Request, received: str) -> Optional[HTMLResponse]:
    """현재 세션의 숨은 폼 토큰과 선택적 Origin을 함께 확인한다."""
    session_token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    try:
        session_is_active = auth_logic.get_session(session_token) is not None
    except Exception:  # noqa: BLE001 — 세션 저장소를 못 읽으면 변경 권한을 주지 않는다
        logger.exception("CSRF 세션 유효성을 확인하지 못했습니다")
        session_is_active = False
    if (
        not session_is_active
        or not _csrf_origin_matches(request)
        or not auth_logic.csrf_token_matches(session_token, received)
    ):
        return _csrf_rejected()
    return None

def require_analysis_action_csrf(
    request: Request, received: str
) -> Optional[HTMLResponse]:
    """회사 확인·거절·조사 시작의 권한 쿠키를 CSRF로 묶는다.

    세션이나 초대 링크 쿠키가 있으면 그 HttpOnly 비밀에서 파생한 토큰이 반드시
    필요하다. 아무 권한 쿠키도 없는 데모 손님은 돈·권한을 쓸 수 없으므로 토큰 없이
    허용하되, 브라우저가 보낸 Origin은 여전히 같은 출처여야 한다.
    """
    origin_matches = _csrf_origin_matches(request)
    secret = _request_csrf_secret(request)
    if evaluation_mode.enabled():
        # 유료 로컬 평가는 exact same-origin Origin, 실제 loopback 소켓, 화면에서
        # 발급한 프로세스 난수 파생 토큰을 모두 요구한다. Origin 누락/null 호환은
        # 무료 DemoPipeline에만 남긴다.
        origin = request.headers.get("origin", "").strip()
        return (
            None
            if (
                origin
                and origin != "null"
                and origin_matches
                and _strict_loopback_http_request(request)
                and auth_logic.csrf_token_matches(secret, received)
            )
            else _csrf_rejected()
        )
    if secret:
        return (
            None
            if origin_matches and auth_logic.csrf_token_matches(secret, received)
            else _csrf_rejected()
        )
    if isinstance(runtime._PIPELINE, DemoPipeline):
        # 로컬 데모는 외부 호출·비용·권한 변경이 전혀 없다. 일부 브라우저가
        # localhost/127.0.0.1을 섞거나 Origin:null을 보내도 미리보기만은 쓸 수
        # 있게 한다. 배포 주소에서는 기존 same-origin 검사를 그대로 지킨다.
        return (
            None
            if origin_matches or _local_demo_origin_matches(request)
            else _csrf_rejected()
        )
    return _csrf_rejected()


def require_evaluation_consent(
    request: Request, received: str
) -> Optional[HTMLResponse]:
    """실시간 평가의 외부 호출을 서버가 검증한 화면 동의에 묶는다."""
    if not evaluation_mode.enabled():
        return None
    if not evaluation_mode.paid_providers_enabled():
        return _throttled(
            request,
            evaluation_mode.PREVIEW_BLOCKED_MESSAGE,
            "evaluation-preview",
        )
    if evaluation_mode.consent_granted(received):
        return None
    return _evaluation_consent_required_response(request)


def _evaluation_consent_required_response(request: Request) -> HTMLResponse:
    """동의 누락을 provider를 부르지 않는 친화적 422 화면으로 돌려준다."""
    response = templates.TemplateResponse(
        request=request,
        name="evaluation_consent_required.html",
        context=_ctx(request),
        status_code=422,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def evaluation_consent_roundtrip(
    request: Request,
    *,
    user_input: UserInput,
    bucket: str,
    received: str,
    grant: str,
    workflow_id: str,
    allow_issue: bool,
) -> tuple[Optional[HTMLResponse], str]:
    """첫 체크를 짧은 서버 서명으로 바꾸고 후속 폼은 그 서명만 검증한다."""
    if not evaluation_mode.enabled():
        return None, ""
    if not evaluation_mode.paid_providers_enabled():
        return (
            _throttled(
                request,
                evaluation_mode.PREVIEW_BLOCKED_MESSAGE,
                "evaluation-preview",
            ),
            "",
        )
    stored_bucket = spend_store.bucket_id(bucket)
    fields = {
        "company": user_input.company,
        "job": user_input.job,
        "region": user_input.region,
        "posting_text": user_input.posting_text,
        "bucket_id": stored_bucket,
    }
    if allow_issue:
        if (
            evaluation_mode.consent_granted(received)
            and evaluation_mode.consume_workflow_id(workflow_id)
        ):
            return None, evaluation_mode.issue_consent_grant(
                **fields,
                workflow_id=workflow_id,
                transition=evaluation_mode.CONSENT_TRANSITION_CONTINUE,
            )
        return _evaluation_consent_required_response(request), ""
    if evaluation_mode.consent_grant_valid(
        grant,
        **fields,
        expected_transition=evaluation_mode.CONSENT_TRANSITION_CONTINUE,
    ):
        return None, grant
    return _evaluation_consent_required_response(request), ""

def require_admin_action(
    request: Request,
    csrf_token: str,
    *,
    action: str = "admin.state_change",
    target: str = "",
) -> Optional[Response]:
    """상태를 바꾸는 관리자 요청에 권한과 CSRF를 같은 입구에서 적용한다."""
    audit_target = target or admin_audit.target_id("route", request.url.path)
    blocked = require_admin(request, action=action, target=audit_target)
    if blocked is not None:
        return blocked
    blocked = require_csrf(request, csrf_token)
    if blocked is None:
        try:
            admin_audit.emit(
                request,
                action=action,
                target=audit_target,
                outcome="attempt",
                reason="authorization_and_csrf_ok",
            )
        except Exception:  # noqa: BLE001 — 기록되지 않은 관리자 변경은 시작하지 않는다
            logger.error("관리자 변경 시도 감사기록을 남기지 못했습니다")
            return _admin_audit_unavailable(request)
        return None
    try:
        admin_audit.emit(
            request,
            action=action,
            target=audit_target,
            outcome="denied",
            reason="csrf_denied",
        )
    except Exception:  # noqa: BLE001 — 이미 거절된 요청은 계속 거절하되 원문은 남기지 않는다
        logger.error("관리자 CSRF 거절 감사기록을 남기지 못했습니다")
        return _admin_audit_unavailable(request)
    blocked.headers["X-Request-ID"] = admin_audit.request_id(request)
    return blocked
