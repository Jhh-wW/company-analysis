"""완성 보고서 화면과 PDF·Notion 내보내기 경로."""

import asyncio
import datetime as dt
import logging
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace

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
    PdfRenderBlockedError,
    PdfReleaseCandidate,
    prepare_pdf_release,
)
from src.features.export_pdf.automatic_release import (
    AutomaticGateStopped,
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
    safe_notion_page_url,
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
from src.features.composer import build_id as composer_build_id
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.validate import (
    V2ValidationError,
    v2_validation_problems,
    validate_v2,
)
from src.features.report_standard import PublishBlockedError, build_published_report
from src.features.report_delivery.artifact import ArtifactInspectionStatus
from src.features.report_delivery.cache_identity import CacheLookupKey
from src.features.report_delivery.models import Delivery
from src.features.report_delivery.singleflight import LeaseKey
from src.features.report_delivery import store as delivery_store
from src.features.report_access import store as report_access_store
from src.features.report_access.models import ReportAudience, ReportBindingResult
from src.features.sharelink import store as share_store
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared.generation_cache_identity import GenerationCacheNamespace
from src.web import job_runtime, report_delivery_adapter, request_helpers
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
_DELIVERY_FINALIZATION_FAILURE = "artifact_finalization_failed"
_DELIVERY_PUBLICATION_BLOCKED = "publication_contract_blocked"
_DELIVERY_PDF_RENDER_BLOCKED = "pdf_render_contract_blocked"
_DELIVERY_AUTOMATIC_GATE_BLOCKED = "automatic_release_gate_blocked"
_DELIVERY_PDF_RELEASE_BLOCKED = "pdf_release_contract_blocked"
_PUBLIC_STORE_MISSING = "missing"
_PUBLIC_STORE_INCOMPLETE = "incomplete"
_PUBLIC_STORE_UNREADABLE = "unreadable"


@dataclass(frozen=True)
class _StoredPublicDelivery:
    """현재 renderer가 아니라 최초 승인 저장본을 읽은 결과."""

    delivery: Delivery
    report: Report
    pdf_bytes: bytes
    pdf_sha256: str
    artifact_id: str
    release_record_sha256: str


def _report_grade_note(report: Report) -> str:
    """레거시 6칸과 canonical 장 수를 섞지 않은 완성도 안내를 돌려준다."""

    from src.shared.report_quality.models import PublicationPolicy  # noqa: PLC0415

    if report.publication_policy == PublicationPolicy.LEGACY_SHADOW_EXCEPTION.value:
        return (
            "안전 확인 중인 임시 부분 보고서 — 확인되지 않은 숫자 문장은 "
            "제외했지만 모든 문장·표·도식의 새 검증은 아직 끝나지 않았습니다."
        )

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


def _notion_v2_unsupported_response(request: Request) -> Response:
    """아직 변환기가 없는 엔진 v2를 외부 전송 실패로 가장하지 않는다."""

    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_icon="ℹ️",
            interruption_title="노션 내보내기를 지원하지 않습니다",
            interruption_message=(
                "엔진 v2 보고서는 현재 웹 화면과 PDF 파일로만 제공합니다."
            ),
            interruption_hint=(
                "노션 연결이나 입력의 문제가 아닙니다. 노션용 변환기가 준비될 때까지 "
                "웹 화면이나 PDF를 사용해 주세요."
            ),
            retry_url=_ADMIN_DASHBOARD_URL,
            retry_label=_ADMIN_DASHBOARD_LABEL,
            retry_same_page=False,
            gate_reasons=(),
            feedback_report_allowed=False,
        ),
        status_code=409,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["X-Notion-Export-Status"] = "unsupported-engine-v2"
    return response


def _mark_link_release_gate_stopped(
    report_id: str,
    *,
    stop_step: str = "automatic_release_gate",
    stop_reason: str = "automatic_release_gate_stopped",
) -> None:
    """출고 검사에 막힌 LINK 실행만 안정적인 중단 상태로 마감한다.

    공개 차단 응답이 정본이므로 이력 저장 장애가 원래 ``PublishBlockedError`` 또는
    ``PDFReleaseBlockedError``를 가려서는 안 된다. LINK에 결속되지 않은 보고서는
    갱신 대상이 없어 그대로 끝난다.

    ★ ``stop_step``/``stop_reason`` 은 «사유 코드»만 받는다 — 보고서 값을 넣지 마라.
      `/admin/link/{key}` 화면에 그대로 그려진다.
    """

    try:
        with storage_db.connect() as conn:
            share_store.mark_release_stopped(
                conn,
                report_id=report_id,
                stopped_at=clock.iso_now_kst(),
                stop_step=stop_step,
                stop_reason=stop_reason,
            )
    except Exception:  # noqa: BLE001 — 원래 출고 차단을 절대 가리지 않는다
        logger.exception(
            "LINK 자동출고 중단 이력을 저장하지 못했습니다 report_id=%s",
            report_id,
        )


