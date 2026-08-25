"""완성 보고서 화면과 PDF·Notion 내보내기 경로."""

import asyncio
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock
from src.features.budget.sharing import SHARED_LINK_HEADERS
from src.features.cost_tracking import store as cost_store
from src.features.export_pdf.constants import CONTENT_TYPE_PDF
from src.features.export_pdf.logic import (
    PDFGenerationError,
    build_content_disposition as build_pdf_content_disposition,
    build_download_filename as build_pdf_download_filename,
)
from src.features.export_pdf.release import (
    PDFReleaseBlockedError,
    PdfReleaseCandidate,
    prepare_pdf_release,
)
from src.features.export_pdf.automatic_release import (
    AutomaticallyReleasedPdf,
    automatic_release_pdf,
    report_sha256,
    restore_automatic_release,
)
from src.features.export_pdf import release_store as pdf_release_store
from src.features.export_notion import store as notion_store
from src.features.admin_dashboard import kpi as dashboard_kpi
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_notion.notion import (
    NotionExportResult,
    is_notion_configured,
    send_report_to_notion,
)
from src.features.feedback_report import constants as feedback_constants
from src.features.grading.logic import grade_message
from src.features.observability import admin_audit
from src.features.pipeline.port import (
    CompanyCard,
    Grade,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.validate import (
    V2ValidationError,
    v2_validation_problems,
    validate_v2,
)
from src.features.report_standard import PublishBlockedError, build_published_report
from src.features.sharelink import store as share_store
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime, request_helpers
from src.web.security import CSRF_TOKEN_MAX_CHARS


router = APIRouter()
logger = logging.getLogger(__name__)

# Keep strong references while a request is cancelled.  ``asyncio.shield``
# lets the sync worker finish and persist the remote outcome instead of leaving
# an ambiguous operation that a later click could duplicate.
_NOTION_EXPORT_WORKERS: set[asyncio.Task] = set()
_REPORT_UNAVAILABLE_HOME = "/?report_status=unavailable"
#: 관리자만 오는 경로(POST /notion)의 출구. 홈보다 관리 화면이 할 일에 가깝다.
_ADMIN_DASHBOARD_URL = "/admin/dashboard"
_ADMIN_DASHBOARD_LABEL = "관리 대시보드로"
#: 자동검사 사유는 automatic_release가 정한 고정 한국어 문구뿐이지만, 화면과
#: 로그가 예상 밖의 길이·줄바꿈에 흔들리지 않도록 상한을 둔다.
_GATE_REASON_MAX_CHARS = 120
_GATE_REASON_MAX_COUNT = 8
_GATE_UNKNOWN_REASON = "자동 출고 승인을 확인하지 못했습니다"
#: 로그 상관 필드에 넣을 보고서 식별자 상한 (경로에서 온 값이라 길이를 자른다).
_LOG_REPORT_ID_MAX_CHARS = 64


def _report_grade_note(report: Report) -> str:
    """레거시 6칸과 canonical 장 수를 섞지 않은 완성도 안내를 돌려준다."""

    if report.schema_version:
        from src.features.report_standard.constants import (  # noqa: PLC0415
            CANONICAL_SCHEMA_VERSION,
            CANONICAL_SECTION_IDS,
        )

        if report.schema_version == CANONICAL_SCHEMA_VERSION:
            if report.grade is Grade.PARTIAL:
                return (
                    "검증된 부분 보고서(부분 완성) — "
                    "공식 근거로 확인된 항목만 담았습니다."
                )
            return grade_message(
                report.grade,
                report.filled_count,
                total=len(CANONICAL_SECTION_IDS),
            )
    return grade_message(report.grade, report.filled_count)


def _report_unavailable_redirect() -> RedirectResponse:
    """보고서 존재 여부를 밝히지 않고 같은 일반 안내로 첫 화면에 보낸다."""

    return RedirectResponse(_REPORT_UNAVAILABLE_HOME, status_code=303)


def _report_for_output(report: Report) -> Report:
    """현재 canonical 출고 게이트를 통과한 공개본만 돌려준다.

    엔진 v2(composer) 보고서는 canonical 게이트 대상이 아니다 — v2 3검사
    (내부 키·인용-부록 1:1·요약 존재)만 통과하면 별도 공개본 투영 없이 정본
    그대로 공개한다 (실행계획 04장 3-4절 2항). v1 경로는 기존 동작 그대로다.
    """

    if report.schema_version == ENGINE_V2_SCHEMA_VERSION:
        validate_v2(report)
        return report
    return build_published_report(report)


def _content_validator_for(report: Report):
    """자동출고 4검사의 내용 검증기 선택 — v2 보고서에만 v2 3검사를 주입한다."""

    if report.schema_version == ENGINE_V2_SCHEMA_VERSION:
        return v2_validation_problems
    return None


def _approved_public_report(report_id: str, fallback: Report) -> Report | None:
    """등록된 관리 상태가 있으면 메모리 결과 대신 승인 snapshot만 공개한다.

    대시보드 도입 전의 legacy 보고서는 상태 projection이 없으므로 기존 저장본을
    fallback으로 쓴다. 반면 상태가 있는데 승인 version 원본을 읽지 못하면
    원본 보고서가 다시 공개되는 일을 막기 위해 None으로 fail-closed 한다.
    """
    with storage_db.connect() as conn:
        state = dashboard_store.get_report_state(conn, report_id)
        if not state.updated_at:
            return fallback
        payload_json = dashboard_store.approved_report_payload(conn, report_id=report_id)
    if not payload_json:
        return None
    return report_store.report_from_json(payload_json)


def _blocked_report_response(request: Request) -> Response:
    """구형·손상 보고서를 현재 결과처럼 노출하지 않는 영구 중단 화면."""

    result = RunResult(
        outcome=Outcome.GATE_STOPPED,
        message="현재 보고서 기준을 통과한 근거가 충분하지 않아 결과를 표시하지 않았습니다.",
        charged=False,
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="stopped.html",
        context=request_helpers._ctx(request, result=result, show_quota_note=False),
        status_code=409,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    return response


def _mark_link_release_gate_stopped(report_id: str) -> None:
    """출고 검사에 막힌 LINK 실행만 안정적인 중단 상태로 마감한다.

    공개 차단 응답이 정본이므로 이력 저장 장애가 원래 ``PublishBlockedError`` 또는
    ``PDFReleaseBlockedError``를 가려서는 안 된다. LINK에 결속되지 않은 보고서는
    갱신 대상이 없어 그대로 끝난다.
    """

    try:
        with storage_db.connect() as conn:
            share_store.mark_release_stopped(
                conn,
                report_id=report_id,
                stopped_at=clock.iso_now_kst(),
                stop_step="automatic_release_gate",
                stop_reason="automatic_release_gate_stopped",
            )
    except Exception:  # noqa: BLE001 — 원래 출고 차단을 절대 가리지 않는다
        logger.exception(
            "LINK 자동출고 중단 이력을 저장하지 못했습니다 report_id=%s",
            report_id,
        )


@dataclass(frozen=True)
class _WorkerResult:
    result: NotionExportResult
    persisted: bool


@dataclass(frozen=True)
class _SupervisorResult:
    decision: notion_store.ClaimResult
    outcome: _WorkerResult | None = None


def _notion_state(result: NotionExportResult) -> tuple[str, str]:
    if result.success:
        return notion_store.STATE_SUCCEEDED, ""
    if result.partial:
        return notion_store.STATE_PARTIAL, "partial"
    if result.uncertain:
        return notion_store.STATE_UNKNOWN, "remote_outcome_unknown"
    return notion_store.STATE_FAILED, "definite_failure"


def _run_and_persist_notion_export(
    job_id: str,
    digest: str,
    revision: int,
    report,
) -> _WorkerResult:
    """Run urllib in a worker thread and persist its outcome in that thread."""
    try:
        result = send_report_to_notion(
            report, grade_note=_report_grade_note(report)
        )
    except Exception as exc:  # noqa: BLE001 - injected adapters must not strand state
        logger.warning("노션 작업자 예외 type=%s", type(exc).__name__)
        result = NotionExportResult(
            success=False,
            error="노션 전송 결과를 확인하지 못했습니다",
            uncertain=True,
        )

    state, error_kind = _notion_state(result)
    try:
        with storage_db.connect() as conn:
            persisted = notion_store.finish(
                conn,
                job_id,
                digest,
                revision,
                state=state,
                page_id=result.page_id,
                page_url=result.page_url,
                error_kind=error_kind,
            )
    except Exception as exc:  # noqa: BLE001 - never claim success without durable state
        logger.warning("노션 전송 상태 저장 실패 type=%s", type(exc).__name__)
        persisted = False
    return _WorkerResult(result=result, persisted=persisted)


def _claim_notion_export(
    job_id: str,
    digest: str,
    *,
    explicit_retry: bool,
    expected_revision: int | None,
) -> notion_store.ClaimResult:
    with storage_db.connect() as conn:
        return notion_store.claim(
            conn,
            job_id,
            digest,
            explicit_retry=explicit_retry,
            expected_revision=expected_revision,
        )


async def _supervise_notion_export(
    job_id: str,
    digest: str,
    report,
    *,
    explicit_retry: bool,
    expected_revision: int | None,
) -> _SupervisorResult:
    """Own claim through final persistence independently of request lifetime."""
    decision = await asyncio.to_thread(
        _claim_notion_export,
        job_id,
        digest,
        explicit_retry=explicit_retry,
        expected_revision=expected_revision,
    )
    if not decision.claimed:
        return _SupervisorResult(decision=decision)
    outcome = await asyncio.to_thread(
        _run_and_persist_notion_export,
        job_id,
        digest,
        decision.record.revision,
        report,
    )
    return _SupervisorResult(decision=decision, outcome=outcome)


def _record_result(record: notion_store.ExportRecord) -> NotionExportResult:
    if record.state == notion_store.STATE_SUCCEEDED:
        return NotionExportResult(
            success=True, page_id=record.page_id, page_url=record.page_url
        )
    if record.state == notion_store.STATE_PARTIAL:
        return NotionExportResult(
            success=False,
            page_id=record.page_id,
            page_url=record.page_url,
            partial=True,
            error="이전 전송에서 일부 내용만 만들어졌습니다",
        )
    if record.state == notion_store.STATE_UNKNOWN:
        return NotionExportResult(
            success=False,
            page_id=record.page_id,
            page_url=record.page_url,
            uncertain=True,
            error="이전 전송이 적용됐는지 확인할 수 없습니다",
        )
    return NotionExportResult(
        success=False, error="이전 전송이 완료되지 않았습니다"
    )


def _render_notion_result(
    request: Request,
    *,
    notion: NotionExportResult,
    report,
    job_id: str,
    record: notion_store.ExportRecord,
    reused: bool,
    status_code: int = 200,
):
    return request_helpers.templates.TemplateResponse(
        request=request,
        name="notion_result.html",
        context=request_helpers._ctx(
            request,
            notion=notion,
            report=report,
            job_id=job_id,
            notion_state=record.state,
            notion_revision=record.revision,
            notion_reused=reused,
            notion_retry_allowed=record.state in notion_store.TERMINAL_RETRY_STATES,
        ),
        status_code=status_code,
    )


def _forget_notion_worker(task: asyncio.Task) -> None:
    _NOTION_EXPORT_WORKERS.discard(task)
    if not task.cancelled():
        # Retrieve a possible exception so asyncio does not emit an unhandled
        # task warning after a client disconnect.  The worker itself is safe.
        task.exception()


@router.get("/result/{job_id}", response_class=HTMLResponse)
async def result_page(request: Request, job_id: str):
    """보고서 또는 「왜 못 만들었는지」를 보여준다."""

    return await _result_page_response(request, job_id, allow_expired=False)


async def _result_page_response(
    request: Request,
    job_id: str,
    *,
    allow_expired: bool,
) -> Response:
    """일반 결과와 관리자 LINK 이력 조회가 공유하는 출고·렌더 경계."""

    blocked = _dashboard_publication_block(request, job_id)
    if blocked is not None:
        return blocked

    job = job_runtime._JOBS.get(job_id)
    if job is None or job.result is None:
        try:
            saved = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
        if saved is None:
            return _report_unavailable_redirect()
        if not allow_expired and job_runtime._link_expired(saved):
            return job_runtime._expired_screen(request)
        try:
            output_report = _report_for_output(saved)
        except (PublishBlockedError, V2ValidationError):
            _mark_link_release_gate_stopped(job_id)
            return _blocked_report_response(request)
        try:
            _candidate, released = await asyncio.to_thread(
                _release_state, report_id=job_id, report=output_report
            )
        except PDFReleaseBlockedError as error:
            return _pdf_review_pending_response(
                request, error, job_id=job_id, company=saved.company
            )
        except Exception as error:  # noqa: BLE001 — 공개 경계는 승인 저장소도 fail-closed
            return _pdf_unavailable_response(request, error)
        preview = released is None
        saved_job = job_runtime.Job(
            job_id=job_id,
            user_input=UserInput(company=saved.company, job=saved.job, region=""),
            card=CompanyCard(
                legal_name=saved.company,
                typed_name=saved.company,
                address="",
                ceo="",
                founded="",
            ),
            finished=True,
            report_persisted=True,
        )
        return _render_result_page(
            request,
            job=saved_job,
            result=RunResult(outcome=Outcome.REPORT, report=output_report),
            report=output_report,
            internal_review_preview=preview,
        )

    result = job.result
    if result.outcome is not Outcome.REPORT or result.report is None:
        return request_helpers.templates.TemplateResponse(
            request=request,
            name="stopped.html",
            context=request_helpers._ctx(request, job=job, result=result),
        )

    report = result.report
    # 첫 저장이 실패했으면 현재 사용자의 새로고침이 실제 재시도가 되게 한다.
    if job.report_persisted is False:
        job_runtime._save_report(job)
    try:
        report = _approved_public_report(job_id, report)
    except Exception:  # noqa: BLE001 - immutable 승인 원본을 못 읽으면 공개하지 않는다
        logger.exception("승인 보고서 원본을 읽지 못했습니다. report_id=%s", job_id)
        return _dashboard_blocked_response(request, unavailable=True)
    if report is None:
        return _dashboard_blocked_response(request, unavailable=False)
    if not allow_expired and job_runtime._link_expired(report):
        return job_runtime._expired_screen(request)
    try:
        output_report = _report_for_output(report)
    except (PublishBlockedError, V2ValidationError):
        _mark_link_release_gate_stopped(job_id)
        return _blocked_report_response(request)
    try:
        _candidate, released = await asyncio.to_thread(
            _release_state, report_id=job_id, report=output_report
        )
    except PDFReleaseBlockedError as error:
        return _pdf_review_pending_response(
            request, error, job_id=job_id, company=job.user_input.company
        )
    except Exception as error:  # noqa: BLE001 — 공개 경계는 승인 저장소도 fail-closed
        return _pdf_unavailable_response(request, error)
    preview = released is None
    return _render_result_page(
        request,
        job=job,
        result=result,
        report=output_report,
        internal_review_preview=preview,
    )


def _report_for_download(request: Request, job_id: str) -> Report | Response:
    """두 다운로드 형식이 공유하는 조회·장애·만료 계약."""
    blocked = _dashboard_publication_block(request, job_id)
    if blocked is not None:
        return blocked
    job = job_runtime._JOBS.get(job_id)
    if job is not None and job.result is not None and job.result.report is not None:
        try:
            report = _approved_public_report(job_id, job.result.report)
        except Exception:  # noqa: BLE001 - immutable 승인 원본을 못 읽으면 공개하지 않는다
            logger.exception("승인 다운로드 원본을 읽지 못했습니다. report_id=%s", job_id)
            return _dashboard_blocked_response(request, unavailable=True)
    else:
        try:
            report = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
    if report is None:
        return _report_unavailable_redirect()
    if job_runtime._link_expired(report):
        return job_runtime._expired_screen(request)
    return report


def _dashboard_publication_block(request: Request, report_id: str) -> Response | None:
    """MEMBER 신고 뒤에는 결과·PDF·공유가 같은 DB transaction으로 닫힌다.

    운영 상태를 읽지 못한 경우도 공개 경계를 열지 않는다. 관리자 원본 검토는
    별도 ``/reports/{id}`` 경로로만 제공한다.
    """
    session = auth_logic.get_session(
        request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    )
    if session is not None and not session.is_admin:
        try:
            with storage_db.connect() as conn:
                member_allowed = share_allow.is_allowed(conn, session.email)
        except Exception:  # noqa: BLE001 - an unknown revocation state cannot reopen reports
            logger.exception("MEMBER 철회 상태를 읽지 못했습니다")
            return _revoked_member_response(request, unavailable=True)
        if not member_allowed:
            return _revoked_member_response(request, unavailable=False)
    try:
        with storage_db.connect() as conn:
            is_blocked = dashboard_store.report_is_blocked(conn, report_id)
            is_trashed = dashboard_store.report_is_trashed(conn, report_id)
    except Exception:  # noqa: BLE001 - moderation state uncertainty must fail closed
        logger.exception("관리 대시보드의 보고서 차단 상태를 읽지 못했습니다")
        return _dashboard_blocked_response(request, unavailable=True)
    if is_blocked or is_trashed:
        return _dashboard_blocked_response(request, unavailable=False)
    return None


def _revoked_member_response(request: Request, *, unavailable: bool) -> Response:
    message = (
        "현재 MEMBER 권한이 없어 저장된 결과와 다운로드를 열 수 없습니다."
        if not unavailable
        else "MEMBER 권한 상태를 확인할 수 없어 결과와 다운로드를 잠시 열지 않습니다."
    )
    # 권한 «상태를 못 읽은» 경우에만 같은 화면 재확인이 진짜 재시도다.
    # 권한이 없는 것이 확정된 경우는 다시 열어도 결과가 같으므로 홈으로 보낸다.
    if unavailable:
        retry_context = job_runtime.retry_or_exit(request, retry_label="다시 확인")
    else:
        retry_context = {
            "retry_url": job_runtime.DEFAULT_EXIT_URL,
            "retry_label": job_runtime.DEFAULT_EXIT_LABEL,
            "retry_same_page": False,
        }
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title="MEMBER 권한을 확인해 주세요",
            interruption_message=message,
            interruption_hint="관리자에게 초대 상태를 문의해 주세요.",
            **retry_context,
        ),
        status_code=503 if unavailable else 403,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    return response


def _dashboard_blocked_response(request: Request, *, unavailable: bool) -> Response:
    title = "보고서를 잠시 확인 중입니다" if not unavailable else "보고서 상태를 확인할 수 없습니다"
    message = (
        "오류 신고가 접수되어 결과·다운로드·공유를 잠시 멈췄습니다. "
        "관리자가 원본과 출처를 확인한 뒤 직접 다시 공개합니다."
        if not unavailable
        else "결과 공개 상태를 안전하게 확인할 수 없어 잠시 열지 않습니다. 잠시 후 다시 시도해 주세요."
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title=title,
            interruption_message=message,
            interruption_hint="새 조사를 다시 시작할 필요가 없습니다.",
            **job_runtime.retry_or_exit(request, retry_label="상태 다시 확인"),
        ),
        status_code=503 if unavailable else 409,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["Cache-Control"] = "no-store"
    return response


def _pdf_unavailable_response(
    request: Request,
    error: Exception,
) -> Response:
    """PDF 내부 오류·보고서 원문을 반사하지 않는 재시도 가능 503."""
    request_id = admin_audit.request_id(request)
    log = logger.warning if isinstance(error, PDFGenerationError) else logger.error
    # 예외 메시지·traceback·회사명은 기록하지 않는다. 내부 cause에는 공급자나
    # 보고서 원문이 들어 있을 수 있어 상관 ID와 오류 종류만 남긴다.
    log(
        "PDF 보고서 생성 실패 error_type=%s request_id=%s",
        type(error).__name__,
        request_id,
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title="PDF 보고서를 잠시 만들 수 없습니다",
            interruption_message=(
                "파일을 만드는 중 문제가 발생했습니다. 잠시 후 다시 받아 주세요."
            ),
            interruption_hint=(
                "현재 보고서 화면은 그대로 사용할 수 있으며 새 조사를 시작할 필요가 없습니다."
            ),
            **job_runtime.retry_or_exit(request, retry_label="PDF 다시 받기"),
            support_reference=request_id,
        ),
        status_code=503,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["X-Request-ID"] = request_id
    return job_runtime._retryable_response(response)


def _gate_reasons(error: Exception) -> tuple[str, ...]:
    """자동검사가 «무엇을» 막았는지 화면과 로그에 같이 쓸 문구.

    ``AutomaticGateStopped.reasons``만 읽는다. ``str(error)``를 쓰면 안 된다 —
    composer.validate에는 보고서 값을 그대로 문장에 박아 넣는 사유가 있어
    공개 화면에 그리면 보고서 원문이 새어 나간다. reasons는 automatic_release가
    정한 고정 한국어 문구뿐이라 안전하다.
    """
    raw = getattr(error, "reasons", ())
    if isinstance(raw, (str, bytes)):
        # 문자열을 그대로 tuple()에 넣으면 한 글자씩 쪼개진다.
        raw = ()
    try:
        candidates = tuple(raw)[:_GATE_REASON_MAX_COUNT]
    except TypeError:
        candidates = ()
    cleaned: list[str] = []
    for reason in candidates:
        # 줄바꿈을 없애 로그 한 줄이 쪼개지지 않게 한다.
        cleaned_reason = " ".join(str(reason).split())[:_GATE_REASON_MAX_CHARS]
        if cleaned_reason and cleaned_reason not in cleaned:
            cleaned.append(cleaned_reason)
    return tuple(cleaned) or (_GATE_UNKNOWN_REASON,)


def _gate_feedback_allowed(request: Request) -> bool:
    """오류 신고 링크를 그릴 대상인지 판정한다(결과 화면과 같은 기준).

    대상은 「로그인 회원 + 링크 손님」이다. 완전 익명은 ``POST /feedback``이
    어차피 막으므로 갈 수 없는 링크를 보여 주지 않는다. 판정 자체가 실패하면
    링크를 감춘다 — 막다른 화면에서 또 막히는 것이 더 나쁘다.
    """
    try:
        if _member_feedback_email(request):
            return True
        return request_helpers._track_of(request)[0] is share_tracks.Track.LINK
    except Exception:  # noqa: BLE001 — 신고 링크 판정 실패가 안내 화면을 깨면 안 된다
        logger.exception("오류 신고 링크 표시 대상을 판정하지 못했습니다")
        return False


def _pdf_review_pending_response(
    request: Request,
    error: Exception,
    *,
    job_id: str = "",
    company: str = "",
    exit_url: str = job_runtime.DEFAULT_EXIT_URL,
    exit_label: str = job_runtime.DEFAULT_EXIT_LABEL,
) -> Response:
    """필수 자동검사에서 멈춘 전체 결과를 부분 공개하지 않는다.

    ★ 버튼은 «다시 시도»가 아니라 «나가기»다. 게이트 판정은 같은 보고서에
      대해 결정적이라 같은 주소를 다시 열어도 결과가 바뀌지 않는다. 그리고
      호출지점이 POST인 경로(``/notion/{id}``)에서 현재 주소를 걸면 브라우저가
      GET으로 열어 405가 난다 — 그래서 출구는 호출지점이 정해 넘긴다.
    """

    request_id = admin_audit.request_id(request)
    reasons = _gate_reasons(error)
    logger.info(
        "자동검사 출고 차단 report_id=%s reasons=%s request_id=%s",
        " ".join(str(job_id).split())[:_LOG_REPORT_ID_MAX_CHARS],
        " | ".join(reasons),
        request_id,
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            # ↻(다시 시도) 대신 멈춤 표시를 쓴다 — 다시 열어도 결과가 같다.
            interruption_icon="⛔",
            interruption_title="보고서 자동검사가 중단되었습니다",
            interruption_message=(
                "필수 검사 중 하나라도 통과하지 못하면 웹·PDF·Notion 전체를 제공하지 않습니다."
            ),
            interruption_hint=(
                "같은 조건으로 다시 열어도 결과는 같습니다. "
                "아래 문의 번호와 함께 알려주시면 원인을 확인하겠습니다."
            ),
            gate_reasons=reasons,
            support_reference=request_id,
            feedback_report_allowed=_gate_feedback_allowed(request),
            feedback_stage=feedback_constants.STAGE_REPORT,
            feedback_company=company,
            # ★ retry_same_page=False — 게이트 판정은 결정적이라 같은 주소를
            #   다시 열면 이 화면이 그대로 다시 뜬다. 새로고침을 권하지 않는다.
            retry_url=exit_url or job_runtime.DEFAULT_EXIT_URL,
            retry_label=exit_label or job_runtime.DEFAULT_EXIT_LABEL,
            retry_same_page=False,
        ),
        status_code=409,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["X-Request-ID"] = request_id
    return response


_PDF_CANDIDATE_CACHE_MAX = 4
_PDF_CANDIDATE_CACHE_MAX_BYTES = 48 * 1024 * 1024
_PDF_CANDIDATE_CACHE: OrderedDict[
    tuple[str, str], PdfReleaseCandidate
] = OrderedDict()
_PDF_CANDIDATE_CACHE_BYTES = 0
_PDF_CANDIDATE_CACHE_LOCK = threading.Lock()


def _candidate_for_report(report_id: str, report: Report) -> PdfReleaseCandidate:
    """보고서 내용 지문별 PDF 후보를 짧게 기억해 반복 렌더링을 피한다."""

    global _PDF_CANDIDATE_CACHE_BYTES
    digest = notion_store.report_digest(report)
    key = (report_id, digest)
    with _PDF_CANDIDATE_CACHE_LOCK:
        cached = _PDF_CANDIDATE_CACHE.get(key)
        if cached is not None:
            _PDF_CANDIDATE_CACHE.move_to_end(key)
            return cached

    candidate = prepare_pdf_release(report)
    candidate_bytes = len(candidate.pdf_bytes) + sum(
        len(page.png_bytes) for page in candidate.pages
    )
    if candidate_bytes > _PDF_CANDIDATE_CACHE_MAX_BYTES:
        return candidate
    with _PDF_CANDIDATE_CACHE_LOCK:
        existing = _PDF_CANDIDATE_CACHE.get(key)
        if existing is not None:
            _PDF_CANDIDATE_CACHE.move_to_end(key)
            return existing
        _PDF_CANDIDATE_CACHE[key] = candidate
        _PDF_CANDIDATE_CACHE_BYTES += candidate_bytes
        while (
            len(_PDF_CANDIDATE_CACHE) > _PDF_CANDIDATE_CACHE_MAX
            or _PDF_CANDIDATE_CACHE_BYTES > _PDF_CANDIDATE_CACHE_MAX_BYTES
        ):
            _old_key, old = _PDF_CANDIDATE_CACHE.popitem(last=False)
            _PDF_CANDIDATE_CACHE_BYTES -= len(old.pdf_bytes) + sum(
                len(page.png_bytes) for page in old.pages
            )
    return candidate


def _release_state(
    *,
    report_id: str,
    report: Report,
) -> tuple[PdfReleaseCandidate, AutomaticallyReleasedPdf]:
    """Run or restore the exact hash-bound automatic release decision."""

    try:
        candidate = _candidate_for_report(report_id, report)
        digest = report_sha256(report)
        with storage_db.connect() as conn:
            # ★ 재대조보다 먼저 «이미 확정됐는지»부터 본다. PDF를 그리는
            #   코드(report_standard/visualization.py 등)가 바뀌면 보고서
            #   내용은 그대로인데 candidate.pdf_sha256만 달라질 수 있다 —
            #   원문 바이트를 DB에 남기지 않고 매번 다시 그려서 해시로만
            #   결속하는 설계라 재렌더가 저장 당시와 «영원히 같아야 한다»는
            #   요구는 지킬 수 없는 계약이다. 아래에서 이미 완료된 건에
            #   한해 그 요구를 내려놓는다(already_completed).
            link_run = share_store.load_run_by_report_id(conn, report_id)
        already_completed = (
            link_run is not None
            and link_run.status == share_store.RUN_STATUS_COMPLETED
            and link_run.report_id == report_id
        )
        stored_record = None
        try:
            with storage_db.connect() as conn:
                stored_record = pdf_release_store.load_automatic_release_record(
                    conn,
                    report_id=report_id,
                    report_sha256=digest,
                    pdf_sha256=candidate.pdf_sha256,
                )
        except PDFReleaseBlockedError:
            if not already_completed:
                raise
            # ★ 이미 확정된 건에서만 이 예외를 흡수한다. 여기서 막힌 이유는
            #   «출고 당시 저장된 pdf_sha256과 지금 렌더가 다르다»는 것뿐
            #   이며, 그 자체는 위조 신호가 아니다(아래 already_completed
            #   분기가 실제 위조/손상 여부를 별도로 다시 검사한다).
            stored_record = None
        if stored_record is None and already_completed:
            # ★ 저장된 해시가 «정본»이다 — 재대조 실패를 이유로 이미 확정된
            #   보고서를 영영 못 열게 만들지 않는다. 다만 진짜 위조·손상
            #   탐지까지 포기하진 않는다: automatic_release_pdf 내부의
            #   4검사(정본·PDF 무결성·채널 동등성·해시 자기일관성)는 «이력과
            #   비교»가 아니라 «지금 렌더 자체가 온전한가»를 보므로 그대로
            #   태운다 — 여기서 실패하면(예: 보고서 내용이 실제로 깨졌다면)
            #   여전히 PDFReleaseBlockedError로 막힌다.
            #   ★ 이미 완료된 건은 새 자동출고 기록을 DB에 남기지도, run
            #   이력(pdf_sha256·release_sha256·청구액)을 재기록하지도 않는다
            #   — 최초 승인·청구 기록을 렌더러가 갱신될 때마다 덮어쓰면 감사
            #   이력이 흔들리고 이중 청구 위험도 생긴다.
            return candidate, automatic_release_pdf(
                report,
                candidate,
                released_at=clock.iso_now_kst(),
                content_validator=_content_validator_for(report),
            )
        if stored_record is None:
            released = automatic_release_pdf(
                report,
                candidate,
                released_at=clock.iso_now_kst(),
                # v2 보고서에만 내용 검증을 v2 3검사로 대체한다 (v1은 None=기존).
                content_validator=_content_validator_for(report),
            )
            with storage_db.connect() as conn:
                stored_record = pdf_release_store.save_automatic_release(
                    conn,
                    report_id=report_id,
                    released_pdf=released,
                )
        released = restore_automatic_release(report, candidate, stored_record)
        with storage_db.connect() as conn:
            # LINK의 public run ID와 저장된 report ID는 별개일 수 있다. 비용 원가
            # 행을 새 report_id 이름으로 하나 더 만들지 말고 실제 생성 run에 붙인다.
            link_run = share_store.load_run_by_report_id(conn, report_id)
            charge = cost_store.mark_automatic_release(
                conn,
                run_id=(link_run.run_id if link_run is not None else report_id),
                automatic_release_sha256=stored_record.record_sha256,
            )
            # run/public ID와 report ID는 저장 계약상 별개다. 우연히 문자열이
            # 같은 정상 경로에 기대지 않고 정확한 report 결속으로만 완료시킨다.
            if link_run is not None:
                already_bound = (
                    link_run.status == share_store.RUN_STATUS_COMPLETED
                    and link_run.report_id == report_id
                    and link_run.pdf_sha256 == stored_record.pdf_sha256
                    and link_run.release_sha256 == stored_record.record_sha256
                    and link_run.customer_charge_krw == charge.amount_krw
                )
                if not already_bound and not share_store.mark_released(
                    conn,
                    report_id=report_id,
                    pdf_sha256=stored_record.pdf_sha256,
                    release_sha256=stored_record.record_sha256,
                    released_at=stored_record.released_at,
                    customer_charge_krw=charge.amount_krw,
                ):
                    raise RuntimeError("LINK 자동출고 이력을 확정하지 못했습니다")
        return candidate, released
    except PDFReleaseBlockedError:
        # 자동검사 실패는 보고서 완료가 아니다. 같은 report가 이후 정본 검사와
        # 해시 결속을 실제 통과하면 mark_released가 이 상태를 완료로 승격한다.
        _mark_link_release_gate_stopped(report_id)
        raise


def _is_admin_request(request: Request) -> bool:
    """검수 전 preview를 공개 응답과 구분하기 위한 서버측 관리자 판정."""

    from src.features.auth import constants as auth_constants  # noqa: PLC0415
    from src.features.auth import logic as auth_logic  # noqa: PLC0415

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    return auth_logic.is_admin_session(token)


def _link_view_event_unavailable_response(request: Request) -> Response:
    """정상 조회 사건을 확정하지 못하면 LINK 보고서를 fail-closed한다."""

    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title="LINK 보고서를 확인할 수 없습니다",
            interruption_message=(
                "이 LINK와 보고서의 연결 상태를 안전하게 확인하지 못해 "
                "현재는 결과를 열지 않습니다."
            ),
            interruption_hint="잠시 후 같은 LINK로 다시 시도해 주세요.",
            # 안내가 「같은 LINK로 다시 시도」이므로 재요청이 진짜 재시도다.
            **job_runtime.retry_or_exit(request, retry_label="다시 확인"),
        ),
        status_code=503,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["Cache-Control"] = "no-store"
    return response


