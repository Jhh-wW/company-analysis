"""오류 신고 사용자 폼 — 검색 결과 없음·기업 선택·생성 중단·보고서 화면의 공용 진입점.

★ 여기는 ``src.features.feedback_report.logic``만 호출한다(store 직접 호출 금지).
  결과 화면에 있던 기존 회원 전용 신고(``POST /reports/{id}/errors`` →
  ``dashboard_store.record_error``, 신고 즉시 보고서 공개 차단)와는 완전히 분리된
  경로다 — 이 신고는 접수돼도 보고서를 막지 않는다.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.feedback_report import constants
from src.features.feedback_report import logic as feedback_logic
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.web import request_helpers
from src.web.security import CSRF_TOKEN_MAX_CHARS

logger = logging.getLogger(__name__)
router = APIRouter()

#: 화면에 보여줄 단계 한국어 라벨. constants.REPORT_STAGES 닫힌 목록과 1:1이다.
_STAGE_LABELS: dict[str, str] = {
    constants.STAGE_NO_SEARCH: "검색 결과 없음",
    constants.STAGE_COMPANY_SELECT: "기업 선택",
    constants.STAGE_GENERATING: "보고서 생성 중",
    constants.STAGE_REPORT: "보고서 열람",
}

#: stage가 닫힌 목록 밖(빈 값)일 때 사용자가 직접 고를 select 선택지.
_STAGE_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (value, _STAGE_LABELS[value]) for value in constants.REPORT_STAGES
)


def _clean_stage(value: str) -> str:
    """닫힌 목록 밖 값은 400을 내지 않고 조용히 빈 값으로 접는다."""
    text = str(value or "").strip()
    return text if text in constants.REPORT_STAGES else ""


def _clean_company(value: str) -> str:
    return str(value or "").strip()[: constants.MAX_COMPANY_NAME_CHARS]


def _clean_report_ref(value: str) -> str:
    return str(value or "").strip()[: constants.MAX_REPORT_REF_CHARS]


#: reporter_key 앞에 붙는 갈래 라벨과 원문 지문 사이의 구분자.
#: track.value("member"/"link"/"public"/"admin")는 콜론이 없는 닫힌 목록이라
#: 이 구분자로 라벨과 지문 경계가 흔들릴 일이 없다.
_REPORTER_TRACK_SEPARATOR: Final[str] = ":"


def _reporter_key(request: Request) -> str:
    """신고자 갈래 라벨 + 지출 통장 지문을 신고자 식별자로 쓴다.

    ``request_helpers._track_of()``가 이미 ADMIN/MEMBER는 이메일, LINK는 열쇠로
    손님을 나눠 돌려준다. 문제는 PUBLIC 갈래다 — ``tracks.bucket_of()``는
    PUBLIC 손님 «전원»에게 고정 공용 통장 하나(``PUBLIC_BUCKET``)를 돌려주므로,
    로그인은 했지만 초대되지 않아 PUBLIC으로 떨어진 손님끼리도 신고 상한을
    통째로 공유한다 — 계정 하나가 하루 상한(20건)을 채우면 같은 계층 전체의
    신고가 그날 막힌다(실측 결함).

    그래서 PUBLIC일 때는 한 단계 더 본다: 세션(로그인) 자체는 있는데 초대
    명단에만 없는 손님이면 그 세션의 이메일을 안정 식별자로 쓴다 — 계정마다
    다른 통장이 생겨 한 계정이 계층 전체를 잠그지 못한다. 세션조차 없는
    «진짜 익명»만 공용 통장을 공유한다(원문 이메일·열쇠는 여기서도 저장하지
    않는다 — ``spend_store.bucket_id()``가 SHA-256 지문으로만 바꾼다).

    ★ 앞에 track.value를 그대로 남기는 이유 — 지문만 저장하면 관리자가
    「이 신고가 회원이 낸 건지 링크 손님이 낸 건지」조차 구분 못 했다(실측
    결함). 라벨은 닫힌 목록(admin/member/link/public)이라 개인정보가 아니고,
    원문 이메일·열쇠는 여전히 저장하지 않는다 — 구분되는 건 «갈래»뿐이다.
    """
    track, bucket, _cap = request_helpers._track_of(request)
    if track is share_tracks.Track.PUBLIC:
        token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
        email = auth_logic.current_email(token) or ""
        if email:
            bucket = share_tracks.bucket_of(
                share_tracks.Track.MEMBER, email=email, share_key=""
            )
    return f"{track.value}{_REPORTER_TRACK_SEPARATOR}{spend_store.bucket_id(bucket)}"


#: reporter_key 갈래 라벨을 관리자 화면용 한국어로 옮긴다 — 상수로 빼서
#: 매직 문자열을 피한다(admin.py가 그대로 가져다 쓴다).
#: ★ track.value(닫힌 목록)만 키로 쓴다. 원문 이메일·열쇠·지문은 이 표에도,
#:   화면에도 절대 올리지 않는다 — 갈래만 구분되면 충분하고, 지문까지
#:   찍으면 캡처·공유로 새어 나갈 이유만 늘어난다.
REPORTER_TRACK_LABELS_KO: Final[dict[str, str]] = {
    share_tracks.Track.ADMIN.value: "관리자",
    share_tracks.Track.MEMBER.value: "회원",
    share_tracks.Track.LINK.value: "링크 손님",
    share_tracks.Track.PUBLIC.value: "비회원",
}

#: 갈래 라벨 도입(2026-08-25) 이전에 저장된 옛 reporter_key는 접두가 없는
#: raw SHA-256 지문뿐이다 — 갈래를 알 방법이 없으므로 이 라벨로 고정한다.
REPORTER_TRACK_UNKNOWN_LABEL: Final[str] = "알 수 없음"


def reporter_track_label(reporter_key: str) -> str:
    """저장된 reporter_key에서 «갈래»만 한국어로 뽑아 관리자 화면에 보여준다.

    지문(콜론 뒤)은 그대로 버린다 — 반환값에 절대 포함하지 않는다.

    ★ 옛 기록 호환 — 접두 자체가 없으면(콜론이 없으면) 이 라벨을 붙이기
      전에 만들어진 기록이다. 예외를 내지 않고 «알 수 없음»으로 고정한다.
    ★ 모르는 갈래 — track 라벨이 있는데도 REPORTER_TRACK_LABELS_KO에 없는
      값이면(예: 이후 새 Track이 추가됐는데 이 표를 못 따라간 경우) 화면이
      깨지지 않도록 원문 라벨을 그대로 보여준다(빈 화면·예외보다 낫다).
    """
    raw = str(reporter_key or "")
    prefix, separator, _digest = raw.partition(_REPORTER_TRACK_SEPARATOR)
    if not separator:
        return REPORTER_TRACK_UNKNOWN_LABEL
    return REPORTER_TRACK_LABELS_KO.get(prefix, prefix)


def _form_context(
    request: Request,
    *,
    stage: str,
    company_name: str,
    report_ref: str,
    category: str = "",
    item_label: str = "",
    body: str = "",
    ref_url: str = "",
    submitted: bool = False,
    report_id: str = "",
    form_error: str = "",
) -> dict:
    return request_helpers._ctx(
        request,
        stage=stage,
        stage_label=_STAGE_LABELS.get(stage, ""),
        # stage가 닫힌 목록 밖(빈 값)일 때만 템플릿이 이걸로 select를 그린다 —
        # hidden 빈 값은 사용자가 고칠 수 없어 POST가 항상 400으로 막다른
        # 폼이 됐다(실측 결함). 유효 stage는 지금처럼 hidden을 그대로 쓴다.
        stage_options=_STAGE_OPTIONS,
        company_name=company_name,
        report_ref=report_ref,
        category=category,
        item_label=item_label,
        body=body,
        ref_url=ref_url,
        categories=constants.REPORT_CATEGORIES,
        guide_notice=constants.FORM_GUIDE_NOTICE,
        max_body_chars=constants.MAX_BODY_CHARS,
        max_item_label_chars=constants.MAX_ITEM_LABEL_CHARS,
        max_ref_url_chars=constants.MAX_REF_URL_CHARS,
        submitted=submitted,
        report_id=report_id,
        form_error=form_error,
    )


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_form_page(
    request: Request,
    stage: str = "",
    company: str = "",
    report: str = "",
):
    """오류 신고 폼. stage가 닫힌 목록 밖이면 빈 값으로 접어 그대로 렌더한다."""
    return request_helpers.templates.TemplateResponse(
        request=request,
        name="feedback_form.html",
        context=_form_context(
            request,
            stage=_clean_stage(stage),
            company_name=_clean_company(company),
            report_ref=_clean_report_ref(report),
        ),
    )


@router.post("/feedback", response_class=HTMLResponse)
async def feedback_form_submit(
    request: Request,
    stage: str = Form(""),
    company: str = Form(""),
    report: str = Form(""),
    category: str = Form(""),
    item_label: str = Form(""),
    body: str = Form(""),
    ref_url: str = Form(""),
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
):
    """신고 접수. 검증 실패·상한 초과는 같은 폼 위에 입력을 보존해 그대로 보여준다."""
    blocked = request_helpers.require_analysis_action_csrf(request, csrf_token)
    if blocked is not None:
        return blocked
    if not request_helpers._request_csrf_secret(request):
        # ★ require_analysis_action_csrf는 회사 확인·거절·조사 시작(confirm·run)을
        #   위해 설계됐다 — 그 화면들은 돈·권한이 없는 DemoPipeline 손님이면
        #   완전 익명이어도 same-origin만 맞으면 통과시킨다(무료 미리보기라
        #   안전하기 때문). 오류 신고는 대상이 다르다: 로그인 회원과 링크
        #   손님만 대상이고, 완전 익명(세션도 유효한 LINK 쿠키도 없음)은
        #   대상이 아니다(사용자 확정). 그래서 위 CSRF 통과와 별개로
        #   «신원 자체가 있는가»를 한 번 더 본다 — 없으면 DemoPipeline의
        #   완화를 그대로 물려받지 않고 여기서 막는다.
        return request_helpers._csrf_rejected()

    clean_stage = _clean_stage(stage)
    clean_company = _clean_company(company)
    clean_report = _clean_report_ref(report)

    def _re_render(message: str, *, status_code: int) -> HTMLResponse:
        return request_helpers.templates.TemplateResponse(
            request=request,
            name="feedback_form.html",
            context=_form_context(
                request,
                stage=clean_stage,
                company_name=clean_company,
                report_ref=clean_report,
                category=category,
                item_label=item_label,
                body=body,
                ref_url=ref_url,
                form_error=message,
            ),
            status_code=status_code,
        )

    try:
        with storage_db.connect() as conn:
            created = feedback_logic.create_report(
                conn,
                stage=clean_stage,
                category=category,
                body=body,
                company_name=clean_company,
                report_ref=clean_report,
                item_label=item_label,
                ref_url=ref_url,
                reporter_key=_reporter_key(request),
            )
    except feedback_logic.FeedbackReportLimitError as error:
        return _re_render(str(error), status_code=429)
    except feedback_logic.FeedbackReportError as error:
        return _re_render(str(error), status_code=400)
    except Exception:  # noqa: BLE001 — 저장 장애도 같은 폼에서 알린다
        logger.exception("오류 신고 접수를 저장하지 못했습니다")
        return _re_render(
            "신고를 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            status_code=503,
        )

    return request_helpers.templates.TemplateResponse(
        request=request,
        name="feedback_form.html",
        context=_form_context(
            request,
            stage=clean_stage,
            company_name=clean_company,
            report_ref=clean_report,
            submitted=True,
            report_id=created.report_id,
        ),
    )