def _pdf_gate_stop_codes(error: Exception) -> dict[str, str]:
    """관리자 이력에 남길 «단계·사유 코드». 보고서 값은 절대 안 넣는다.

    자동검사가 떨어진 것과 「PDF 만들기」가 실패한 것은 다른 사건이다 —
    같은 코드로 남기면 관리자가 로그에서 둘을 못 가른다.
    """
    if isinstance(error, PdfRenderBlockedError):
        return {"stop_step": "pdf_candidate_render", "stop_reason": "pdf_render_failed"}
    if isinstance(error, AutomaticGateStopped):
        return {
            "stop_step": "automatic_release_gate",
            "stop_reason": "automatic_release_gate_stopped",
        }
    # 그 밖의 출고 차단(승인 없음·장부 무결성 등) — 억지로 둘 중 하나로 몰지 않는다.
    return {"stop_step": "pdf_release", "stop_reason": "pdf_release_blocked"}


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
    result = replace(
        result,
        page_url=safe_notion_page_url(result.page_url),
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
    # 과거 DB에 외부 응답 원문이 남아 있어도 템플릿의 href 경계에서는 다시
    # 검사한다. 새 저장 경로만 고치면 이미 저장된 javascript: 값이 살아남는다.
    notion = replace(
        notion,
        page_url=safe_notion_page_url(notion.page_url),
    )
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

    request_helpers.mark_public_get_readonly_existing(request)
    access_blocked = request_helpers.require_report_access(request, job_id)
    if access_blocked is not None:
        return access_blocked
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

    # 성공 보고서는 언제나 영속 Delivery를 정본으로 읽지만, 보고서 자체가
    # 만들어지지 않은 terminal 결과에는 저장 artifact가 없다. 이 갈래까지
    # 메모리를 무시하면 방금 중단된 자기 조사도 첫 화면으로 사라진다. 공통
    # access 판정을 지난 현재 브라우저에게만 유한 in-memory 중단 화면을 보인다.
    live_job = job_runtime._JOBS.get(job_id)
    if (
        live_job is not None
        and live_job.finished
        and live_job.result is not None
        and (
            live_job.result.outcome is not Outcome.REPORT
            or live_job.result.report is None
        )
    ):
        response = request_helpers.templates.TemplateResponse(
            request=request,
            name="stopped.html",
            context=request_helpers._ctx(
                request, job=live_job, result=live_job.result
            ),
        )
        response.headers.update(SHARED_LINK_HEADERS)
        return response

    try:
        stored_delivery = _stored_public_delivery(job_id)
    except Exception:  # noqa: BLE001 - 불변 출고물 손상은 fail-closed
        logger.exception("저장된 delivery를 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)
    if stored_delivery is not None:
        if _delivery_is_expired(stored_delivery.delivery):
            return job_runtime._expired_screen(request)
        # 같은 공개 ID는 재시작 전·후 모두 영속 delivery에서 만든 같은 최소
        # 화면 상태를 쓴다. 메모리 Job의 임시 경고·입력값이 끼면 HTML이 달라진다.
        job = _job_for_stored_delivery(job_id, stored_delivery.report)
        return _render_result_page(
            request,
            job=job,
            result=RunResult(outcome=Outcome.REPORT, report=stored_delivery.report),
            report=stored_delivery.report,
            internal_review_preview=False,
            pure_delivery_read=True,
        )
    try:
        delivery_intent = report_delivery_adapter.load_public_delivery_intent(job_id)
    except Exception:  # noqa: BLE001 - 손상된 의무 표식도 legacy로 열지 않는다
        logger.exception("delivery 의무 표식을 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)
    if delivery_intent is not None:
        return _delivery_intent_response(
            request,
            public_id=job_id,
            intent=delivery_intent,
        )

    # delivery/intent가 하나도 없는 행만 cutover 이전 legacy다. 메모리 Job을
    # 우선하면 재시작 전에는 오늘 renderer를 쓰고 재시작 뒤에는 다른 결과가
    # 나오는 시간 의존 버그가 생긴다. 공개 GET의 정본은 언제나 당시 DB payload다.
    try:
        legacy = report_delivery_adapter.load_legacy_public_report(job_id)
    except Exception:  # noqa: BLE001 - 손상·부분 schema는 없는 보고서로 가장하지 않는다
        logger.exception("과거 보고서 원본을 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)
    if legacy is None:
        return _report_unavailable_redirect()
    if not allow_expired and job_runtime._link_expired(legacy.report):
        return job_runtime._expired_screen(request)
    legacy_job = _job_for_legacy_report(job_id, legacy.report)
    return _render_result_page(
        request,
        job=legacy_job,
        result=RunResult(outcome=Outcome.REPORT, report=legacy.report),
        report=legacy.report,
        internal_review_preview=False,
        pure_delivery_read=True,
        legacy_readonly=True,
        legacy_generated_at=legacy.generated_at or legacy.report.generated_at,
        legacy_stored_at=legacy.stored_at,
    )


def _report_for_download(
    request: Request,
    job_id: str,
    *,
    publication_checked: bool = False,
) -> Report | Response:
    """두 다운로드 형식이 공유하는 조회·장애·만료 계약."""
    if not publication_checked:
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


def _stored_public_delivery(public_id: str) -> _StoredPublicDelivery | None:
    """승인 record와 불변 artifact가 모두 맞는 새 delivery만 읽는다."""

    stored = report_delivery_adapter.load_public_delivery(public_id)
    if stored is None:
        return None
    intent = report_delivery_adapter.load_public_delivery_intent(public_id)
    if (
        intent is not None
        and intent.state != delivery_store.DELIVERY_INTENT_COMPLETE
    ):
        raise report_delivery_adapter.DeliveryAdapterError(
            "완료되지 않은 delivery 의무에 공개 artifact가 연결됐습니다"
        )
    metadata = stored.artifact
    inspection = stored.inspection
    if (
        metadata is None
        or metadata.blob_pointer is None
        or inspection is None
        or inspection.status is not ArtifactInspectionStatus.AVAILABLE
        or inspection.pdf_bytes is None
    ):
        raise report_delivery_adapter.DeliveryAdapterError(
            "delivery의 최초 승인 PDF artifact를 사용할 수 없습니다"
        )
    with storage_db.connect_readonly_existing() as conn:
        if conn is None:
            raise report_delivery_adapter.DeliveryAdapterError(
                "delivery 승인 저장소가 없습니다"
            )
        release_record = pdf_release_store.load_automatic_release_record(
            conn,
            report_id=public_id,
            report_sha256=report_sha256(stored.report),
            pdf_sha256=metadata.blob_pointer.sha256,
            # 최초 artifact가 승인될 때 저장한 검사 계약으로 읽는다. 현재
            # checker 버전으로 바뀌었다는 이유만으로 과거 링크를 소급 차단하지 않는다.
            checker_version=metadata.version.checker_version,
        )
    if release_record is None:
        raise report_delivery_adapter.DeliveryAdapterError(
            "delivery의 자동출고 승인 record가 없습니다"
        )
    return _StoredPublicDelivery(
        delivery=stored.delivery,
        report=stored.report,
        pdf_bytes=inspection.pdf_bytes,
        pdf_sha256=metadata.blob_pointer.sha256,
        artifact_id=metadata.artifact_id,
        release_record_sha256=release_record.record_sha256,
    )


def _job_for_stored_delivery(job_id: str, report: Report) -> job_runtime.Job:
    """재시작 뒤 화면 표시에만 필요한 최소 Job을 복원한다."""

    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
        ),
        finished=True,
        report_persisted=True,
        delivery_persisted=True,
    )


def _job_for_legacy_report(job_id: str, report: Report) -> job_runtime.Job:
    """과거 저장 payload를 화면에 싣기 위한 읽기 전용 최소 Job."""

    job = _job_for_stored_delivery(job_id, report)
    job.delivery_persisted = False
    return job


def _delivery_is_expired(delivery: Delivery) -> bool:
    """Delivery의 offset 포함 만료 시각을 한 시계로 판정한다."""

    return clock.now_kst().astimezone(dt.timezone.utc) >= delivery.expires_at


def _dashboard_publication_block(request: Request, report_id: str) -> Response | None:
    """MEMBER 신고 뒤에는 결과·PDF·공유가 같은 DB transaction으로 닫힌다.

    운영 상태를 읽지 못한 경우도 공개 경계를 열지 않는다. 관리자 원본 검토는
    별도 ``/reports/{id}`` 경로로만 제공한다.
    """
    try:
        with storage_db.connect_readonly_existing() as conn:
            if conn is None:
                return _dashboard_blocked_response(
                    request,
                    unavailable=True,
                    store_status=_PUBLIC_STORE_MISSING,
                )
            session = auth_logic.get_session(
                request.cookies.get(auth_constants.SESSION_COOKIE_NAME),
                readonly_existing=True,
            )
            if (
                session is not None
                and not session.is_admin
                and not share_allow.is_allowed(conn, session.email)
            ):
                return _revoked_member_response(request, unavailable=False)
            is_blocked = dashboard_store.report_is_blocked(conn, report_id)
            is_trashed = dashboard_store.report_is_trashed(conn, report_id)
    except Exception as error:  # noqa: BLE001 - moderation state uncertainty must fail closed
        logger.exception("관리 대시보드의 보고서 차단 상태를 읽지 못했습니다")
        return _dashboard_blocked_response(
            request,
            unavailable=True,
            store_status=_public_store_failure_status(error),
        )
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


def _public_store_failure_status(error: Exception) -> str:
    """공개 문구에 예외 원문을 싣지 않고 저장소 장애 종류만 고정한다."""

    message = str(error).lower()
    if isinstance(error, sqlite3.OperationalError) and (
        "no such table" in message
        or "readonly database" in message
        or "read-only database" in message
    ):
        return _PUBLIC_STORE_INCOMPLETE
    return _PUBLIC_STORE_UNREADABLE


def _dashboard_blocked_response(
    request: Request,
    *,
    unavailable: bool,
    store_status: str = "",
) -> Response:
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
    if unavailable and store_status:
        response.headers["X-Report-Store-Status"] = store_status
    return job_runtime._retryable_response(response) if unavailable else response


def _delivery_unavailable_response(request: Request) -> Response:
    """새 delivery의 불변 본문·PDF·승인 중 하나라도 못 읽으면 닫는다."""

    request_id = admin_audit.request_id(request)
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title="저장된 보고서를 확인할 수 없습니다",
            interruption_message=(
                "최초 승인된 본문과 PDF 원본의 무결성을 "
                "확인할 수 없어 현재는 열지 않습니다."
            ),
            interruption_hint="새 조사를 시작하지 말고 관리자에게 문의해 주세요.",
            **job_runtime.retry_or_exit(request, retry_label="다시 확인"),
            support_reference=request_id,
        ),
        status_code=503,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Request-ID"] = request_id
    return job_runtime._retryable_response(response)


def _legacy_pdf_unavailable_response(request: Request) -> Response:
    """당시 bytes를 저장하지 않은 PDF를 오늘 renderer로 위조하지 않는다."""

    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_icon="ℹ️",
            interruption_title="이 과거 보고서의 PDF 원본은 확인할 수 없습니다",
            interruption_message=(
                "이 보고서는 PDF 원본을 따로 보관하기 전 방식으로 만들어졌습니다. "
                "같은 주소에서 오늘 코드로 다른 파일을 새로 만들지 않습니다."
            ),
            interruption_hint=(
                "웹 화면에서 당시 저장된 본문을 확인해 주세요. "
                "정확한 옛 PDF 파일이 별도로 발견되기 전에는 다운로드할 수 없습니다."
            ),
            retry_url="",
            retry_label="",
            retry_same_page=False,
            gate_reasons=(),
            feedback_report_allowed=False,
        ),
        status_code=410,
    )
    response.headers.update(SHARED_LINK_HEADERS)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-PDF-Artifact-Status"] = "legacy-original-unknown"
    return response


