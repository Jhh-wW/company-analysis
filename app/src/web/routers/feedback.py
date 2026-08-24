"""오류 신고 사용자 폼 — 검색 결과 없음·기업 선택·생성 중단·보고서 화면의 공용 진입점.

★ 여기는 ``src.features.feedback_report.logic``만 호출한다(store 직접 호출 금지).
  결과 화면에 있던 기존 회원 전용 신고(``POST /reports/{id}/errors`` →
  ``dashboard_store.record_error``, 신고 즉시 보고서 공개 차단)와는 완전히 분리된
  경로다 — 이 신고는 접수돼도 보고서를 막지 않는다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from src.features.budget import spend_store
from src.features.feedback_report import constants
from src.features.feedback_report import logic as feedback_logic
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


def _clean_stage(value: str) -> str:
    """닫힌 목록 밖 값은 400을 내지 않고 조용히 빈 값으로 접는다."""
    text = str(value or "").strip()
    return text if text in constants.REPORT_STAGES else ""


def _clean_company(value: str) -> str:
    return str(value or "").strip()[: constants.MAX_COMPANY_NAME_CHARS]


def _clean_report_ref(value: str) -> str:
    return str(value or "").strip()[: constants.MAX_REPORT_REF_CHARS]


def _reporter_key(request: Request) -> str:
    """원문 이메일·열쇠 대신 기존 지출 통장 지문을 신고자 식별자로 재사용한다.

    ``request_helpers._track_of()``가 이미 ADMIN/MEMBER는 이메일, LINK는 열쇠,
    PUBLIC은 공용 통장으로 손님을 나눠 돌려준다. ``spend_store.bucket_id()``가
    그 값을 원문 없이 SHA-256 지문으로만 바꿔, 하루 접수 상한이 실제 방문자
    단위로 나뉘게 한다. 빈 문자열이 나올 일이 없어 익명 전체가 한 버킷으로
    묶이는 사고를 막는다.
    """
    _track, bucket, _cap = request_helpers._track_of(request)
    return spend_store.bucket_id(bucket)


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