def _render_result_page(
    request: Request,
    *,
    job: job_runtime.Job,
    result: RunResult,
    report: Report,
    internal_review_preview: bool,
) -> Response:
    resolved_track = request_helpers._track_of(request)
    if resolved_track[0] is share_tracks.Track.LINK:
        current_link = request_helpers._current_share_link(request)
        if current_link is None:
            return _link_view_event_unavailable_response(request)
        # LINK에서 새로 생성한 보고서는 run history만 생성 사건으로
        # 남긴다. 최초 연결 보고서를 연 경우에만 별도 조회 사건이다.
        if current_link.report_id == job.job_id:
            try:
                with storage_db.connect() as conn:
                    recorded = share_store.record_report_view_by_hash(
                        conn,
                        key_hash=current_link.key_hash,
                        report_id=job.job_id,
                        viewed_at=clock.iso_now_kst(),
                    )
                if not recorded:
                    return _link_view_event_unavailable_response(request)
            except Exception:
                logger.exception("LINK 보고서 조회 사건을 확정하지 못했습니다")
                return _link_view_event_unavailable_response(request)
    member_email = _member_feedback_email(request)
    member_survey = None
    member_feedback_report_version = 0
    if member_email:
        _record_member_result_view(
            report_id=job.job_id,
            actor_email=member_email,
        )
        try:
            with storage_db.connect() as conn:
                report_version = dashboard_store.get_report_state(
                    conn, job.job_id
                ).version
                member_feedback_report_version = report_version
                member_survey = dashboard_store.get_survey_snapshot(
                    conn,
                    report_id=job.job_id,
                    report_version=report_version,
                    actor_email=member_email,
                )
        except Exception:
            logger.exception("현재 보고서 버전의 기존 MEMBER 설문을 읽지 못했습니다")
    # ★ 오류 신고는 MEMBER 설문과 달리 링크 손님도 낼 수 있어야 한다
    #   (사용자 확정: 대상은 「로그인 회원 + 링크 손님」, 완전 익명은 제외).
    #   resolved_track은 위에서 이미 계산해 둔 «지금 이 요청이 유효한 LINK
    #   쿠키로 들어왔는지»다 — 만료·철회된 링크는 애초에 Track.LINK가 아니라
    #   PUBLIC으로 떨어지므로(request_helpers._track_of → _raw_share_key)
    #   여기서 다시 만료를 검사할 필요가 없다.
    feedback_report_allowed = (
        bool(member_email) or resolved_track[0] is share_tracks.Track.LINK
    )
    return job_runtime._shared(
        request_helpers.templates.TemplateResponse(
            request=request,
            name="result.html",
            context=request_helpers._ctx(
                request,
                job=job,
                result=result,
                report=report,
                grade_note=_report_grade_note(report),
                notion_configured=is_notion_configured(),
                internal_review_preview=internal_review_preview,
                member_feedback_allowed=bool(member_email),
                feedback_report_allowed=feedback_report_allowed,
                member_survey=member_survey,
                member_feedback_report_version=member_feedback_report_version,
                member_feedback_draft_key=(
                    dashboard_store.actor_digest(member_email) if member_email else ""
                ),
            ),
        )
    )