def _delivery_intent_response(
    request: Request,
    *,
    public_id: str,
    intent: delivery_store.DeliveryIntent,
) -> Response:
    """영속 실패 코드를 읽기만 해 계약 차단과 저장 장애를 구분한다.

    예외 원문은 저장하지 않는다. 공개 계약·PDF 검사처럼 같은 입력에 대해
    결정적인 거부는 409로, artifact/DB 손상이나 아직 완료되지 않은 의무는
    503으로 남겨 사용자가 같은 사건을 새 조사로 우회하지 않게 한다.
    """

    if intent.state != delivery_store.DELIVERY_INTENT_FAILED:
        return _delivery_unavailable_response(request)
    if intent.failure_code == _DELIVERY_PUBLICATION_BLOCKED:
        return _blocked_report_response(request)
    if intent.failure_code == _DELIVERY_PDF_RENDER_BLOCKED:
        return _pdf_review_pending_response(
            request,
            PdfRenderBlockedError("PDF 후보 생성 계약이 중단됐습니다"),
            job_id=public_id,
        )
    if intent.failure_code == _DELIVERY_AUTOMATIC_GATE_BLOCKED:
        return _pdf_review_pending_response(
            request,
            AutomaticGateStopped((_GATE_UNKNOWN_REASON,)),
            job_id=public_id,
        )
    if intent.failure_code == _DELIVERY_PDF_RELEASE_BLOCKED:
        return _pdf_review_pending_response(
            request,
            PDFReleaseBlockedError("PDF 출고 계약이 중단됐습니다"),
            job_id=public_id,
        )
    return _delivery_unavailable_response(request)


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
    # ★ 「자동검사가 떨어진 것」과 「만들다가 실패한 것」은 다른 사건이다.
    #   2026-08-28 우리은행 건에서 후자였는데 화면이 전자라고 말했다 —
    #   자동검사는 돌지도 않았다. 사용자도 관리자도 엉뚱한 곳을 보게 된다.
    # ★ 렌더 실패«만» 따로 말한다. 나머지 차단은 종전 문구를 그대로 둔다 —
    #   맨 예외를 싸잡아 「만들다 실패」라고 하면 그것 또한 틀린 말이 된다.
    만들다_실패했다 = isinstance(error, PdfRenderBlockedError)
    logger.info(
        "출고 차단 stage=%s report_id=%s reasons=%s request_id=%s",
        "render" if 만들다_실패했다 else "gate",
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
            interruption_title=(
                "보고서를 내보내지 못했습니다"
                if 만들다_실패했다
                else "보고서 자동검사가 중단되었습니다"
            ),
            interruption_message=(
                "완성본을 파일로 만드는 도중 멈췄습니다. 일부만 보여 드리지 않습니다."
                if 만들다_실패했다
                else "필수 검사 중 하나라도 통과하지 못하면 웹·PDF·Notion 전체를 제공하지 않습니다."
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


def finalize_new_report_delivery(
    *,
    report_id: str,
    corp_id: str,
    billing_bucket_id: str,
    report: Report,
    actual_models: tuple[str, ...],
    reused_from_cache: bool,
    dart_receipt_numbers: tuple[str, ...] = (),
    financial_payload_digest: str = "",
    reuse_content_snapshot_id: str = "",
    reuse_artifact_id: str = "",
    cache_namespace: GenerationCacheNamespace | None = None,
    preflight_identity_digest: str = "",
    cache_eligible: bool = False,
    completed_at: dt.datetime | None = None,
    public_access_run_id: str = "",
    engine_build_identity: composer_build_id.EngineBuildIdentity | None = None,
    reuse_singleflight_key: LeaseKey | None = None,
) -> report_delivery_adapter.PublicDelivery:
    """새 보고서 완료 경계에서 자동승인·과금·artifact를 한번만 확정한다.

    PDF 렌더링과 4개 자동검사는 DB 쓰기 전에 끝낸다. 그 뒤
    승인 record·고객 청구 결정·Delivery·Artifact metadata를 같은
    SQLite 거래에 넣어, 일부만 성공한 출고를 남기지 않는다.
    """

    completed_at = completed_at or clock.now_kst()
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise report_delivery_adapter.DeliveryAdapterError(
            "Delivery 완료 시각에는 시간대가 필요합니다"
        )

    def bind_public_access(
        conn: sqlite3.Connection,
        public_delivery: report_delivery_adapter.PublicDelivery,
    ) -> None:
        clean_run = str(public_access_run_id).strip().lower()
        if not clean_run:
            return
        binding = report_access_store.bind_report(
            conn,
            run_id=clean_run,
            report_id=report_id,
            expected_audience=ReportAudience.PUBLIC,
            delivery_expires_at=public_delivery.delivery.expires_at.timestamp(),
        )
        if binding != ReportBindingResult(ReportAudience.PUBLIC, True):
            raise report_access_store.PublicGrantBindingUnavailable(
                "PUBLIC 실행의 report 결속을 최종 출고에서 확인하지 못했습니다"
            )

    frozen_build_identity = (
        engine_build_identity
        or composer_build_id.capture_engine_build_identity()
    )
    if engine_build_identity is not None:
        report_delivery_adapter._assert_frozen_identity_is_current(
            frozen_build_identity
        )
    if bool(str(reuse_content_snapshot_id).strip()) != bool(
        str(reuse_artifact_id).strip()
    ):
        raise report_delivery_adapter.DeliveryAdapterError(
            "재사용 출고의 content와 artifact 결속이 불완전합니다"
        )
    if bool(cache_namespace) != bool(str(preflight_identity_digest).strip()):
        raise report_delivery_adapter.DeliveryAdapterError(
            "캐시 출고의 생성기 namespace와 사전 출처 지문이 불완전합니다"
        )
    cache_key = (
        CacheLookupKey.from_preflight(
            billing_bucket_id=billing_bucket_id,
            corp_id=corp_id,
            namespace=cache_namespace,
            preflight_identity_digest=preflight_identity_digest,
            preflight_cache_usable=True,
        )
        if cache_namespace is not None and cache_eligible
        else None
    )
    intent = report_delivery_adapter.require_public_delivery(
        report_id,
        required_at=completed_at,
    )
    try:
        output_report = _report_for_output(report)
        if intent.state == delivery_store.DELIVERY_INTENT_COMPLETE:
            existing = report_delivery_adapter.load_public_delivery(report_id)
            if (
                existing is None
                or report_sha256(existing.report) != report_sha256(output_report)
                or existing.inspection is None
                or existing.inspection.status
                is not ArtifactInspectionStatus.AVAILABLE
            ):
                raise report_delivery_adapter.DeliveryAdapterError(
                    "완료 delivery의 본문 또는 artifact를 확인할 수 없습니다"
                )
            if existing.delivery.billing_bucket_id != str(
                billing_bucket_id
            ).strip():
                raise report_delivery_adapter.DeliveryAdapterError(
                    "완료 delivery와 재시도의 비용 통장이 다릅니다"
                )
            existing_artifact_id = (
                existing.artifact.artifact_id if existing.artifact is not None else ""
            )
            if reuse_content_snapshot_id and (
                existing.content.content_id != reuse_content_snapshot_id
                or existing_artifact_id != reuse_artifact_id
            ):
                raise report_delivery_adapter.DeliveryAdapterError(
                    "완료 delivery와 재사용 원본 신원이 다릅니다"
                )
            if cache_key is not None:
                with storage_db.connect_readonly_existing() as conn:
                    if conn is None or not delivery_store.cache_entry_matches_exactly(
                        conn,
                        key=cache_key,
                        content_snapshot_id=existing.content.content_id,
                        artifact_id=existing_artifact_id,
                    ):
                        raise report_delivery_adapter.DeliveryAdapterError(
                            "완료 delivery와 재시도의 정식 캐시 신원이 다릅니다"
                        )
            if public_access_run_id:
                with storage_db.connect() as conn:
                    bind_public_access(conn, existing)
                    conn.commit()
            return existing
        if reuse_content_snapshot_id:
            # single-flight waiter는 owner가 승인받은 최초 content/PDF를
            # 그대로 쓴다. 다시 렌더하거나 새 SourceSnapshot을 만들지 않는다.
            backend = report_delivery_adapter.configured_artifact_backend()
            with storage_db.connect() as conn:
                public_delivery, owner_record = (
                    report_delivery_adapter.persist_reused_delivery(
                        conn,
                        backend,
                        public_id=report_id,
                        corp_id=corp_id,
                        billing_bucket_id=billing_bucket_id,
                        report=output_report,
                        completed_at=completed_at,
                        content_snapshot_id=reuse_content_snapshot_id,
                        artifact_id=reuse_artifact_id,
                        dart_receipt_numbers=dart_receipt_numbers,
                        financial_payload_digest=financial_payload_digest,
                        cache_key=cache_key,
                        reuse_singleflight_key=reuse_singleflight_key,
                        engine_build_identity=frozen_build_identity,
                    )
                )
                inspection = public_delivery.inspection
                if inspection is None or inspection.pdf_bytes is None:
                    raise report_delivery_adapter.DeliveryAdapterError(
                        "owner의 최초 승인 PDF bytes를 읽지 못했습니다"
                    )
                stored_record = pdf_release_store.save_automatic_release(
                    conn,
                    report_id=report_id,
                    released_pdf=AutomaticallyReleasedPdf(
                        content=inspection.pdf_bytes,
                        record=owner_record,
                    ),
                )
                link_run = share_store.load_run_by_report_id(conn, report_id)
                charge = cost_store.mark_automatic_release(
                    conn,
                    run_id=(
                        link_run.run_id if link_run is not None else report_id
                    ),
                    automatic_release_sha256=stored_record.record_sha256,
                )
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
                # 실제 Delivery.expires_at을 확인하는 권한 fence가 출고·청구와
                # 같은 transaction의 마지막 쓰기다. 실패하면 모두 rollback한다.
                bind_public_access(conn, public_delivery)
                conn.commit()
            return public_delivery
        candidate = _candidate_for_report(report_id, output_report)
        released = automatic_release_pdf(
            output_report,
            candidate,
            released_at=completed_at.isoformat(timespec="seconds"),
            content_validator=_content_validator_for(output_report),
        )
        backend = report_delivery_adapter.configured_artifact_backend()
        # 파일을 쓰기 전 본 출고 transaction과 별도로 intent를
        # commit한다. 이후 어느 DB 단계가 rollback되어도 고아 blob을
        # 정확히 식별해 유예 후 정리할 수 있다.
        blob_intent = report_delivery_adapter.prepare_approved_pdf_blob_intent(
            backend,
            pdf_bytes=released.content,
            prepared_at=completed_at,
        )
        with storage_db.connect() as conn:
            stored_record = pdf_release_store.save_automatic_release(
                conn,
                report_id=report_id,
                released_pdf=released,
            )
            public_delivery = report_delivery_adapter.persist_approved_delivery(
                conn,
                backend,
                blob_intent=blob_intent,
                public_id=report_id,
                corp_id=corp_id,
                billing_bucket_id=billing_bucket_id,
                report=output_report,
                pdf_bytes=released.content,
                completed_at=completed_at,
                actual_models=actual_models,
                reused_from_cache=reused_from_cache,
                dart_receipt_numbers=dart_receipt_numbers,
                financial_payload_digest=financial_payload_digest,
                cache_namespace=cache_namespace,
                preflight_identity_digest=preflight_identity_digest,
                bind_cache_entry=cache_eligible,
                engine_build_identity=frozen_build_identity,
            )
            link_run = share_store.load_run_by_report_id(conn, report_id)
            charge = cost_store.mark_automatic_release(
                conn,
                run_id=(link_run.run_id if link_run is not None else report_id),
                automatic_release_sha256=stored_record.record_sha256,
            )
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
            bind_public_access(conn, public_delivery)
            conn.commit()
        return public_delivery
    except Exception as exc:
        if isinstance(exc, (PublishBlockedError, V2ValidationError)):
            failure_code = _DELIVERY_PUBLICATION_BLOCKED
        elif isinstance(exc, PdfRenderBlockedError):
            failure_code = _DELIVERY_PDF_RENDER_BLOCKED
        elif isinstance(exc, AutomaticGateStopped):
            failure_code = _DELIVERY_AUTOMATIC_GATE_BLOCKED
        elif isinstance(exc, PDFReleaseBlockedError):
            failure_code = _DELIVERY_PDF_RELEASE_BLOCKED
        else:
            failure_code = _DELIVERY_FINALIZATION_FAILURE
        try:
            report_delivery_adapter.fail_public_delivery(
                report_id,
                failure_code=failure_code,
                failed_at=clock.now_kst(),
            )
        except Exception:  # noqa: BLE001 - 원래 출고 실패를 가리지 않는다
            logger.exception("delivery 실패 표식을 남기지 못했습니다 report_id=%s", report_id)
        if isinstance(exc, (PublishBlockedError, V2ValidationError)):
            _mark_link_release_gate_stopped(report_id)
        elif isinstance(exc, PDFReleaseBlockedError):
            _mark_link_release_gate_stopped(
                report_id,
                **_pdf_gate_stop_codes(exc),
            )
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
    pure_delivery_read: bool = False,
    legacy_readonly: bool = False,
    legacy_generated_at: str = "",
    legacy_stored_at: str = "",
) -> Response:
    resolved_track = request_helpers._track_of(request)
    if resolved_track[0] is share_tracks.Track.LINK:
        current_link = request_helpers._current_share_link(request)
        if current_link is None:
            return _link_view_event_unavailable_response(request)
        # LINK에서 새로 생성한 보고서는 run history만 생성 사건으로
        # 남긴다. 최초 연결 보고서를 연 경우에만 별도 조회 사건이다.
        if not pure_delivery_read and current_link.report_id == job.job_id:
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
        if not pure_delivery_read:
            _record_member_result_view(
                report_id=job.job_id,
                actor_email=member_email,
            )
        try:
            connection = (
                storage_db.connect_readonly_existing()
                if pure_delivery_read
                else storage_db.connect()
            )
            with connection as conn:
                if conn is None:
                    raise report_delivery_adapter.DeliveryAdapterError(
                        "MEMBER 설문 저장소가 없습니다"
                    )
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
                legacy_readonly=legacy_readonly,
                legacy_generated_at=legacy_generated_at,
                legacy_stored_at=legacy_stored_at,
            ),
        )
    )


