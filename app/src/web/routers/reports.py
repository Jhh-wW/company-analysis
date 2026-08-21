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
from src.features.export_notion.notion import (
    NotionExportResult,
    is_notion_configured,
    send_report_to_notion,
)
from src.features.grading.logic import grade_message
from src.features.observability import admin_audit
from src.features.pipeline.port import CompanyCard, Outcome, Report, RunResult, UserInput
from src.features.report_standard import PublishBlockedError, build_published_report
from src.features.storage import db as storage_db
from src.web import job_runtime, request_helpers
from src.web.security import CSRF_TOKEN_MAX_CHARS


router = APIRouter()
logger = logging.getLogger(__name__)

# Keep strong references while a request is cancelled.  ``asyncio.shield``
# lets the sync worker finish and persist the remote outcome instead of leaving
# an ambiguous operation that a later click could duplicate.
_NOTION_EXPORT_WORKERS: set[asyncio.Task] = set()


def _report_for_output(report: Report) -> Report:
    """현재 canonical 출고 게이트를 통과한 공개본만 돌려준다."""

    return build_published_report(report)


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
            report, grade_note=grade_message(report.grade, report.filled_count)
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
    job = job_runtime._JOBS.get(job_id)
    if job is None or job.result is None:
        try:
            saved = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
        if saved is None:
            return RedirectResponse("/", status_code=303)
        if job_runtime._link_expired(saved):
            return job_runtime._expired_screen(request)
        try:
            output_report = _report_for_output(saved)
        except PublishBlockedError:
            return _blocked_report_response(request)
        try:
            _candidate, released = await asyncio.to_thread(
                _release_state, report_id=job_id, report=output_report
            )
        except PDFReleaseBlockedError as error:
            return _pdf_review_pending_response(request, error)
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
    if job_runtime._link_expired(report):
        return job_runtime._expired_screen(request)
    try:
        output_report = _report_for_output(report)
    except PublishBlockedError:
        return _blocked_report_response(request)
    try:
        _candidate, released = await asyncio.to_thread(
            _release_state, report_id=job_id, report=output_report
        )
    except PDFReleaseBlockedError as error:
        return _pdf_review_pending_response(request, error)
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
    job = job_runtime._JOBS.get(job_id)
    if job is not None and job.result is not None:
        report = job.result.report
    else:
        try:
            report = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
    if report is None:
        return RedirectResponse("/", status_code=303)
    if job_runtime._link_expired(report):
        return job_runtime._expired_screen(request)
    return report


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
            retry_url="",
            retry_label="PDF 다시 받기",
        ),
        status_code=503,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["X-Request-ID"] = request_id
    return job_runtime._retryable_response(response)


def _pdf_review_pending_response(request: Request, error: Exception) -> Response:
    """필수 자동검사에서 멈춘 전체 결과를 부분 공개하지 않는다."""

    request_id = admin_audit.request_id(request)
    logger.info(
        "자동검사 출고 차단 error_type=%s request_id=%s",
        type(error).__name__,
        request_id,
    )
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title="보고서 자동검사가 중단되었습니다",
            interruption_message=(
                "필수 검사 중 하나라도 통과하지 못하면 웹·PDF·Notion 전체를 제공하지 않습니다."
            ),
            interruption_hint=(
                "이 건은 고객에게 청구되지 않습니다. 관리자가 원인을 확인한 뒤 새로 생성해야 합니다."
            ),
            retry_url="",
            retry_label="자동검사 상태 다시 확인",
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

    candidate = _candidate_for_report(report_id, report)
    digest = report_sha256(report)
    with storage_db.connect() as conn:
        stored_record = pdf_release_store.load_automatic_release_record(
            conn,
            report_id=report_id,
            report_sha256=digest,
            pdf_sha256=candidate.pdf_sha256,
        )
    if stored_record is not None:
        released = restore_automatic_release(report, candidate, stored_record)
        with storage_db.connect() as conn:
            cost_store.mark_automatic_release(
                conn,
                run_id=report_id,
                automatic_release_sha256=stored_record.record_sha256,
            )
        return candidate, released

    released = automatic_release_pdf(
        report,
        candidate,
        released_at=clock.iso_now_kst(),
    )
    with storage_db.connect() as conn:
        stored_record = pdf_release_store.save_automatic_release(
            conn,
            report_id=report_id,
            released_pdf=released,
        )
        cost_store.mark_automatic_release(
            conn,
            run_id=report_id,
            automatic_release_sha256=stored_record.record_sha256,
        )
    released = restore_automatic_release(report, candidate, stored_record)
    return candidate, released


def _is_admin_request(request: Request) -> bool:
    """검수 전 preview를 공개 응답과 구분하기 위한 서버측 관리자 판정."""

    from src.features.auth import constants as auth_constants  # noqa: PLC0415
    from src.features.auth import logic as auth_logic  # noqa: PLC0415

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    return auth_logic.is_admin_session(token)


def _render_result_page(
    request: Request,
    *,
    job: job_runtime.Job,
    result: RunResult,
    report: Report,
    internal_review_preview: bool,
) -> Response:
    return job_runtime._shared(
        request_helpers.templates.TemplateResponse(
            request=request,
            name="result.html",
            context=request_helpers._ctx(
                request,
                job=job,
                result=result,
                report=report,
                grade_note=grade_message(report.grade, report.filled_count),
                notion_configured=is_notion_configured(),
                internal_review_preview=internal_review_preview,
            ),
        )
    )


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
        return _pdf_review_pending_response(request, error)
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
    job = job_runtime._JOBS.get(job_id)
    if job is not None and job.result is not None:
        report = job.result.report
    else:
        try:
            report = job_runtime._load_saved_report(job_id)
        except job_runtime.ReportStoreUnavailable:
            return job_runtime._storage_unavailable_response(request)
    if report is None:
        return RedirectResponse("/", status_code=303)
    # 권한/CSRF 다음, adapter나 멱등성 row보다 먼저 만료를 판정한다. 기간이
    # 지난 보고서는 명시적 410이며 Notion adapter 호출 수가 항상 0이다.
    if job_runtime._link_expired(report):
        return job_runtime._expired_screen(request)

    try:
        report = _report_for_output(report)
    except PublishBlockedError:
        return _blocked_report_response(request)
    try:
        _candidate, released = await asyncio.to_thread(
            _release_state, report_id=job_id, report=report
        )
    except PDFReleaseBlockedError as error:
        return _pdf_review_pending_response(request, error)
    except Exception as error:  # noqa: BLE001 — 승인 원장을 확인 못 하면 Notion도 닫는다
        return _pdf_unavailable_response(request, error)
    if released is None:
        return _pdf_review_pending_response(
            request,
            PDFReleaseBlockedError("PDF 출고 승인이 없습니다"),
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