def _member_feedback_allowed(request: Request) -> bool:
    """MEMBER 설문·오류 신고 입력은 초대 명단을 다시 읽어 표시한다."""
    return bool(_member_feedback_email(request))


def _member_feedback_email(request: Request) -> str:
    """현재 초대 MEMBER의 정규화 이메일만 돌려준다."""
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    session = auth_logic.get_session(token)
    if session is None or session.is_admin:
        return ""
    try:
        with storage_db.connect() as conn:
            return session.email if share_allow.is_allowed(conn, session.email) else ""
    except Exception:
        logger.exception("MEMBER 설문 권한을 읽지 못했습니다")
        return ""


def _record_member_result_view(*, report_id: str, actor_email: str) -> None:
    """KPI 저장 실패가 승인된 보고서 열람을 막지 않게 별도 기록한다."""
    try:
        with storage_db.connect() as conn:
            version = dashboard_store.get_report_state(conn, report_id).version
            dashboard_kpi.record_first_view(
                conn,
                report_id=report_id,
                report_version=version,
                actor_email=actor_email,
                now_iso=clock.iso_now_kst(),
            )
    except Exception:
        logger.exception("보고서 첫 열람 KPI를 기록하지 못했습니다")


@router.get("/review/pdf/{job_id}", response_class=HTMLResponse)
async def review_pdf(request: Request, job_id: str):
    """Retired manual approval URL; legacy records remain audit-only."""

    response = HTMLResponse(
        "<h1>수동 PDF 승인이 종료되었습니다</h1>"
        "<p>보고서는 필수 자동검사를 모두 통과한 경우에만 자동으로 출고됩니다.</p>",
        status_code=410,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/review/pdf/{job_id}", response_class=HTMLResponse)
async def approve_pdf(
    request: Request,
    job_id: str,
):
    """Retired POST endpoint; it can never authorize automatic release."""

    response = HTMLResponse(
        "<h1>수동 PDF 승인이 종료되었습니다</h1>"
        "<p>기존 승인 기록은 감사자료로만 보존되며 출고 권한이 없습니다.</p>",
        status_code=410,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/download/{job_id}")
async def download_docx(request: Request, job_id: str):
    """폐기한 DOCX 주소를 닫아 옛 직무·채용공고 내용의 재노출을 막는다."""
    response = HTMLResponse(
        "<h1>워드 보고서 제공이 종료되었습니다</h1>"
        "<p>결과 화면의 ‘PDF 보고서 받기’를 이용해 주세요.</p>",
        status_code=410,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/download/pdf/{job_id}")
async def download_pdf(request: Request, job_id: str):
    """화면과 같은 보고서를 다운로드 전용 PDF 바이트로 내려준다."""
    loaded = _report_for_download(request, job_id)
    if isinstance(loaded, Response):
        return loaded
    report = loaded
    try:
        _candidate, released = await asyncio.to_thread(
            _release_state, report_id=job_id, report=report
        )
        if released is None:
            raise PDFReleaseBlockedError("PDF 출고 승인이 없습니다")
        content = released.content
        disposition = build_pdf_content_disposition(
            build_pdf_download_filename(report)
        )
    except PDFReleaseBlockedError as error:
        return _pdf_review_pending_response(
            request,
            error,
            job_id=job_id,
            company=getattr(report, "company", ""),
        )
    except PDFGenerationError as error:
        return _pdf_unavailable_response(request, error)
    except Exception as error:  # noqa: BLE001 - 다운로드 경계는 raw 500을 내보내지 않는다
        return _pdf_unavailable_response(request, error)

    return Response(
        content=content,
        media_type=CONTENT_TYPE_PDF,
        headers={
            "Content-Disposition": disposition,
            "X-PDF-SHA256": released.record.pdf_sha256,
            "X-PDF-Release-Record": released.record.record_sha256,
            **SHARED_LINK_HEADERS,
        },
    )


@router.post("/notion/{job_id}", response_class=HTMLResponse)
async def send_to_notion(
    request: Request,
    job_id: str,
    csrf_token: str = Form("", max_length=CSRF_TOKEN_MAX_CHARS),
    retry_revision: str = Form("", max_length=20),
    confirm_duplicate: str = Form("", max_length=8),
):
    """보고서를 노션으로 보낸다. 관리자·CSRF·영속 멱등성을 모두 확인한다."""
    blocked = request_helpers.require_admin_action(request, csrf_token)
    if blocked is not None:
        return blocked
    dashboard_blocked = _dashboard_publication_block(request, job_id)
    if dashboard_blocked is not None:
        return dashboard_blocked
    job = job_runtime._JOBS.get(job_id)
    if job is not None and job.result is not None:
        report = job.result.report
    else:
        try:
            report = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
    if report is None:
        return _report_unavailable_redirect()
    # 권한/CSRF 다음, adapter나 멱등성 row보다 먼저 만료를 판정한다. 기간이
    # 지난 보고서는 명시적 410이며 Notion adapter 호출 수가 항상 0이다.
    if job_runtime._link_expired(report):
        return job_runtime._expired_screen(request)

    try:
        report = _report_for_output(report)
    except (PublishBlockedError, V2ValidationError):
        _mark_link_release_gate_stopped(job_id)
        return _blocked_report_response(request)
    try:
        _candidate, released = await asyncio.to_thread(
            _release_state, report_id=job_id, report=report
        )
    except PDFReleaseBlockedError as error:
        return _pdf_review_pending_response(
            request,
            error,
            job_id=job_id,
            company=getattr(report, "company", ""),
            exit_url=f"/notion/{job_id}",
            exit_label="자동검사 상태 다시 확인",
        )
    except Exception as error:  # noqa: BLE001 — 승인 원장을 확인 못 하면 Notion도 닫는다
        return _pdf_unavailable_response(request, error)
    if released is None:
        return _pdf_review_pending_response(
            request,
            PDFReleaseBlockedError("PDF 출고 승인이 없습니다"),
            job_id=job_id,
            company=getattr(report, "company", ""),
            exit_url=_ADMIN_DASHBOARD_URL,
            exit_label=_ADMIN_DASHBOARD_LABEL,
        )

    digest = notion_store.report_digest(report)
    try:
        expected_revision = int(retry_revision) if retry_revision else None
    except ValueError:
        expected_revision = None
    explicit_retry = (
        confirm_duplicate == "yes"
        and expected_revision is not None
        and expected_revision >= 1
    )

    # Claim과 adapter를 하나의 strong supervisor가 소유한다. claim commit 직후
    # 요청이 취소돼도 supervisor는 decision을 회수해 반드시 adapter+finish까지
    # 이어가므로 adapter 0회인 in_progress가 남지 않는다.
    supervisor = asyncio.create_task(
        _supervise_notion_export(
            job_id,
            digest,
            report,
            explicit_retry=explicit_retry,
            expected_revision=expected_revision,
        )
    )
    _NOTION_EXPORT_WORKERS.add(supervisor)
    supervisor.add_done_callback(_forget_notion_worker)
    try:
        supervised = await asyncio.shield(supervisor)
    except Exception as exc:  # noqa: BLE001 - storage outage must not call Notion
        logger.warning("노션 멱등성 상태 조회 실패 type=%s", type(exc).__name__)
        return job_runtime._storage_unavailable_response(request)

    decision = supervised.decision
    if not decision.claimed:
        record = decision.record
        cached = _record_result(record)
        if record.state == notion_store.STATE_IN_PROGRESS:
            status_code = 202
        elif record.state == notion_store.STATE_SUCCEEDED:
            status_code = 200
        else:
            # A repeat POST is not a new export.  It presents the explicit retry
            # UX and records the duplicate-risk decision as a conflict.
            status_code = 409
        return _render_notion_result(
            request,
            notion=cached,
            report=report,
            job_id=job_id,
            record=record,
            reused=True,
            status_code=status_code,
        )

    outcome = supervised.outcome
    assert outcome is not None

    if not outcome.persisted:
        unknown = NotionExportResult(
            success=False,
            page_id=outcome.result.page_id,
            page_url=outcome.result.page_url,
            error="전송 결과를 안전하게 저장하지 못했습니다",
            uncertain=True,
        )
        record = decision.record
        return _render_notion_result(
            request,
            notion=unknown,
            report=report,
            job_id=job_id,
            record=record,
            reused=False,
            status_code=503,
        )

    try:
        with storage_db.connect() as conn:
            record = notion_store.load(conn, job_id, digest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("노션 완료 상태 재조회 실패 type=%s", type(exc).__name__)
        return job_runtime._storage_unavailable_response(request)
    if record is None:
        return job_runtime._storage_unavailable_response(request)
    return _render_notion_result(
        request,
        notion=outcome.result,
        report=report,
        job_id=job_id,
        record=record,
        reused=False,
    )