def _member_feedback_allowed(request: Request) -> bool:
    """MEMBER 설문·오류 신고 입력은 초대 명단을 다시 읽어 표시한다."""
    return bool(_member_feedback_email(request))


def _member_feedback_email(request: Request) -> str:
    """현재 초대 MEMBER의 정규화 이메일만 돌려준다."""
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    readonly_existing = request_helpers.public_get_uses_readonly_existing(request)
    session = auth_logic.get_session(token, readonly_existing=readonly_existing)
    if session is None or session.is_admin:
        return ""
    try:
        connection = (
            storage_db.connect_readonly_existing()
            if readonly_existing
            else storage_db.connect()
        )
        with connection as conn:
            return (
                session.email
                if conn is not None and share_allow.is_allowed(conn, session.email)
                else ""
            )
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

    request_helpers.mark_public_get_readonly_existing(request)
    access_blocked = request_helpers.require_report_access(request, job_id)
    if access_blocked is not None:
        return access_blocked
    blocked = _dashboard_publication_block(request, job_id)
    if blocked is not None:
        return blocked
    try:
        stored_delivery = _stored_public_delivery(job_id)
    except Exception:  # noqa: BLE001 - 불변 출고물 손상은 fail-closed
        logger.exception("저장된 PDF delivery를 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)
    if stored_delivery is not None:
        if _delivery_is_expired(stored_delivery.delivery):
            return job_runtime._expired_screen(request)
        disposition = build_pdf_content_disposition(
            build_pdf_download_filename(stored_delivery.report)
        )
        return Response(
            content=stored_delivery.pdf_bytes,
            media_type=CONTENT_TYPE_PDF,
            headers={
                "Content-Disposition": disposition,
                "X-PDF-SHA256": stored_delivery.pdf_sha256,
                "X-PDF-Release-Record": stored_delivery.release_record_sha256,
                "X-PDF-Artifact-ID": stored_delivery.artifact_id,
                **SHARED_LINK_HEADERS,
            },
        )

    try:
        delivery_intent = report_delivery_adapter.load_public_delivery_intent(job_id)
    except Exception:  # noqa: BLE001 - 손상된 의무 표식도 legacy로 열지 않는다
        logger.exception("PDF delivery 의무 표식을 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)
    if delivery_intent is not None:
        return _delivery_intent_response(
            request,
            public_id=job_id,
            intent=delivery_intent,
        )

    try:
        legacy = report_delivery_adapter.load_legacy_public_report(job_id)
    except Exception:  # noqa: BLE001 - 손상된 legacy도 오늘 renderer로 복구하지 않는다
        logger.exception("과거 PDF의 저장 본문을 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)
    if legacy is None:
        return _report_unavailable_redirect()
    if job_runtime._link_expired(legacy.report):
        return job_runtime._expired_screen(request)
    return _legacy_pdf_unavailable_response(request)


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

    # 새 출고물은 결과/PDF와 같은 영속 Delivery 의무를 먼저 읽는다. 자동검사
    # 실패가 이미 기록됐는데 Notion만 옛 동적 ``_release_state``를 다시 실행하면,
    # 같은 보고서가 웹·PDF에서는 차단되고 외부 Notion에는 나가는 우회가 생긴다.
    # 완료 Delivery는 당시 승인 record와 PDF bytes까지 검증한 저장 본문을 쓰고,
    # 현재 renderer나 checker로 다시 판정하지 않는다.
    try:
        delivery_intent = report_delivery_adapter.load_public_delivery_intent(job_id)
    except Exception:  # noqa: BLE001 - 의무 상태를 모르면 외부 변경도 닫는다
        logger.exception("Notion 출고에서 delivery 의무 표식을 읽지 못했습니다 report_id=%s", job_id)
        return _delivery_unavailable_response(request)

    stored_delivery: _StoredPublicDelivery | None = None
    if delivery_intent is not None:
        if delivery_intent.state != delivery_store.DELIVERY_INTENT_COMPLETE:
            return _delivery_intent_response(
                request,
                public_id=job_id,
                intent=delivery_intent,
            )
        try:
            stored_delivery = _stored_public_delivery(job_id)
        except Exception:  # noqa: BLE001 - 승인 결속이 깨지면 외부로 내보내지 않는다
            logger.exception("Notion 출고에서 저장된 delivery를 읽지 못했습니다 report_id=%s", job_id)
            return _delivery_unavailable_response(request)
        if stored_delivery is None:
            return _delivery_unavailable_response(request)
        report = stored_delivery.report
    else:
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
    # 새 Delivery의 만료는 본문 생성일이 아니라 링크 발급 때 저장한 expires_at이
    # 정본이다. 과거 보고서에만 옛 generated_at 기반 판정을 쓴다. 두 시계를
    # 섞으면 오래 전에 작성한 캐시 보고서의 새 링크가 즉시 만료되거나, 반대로
    # 만료된 Delivery가 Notion만 우회하는 채널 불일치가 생긴다.
    delivery_expired = (
        _delivery_is_expired(stored_delivery.delivery)
        if stored_delivery is not None
        else job_runtime._link_expired(report)
    )
    # 권한/CSRF 다음, adapter나 멱등성 row보다 먼저 만료를 판정한다. 기간이
    # 지난 보고서는 명시적 410이며 Notion adapter 호출 수가 항상 0이다.
    if delivery_expired:
        return job_runtime._expired_screen(request)

    # 결과 화면에서 버튼을 숨기는 것만으로는 직접 POST를 막지 못한다. v2는
    # 현재 Notion 변환기가 없으므로 PDF 후보 생성·승인 원장·멱등성 claim·외부
    # adapter 중 어느 것도 건드리지 않고 제품 계약을 명시적으로 알린다.
    if getattr(report, "schema_version", "") == ENGINE_V2_SCHEMA_VERSION:
        return _notion_v2_unsupported_response(request)

    if stored_delivery is None:
        # Delivery 이전 보고서만 과거 동적 승인 호환 경로를 쓴다. 새 Delivery는
        # 위에서 저장 당시의 승인·artifact 결속을 이미 확인했다.
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
                exit_url=_ADMIN_DASHBOARD_URL,
                exit_label=_ADMIN_DASHBOARD_LABEL,
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
