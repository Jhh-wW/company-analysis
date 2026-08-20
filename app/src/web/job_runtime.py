"""조사 작업, 업로드 처리, 보고서 저장과 진행 상태."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, TypeVar

from fastapi import File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock, paths
from src.core.constants import PIPELINE_FAILED_MESSAGE, PROGRESS_STEPS
from src.features.budget import expiry as link_expiry
from src.features.budget import spend_store
from src.features.budget.constants import (
    BUSY_MESSAGE,
    JOB_KEEP_SEC,
    JOB_MAX_KEPT,
    SPEND_PHASE_OCR,
    SPEND_PHASE_PIPELINE,
)
from src.features.budget.sharing import (
    LINK_EXPIRED_MESSAGE,
    REPORT_LINK_MAX_AGE_DAYS,
    SHARED_LINK_HEADERS,
)
from src.features.business_candidate.constants import CANDIDATE_ATTEMPT_TTL_SEC
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline.demo import available_companies
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.posting_image.constants import (
    ERROR_EMPTY_FILE,
    ERROR_TOO_LARGE,
    ERROR_TOO_MANY,
    ERROR_TOTAL_TOO_LARGE,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_COUNT,
    MAX_TOTAL_BYTES,
)
from src.features.posting_image.logic import (
    PostingImageResult,
    default_extract,
    extract_posting_text,
)
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import PUBLIC_BUCKET
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import public_ids, request_helpers, runtime
from src.web.paid_runtime import (
    PaidPhase,
    _begin_paid_phase,
    _call_paid_provider,
    _cancel_paid_phase,
    _expire_observation_pending,
    _model_label,
    _model_tuple,
    _release_run_slot,
    _settle_paid_phase,
)
from src.web.recording import record_end, record_run


logger = logging.getLogger(__name__)
_WorkerResult = TypeVar("_WorkerResult")


_DEMO_IMAGE_NOTICE = (
    "지금은 데모라 사진을 읽지 않습니다. "
    "공고 «글자»를 붙여넣어 주세요 — 진짜 조사 모드에서는 사진도 읽습니다."
)
_IMAGE_CONSENT_ERROR = (
    "사진 원본을 Anthropic에 보내 글자를 추출하는 데 동의해야 사진을 사용할 수 있습니다. "
    "동의하지 않으시면 사진을 지우고 공고 글자를 붙여넣어 주세요."
)
_UPLOAD_READ_CHUNK_BYTES = 64 * 1024
_UPLOAD_CLOSE_TIMEOUT_SEC = 1.0
_JOB_DRAIN_TIMEOUT_SEC = 10.0
_JOB_CANCEL_GRACE_SEC = 1.0
_RETRY_AFTER_SEC = "3"
_PERSISTENCE_WARNING = (
    "이 보고서는 아직 저장되지 않았습니다. 현재 화면과 다운로드는 사용할 수 있지만 "
    "서버가 다시 시작되면 복구할 수 없습니다. 잠시 후 새로고침해 저장을 다시 시도하고, "
    "지금 PDF도 내려받아 보관해 주세요."
)


class JobAdmissionClosed(RuntimeError):
    """서버 종료가 시작되어 새 배경 작업을 받을 수 없음."""


class ReportStoreUnavailable(RuntimeError):
    """보고서 저장소를 읽을 수 없어 없음 여부를 판단할 수 없음."""


@dataclass
class Job:
    """돌아가는 중인 요청 하나."""

    job_id: str
    user_input: UserInput
    card: CompanyCard
    #: 지금까지 끝난 단계 키 목록
    done_steps: list[str] = field(default_factory=list)
    #: 지금 하고 있는 단계 키
    current_step: str = ""
    finished: bool = False
    result: Optional[RunResult] = None
    #: 끝난 시각 (`time.monotonic()`). 메모리 청소에 쓴다 (P-92).
    finished_at: float = 0.0
    #: 어느 «열쇠 링크»로 들어온 요청인가 (P-94).
    #: ★ 시작할 때 적어 둔다 — 끝난 뒤에는 요청 정보가 없어서 못 알아낸다.
    share_key: str = PUBLIC_BUCKET
    #: 확인 카드·이미지 OCR에서 먼저 쓴 돈. 본조사 결과 비용과 마지막에 합친다.
    upfront_cost_krw: float = 0.0
    upfront_models: tuple[str, ...] = ()
    upfront_elapsed_sec: float = 0.0
    #: 진짜 본조사라면 provider 호출 전 만든 표식. 데모는 None이다.
    paid_phase: Optional[PaidPhase] = None
    #: 예약한 동시 실행 자리. 작업과 종료 정리가 같은 자리를 두 번 풀지 않게 쓴다.
    slot_bucket_id: str = ""
    slot_released: bool = False
    paid_phase_settled: bool = False
    #: 보고서가 DB에 남았는지. None은 보고서가 없거나 아직 시도 전이다.
    report_persisted: Optional[bool] = None
    persistence_warning: str = ""


@dataclass(frozen=True)
class PaidAttempt:
    """회사 확인과 본조사를 잇는 일회용 서버 기록.

    ★ 데모·유료 모두 숨은 입력칸의 비용·권한·회사 식별값을 믿지 않는다.
      브라우저에는 추측할 수 없는 `token`만 보내고 실제 값은 서버 메모리에 둔다.
      토큰은 `/run`에서 한 번 꺼내면 바로 없어져 같은 식별 결과로 본조사를 반복
      호출하거나 다른 회사로 바꿔치기할 수 없다.
    """

    token: str
    run_id: str
    user_input: UserInput
    card: CompanyCard
    share_key: str
    bucket_id: str
    lookup_cost_krw: float
    models: tuple[str, ...]
    elapsed_sec: float
    created_at: float
    is_paid: bool = True


@dataclass(frozen=True)
class CandidateAttempt:
    """후보 화면과 DART 재검증을 잇는 5분짜리 서버 메모리 기록.

    Google Places 콘텐츠(이름·주소·홈페이지·attribution)는 여기에도 DB에도 두지
    않는다. 브라우저에 한 번 렌더한 후보별 최소 선택값은 프로세스 비밀 HMAC으로
    검증하고, 이 기록에는 nonce 역할의 token과 후보 수·원 입력·비용만 남긴다.
    """

    token: str
    run_id: str
    user_input: UserInput
    candidate_count: int
    share_key: str
    bucket_id: str
    candidate_cost_krw: float
    elapsed_sec: float
    posting_image_consent: bool
    evaluation_paid_consent: bool
    created_at: float


@dataclass
class CandidateSearchGrant:
    """DART 0건 화면이 발급한 Google fallback 1회 권한.

    ``candidate_attempt_token``은 결과가 한 번 브라우저에 전달됐다는 표식일 뿐이다.
    Places 콘텐츠는 캐시하지 않으며 같은 POST를 재생하면 재호출 없이 410으로 닫는다.
    """

    token: str
    user_input: UserInput
    share_key: str
    bucket_id: str
    posting_image_consent: bool
    evaluation_paid_consent: bool
    created_at: float
    in_flight: bool = False
    candidate_attempt_token: str = ""
    resolution_status: str = ""


_JOBS: dict[str, Job] = {}
_PAID_ATTEMPTS: dict[str, PaidAttempt] = {}
_CANDIDATE_ATTEMPTS: dict[str, CandidateAttempt] = {}
_CANDIDATE_SEARCH_GRANTS: dict[str, CandidateSearchGrant] = {}
_TASKS: set[asyncio.Task[None]] = set()
_TASK_JOBS: dict[asyncio.Task[None], Job] = {}
_UPLOAD_CLOSE_TASKS: set[asyncio.Task[None]] = set()
_ACCEPTING_JOBS = True
_SHUTTING_DOWN = False


def _abandon_confirmation_attempt(token: str) -> Optional[PaidAttempt]:
    """확인 시도를 소비하고, 유료 시도라면 대기 관측·ID 예약도 닫는다."""
    attempt = _PAID_ATTEMPTS.pop(token, None)
    if attempt is None:
        return None
    if attempt.is_paid:
        record_end(
            run_id=attempt.run_id,
            job=attempt.user_input.job,
            end_step=obs.END_STEP_CONFIRM,
            cost_krw=attempt.lookup_cost_krw,
            elapsed_sec=attempt.elapsed_sec,
            model=_model_label(attempt.models),
            expected_state=lifecycle.STATE_PENDING,
        )
        public_ids.release(attempt.run_id)
    return attempt


def _start_job_runtime() -> None:
    """새 lifespan이 시작되면 조사 admission을 다시 연다."""
    global _ACCEPTING_JOBS, _SHUTTING_DOWN
    _SHUTTING_DOWN = False
    _ACCEPTING_JOBS = True


def _begin_job_shutdown() -> None:
    """새 예약을 원자적인 동기 경계에서 닫고 종료 취소 모드로 바꾼다."""
    global _ACCEPTING_JOBS, _SHUTTING_DOWN
    _ACCEPTING_JOBS = False
    _SHUTTING_DOWN = True


def _accepting_jobs() -> bool:
    return _ACCEPTING_JOBS


def _reserved_work_admitted(slot_bucket_id: str) -> bool:
    """종료 admission과 이 요청의 슬롯 소유 표식이 모두 살아 있는가."""
    return _ACCEPTING_JOBS and bool(slot_bucket_id)


def _job_work_admitted(job: Job) -> bool:
    """등록된 Job의 슬롯 표식까지 포함해 provider 시작 가능 여부를 본다."""
    stored_bucket = job.slot_bucket_id or getattr(job.paid_phase, "bucket_id", "")
    return _ACCEPTING_JOBS and not job.slot_released and bool(stored_bucket)


def _retryable_response(response: Response) -> Response:
    """일시 장애 응답을 브라우저·중간 캐시가 저장하지 않게 한다."""
    response.headers["Retry-After"] = _RETRY_AFTER_SEC
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def _admission_unavailable_response(request: Request) -> HTMLResponse:
    """종료 중 들어온 새 조사에 재시도 가능한 503을 돌려준다."""
    return _retryable_response(
        request_helpers.templates.TemplateResponse(
            request=request,
            name="progress_unavailable.html",
            context=request_helpers._ctx(
                request,
                interruption_title="지금은 새 조사를 시작할 수 없습니다",
                interruption_message=(
                    "서버가 안전하게 종료 중입니다. 잠시 후 다시 접속해 조사를 시작해 주세요."
                ),
                interruption_hint="입력 문제나 사용량 초과가 아닙니다.",
                retry_url="/",
                retry_label="잠시 후 다시 시도",
            ),
            status_code=503,
        )
    )


def _storage_unavailable_response(request: Request) -> HTMLResponse:
    """DB 장애를 없는 결과로 숨기지 않는 재시도 가능한 503 화면."""
    return _retryable_response(
        request_helpers.templates.TemplateResponse(
            request=request,
            name="progress_unavailable.html",
            context=request_helpers._ctx(
                request,
                interruption_title="저장된 상태를 잠시 확인할 수 없습니다",
                interruption_message=(
                    "저장소 연결이 원활하지 않아 작업이나 보고서가 없는지 지금은 판단할 수 없습니다."
                ),
                interruption_hint=(
                    "새 조사를 시작하지 마세요. 잠시 후 이 페이지에서 다시 확인해 주세요."
                ),
                retry_url="",
                retry_label="다시 확인",
            ),
            status_code=503,
        )
    )

async def _await_worker_after_cancel(
    worker: asyncio.Task[_WorkerResult],
) -> _WorkerResult:
    """바깥 요청이 여러 번 취소돼도 실제 스레드가 끝날 때까지 기다린다.

    ``asyncio.to_thread``의 Task를 취소해도 이미 시작한 provider 스레드는 멈추지
    않는다. 정리가 기다리는 동안 두 번째 취소가 와도 계속 shield해야 실제 호출 수와
    비용 표식이 먼저 풀리지 않는다.
    """
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
    return worker.result()

def _sweep_jobs(now: float) -> None:
    """끝난 지 오래된 조사를 메모리에서 치운다 (P-92).

    Args:
        now: 지금 시각 (`time.monotonic()`).

    ★ 보고서는 DB에 저장돼 있어서 **치워도 화면은 그대로 열린다**
      (`/result`가 메모리에 없으면 저장소에서 찾는다).
    ★ 시간·개수 «두 기준»을 같이 건다 — 시간만 두면 1시간 안에 몰아치는 것을 못 막는다.
    """
    낡음 = [
        job_id
        for job_id, job in _JOBS.items()
        if job.finished and job.finished_at and now - job.finished_at > JOB_KEEP_SEC
    ]
    for job_id in 낡음:
        del _JOBS[job_id]

    # 확인 화면을 닫고 떠난 사람의 일회용 토큰도 영원히 쌓이지 않게 치운다.
    for token, attempt in list(_PAID_ATTEMPTS.items()):
        if now - attempt.created_at > JOB_KEEP_SEC:
            _abandon_confirmation_attempt(token)

    # Places/DART 후보의 일시 메모리는 5분만 둔다. 응답 원문·주소·홈페이지는 애초에
    # 저장하지 않고, 만료 시 같은 분석 ID 예약도 반환한다.
    for token, attempt in list(_CANDIDATE_ATTEMPTS.items()):
        if now - attempt.created_at > CANDIDATE_ATTEMPT_TTL_SEC:
            del _CANDIDATE_ATTEMPTS[token]
            public_ids.release(attempt.run_id)
    for token, grant in list(_CANDIDATE_SEARCH_GRANTS.items()):
        if now - grant.created_at > CANDIDATE_ATTEMPT_TTL_SEC:
            del _CANDIDATE_SEARCH_GRANTS[token]

    # 메모리에 없는 재시작 전 대기표도 같은 벽시계 만료 기준으로 정리한다.
    _expire_observation_pending()

    if len(_JOBS) > JOB_MAX_KEPT:
        # 끝난 것부터, 오래된 것부터 치운다. **돌고 있는 것은 안 건드린다.**
        끝난것 = sorted(
            (j for j in _JOBS.values() if j.finished),
            key=lambda j: j.finished_at,
        )
        for job in 끝난것[: len(_JOBS) - JOB_MAX_KEPT]:
            _JOBS.pop(job.job_id, None)

def _mark_step(job: Job) -> Callable[[str], None]:
    """알맹이가 「이 단계 시작했다」고 알려올 때 진행 화면에 반영하는 함수를 만든다."""

    def report(key: str) -> None:
        if job.current_step and job.current_step not in job.done_steps:
            job.done_steps.append(job.current_step)
        job.current_step = key

    return report

async def _run_job(job: Job) -> None:
    """뒤에서 파이프라인을 돌리며 진행 상황을 갱신한다.

    ★ 파이프라인은 오래 걸리고 중간에 막힌다 (최대 5분). 그대로 부르면
      그동안 **다른 사용자의 화면까지 멈춘다.** 그래서 별도 실행 흐름에서 돌린다.
    """
    started = time.perf_counter()
    worker: Optional[asyncio.Task[RunResult]] = None
    try:
        # schedule 검사 뒤 task가 실제로 실행되기 전 shutdown이 시작될 수 있다.
        # provider를 한 번도 부르지 않은 phase는 취소하고 비용 불확실성을 만들지 않는다.
        if not _ACCEPTING_JOBS or (
            job.paid_phase is not None and not _job_work_admitted(job)
        ):
            if job.paid_phase is not None and not job.paid_phase_settled:
                job.paid_phase_settled = True
                _cancel_paid_phase(job.paid_phase)
            job.result = RunResult(
                outcome=Outcome.FAILED,
                message=PIPELINE_FAILED_MESSAGE,
            )
            return
        worker = asyncio.create_task(
            asyncio.to_thread(
                _call_paid_provider,
                job.paid_phase,
                runtime._PIPELINE.run,
                job.user_input,
                job.card,
                _mark_step(job),
            )
            if job.paid_phase is not None
            else asyncio.to_thread(
                runtime._PIPELINE.run, job.user_input, job.card, _mark_step(job)
            )
        )
        job.result = await asyncio.shield(worker)
        if not isinstance(job.result, RunResult):
            raise TypeError("파이프라인 결과 계약이 올바르지 않습니다")
    except asyncio.CancelledError:
        if _SHUTTING_DOWN:
            # 종료 제한시간이 지난 뒤에는 죽일 수 없는 provider 스레드를 더 기다리지
            # 않는다. 실제 비용은 알 수 없으므로 아래 마감에서 미확정으로 fail-closed한다.
            if worker is not None and not worker.done():
                worker.cancel()
            job.result = RunResult(
                outcome=Outcome.FAILED,
                message=PIPELINE_FAILED_MESSAGE,
                billing_uncertain=job.paid_phase is not None,
            )
            raise
        # 사용자가 창을 닫아도 provider 스레드는 멈추지 않는다. 끝날 때까지 슬롯을
        # 유지한다. 정리 중 다시 취소돼도 shield를 반복해 실제 worker를 기다린다.
        try:
            if worker is None:
                raise RuntimeError("파이프라인 worker가 만들어지지 않았습니다")
            job.result = await _await_worker_after_cancel(worker)
            if not isinstance(job.result, RunResult):
                raise TypeError("파이프라인 결과 계약이 올바르지 않습니다")
        except BaseException:  # noqa: BLE001 — 끝내 결과를 모르면 fail-closed
            job.result = RunResult(
                outcome=Outcome.FAILED,
                message=PIPELINE_FAILED_MESSAGE,
                billing_uncertain=job.paid_phase is not None,
            )
        raise
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 화면은 살아 있어야 한다
        # ★ 사용자에게 내부 오류 내용을 보여주지 않는다 (경로·스택 노출 금지).
        logger.exception("파이프라인 실패 job_id=%s", job.job_id)
        job.result = RunResult(
            outcome=Outcome.FAILED,
            message=PIPELINE_FAILED_MESSAGE,
            # 진짜 알맹이가 계약 밖 예외를 냈다면 provider 호출 뒤였을 수 있다.
            billing_uncertain=job.paid_phase is not None,
        )
        del exc
    finally:
        try:
            # 최외곽에서도 계약을 한 번 더 닫는다. provider 호출 뒤 잘못된 객체가
            # 돌아와도 비용 표식을 active로 남기거나 0원 확정하면 안 된다.
            if not isinstance(job.result, RunResult):
                job.result = RunResult(
                    outcome=Outcome.FAILED,
                    message=PIPELINE_FAILED_MESSAGE,
                    billing_uncertain=job.paid_phase is not None,
                )

            # 마지막 단계까지 끝난 것으로 채운다 — 화면에 반쯤 돈 상태가 남지 않게.
            for key, _label in PROGRESS_STEPS:
                if key not in job.done_steps:
                    job.done_steps.append(key)
            job.current_step = ""
            job.finished = True
            job.finished_at = time.monotonic()
            # 14. 이력 1행 — 성공·실패 무관하게 남긴다 (기획서 08 관측).
            if (
                job.result.billing_uncertain
                and job.result.outcome is not Outcome.REPORT
            ):
                # API 예외 뒤 나온 「공고 아님·자료 없음」은 사실 판정이 아니라
                # 기술 실패일 수 있다. 보고서가 실제 나온 경우만 그대로 두고,
                # 나머지는 거짓 종료값 대신 결함(FAILED)으로 기록한다.
                job.result = replace(
                    job.result,
                    outcome=Outcome.FAILED,
                    report=None,
                    message=PIPELINE_FAILED_MESSAGE,
                    charged=False,
                )
            pipeline_cost = job.result.cost_krw
            if job.paid_phase is not None and not job.paid_phase_settled:
                job.paid_phase_settled = True
                _settle_paid_phase(
                    job.paid_phase,
                    amount_krw=pipeline_cost,
                    billing_uncertain=job.result.billing_uncertain,
                )
            # 식별·OCR·본조사를 이력 한 건의 실제 총비용으로 합친다. 앞단 값을
            # 브라우저에서 다시 받지 않고 서버 Job이 들고 온 값만 쓴다.
            models = _model_tuple(*job.upfront_models, job.result.model)
            job.result = replace(
                job.result,
                cost_krw=job.upfront_cost_krw + pipeline_cost,
                model=_model_label(models),
            )
            record_run(
                job.user_input,
                job.result,
                job.upfront_elapsed_sec + time.perf_counter() - started,
                run_id=job.job_id,
                expected_state=(
                    lifecycle.STATE_RUNNING if job.paid_phase is not None else None
                ),
            )
            _save_report(job)
        finally:
            # 비용 마감과 이력 정리를 시도한 뒤에만 자리를 돌려준다. 중간에 어떤
            # 예외가 나도 이 최외곽 finally는 실행되어 자리가 영구히 새지 않는다.
            _release_job_slot(job)


def _release_job_slot(job: Job) -> None:
    """Job이 소유한 동시 실행 자리를 동기 경계에서 정확히 한 번 반환한다."""
    if job.slot_released:
        return
    job.slot_released = True
    _release_run_slot(
        job.slot_bucket_id or spend_store.bucket_id(job.share_key)
    )


def _save_report(job: Job) -> bool:
    """만든 보고서를 파일 저장소에 남긴다.

    ★ 이게 없으면 **서버를 끄는 순간 보고서가 사라진다** (메모리에만 있었다).
    ★ 저장이 실패해도 사용자가 화면을 못 보게 만들지 않는다 — 화면은 메모리에도 있다.
    """
    if job.result is None or job.result.report is None:
        return False
    try:
        with storage_db.connect() as conn:
            if not report_store.insert_new(
                conn,
                report_id=job.job_id,
                corp_id=job.card.ref or job.card.legal_name,
                job=job.user_input.job,
                report=job.result.report,
            ):
                raise RuntimeError("공개 보고서 ID가 이미 사용 중입니다")
        job.report_persisted = True
        job.persistence_warning = ""
        return True
    except Exception:  # noqa: BLE001 — 저장 실패가 사용자를 막으면 안 된다
        job.report_persisted = False
        job.persistence_warning = _PERSISTENCE_WARNING
        logger.exception("보고서 저장 실패 (현재 화면은 임시이며 재시작 복구 불가)")
        return False


def _shared(response: Response) -> Response:
    """공유 링크 보호 헤더를 응답에 붙인다 (P-93).

    Args:
        response: 이미 만들어진 응답.

    Returns:
        헤더가 붙은 «같은» 응답.

    ⚠️ `TemplateResponse(headers=...)`로 넘기면 **조용히 무시된다** —
      실제로 그렇게 넣었다가 헤더가 하나도 안 붙는 것을 시험이 잡았다.
      만들어진 응답에 «직접» 붙이는 이 방식만 확실하다.
    """
    for name, value in SHARED_LINK_HEADERS.items():
        response.headers[name] = value
    return response

def _expired_screen(request: Request) -> HTMLResponse:
    """기간이 지난 링크에 보여줄 화면 (P-93).

    ★ 「없는 보고서」로 처리하지 않는다 — 있었는데 «기간이 지난» 것이고,
      그 둘은 사용자에게 완전히 다른 뜻이다.
    """
    return _shared(
        request_helpers.templates.TemplateResponse(
            request=request,
            name="expired.html",
            context=request_helpers._ctx(
                request, expired_message=LINK_EXPIRED_MESSAGE
            ),
            status_code=410,                  # 410 Gone = 「있었는데 이제 없다」
        )
    )

def _link_expired(report: Optional[Report]) -> bool:
    """이 보고서 링크가 기간이 지났는가."""
    if report is None:
        return False
    return link_expiry.is_expired(
        report.generated_at, clock.today_kst(), REPORT_LINK_MAX_AGE_DAYS
    )

def _load_saved_report(report_id: str) -> Optional[Report]:
    """서버를 껐다 켠 뒤에도 옛 보고서를 다시 볼 수 있게 한다."""
    try:
        with storage_db.connect() as conn:
            return report_store.load(conn, report_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("보고서 불러오기 실패")
        raise ReportStoreUnavailable from exc


async def _close_posting_images(files: list[UploadFile]) -> None:
    """반복 취소 중에도 모든 임시 업로드에 닫기를 최선 노력한다.

    닫기 루프는 별도 task로 강하게 참조하고 shield한다. 호출 task에 취소가 여러 번
    들어와도 루프를 끝까지 마친 뒤 취소를 다시 전파하므로 파일 정리와 취소 의미를
    둘 다 보존한다. 개별 ``close()``의 ``CancelledError``도 다음 파일을 막지 않는다.
    """
    if not files:
        return

    async def close_one(upload: UploadFile) -> None:
        try:
            await upload.close()
        except BaseException:  # noqa: BLE001 — 파일 하나가 다른 close를 막지 않는다
            logger.warning("임시 업로드 파일을 닫지 못했습니다")

    async def close_all() -> None:
        # 하나의 close가 오래 걸려도 나머지 파일 close는 모두 즉시 시작한다.
        close_tasks = tuple(
            asyncio.create_task(close_one(upload), name="close-posting-upload")
            for upload in files
        )
        for close_task in close_tasks:
            _UPLOAD_CLOSE_TASKS.add(close_task)
            close_task.add_done_callback(_UPLOAD_CLOSE_TASKS.discard)
        done, pending = await asyncio.wait(
            close_tasks, timeout=_UPLOAD_CLOSE_TIMEOUT_SEC
        )
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        for close_task in pending:
            close_task.cancel()
            # close 구현이 취소를 무시해도 서버 종료를 무한히 붙들지 않는다.
            # 전역 set이 끝날 때까지 강하게 참조하고 close_one이 최종 예외를 회수한다.

    closer = asyncio.create_task(close_all(), name="close-posting-uploads")
    cancelled = False
    while not closer.done():
        try:
            await asyncio.shield(closer)
        except asyncio.CancelledError:
            cancelled = True
            continue
    # close_all은 개별 실패를 회수하지만 계약 변경에도 예외가 유실되지 않게 소비한다.
    try:
        closer.result()
    except BaseException:  # pragma: no cover - 방어적 회수
        logger.exception("임시 업로드 파일 정리 task가 실패했습니다")
    if cancelled:
        raise asyncio.CancelledError


async def _posting_images_dependency(
    posting_images: list[UploadFile | str] = File(default=[]),
):
    """빈 file input은 없애고 실제 업로드의 임시 파일은 반드시 닫는다.

    브라우저는 선택하지 않은 file input을 빈 문자열 part로 보낼 수 있다. FastAPI가
    그 값을 문자열로 넘길 때 422로 끊지 않고 «업로드 없음»으로 정규화한다. 파일명이
    있는 실제 업로드는 그대로 넘겨 아래의 동의·개수·크기·OCR 검사를 모두 거친다.
    """
    uploads = [upload for upload in posting_images if not isinstance(upload, str)]
    files = [upload for upload in uploads if upload.filename]
    try:
        yield files
    finally:
        await _close_posting_images(uploads)

async def _read_posting_images_bounded(
    files: list[UploadFile],
    *,
    close_files: bool = True,
) -> tuple[list[bytes], PostingImageResult | None]:
    """파일을 작은 조각으로 읽으며 장수·한 장·전체 상한을 즉시 적용한다.

    독립 호출은 직접 닫는다. 예약 슬롯을 소유한 실행 경로는 ``close_files=False``로
    두어 슬롯 반환보다 앞에 어떤 close await도 놓이지 않게 하고 최외곽에서 닫는다.
    """
    named = [upload for upload in files if upload.filename]
    try:
        if len(named) > MAX_IMAGE_COUNT:
            return [], PostingImageResult(
                ok=False,
                error=ERROR_TOO_MANY.format(count=len(named), limit=MAX_IMAGE_COUNT),
                failure_kind="input",
            )

        images: list[bytes] = []
        total = 0
        for upload in named:
            data = bytearray()
            while True:
                chunk = await upload.read(_UPLOAD_READ_CHUNK_BYTES)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    return [], PostingImageResult(
                        ok=False,
                        error=ERROR_TOO_LARGE.format(
                            mb=len(data) / (1024 * 1024),
                            limit_mb=MAX_IMAGE_BYTES // (1024 * 1024),
                        ),
                        failure_kind="input",
                    )
                total += len(chunk)
                if total > MAX_TOTAL_BYTES:
                    return [], PostingImageResult(
                        ok=False,
                        error=ERROR_TOTAL_TOO_LARGE.format(
                            mb=total / (1024 * 1024),
                            limit_mb=MAX_TOTAL_BYTES // (1024 * 1024),
                        ),
                        failure_kind="input",
                    )
            if not data:
                return [], PostingImageResult(
                    ok=False,
                    error=ERROR_EMPTY_FILE,
                    failure_kind="input",
                )
            images.append(bytes(data))
        return images, None
    finally:
        if close_files:
            await _close_posting_images(files)


def _forget_finished_task(task: asyncio.Task[None]) -> None:
    """끝난 작업의 강한 참조를 놓고 예상 밖 예외를 로그에 남긴다."""
    _TASKS.discard(task)
    _TASK_JOBS.pop(task, None)
    if task.cancelled():
        return
    try:
        failure = task.exception()
    except asyncio.CancelledError:
        return
    if failure is not None:
        logger.error(
            "배경 조사 작업이 정리 밖에서 실패했습니다",
            exc_info=(type(failure), failure, failure.__traceback__),
        )


def _schedule_job(job: Job) -> asyncio.Task[None]:
    """배경 작업을 강하게 참조해 실행 중 소실과 종료 시 누락을 막는다."""
    if not _ACCEPTING_JOBS or (
        job.paid_phase is not None and not _job_work_admitted(job)
    ):
        raise JobAdmissionClosed("server shutdown has started")
    task = asyncio.create_task(
        _run_job(job), name=f"analysis-job:{job.job_id}"
    )
    _TASKS.add(task)
    _TASK_JOBS[task] = job
    task.add_done_callback(_forget_finished_task)
    return task


def _force_shutdown_cleanup(job: Job) -> None:
    """취소 유예 안에도 끝나지 않은 Job의 비용과 슬롯을 fail-closed로 닫는다."""
    if not isinstance(job.result, RunResult):
        job.result = RunResult(
            outcome=Outcome.FAILED,
            message=PIPELINE_FAILED_MESSAGE,
            billing_uncertain=job.paid_phase is not None,
        )
    job.current_step = ""
    job.finished = True
    job.finished_at = job.finished_at or time.monotonic()
    if job.paid_phase is not None and not job.paid_phase_settled:
        job.paid_phase_settled = True
        try:
            _settle_paid_phase(
                job.paid_phase, amount_krw=0.0, billing_uncertain=True
            )
        except BaseException:  # noqa: BLE001 — 종료는 계속하고 통장은 보수적으로 닫는다
            logger.exception("종료 중 비용 표식을 미확정으로 마감하지 못했습니다")
    if job.paid_phase is not None:
        record_end(
            run_id=job.job_id,
            job=job.user_input.job,
            end_step=obs.END_STEP_GENERATE,
            cost_krw=job.upfront_cost_krw,
            elapsed_sec=job.upfront_elapsed_sec,
            model=_model_label(job.upfront_models),
            expected_state=lifecycle.STATE_RUNNING,
        )
    try:
        _release_job_slot(job)
    except BaseException:  # noqa: BLE001 — 한 작업 오류가 다른 작업 정리를 막지 않는다
        logger.exception("종료 중 조사 슬롯을 반환하지 못했습니다")


async def _drain_job_tasks(
    *,
    timeout_sec: float = _JOB_DRAIN_TIMEOUT_SEC,
    cancel_grace_sec: float = _JOB_CANCEL_GRACE_SEC,
) -> None:
    """새 admission을 닫고 제한시간 안에서 배경 조사를 마감한다.

    정상 완료를 먼저 기다린 뒤 남은 task를 취소·회수한다. 종료 취소를 받은
    ``_run_job``은 죽일 수 없는 provider 스레드를 더 기다리지 않고 비용을 미확정으로
    닫는다. 취소 유예에도 남은 비정상 task는 마지막 동기 정리로 슬롯을 반환한다.
    """
    _begin_job_shutdown()
    loop = asyncio.get_running_loop()
    pending = tuple(
        task
        for task in _TASKS
        if task.get_loop() is loop and not task.done()
    )
    if not pending:
        return

    timeout = max(0.0, float(timeout_sec))
    logger.info(
        "실행 중인 조사 %d건을 최대 %.1f초 기다립니다", len(pending), timeout
    )
    done, pending_set = await asyncio.wait(pending, timeout=timeout)
    if done:
        await asyncio.gather(*done, return_exceptions=True)
    if not pending_set:
        return

    logger.warning(
        "종료 제한시간을 넘긴 조사 %d건을 취소하고 비용을 미확정 마감합니다",
        len(pending_set),
    )
    for task in pending_set:
        task.cancel()

    cancelled_done, stubborn = await asyncio.wait(
        pending_set, timeout=max(0.0, float(cancel_grace_sec))
    )
    if cancelled_done:
        await asyncio.gather(*cancelled_done, return_exceptions=True)

    for task in stubborn:
        job = _TASK_JOBS.get(task)
        if job is not None:
            _force_shutdown_cleanup(job)
        task.cancel()
        # process teardown 직전이므로 callback을 기다리지 못하는 task도 강한 목록에서 뺀다.
        _TASKS.discard(task)
        _TASK_JOBS.pop(task, None)

async def _start_with_reserved_slot(
    *,
    request: Request,
    original_input: UserInput,
    card: CompanyCard,
    posting_images: list[UploadFile],
    posting_image_consent: bool,
    is_paid: bool,
    resolved_track: tuple[share_tracks.Track, str, float],
    run_id: str,
    upfront_cost: float,
    upfront_models: tuple[str, ...],
    upfront_elapsed: float,
    slot_bucket_id: str,
) -> Response:
    """이미 잡은 한 자리를 OCR부터 배경 본조사로 안전하게 넘긴다.

    ★ 업로드 읽기·OCR·DB·작업 등록 어디서 예외가 나도 `finally`가 자리를 돌려준다.
      배경 작업 등록에 성공한 순간부터는 `_run_job()`이 그 자리를 돌려준다.
    """
    handed_off = False
    share_key = resolved_track[1]
    pipeline_phase: Optional[PaidPhase] = None
    admission_recorded = False

    def admission_rejection() -> HTMLResponse:
        """종료 경합을 503으로 닫고 유료 관측을 정확히 한 번 마감한다."""
        nonlocal admission_recorded
        if is_paid and not admission_recorded:
            admission_recorded = True
            record_end(
                run_id=run_id,
                job=original_input.job,
                end_step=obs.END_STEP_GENERATE,
                cost_krw=upfront_cost,
                elapsed_sec=upfront_elapsed,
                model=_model_label(upfront_models),
                expected_state=lifecycle.STATE_RUNNING,
            )
        return _admission_unavailable_response(request)

    try:
        if not _reserved_work_admitted(slot_bucket_id):
            return admission_rejection()
        # 사진으로 올린 공고는 «여기서 처음» 서버에 들어온다 (기획서 D2 — 판정 통과 후).
        # ★ 원본 바이트를 파일·로그·결과 어디에도 남기지 않는다 (S2).
        posting_body = original_input.posting_text
        image_error = ""
        image_failure_kind = ""
        if posting_images:
            image_bytes: list[bytes] = []
            has_named_image = any(upload.filename for upload in posting_images)
            if has_named_image and not posting_image_consent:
                image_bytes = []
                image_error = _IMAGE_CONSENT_ERROR
                image_failure_kind = "input"
            else:
                image_bytes, upload_failure = await _read_posting_images_bounded(
                    posting_images, close_files=False
                )
                # 이미지 read await 중 shutdown이 시작됐으면 비용 phase/provider보다
                # 먼저 끝낸다. 이 검사가 잔존 race의 핵심 admission fence다.
                if not _reserved_work_admitted(slot_bucket_id):
                    del image_bytes
                    return admission_rejection()
                if upload_failure is not None:
                    image_error = upload_failure.error
                    image_failure_kind = upload_failure.failure_kind
            if image_bytes and not is_paid:
                del image_bytes          # ★ 안 쓸 거면 «바로» 버린다 (S2)
                image_error = _DEMO_IMAGE_NOTICE
                image_failure_kind = "input"
            elif image_bytes:
                # 유료 phase를 DB에 만들기 직전 admission과 슬롯 소유를 함께 재검사한다.
                if not _reserved_work_admitted(slot_bucket_id):
                    del image_bytes
                    return admission_rejection()
                ocr_phase = _begin_paid_phase(
                    run_id=run_id,
                    phase=SPEND_PHASE_OCR,
                    share_key=share_key,
                    cap_krw=resolved_track[2],
                )
                if ocr_phase is None:
                    del image_bytes
                    record_end(
                        run_id=run_id,
                        job=original_input.job,
                        end_step=obs.END_STEP_GENERATE,
                        cost_krw=upfront_cost,
                        elapsed_sec=upfront_elapsed,
                        model=_model_label(upfront_models),
                        expected_state=lifecycle.STATE_RUNNING,
                    )
                    return request_helpers._throttled(
                        request, BUSY_MESSAGE, "budget-store"
                    )
                # phase 생성 자체가 지연되는 동안 닫힐 수도 있다. provider를 부르기
                # 직전 다시 보고, 아직 호출 전인 phase는 정산이 아니라 취소한다.
                if not _reserved_work_admitted(slot_bucket_id):
                    del image_bytes
                    _cancel_paid_phase(ocr_phase)
                    return admission_rejection()
                ocr_started = time.perf_counter()
                ocr_task = asyncio.create_task(
                    asyncio.to_thread(
                        _call_paid_provider,
                        ocr_phase,
                        extract_posting_text,
                        image_bytes,
                        extract=default_extract,
                    )
                )
                try:
                    outcome = await asyncio.shield(ocr_task)
                    if not isinstance(outcome, PostingImageResult):
                        raise TypeError("OCR 결과 계약이 올바르지 않습니다")
                except asyncio.CancelledError:
                    # OCR도 내부 스레드가 끝날 때까지 같은 자리를 유지한다. 정리 중
                    # 다시 취소돼도 helper가 실제 worker 완료까지 shield를 반복한다.
                    try:
                        outcome = await _await_worker_after_cancel(ocr_task)
                        if not isinstance(outcome, PostingImageResult):
                            raise TypeError("OCR 결과 계약이 올바르지 않습니다")
                    except BaseException:  # noqa: BLE001
                        _settle_paid_phase(
                            ocr_phase, amount_krw=0.0, billing_uncertain=True
                        )
                    else:
                        _settle_paid_phase(
                            ocr_phase,
                            amount_krw=outcome.cost_krw,
                            billing_uncertain=outcome.billing_uncertain,
                        )
                    raise
                except Exception:  # noqa: BLE001 — provider 뒤 계약 오류도 미확정 마감
                    _settle_paid_phase(
                        ocr_phase, amount_krw=0.0, billing_uncertain=True
                    )
                    raise
                upfront_elapsed += time.perf_counter() - ocr_started
                del image_bytes          # ★ 원본 참조를 즉시 끊는다 (S2)
                _settle_paid_phase(
                    ocr_phase,
                    amount_krw=outcome.cost_krw,
                    billing_uncertain=outcome.billing_uncertain,
                )
                upfront_cost += outcome.cost_krw
                upfront_models = _model_tuple(*upfront_models, outcome.model)
                # 이미 시작한 OCR은 정상 정산했지만, 완료와 본조사 phase 사이에서
                # shutdown이 시작됐으면 새 본조사 비용 표식·provider는 만들지 않는다.
                if not _reserved_work_admitted(slot_bucket_id):
                    return admission_rejection()
                if outcome.ok and not outcome.billing_uncertain:
                    posting_body = outcome.text
                else:
                    image_error = outcome.error
                    image_failure_kind = outcome.failure_kind

                if not image_error:
                    # OCR 비용이 남은 몫을 다 썼다면 본조사 AI는 시작하지 않는다.
                    blocked = request_helpers._guard_run(
                        request,
                        count_start=False,
                        owns_slot=True,
                        resolved_track=resolved_track,
                    )
                    if blocked is not None:
                        record_end(
                            run_id=run_id,
                            job=original_input.job,
                            end_step=obs.END_STEP_GENERATE,
                            cost_krw=upfront_cost,
                            elapsed_sec=upfront_elapsed,
                            model=_model_label(upfront_models),
                            expected_state=lifecycle.STATE_RUNNING,
                        )
                        return blocked

        user_input = replace(original_input, posting_text=posting_body)
        if image_error:
            # OCR 실패는 「공고가 아님」이 아니다. 입력·화질 문제와 기술 오류도
            # 신호가 다르므로 서로 다른 종료값으로 같은 요청 번호를 마감한다.
            if is_paid:
                record_end(
                    run_id=run_id,
                    job=original_input.job,
                    end_step=(
                        obs.END_STEP_IMAGE_ERROR
                        if image_failure_kind == "technical"
                        else obs.END_STEP_IMAGE_INPUT
                    ),
                    cost_krw=upfront_cost,
                    elapsed_sec=upfront_elapsed,
                    model=_model_label(upfront_models),
                    expected_state=lifecycle.STATE_RUNNING,
                )
            return request_helpers.templates.TemplateResponse(
                request=request,
                name="input.html",
                context=request_helpers._ctx(
                    request,
                    image_error=image_error,
                    prefill=user_input,
                    demo_companies=available_companies(),
                    demo_available=paths.demo_data_available(),
                ),
            )

        if is_paid:
            # route가 죽어도 첫 provider 호출 전 표식은 이미 커밋돼 있어야 한다.
            if not _reserved_work_admitted(slot_bucket_id):
                return admission_rejection()
            pipeline_phase = _begin_paid_phase(
                run_id=run_id,
                phase=SPEND_PHASE_PIPELINE,
                share_key=share_key,
                cap_krw=resolved_track[2],
            )
            if pipeline_phase is None:
                record_end(
                    run_id=run_id,
                    job=original_input.job,
                    end_step=obs.END_STEP_GENERATE,
                    cost_krw=upfront_cost,
                    elapsed_sec=upfront_elapsed,
                    model=_model_label(upfront_models),
                    expected_state=lifecycle.STATE_RUNNING,
                )
                return request_helpers._throttled(
                    request, BUSY_MESSAGE, "budget-store"
                )
            # phase DB 쓰기와 실제 pipeline task 등록 사이의 마지막 fail-closed fence.
            if not _reserved_work_admitted(slot_bucket_id):
                _cancel_paid_phase(pipeline_phase)
                return admission_rejection()

        new_job = Job(
            job_id=run_id,
            user_input=user_input,
            card=card,
            share_key=share_key,
            upfront_cost_krw=upfront_cost,
            upfront_models=upfront_models,
            upfront_elapsed_sec=upfront_elapsed,
            paid_phase=pipeline_phase,
            slot_bucket_id=slot_bucket_id,
        )
        if not public_ids.register(_JOBS, run_id, new_job):
            if pipeline_phase is not None:
                _cancel_paid_phase(pipeline_phase)
            if is_paid and not admission_recorded:
                admission_recorded = True
                record_end(
                    run_id=run_id,
                    job=original_input.job,
                    end_step=obs.END_STEP_GENERATE,
                    cost_krw=upfront_cost,
                    elapsed_sec=upfront_elapsed,
                    model=_model_label(upfront_models),
                    expected_state=lifecycle.STATE_RUNNING,
                )
            return _storage_unavailable_response(request)
        try:
            _schedule_job(new_job)
        except JobAdmissionClosed:
            if _JOBS.get(run_id) is new_job:
                _JOBS.pop(run_id, None)
            if pipeline_phase is not None:
                _cancel_paid_phase(pipeline_phase)
            return admission_rejection()
        except BaseException:
            if _JOBS.get(run_id) is new_job:
                _JOBS.pop(run_id, None)
            # 작업 등록 전이라 provider를 부르지 않았음이 확실하다.
            if pipeline_phase is not None:
                _cancel_paid_phase(pipeline_phase)
            raise
        handed_off = True
        return RedirectResponse(f"/progress/{run_id}", status_code=303)
    except BaseException:
        if is_paid:
            record_end(
                run_id=run_id,
                job=original_input.job,
                end_step=obs.END_STEP_GENERATE,
                cost_krw=upfront_cost,
                elapsed_sec=upfront_elapsed,
                model=_model_label(upfront_models),
                expected_state=lifecycle.STATE_RUNNING,
            )
        raise
    finally:
        # 슬롯 반환 앞에는 await를 두지 않는다. close 중 반복 취소가 들어와도 이 동기
        # 최외곽 경계가 먼저 정확히 한 번 실행된다. hand-off 뒤 슬롯은 Job이 소유한다.
        try:
            if not handed_off:
                _release_run_slot(slot_bucket_id)
        finally:
            try:
                if not handed_off:
                    public_ids.release(run_id)
            finally:
                await _close_posting_images(posting_images)
