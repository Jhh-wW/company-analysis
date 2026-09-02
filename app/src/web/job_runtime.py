"""조사 작업, 업로드 처리, 보고서 저장과 진행 상태."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional, TypeVar

from fastapi import File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.core import clock, paths
from src.core.constants import PIPELINE_FAILED_MESSAGE, PROGRESS_STEPS
from src.features.budget import expiry as link_expiry
from src.features.budget import spend_store
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget.constants import (
    BUSY_MESSAGE,
    JOB_KEEP_SEC,
    JOB_MAX_KEPT,
    PAID_PHASE_LEASE_SEC,
    SPEND_PHASE_OCR,
    SPEND_PHASE_PIPELINE,
)
from src.features.budget.sharing import (
    LINK_EXPIRED_MESSAGE,
    REPORT_LINK_MAX_AGE_DAYS,
    SHARED_LINK_HEADERS,
)
from src.features.business_candidate.constants import CANDIDATE_ATTEMPT_TTL_SEC
from src.features.cost_tracking import store as cost_store
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
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import PUBLIC_BUCKET
from src.features.storage import db as storage_db
from src.features.storage import job_interruptions
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination
from src.shared.report_evidence.constants import ReleaseMode
from src.web import (
    evaluation_mode,
    generation_singleflight,
    public_ids,
    request_helpers,
    runtime,
)
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
from src.features.report_access import constants as report_access_constants
from src.features.report_access.models import ReportAudience, ReportBindingResult
from src.features.report_access import store as report_access_store
_IMAGE_PIPELINE_UNSUPPORTED_ERROR = (
    "현재 기업분석 엔진은 공고 사진을 보고서에 사용하지 않습니다. "
    "사진을 지우고 회사명으로 조사해 주세요."
)
_UPLOAD_READ_CHUNK_BYTES = 64 * 1024
_UPLOAD_CLOSE_TIMEOUT_SEC = 1.0
_JOB_DRAIN_TIMEOUT_SEC = 240.0
_JOB_CANCEL_GRACE_SEC = 1.0
# 5분은 진행 화면 문구가 바뀌는 UX 기준일 뿐 강제 종료 계약이 아니다.
# 실제 provider 경계는 15회×180초를 포함하도록 기존 유료 phase lease 1시간을
# 쓰며, generation_singleflight도 같은 정본으로 새 호출 마감·heartbeat를 닫는다.
_JOB_EXECUTION_MAX_SEC = float(PAID_PHASE_LEASE_SEC)
_RETRY_AFTER_SEC = "3"
_PERSISTENCE_WARNING = (
    "이 보고서는 아직 저장되지 않았습니다. 현재 화면과 다운로드는 사용할 수 있지만 "
    "서버가 다시 시작되면 복구할 수 없습니다. 잠시 후 새로고침해 저장을 다시 시도하고, "
    "지금 PDF도 내려받아 보관해 주세요."
)


def _commit_report_connection(conn: Any) -> None:
    """legacy 보고서 fence 직후 commit하며 시험은 실패를 이 seam에 주입한다."""

    conn.commit()


def _report_transaction_matches_exactly(
    conn: Any,
    *,
    job: "Job",
    identity: build_identity_contract.EngineBuildIdentity,
) -> bool:
    """legacy commit 응답 손실 뒤 이번 보고서 영수증만 성공으로 인정한다."""

    if job.result is None or job.result.report is None:
        return False
    row = conn.execute(
        f"""
        SELECT corp_id, job, payload_json, engine_epoch_digest
        FROM {report_store.TABLE_REPORTS} WHERE report_id = ?
        """,
        (job.job_id,),
    ).fetchone()
    if row is None:
        return False
    return tuple(row) == (
        job.card.ref or job.card.legal_name,
        job.user_input.job,
        report_store.report_to_json(job.result.report),
        identity.epoch_digest,
    )


class JobAdmissionClosed(RuntimeError):
    """서버 종료가 시작되어 새 배경 작업을 받을 수 없음."""


class ReportStoreUnavailable(RuntimeError):
    """보고서 저장소를 읽을 수 없어 없음 여부를 판단할 수 없음."""


class JobExecutionDeadlineExceeded(RuntimeError):
    """한 작업이 전체 실행 절대 마감을 넘겨 더는 슬롯을 소유할 수 없음."""


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
    #: MEMBER 성공 3건 예약을 소유한 계정. 빈 값이면 관리자·LINK·공개 요청이다.
    member_email: str = ""
    #: LINK 실행 이력을 찾는 안전한 SHA-256 식별자. 원문 열쇠는 영속화하지 않는다.
    share_link_hash: str = ""
    #: 확인 카드·이미지 OCR에서 먼저 쓴 돈. 본조사 결과 비용과 마지막에 합친다.
    upfront_cost_krw: float = 0.0
    upfront_models: tuple[str, ...] = ()
    upfront_elapsed_sec: float = 0.0
    #: 회사 식별처럼 본조사 전에 발생한 AI 호출의 단계별 실제 사용량.
    upfront_cost_events: tuple[cost_store.AiCostEvent, ...] = ()
    #: 진짜 본조사라면 provider 호출 전 만든 표식. 데모는 None이다.
    paid_phase: Optional[PaidPhase] = None
    #: 유료 조사 자격. phase는 single-flight owner 확정 뒤에만 지연 생성되므로
    #: ``paid_phase is not None``으로 자격을 추측하면 waiter/cache hit이 누락된다.
    is_paid: bool = False
    paid_cap_krw: float | None = None
    #: 웹 adapter가 소유하는 lease·waiter·지연 phase 상태. 영속 정보는 DB에 있다.
    generation_session: Any = None
    #: scheduler가 생성 시작 전에 한 번 고정한 배포·빌드 신원. 저장 시 다시
    #: 환경에서 만들지 않으며 unknown도 이 Job이 끝날 때까지 그대로다.
    engine_build_identity: build_identity_contract.EngineBuildIdentity | None = None
    generation_abandoned: bool = False
    #: scheduler가 자리를 넘긴 단조 시각. worker 시작이 밀려도 전체 한 시간이
    #: 새로 시작되지 않게 single-flight와 실행 supervisor가 함께 쓴다.
    execution_started_monotonic: float = 0.0
    #: 종료 drain이 provider task 취소 전에 중단 원장을 이미 커밋했는지.
    #: stubborn 강제 정리가 같은 행을 위해 무거운 schema 연결을 다시 열지 않는다.
    shutdown_interruption_persisted: bool = False
    #: drain의 stubborn 정리와 task 자체 finally가 경합해도 종료 이력·비용·슬롯
    #: 묶음을 두 번 실행하지 않게 하는 마지막 표식.
    shutdown_cleanup_completed: bool = False
    #: 예약한 동시 실행 자리. 작업과 종료 정리가 같은 자리를 두 번 풀지 않게 쓴다.
    slot_bucket_id: str = ""
    slot_released: bool = False
    paid_phase_settled: bool = False
    #: 보고서가 DB에 남았는지. None은 보고서가 없거나 아직 시도 전이다.
    report_persisted: Optional[bool] = None
    #: 불변 Content·Delivery·PDF artifact가 함께 확정됐는지.
    delivery_persisted: Optional[bool] = None
    #: 작업 입장 때 확정한 닫힌 열람 소유권. None은 아직 입장하지 않은
    #: 임시·중단 Job에만 허용하며 보고서 저장 경계는 반드시 거절한다.
    report_audience: ReportAudience | None = None
    #: 입장 응답 당시 관측한 PUBLIC grant 만료. 최종 권한 판정에는 쓰지 않는다.
    #: 같은 token을 다른 탭이 연장할 수 있으므로 정본은 마지막 DB 거래의 행이다.
    public_grant_expires_at: float = 0.0
    #: 한 번 고정한 Delivery 발급/만료 시각. 저장과 최종 출고가 같은 값을 써야
    #: 60일 권한 보장을 현재시각 추정으로 대신하지 않는다.
    delivery_issued_at: dt.datetime | None = None
    delivery_expires_at: dt.datetime | None = None
    delivery_content_id: str = ""
    delivery_artifact_id: str = ""
    #: single-flight waiter가 owner의 원본을 새로 만들지 않고 발급받는 결속.
    delivery_origin_content_id: str = ""
    delivery_origin_artifact_id: str = ""
    persistence_warning: str = ""

    @property
    def requires_public_report_grant(self) -> bool:
        """PUBLIC 여부는 닫힌 audience에서만 계산한다."""

        return self.report_audience is ReportAudience.PUBLIC


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
    cost_events: tuple[cost_store.AiCostEvent, ...] = ()


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


def _requires_public_report_grant(
    resolved_track: tuple[share_tracks.Track, str, float | None],
) -> bool:
    """로그인 소유자가 없는 실행은 브라우저별 열람 난수를 요구한다.

    로컬 실시간 평가는 비용 한도를 재사용하려고 ``Track.ADMIN``을 쓰지만 로그인한
    관리자는 아니다. 비용 갈래를 곧 열람 신원으로 해석하면 조사는 완료·과금되고도
    progress와 결과는 404가 된다. 엄격한 loopback 판정이 만든 전용 bucket에만
    PUBLIC과 같은 브라우저 결속을 발급해 두 책임을 분리한다.
    """

    track, bucket, _cap = resolved_track
    return track is share_tracks.Track.PUBLIC or bool(
        track is share_tracks.Track.ADMIN
        and bucket == evaluation_mode.LOCAL_BUCKET
        and evaluation_mode.enabled()
    )


def _report_audience_for_track(
    resolved_track: tuple[share_tracks.Track, str, float | None],
) -> ReportAudience:
    """입장이 확정한 비용 track을 닫힌 열람 소유권으로 바꾼다."""

    track = resolved_track[0]
    # Track은 문자열 Enum이라 원시 문자열과 값·hash가 같을 수 있다. 여기서
    # exact type을 닫지 않으면 "member" 같은 느슨한 입력도 정식 MEMBER
    # 입장으로 승격된다. 입장 시점의 typed 결정만 이후 저장 권위로 운반한다.
    if type(track) is not share_tracks.Track:
        raise TypeError("보고서 입장에는 닫힌 비용 track이 필요합니다")
    if _requires_public_report_grant(resolved_track):
        return ReportAudience.PUBLIC
    mapping = {
        share_tracks.Track.MEMBER: ReportAudience.MEMBER,
        share_tracks.Track.LINK: ReportAudience.LINK,
        share_tracks.Track.ADMIN: ReportAudience.ADMIN,
    }
    try:
        return mapping[track]
    except KeyError as exc:
        raise TypeError("알 수 없는 보고서 입장 track입니다") from exc


#: 재시도가 의미 없을 때 안내 화면 버튼이 향할 기본 출구.
DEFAULT_EXIT_URL = "/"
DEFAULT_EXIT_LABEL = "처음 화면으로"


def retry_or_exit(
    request: Request,
    *,
    retry_label: str,
    fallback_url: str = DEFAULT_EXIT_URL,
    fallback_label: str = DEFAULT_EXIT_LABEL,
) -> dict[str, object]:
    """안내 화면 버튼에 실을 값을 정해 template context 조각으로 돌려준다.

    «이 화면을 그대로 다시 여는 것»이 진짜 재시도인 일시 장애 화면들이 쓴다.

    ★ 왜 현재 주소를 문자열로 만들어 넣지 않는가
      주소에는 조사 번호·보고서 번호가 들어 있다. 그 값을 화면에 되비추지
      않는 것이 이미 시험으로 고정된 계약이다(``test_survives_restart.py`` —
      「없는 진행번호의 410은 번호나 내부정보를 반사하지 않는다」). 그래서
      같은 화면 재요청은 빈 ``href``(= HTML에서 «지금 이 주소»)로 표현하고,
      템플릿이 그것을 «의도된 새로고침»으로 알아보게 ``retry_same_page``를
      같이 넘긴다.

    ★ 왜 POST를 갈라내는가
      POST로 들어온 요청의 주소를 버튼에 걸면 브라우저는 그 주소를 GET으로
      연다. ``/notion/{id}``처럼 POST만 있는 경로는 405 평문이 뜬다. 그래서
      POST일 때는 안전한 출구로 접고 글자도 같이 바꿔 「다시 확인하면
      달라진다」는 거짓 기대를 주지 않는다.
    """
    if request.method != "GET":
        return {
            "retry_url": fallback_url,
            "retry_label": fallback_label,
            "retry_same_page": False,
        }
    return {"retry_url": "", "retry_label": retry_label, "retry_same_page": True}


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
                **retry_or_exit(request, retry_label="다시 확인"),
            ),
            status_code=503,
        )
    )

async def _await_worker_after_cancel(
    worker: asyncio.Task[_WorkerResult],
    *,
    execution_deadline_monotonic: float | None = None,
) -> _WorkerResult:
    """반복 취소는 worker에 전파하지 않되 명시된 전체 마감은 지킨다.

    ``asyncio.to_thread``의 Task를 취소해도 이미 시작한 provider 스레드는 멈추지
    않는다. 정리가 기다리는 동안 두 번째 취소가 와도 계속 shield해야 실제 호출 수와
    비용 표식이 먼저 풀리지 않는다. 다만 Job 전체 절대 마감까지 무시하면 바깥 취소
    한 번으로 1시간 supervisor를 우회할 수 있으므로 Job 호출자는 같은 마감을 넘긴다.
    """
    while not worker.done():
        timeout: float | None = None
        if execution_deadline_monotonic is not None:
            timeout = max(
                0.0,
                execution_deadline_monotonic - time.monotonic(),
            )
            if timeout <= 0:
                raise JobExecutionDeadlineExceeded(
                    "조사 전체 실행 제한시간을 넘었습니다"
                )
        try:
            done, _pending = await asyncio.wait((worker,), timeout=timeout)
        except asyncio.CancelledError:
            continue
        if worker not in done:
            raise JobExecutionDeadlineExceeded(
                "조사 전체 실행 제한시간을 넘었습니다"
            )
    return worker.result()


def _mark_job_execution_deadline(
    job: Job,
    worker: asyncio.Task[_WorkerResult] | None,
) -> None:
    """어느 await 경로에서 마감됐든 같은 취소·원장 표식을 남긴다."""

    logger.error("파이프라인 전체 실행 마감 초과 job_id=%s", job.job_id)
    if job.generation_session is not None:
        job.generation_session.cancel_waiter()
        job.generation_session.abandon()
        job.generation_abandoned = True
    if worker is not None and not worker.done():
        worker.cancel()
    job.result = RunResult(
        outcome=Outcome.FAILED,
        message=PIPELINE_FAILED_MESSAGE,
        billing_uncertain=job.paid_phase is not None,
    )
    try:
        job_interruptions.persist(
            job_id=job.job_id,
            interrupted_at=clock.iso_now_kst(),
            reason="execution_deadline",
        )
    except BaseException:  # noqa: BLE001 — 비용·슬롯 마감은 계속한다
        logger.exception(
            "실행 마감 초과 상태를 저장하지 못했습니다 job_id=%s",
            job.job_id,
        )


async def _await_worker_before_execution_deadline(
    job: Job,
    worker: asyncio.Task[_WorkerResult],
) -> _WorkerResult:
    """worker를 전체 작업 절대 마감까지만 기다리고 wrapper는 임의 취소하지 않는다.

    ``asyncio.wait_for(to_thread(...))``는 timeout과 worker가 직접 던진
    ``TimeoutError``를 구분하기 어렵고 wrapper를 먼저 취소한다. 완료 집합을 직접
    확인하면 provider 예외는 그대로 보존하면서 supervisor 마감만 별도 분류할 수
    있다. 실제 thread는 Python에서 강제 종료할 수 없으므로 호출자는 세션을 먼저
    abandon해 이후 provider를 닫고 wrapper를 정리한다.
    """

    started = job.execution_started_monotonic
    if started <= 0:
        started = time.monotonic()
        job.execution_started_monotonic = started
    remaining = max(
        0.0,
        started + _JOB_EXECUTION_MAX_SEC - time.monotonic(),
    )
    done, _pending = await asyncio.wait((worker,), timeout=remaining)
    if worker not in done:
        raise JobExecutionDeadlineExceeded(
            "조사 전체 실행 제한시간을 넘었습니다"
        )
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


def _job_is_paid(job: Job) -> bool:
    """지연 phase 생성 전의 owner·waiter도 유료 Job으로 판정한다."""

    return bool(job.is_paid or job.paid_phase is not None)


def _install_job_paid_phase(job: Job, ticket: PaidPhase) -> None:
    """single-flight owner가 얻은 phase를 마감 주체인 Job에 즉시 인계한다."""

    if job.paid_phase is not None and job.paid_phase != ticket:
        raise RuntimeError("한 조사에 본조사 비용 phase를 두 번 설치할 수 없습니다")
    job.paid_phase = ticket


def _prepare_generation_session(job: Job) -> None:
    """배경 task 시작 전에 취소 신호를 받을 요청 로컬 세션을 만든다."""

    if job.engine_build_identity is None:
        job.engine_build_identity = (
            build_identity_contract.process_engine_build_identity()
        )
    if (
        not job.is_paid
        or job.paid_phase is not None
        or job.generation_session is not None
        or not bool(
            getattr(runtime._PIPELINE, "supports_deferred_paid_phase", False)
        )
    ):
        return
    job.generation_session = generation_singleflight.GenerationSession(
        run_id=job.job_id,
        share_key=job.share_key,
        billing_bucket_id=(
            job.slot_bucket_id or spend_store.bucket_id(job.share_key)
        ),
        cap_krw=job.paid_cap_krw,
        on_paid_phase=lambda ticket: _install_job_paid_phase(job, ticket),
        build_identity=job.engine_build_identity,
    )


def _run_pipeline_worker(job: Job) -> RunResult:
    """무료 preflight→owner 확정→지연 비용 phase 순서로 pipeline을 돌린다."""

    # 직접 worker를 부르는 복구·시험 경로도 scheduler와 같은 시작 영수증을
    # 먼저 받는다. 이미 준비된 Job에는 아무 변화가 없는 멱등 호출이다.
    _prepare_generation_session(job)
    if job.is_paid or job.paid_phase is not None:
        # deferred capability가 없는 교체 pipeline과 이미 예약된 legacy phase도
        # epoch 영수증 없이 provider로 빠져나가면 안 된다.
        _frozen_job_build_identity(job)

    # 기존 단위시험·즉시 예약 호출자는 엣 경계를 그대로 통과한다.
    if job.paid_phase is not None:
        return _call_paid_provider(
            job.paid_phase,
            runtime._PIPELINE.run,
            job.user_input,
            job.card,
            _mark_step(job),
        )
    if not job.is_paid:
        return runtime._PIPELINE.run(job.user_input, job.card, _mark_step(job))

    # 새 계약을 모르는 교체 pipeline은 provider를 무표식으로 보내게 두지
    # 않고 기존처럼 전체 phase를 먼저 연다. 운영 RealPipeline만 아래의
    # DART snapshot→owner→지연 phase capability를 명시한다.
    if not bool(
        getattr(runtime._PIPELINE, "supports_deferred_paid_phase", False)
    ):
        ticket = _begin_paid_phase(
            run_id=job.job_id,
            phase=SPEND_PHASE_PIPELINE,
            share_key=job.share_key,
            cap_krw=job.paid_cap_krw,
        )
        if ticket is None:
            raise generation_singleflight.PaidGenerationAdmissionUnavailable(
                "본조사 비용 phase를 예약하지 못했습니다"
            )
        _install_job_paid_phase(job, ticket)
        return _call_paid_provider(
            ticket,
            runtime._PIPELINE.run,
            job.user_input,
            job.card,
            _mark_step(job),
        )

    session = job.generation_session
    if session is None:  # pragma: no cover - capability 방어선
        raise RuntimeError("지연 본조사 조정 세션이 없습니다")
    try:
        with generation_coordination.activate(session.callbacks):
            return runtime._PIPELINE.run(
                job.user_input,
                job.card,
                _mark_step(job),
            )
    finally:
        # ContextVar token은 설치한 같은 worker thread에서 닫아야 한다.
        session.close_provider_context()


def _report_requires_atomic_completion(report: Report) -> bool:
    """FULL 생성물만 저장·출고 실패에도 메모리 결과를 그대로 두지 않는다.

    demo·v1·SHADOW·ENFORCE_NO_PARTIAL은 이 계약 밖이며 audience와 무관하게
    기존 동작(저장 실패 뒤에도 LINK·ADMIN 임시 미리보기가 메모리에 남는 것)을
    그대로 유지한다 — 「FULL 밖 demo/non-FULL 동작은 불변이다」.
    """

    return report.release_mode == ReleaseMode.FULL.value


def _apply_reused_delivery_origin(job: Job, result: RunResult) -> None:
    """모든 worker 완료 경로에서 content·artifact 원본을 함께 인계한다."""

    origin_ids = (
        result.reused_content_snapshot_id,
        result.reused_artifact_id,
    )
    if bool(origin_ids[0]) != bool(origin_ids[1]):
        raise TypeError("재사용 보고서의 content와 artifact 결속이 불완전합니다")
    job.delivery_origin_content_id = origin_ids[0]
    job.delivery_origin_artifact_id = origin_ids[1]


async def _run_job(job: Job) -> None:
    """뒤에서 파이프라인을 돌리며 진행 상황을 갱신한다.

    ★ 파이프라인은 오래 걸리고 중간에 막힐 수 있다. 그대로 부르면 그동안
      **다른 사용자의 화면까지 멈춘다.** 그래서 별도 실행 흐름에서 돌린다.
    ★ 5분은 진행 화면 안내가 바뀌는 시점이다. 강제 종료는 기존 15회×180초
      provider 계약과 유료 phase lease를 함께 만족하는 전체 1시간에서 한다.
    """
    started = time.perf_counter()
    if job.execution_started_monotonic <= 0:
        job.execution_started_monotonic = time.monotonic()
    worker: Optional[asyncio.Task[RunResult]] = None
    shutdown_cleanup_only = False
    try:
        # schedule 검사 뒤 task가 실제로 실행되기 전 shutdown이 시작될 수 있다.
        # provider를 한 번도 부르지 않은 phase는 취소하고 비용 불확실성을 만들지 않는다.
        if not _ACCEPTING_JOBS or (
            _job_is_paid(job) and not _job_work_admitted(job)
        ):
            if job.paid_phase is not None and not job.paid_phase_settled:
                job.paid_phase_settled = True
                _cancel_paid_phase(job.paid_phase)
            job.result = RunResult(
                outcome=Outcome.FAILED,
                message=PIPELINE_FAILED_MESSAGE,
            )
            return
        worker = asyncio.create_task(asyncio.to_thread(_run_pipeline_worker, job))
        job.result = await _await_worker_before_execution_deadline(job, worker)
        if not isinstance(job.result, RunResult):
            raise TypeError("파이프라인 결과 계약이 올바르지 않습니다")
        _apply_reused_delivery_origin(job, job.result)
    except JobExecutionDeadlineExceeded:
        # configured provider는 마감 4분 전부터 새로 시작할 수 없고 단일 호출
        # timeout은 180초다. 여기까지 살아 있다면 정상 지연이 아니라 계약 위반·
        # 멈춘 로컬 처리다. 세션을 먼저 닫아 background thread가 뒤늦게 다음
        # provider를 보내지 못하게 한 뒤 슬롯과 비용을 보수적으로 마감한다.
        _mark_job_execution_deadline(job, worker)
    except asyncio.CancelledError:
        if _SHUTTING_DOWN:
            # 종료 제한시간이 지난 뒤에는 죽일 수 없는 provider 스레드를 더 기다리지
            # 않는다. 실제 비용은 알 수 없으므로 아래 마감에서 미확정으로 fail-closed한다.
            if worker is not None and not worker.done():
                if job.generation_session is not None:
                    job.generation_session.cancel_waiter()
                    job.generation_session.abandon()
                    job.generation_abandoned = True
                worker.cancel()
            job.result = RunResult(
                outcome=Outcome.FAILED,
                message=PIPELINE_FAILED_MESSAGE,
                billing_uncertain=job.paid_phase is not None,
            )
            shutdown_cleanup_only = True
            raise
        # 사용자가 창을 닫아도 provider 스레드는 멈추지 않는다. 끝날 때까지 슬롯을
        # 유지한다. 정리 중 다시 취소돼도 shield를 반복해 실제 worker를 기다린다.
        try:
            if worker is None:
                raise RuntimeError("파이프라인 worker가 만들어지지 않았습니다")
            job.result = await _await_worker_after_cancel(
                worker,
                execution_deadline_monotonic=(
                    job.execution_started_monotonic + _JOB_EXECUTION_MAX_SEC
                ),
            )
            if not isinstance(job.result, RunResult):
                raise TypeError("파이프라인 결과 계약이 올바르지 않습니다")
            _apply_reused_delivery_origin(job, job.result)
        except JobExecutionDeadlineExceeded:
            _mark_job_execution_deadline(job, worker)
        except BaseException:  # noqa: BLE001 — 끝내 결과를 모르면 fail-closed
            if job.generation_session is not None:
                job.generation_session.abandon()
                job.generation_abandoned = True
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
        if shutdown_cleanup_only:
            # process 종료 취소에는 전체 78표 bootstrap·보고서 출고를 새로
            # 시작하지 않는다. 앞서 커밋한 중단 표식과 최소 비용/슬롯 정리만
            # 끝내고 취소를 그대로 전파한다.
            _force_shutdown_cleanup(job)
            raise asyncio.CancelledError
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
                    # 실제 게이트가 본 닫힌 사유는 지우지 않는다.
                    # recording이 FAILED+billing_uncertain 조합으로
                    # 생명주기 행과 부속 진단을 같이 남긴다.
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
            cost_events = (*job.upfront_cost_events, *job.result.ai_cost_events)
            job.result = replace(
                job.result,
                cost_krw=job.upfront_cost_krw + pipeline_cost,
                model=_model_label(models),
                ai_cost_events=cost_events,
            )
            # 내부 AI 원가는 실패·GATE_STOPPED에도 그대로 보존한다. 고객 청구는
            # 이 시점에는 항상 0이며, 해시 결속 자동출고 뒤 reports 경계에서만
            # 별도의 eligibility/청구 결정을 붙인다.
            with storage_db.connect() as conn:
                cost_store.record_run_costs(
                    conn,
                    run_id=job.job_id,
                    outcome=job.result.outcome,
                    internal_ai_cost_krw=job.result.cost_krw,
                    events=job.result.ai_cost_events,
                )
            if any(
                event.model_id.lower().startswith("claude")
                for event in job.result.ai_cost_events
            ):
                try:
                    with storage_db.connect() as conn:
                        dashboard_store.record_external_status(
                            conn,
                            provider="Anthropic",
                            status="normal",
                            last_success_at=clock.iso_now_kst(),
                            now_iso=clock.iso_now_kst(),
                        )
                except Exception:  # noqa: BLE001 — 관측 누락이 보고서 저장을 막지 않는다
                    logger.exception("Anthropic 성공 관측을 저장하지 못했습니다")
            # FULL 생성물은 파이프라인 완료 시점이 아니라 저장·출고 결과를
            # 알고 난 뒤에만 lifecycle 최종 행을 남긴다. lifecycle의 final
            # 상태는 되돌릴 수 없어서(lifecycle.finalize_once) 여기서 먼저
            # 쓰면 이후 저장·출고가 깨져도 이력은 「완주」로 영구히 남는다.
            # 이 판정은 지금 시점의 job.result만 보고
            # 정한다 — 아래 실패 정리가 job.result를 FAILED로 되돌리면
            # report가 사라져 이후에는 다시 판정할 수 없기 때문이다.
            # ★ 내부 AI 원가 기록(cost_store.record_run_costs, 위)은 이
            #   순서와 무관하게 그대로 파이프라인 직후에 남는다 — 옮기면 안
            #   되는 것은 원가 기록이지 lifecycle 기록이 아니다.
            requires_full_completion = (
                job.result.outcome is Outcome.REPORT
                and job.result.report is not None
                and _report_requires_atomic_completion(job.result.report)
            )
            if not requires_full_completion:
                record_run(
                    job.user_input,
                    job.result,
                    job.upfront_elapsed_sec + time.perf_counter() - started,
                    run_id=job.job_id,
                    expected_state=(
                        lifecycle.STATE_RUNNING if _job_is_paid(job) else None
                    ),
                )
            delivery_required = False
            if job.result.outcome is Outcome.REPORT and job.result.report is not None:
                job.delivery_issued_at = clock.now_kst()
                job.delivery_expires_at = job.delivery_issued_at + dt.timedelta(
                    days=REPORT_LINK_MAX_AGE_DAYS
                )
                try:
                    delivery_required = await asyncio.to_thread(
                        _require_report_delivery,
                        job,
                    )
                except Exception:  # noqa: BLE001 - 새 보고서를 legacy로 저장하지 않는다
                    job.delivery_persisted = False
                    logger.exception(
                        "불변 보고서 delivery 의무 표식 실패 job_id=%s",
                        job.job_id,
                    )
            # FULL 생성물은 audience와 무관하게 저장·출고 실패 뒤 메모리
            # 결과를 그대로 남기지 않는다.
            report_saved = (
                _save_report(job)
                if job.result.outcome is not Outcome.REPORT or delivery_required
                else False
            )
            if (
                job.result.outcome is Outcome.REPORT
                and not report_saved
                and (job.requires_public_report_grant or requires_full_completion)
            ):
                # PUBLIC과 FULL 생성물은 메모리 보고서만 보여 주는 임시 성공이
                # 될 수 없다. 저장 transaction이 되돌아갔으므로 최종 결과도
                # 실패·무차감으로 닫아 출고 adapter가 호출될 여지를 없앤다.
                job.delivery_persisted = False
                if delivery_required:
                    try:
                        await asyncio.to_thread(_fail_report_delivery, job)
                    except Exception:  # noqa: BLE001 — required 표식만으로도 공개는 닫힌다
                        logger.exception(
                            "보고서 저장 실패의 delivery 표식 마감 실패 job_id=%s",
                            job.job_id,
                        )
                job.result = replace(
                    job.result,
                    outcome=Outcome.FAILED,
                    report=None,
                    message=PIPELINE_FAILED_MESSAGE,
                    charged=False,
                )
            delivery_content_id = ""
            if job.result.outcome is Outcome.REPORT and report_saved:
                try:
                    job.delivery_persisted = await asyncio.to_thread(
                        _finalize_report_delivery,
                        job,
                    )
                    delivery_content_id = (
                        job.delivery_content_id
                        if job.delivery_persisted is True
                        else ""
                    )
                except Exception:  # noqa: BLE001 - 구형 보고서 저장과 다른 경계
                    job.delivery_persisted = False
                    logger.exception(
                        "불변 보고서 delivery 확정 실패 job_id=%s",
                        job.job_id,
                    )
                    try:
                        await asyncio.to_thread(_fail_report_delivery, job)
                    except Exception:  # noqa: BLE001 - required 표식만으로도 fail-closed
                        logger.exception(
                            "불변 보고서 delivery 실패 표식 실패 job_id=%s",
                            job.job_id,
                        )
                    if requires_full_completion:
                        # FULL 원자 출고는 content·delivery·artifact·자동승인·
                        # charge·권한 결속이 한 거래로 rollback됐다. 화면·PDF·
                        # single-flight가 그 실패를 REPORT 성공으로 잘못 읽지
                        # 않도록 audience와 무관하게 결과를 닫는다.
                        job.result = replace(
                            job.result,
                            outcome=Outcome.FAILED,
                            report=None,
                            message=PIPELINE_FAILED_MESSAGE,
                            charged=False,
                        )
            elif job.result.outcome is Outcome.REPORT and delivery_required:
                job.delivery_persisted = False
                try:
                    await asyncio.to_thread(_fail_report_delivery, job)
                except Exception:  # noqa: BLE001 - required 표식만으로도 fail-closed
                    logger.exception(
                        "보고서 저장 실패의 delivery 표식 마감 실패 job_id=%s",
                        job.job_id,
                    )
            if requires_full_completion:
                # 저장·출고 실패 정리가 위에서 이미 job.result를 FAILED로
                # 되돌렸을 수 있다. lifecycle 최종 행은 이제야 실제 결과를
                # 반영해 한 번만 쓴다 — 파이프라인 직후 값을 썼다가 나중에
                # 다시 쓸 수는 없다(final 상태는 불변).
                record_run(
                    job.user_input,
                    job.result,
                    job.upfront_elapsed_sec + time.perf_counter() - started,
                    run_id=job.job_id,
                    expected_state=(
                        lifecycle.STATE_RUNNING if _job_is_paid(job) else None
                    ),
                )
            if (
                job.generation_session is not None
                and not job.generation_abandoned
            ):
                try:
                    if delivery_content_id:
                        await asyncio.to_thread(
                            job.generation_session.complete,
                            delivery_content_id,
                            job.delivery_artifact_id,
                            cache_eligible=(
                                job.result.generation_cache_eligible
                            ),
                        )
                    elif job.generation_session.owns_generation:
                        await asyncio.to_thread(
                            job.generation_session.fail,
                            "generation_failed",
                        )
                except Exception:  # noqa: BLE001 - 새 delivery는 유지하고 lease는 만료로 회수
                    logger.exception(
                        "보고서 single-flight 마감 실패 job_id=%s",
                        job.job_id,
                    )
                    job.generation_session.abandon()
            report_available = (
                job.result.outcome is Outcome.REPORT
                and report_saved
                and job.delivery_persisted is True
            )
            if job.member_email:
                try:
                    with storage_db.connect() as conn:
                        dashboard_store.settle_member_run(
                            conn,
                            run_id=job.job_id,
                            succeeded=report_available,
                            report_id=(job.job_id if report_available else ""),
                            now_iso=clock.iso_now_kst(),
                            outcome=job.result.outcome.value,
                            company_type=(
                                dashboard_store.company_type_from_report(job.result.report.corp_type)
                                if job.result.report is not None
                                else dashboard_store.COMPANY_UNDECIDED
                            ),
                            cost_krw=job.result.cost_krw,
                            cost_uncertain=job.result.billing_uncertain,
                        )
                except Exception:  # noqa: BLE001 — unconfirmed reservation must not reopen itself
                    logger.exception("MEMBER 성공 보고서 사용량을 마감하지 못했습니다")
            if job.share_link_hash and not report_available:
                _finish_link_job(
                    job,
                    status=share_store.RUN_STATUS_STOPPED,
                    stop_step=_link_stop_step(job.result.outcome),
                    stop_reason=_link_stop_reason(job.result.outcome),
                )
        finally:
            # 비용 마감과 이력 정리를 시도한 뒤에만 자리를 돌려준다. 중간에 어떤
            # 예외가 나도 이 최외곽 finally는 실행되어 자리가 영구히 새지 않는다.
            # 화면의 ``finished``는 본문·PDF artifact 확정 시도보다 먼저
            # 열리지 않아야 최초 GET이 구형 재렌더 경로로 빠지지 않는다.
            job.finished = True
            job.finished_at = time.monotonic()
            _ensure_link_job_closed(job)
            _release_job_slot(job)


def _release_job_slot(job: Job) -> None:
    """Job이 소유한 동시 실행 자리를 동기 경계에서 정확히 한 번 반환한다."""
    if job.slot_released:
        return
    job.slot_released = True
    _release_run_slot(
        job.slot_bucket_id or spend_store.bucket_id(job.share_key)
    )


def _link_stop_step(outcome: Outcome) -> str:
    """파이프라인 종료값을 관리자 LINK 이력의 중단 단계에 맞춘다."""

    return {
        Outcome.NOT_FOUND: obs.END_STEP_IDENTIFY,
        Outcome.REJECT_PUBLIC: obs.END_STEP_JUDGE,
        Outcome.REJECT_NO_DISCLOSURE: obs.END_STEP_JUDGE,
        Outcome.POSTING_DISCARDED: obs.END_STEP_POSTING,
        Outcome.GATE_STOPPED: obs.END_STEP_GATE,
        Outcome.FAILED: obs.END_STEP_GENERATE,
    }.get(outcome, obs.END_STEP_GENERATE)


def _link_stop_reason(outcome: Outcome) -> str:
    """보고서 원문·예외문을 저장하지 않는 안정적인 LINK 종료 사유."""

    return {
        Outcome.NOT_FOUND: "company_not_found",
        Outcome.REJECT_PUBLIC: "unsupported_public_entity",
        Outcome.REJECT_NO_DISCLOSURE: "disclosure_not_available",
        Outcome.POSTING_DISCARDED: "posting_discarded",
        Outcome.GATE_STOPPED: "evidence_gate_stopped",
        Outcome.FAILED: "generation_failed",
    }.get(outcome, "generation_failed")


def _finish_link_job(
    job: Job,
    *,
    status: str,
    stop_step: str,
    stop_reason: str,
) -> bool:
    """LINK 실행을 종결한다. 원문 열쇠·입력 원문은 로그에 남기지 않는다."""

    if not job.share_link_hash:
        return False
    try:
        with storage_db.connect() as conn:
            return share_store.finish_run(
                conn,
                run_id=job.job_id,
                status=status,
                finished_at=clock.iso_now_kst(),
                stop_step=stop_step,
                stop_reason=stop_reason,
                internal_ai_cost_krw=(
                    job.result.cost_krw
                    if isinstance(job.result, RunResult)
                    else job.upfront_cost_krw
                ),
            )
    except Exception:  # noqa: BLE001 — 다른 종료 정리와 슬롯 반환은 계속한다
        logger.exception("LINK 생성 종료 이력을 저장하지 못했습니다 job_id=%s", job.job_id)
        return False


def _ensure_link_job_closed(job: Job) -> None:
    """예상 밖의 마감 예외에도 LINK 이력을 ``running``으로 방치하지 않는다."""

    if not job.share_link_hash:
        return
    try:
        with storage_db.connect() as conn:
            row = share_store.load_run(conn, job.job_id)
            if row is None or row.status != share_store.RUN_STATUS_RUNNING:
                return
            outcome = (
                job.result.outcome
                if isinstance(job.result, RunResult)
                else Outcome.FAILED
            )
            if not share_store.finish_run(
                conn,
                run_id=job.job_id,
                status=share_store.RUN_STATUS_STOPPED,
                finished_at=clock.iso_now_kst(),
                stop_step=_link_stop_step(outcome),
                stop_reason=_link_stop_reason(outcome),
                internal_ai_cost_krw=(
                    job.result.cost_krw
                    if isinstance(job.result, RunResult)
                    else job.upfront_cost_krw
                ),
            ):
                raise RuntimeError("LINK 생성 이력을 종결하지 못했습니다")
    except Exception:  # noqa: BLE001 — 슬롯 반환은 반드시 이어간다
        logger.exception("LINK 생성 이력을 확인하지 못했습니다 job_id=%s", job.job_id)


def _frozen_job_build_identity(job: Job) -> build_identity_contract.EngineBuildIdentity:
    """Job과 세션이 함께 운반한 생성 시작 신원을 한 벌로 확인한다."""

    job_identity = job.engine_build_identity
    session_identity = getattr(
        job.generation_session,
        "engine_build_identity",
        None,
    )
    if (
        job_identity is not None
        and session_identity is not None
        and job_identity != session_identity
    ):
        raise RuntimeError("Job과 생성 세션의 엔진 빌드 신원이 다릅니다")
    identity = session_identity or job_identity
    if identity is None:
        raise RuntimeError("Job에 생성 시작 epoch 영수증이 없습니다")
    try:
        exact = build_identity_contract.require_exact_engine_build_identity(identity)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Job의 엔진 빌드 신원 형식이 올바르지 않습니다") from exc
    build_identity_contract.assert_engine_build_identity_current(exact)
    if not exact.cache_usable:
        raise RuntimeError("Job의 생성 시작 epoch를 캐시·출고에 사용할 수 없습니다")
    return exact


def _finalize_report_delivery(job: Job) -> bool:
    """보고서 생성 worker 안에서만 최초 승인 PDF와 delivery를 확정한다."""

    if (
        not isinstance(job.result, RunResult)
        or job.result.outcome is not Outcome.REPORT
        or job.result.report is None
    ):
        return False
    # reports는 job_runtime을 import하므로 module 로드 중 순환을 피해
    # worker가 실제 완료될 때 adapter 함수만 늦게 읽는다.
    from src.web.routers import reports as reports_router  # noqa: PLC0415

    models = _model_tuple(
        *(event.model_id for event in job.result.ai_cost_events),
        job.result.model,
    )
    generation_session = job.generation_session
    cache_namespace = (
        generation_session.cache_namespace
        if generation_session is not None
        else None
    )
    preflight_identity_digest = (
        generation_session.preflight_identity_digest
        if generation_session is not None
        else ""
    )
    public_delivery = reports_router.finalize_new_report_delivery(
        report_id=job.job_id,
        corp_id=job.card.ref or job.card.legal_name,
        billing_bucket_id=(
            job.slot_bucket_id or spend_store.bucket_id(job.share_key)
        ),
        report=job.result.report,
        actual_models=models,
        # 옛 layer1은 Report 값만 돌려주고 불변 content ID는 운반하지 않는다.
        # 실제 원본 ID가 있을 때만 Delivery에 cache-origin을 기록한다.
        reused_from_cache=bool(job.delivery_origin_content_id),
        dart_receipt_numbers=job.result.dart_receipt_numbers,
        financial_payload_digest=job.result.financial_payload_digest,
        reuse_content_snapshot_id=job.delivery_origin_content_id,
        reuse_artifact_id=job.delivery_origin_artifact_id,
        cache_namespace=cache_namespace,
        preflight_identity_digest=preflight_identity_digest,
        cache_eligible=job.result.generation_cache_eligible,
        completed_at=job.delivery_issued_at,
        public_access_run_id=(
            job.job_id if job.requires_public_report_grant else ""
        ),
        engine_build_identity=_frozen_job_build_identity(job),
        reuse_singleflight_key=(
            generation_session.completed_reuse_key
            if generation_session is not None
            else None
        ),
    )
    job.delivery_content_id = public_delivery.content.content_id
    if public_delivery.artifact is None:
        raise RuntimeError("확정 delivery의 PDF artifact가 없습니다")
    job.delivery_artifact_id = public_delivery.artifact.artifact_id
    return True


def _require_report_delivery(job: Job) -> bool:
    """한 번 고정한 완료 경계 시각으로 새 delivery 의무를 확정한다.

    worker가 ``delivery_issued_at``을 잡은 뒤 여기서 시계를 다시 읽으면 의무
    시작이 Delivery 완료보다 몇 μs 늦어져 정상 출고를 손상으로 오인한다. 이
    함수는 새 시각을 만들지 않고 저장·Delivery·grant fence가 공유할 값만 쓴다.
    """

    from src.web import report_delivery_adapter  # noqa: PLC0415

    required_at = job.delivery_issued_at
    if (
        required_at is None
        or required_at.tzinfo is None
        or required_at.utcoffset() is None
    ):
        raise report_delivery_adapter.DeliveryAdapterError(
            "보고서 완료 경계 시각에는 시간대가 필요합니다"
        )
    # raw 보고서 저장도 delivery보다 먼저 일어나므로 여기서 배포 연속성을 먼저
    # 확인한다. A에서 만든 본문을 B/unknown DB에 잠깐이라도 쓰지 않는다.
    report_delivery_adapter._assert_frozen_identity_is_current(
        _frozen_job_build_identity(job)
    )
    report_delivery_adapter.require_public_delivery(
        job.job_id,
        required_at=required_at,
    )
    return True


def _fail_report_delivery(job: Job) -> None:
    """완료 worker 바깥 예외도 새 보고서 표식을 required로 방치하지 않는다."""

    from src.web import report_delivery_adapter  # noqa: PLC0415

    report_delivery_adapter.fail_public_delivery(
        job.job_id,
        failure_code="artifact_finalization_failed",
        failed_at=clock.now_kst(),
    )


def stage_report_storage(conn: sqlite3.Connection, job: Job) -> None:
    """caller 소유 transaction에 보고서·projection·권한 결속을 준비한다.

    연결을 열거나 commit/rollback하지 않는다. 최종 불변 출고 경계는 이 함수를
    Delivery·artifact metadata·cache·charge와 같은 연결에서 호출할 수 있다.
    현재 독립 저장 호환 경로는 아래 ``_save_report`` wrapper만 사용한다.
    """

    if job.result is None or job.result.report is None:
        raise ValueError("저장할 보고서가 없습니다")
    frozen_identity = _frozen_job_build_identity(job)
    audience = job.report_audience
    if type(audience) is not ReportAudience:
        raise TypeError("보고서 저장 전에 닫힌 audience가 확정돼야 합니다")
    has_member = bool(str(job.member_email).strip())
    has_link = bool(str(job.share_link_hash).strip())
    if audience is ReportAudience.MEMBER:
        if not has_member or has_link:
            raise report_access_store.ReportAudienceConflict(
                "MEMBER 저장의 소유자 표식이 불완전합니다"
            )
    elif audience is ReportAudience.LINK:
        if not has_link or has_member:
            raise report_access_store.ReportAudienceConflict(
                "LINK 저장의 소유자 표식이 불완전합니다"
            )
    elif has_member or has_link:
        raise report_access_store.ReportAudienceConflict(
            "PUBLIC·ADMIN 저장에 다른 audience 표식을 섞을 수 없습니다"
        )
    delivery_expires_at: float | None = None
    if audience is ReportAudience.PUBLIC:
        if job.delivery_expires_at is None:
            raise report_access_store.PublicGrantBindingUnavailable(
                "PUBLIC 저장 전에 Delivery 만료 시각이 고정되지 않았습니다"
            )
        delivery_expires_at = job.delivery_expires_at.timestamp()
    if not report_store.insert_new(
        conn,
        report_id=job.job_id,
        corp_id=job.card.ref or job.card.legal_name,
        job=job.user_input.job,
        report=job.result.report,
        engine_epoch_digest=frozen_identity.epoch_digest,
    ):
        raise RuntimeError("공개 보고서 ID가 이미 사용 중입니다")
    dashboard_store.register_report(
        conn,
        report_id=job.job_id,
        corp_type=job.result.report.corp_type,
        now_iso=clock.iso_now_kst(),
        payload_json=report_store.report_to_json(job.result.report),
    )
    binding = report_access_store.bind_report(
        conn,
        run_id=job.job_id,
        report_id=job.job_id,
        expected_audience=audience,
        delivery_expires_at=delivery_expires_at,
    )
    if type(binding) is not ReportBindingResult:
        raise TypeError("보고서 저장은 정확한 typed 결속 결과만 받습니다")
    if binding.audience is not audience:
        raise report_access_store.ReportAudienceConflict(
            "저장 audience와 DB 결속 결과가 다릅니다"
        )
    if audience is ReportAudience.PUBLIC and not binding.bound:
        raise report_access_store.PublicGrantBindingUnavailable(
            "PUBLIC 실행에 결속된 브라우저 grant가 없습니다"
        )
    if audience is ReportAudience.MEMBER and not binding.bound:
        raise RuntimeError("MEMBER 실행에 결속된 불변 계정 소유권이 없습니다")
    if audience in (ReportAudience.LINK, ReportAudience.ADMIN) and binding.bound:
        raise report_access_store.ReportAudienceConflict(
            "LINK·ADMIN 저장에 report_access 결속을 붙일 수 없습니다"
        )
    job_interruptions.delete(conn, job.job_id)
    if audience is ReportAudience.LINK and not share_store.finish_run(
        conn,
        run_id=job.job_id,
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at=clock.iso_now_kst(),
        report_id=job.job_id,
        internal_ai_cost_krw=job.result.cost_krw,
    ):
        raise RuntimeError("LINK 보고서 생성 이력을 연결하지 못했습니다")
    # caller가 나중에 같은 transaction에 Delivery·charge를 더 쓰더라도,
    # 보고서 staging 자체는 시작 epoch가 바뀐 상태로 반환되지 않는다.
    build_identity_contract.assert_engine_build_identity_current(frozen_identity)


def _save_report(job: Job) -> bool:
    """기존 호출자를 위해 staging을 독립 transaction으로 확정한다.

    ★ 이게 없으면 **서버를 끄는 순간 보고서가 사라진다** (메모리에만 있었다).
    ★ LINK·ADMIN의 임시 미리보기는 저장 실패 경고와 함께 메모리에 남을 수 있다.
      PUBLIC은 다르다. 브라우저 grant와 60일 Delivery를 함께 확정하지 못한 본문을
      성공 화면으로 열면 저장·출고·차감이 서로 다른 상태가 되므로 전체 실패한다.
    """
    if job.result is None or job.result.report is None:
        return False
    try:
        with storage_db.connect_explicit_commit() as conn:
            frozen_identity = _frozen_job_build_identity(job)
            build_identity_contract.assert_engine_build_identity_current(
                frozen_identity
            )
            stage_report_storage(conn, job)
            # transaction 안의 여러 INSERT 도중 배포가 바뀌었으면 commit 전에
            # 모두 rollback한다.
            build_identity_contract.assert_engine_build_identity_current(
                frozen_identity
            )
            try:
                _commit_report_connection(conn)
            except sqlite3.Error:
                # SQLite가 실제 commit한 뒤 응답만 잃은 경우에는 rollback이
                # no-op이다. 정확한 report ID·본문·epoch가 모두 같은 때만
                # 성공으로 복구하며, 행이 없거나 다르면 원래 오류를 보존한다.
                conn.rollback()
                if not _report_transaction_matches_exactly(
                    conn,
                    job=job,
                    identity=frozen_identity,
                ):
                    raise
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
            if dashboard_store.report_is_trashed(conn, report_id):
                return None
            state = dashboard_store.get_report_state(conn, report_id)
            if state.updated_at:
                approved_payload = dashboard_store.approved_report_payload(
                    conn, report_id=report_id
                )
                if not approved_payload:
                    return None
                # 공개 봉인은 payload가 아니라 별도 표에 있다(root 결정 C).
                # 다시 붙이지 않으면 재시작 뒤 조회에서만 봉인이 사라진다(I7).
                # 어긋나면 아래 except가 ReportStoreUnavailable로 닫는다
                # (I3 fail-closed — 이 함수의 기존 오류 처리 그대로다).
                return report_store.attach_public_projection(
                    conn,
                    report_id,
                    report_store.report_from_json(approved_payload),
                )
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
        _job_is_paid(job) and not _job_work_admitted(job)
    ):
        raise JobAdmissionClosed("server shutdown has started")
    if job.execution_started_monotonic <= 0:
        # task가 event loop에서 실제로 실행될 때가 아니라 이미 예약한 슬롯을
        # 넘기는 이 순간부터 전체 절대 마감이 흐른다.
        job.execution_started_monotonic = time.monotonic()
    # task/worker 스레드보다 먼저 세션을 Job에 달아야 이벤트루프
    # 취소가 먼저 와도 늦은 provider 시작을 닫을 수 있다.
    _prepare_generation_session(job)
    task = asyncio.create_task(
        _run_job(job), name=f"analysis-job:{job.job_id}"
    )
    _TASKS.add(task)
    _TASK_JOBS[task] = job
    task.add_done_callback(_forget_finished_task)
    return task


def _force_shutdown_cleanup(job: Job) -> None:
    """취소 유예 안에도 끝나지 않은 Job의 비용과 슬롯을 fail-closed로 닫는다."""
    if job.shutdown_cleanup_completed:
        return
    if job.generation_session is not None:
        # 아직 살아 있을 수 있는 provider thread와 takeover가 겹치지 않게
        # 성공·실패를 지어내지 않고 마지막 heartbeat+TTL에 회수를 맡긴다.
        job.generation_session.abandon()
        job.generation_abandoned = True
    if not isinstance(job.result, RunResult):
        job.result = RunResult(
            outcome=Outcome.FAILED,
            message=PIPELINE_FAILED_MESSAGE,
            billing_uncertain=job.paid_phase is not None,
        )
    if job.result.cost_krw == 0 and job.upfront_cost_krw > 0:
        # 강제 종료로 본조사 usage를 모르는 경우에도 이미 확정된 식별/OCR 원가는
        # LINK 이력에서 0원으로 사라지지 않게 한다. 미확정 본조사는 별도 원장이 닫는다.
        job.result = replace(job.result, cost_krw=job.upfront_cost_krw)
    job.current_step = ""
    job.finished = True
    job.finished_at = job.finished_at or time.monotonic()
    if not job.shutdown_interruption_persisted:
        try:
            job_interruptions.persist(
                job_id=job.job_id,
                interrupted_at=clock.iso_now_kst(),
                reason="shutdown_timeout",
            )
            job.shutdown_interruption_persisted = True
        except BaseException:  # noqa: BLE001 — 종료 정리는 계속하되 원인은 로그에 남긴다
            logger.exception("중단된 조사 상태를 저장하지 못했습니다 job_id=%s", job.job_id)
    if job.paid_phase is not None and not job.paid_phase_settled:
        job.paid_phase_settled = True
        try:
            _settle_paid_phase(
                job.paid_phase, amount_krw=0.0, billing_uncertain=True
            )
        except BaseException:  # noqa: BLE001 — 종료는 계속하고 통장은 보수적으로 닫는다
            logger.exception("종료 중 비용 표식을 미확정으로 마감하지 못했습니다")
    if job.paid_phase is not None:
        try:
            record_end(
                run_id=job.job_id,
                job=job.user_input.job,
                end_step=obs.END_STEP_GENERATE,
                cost_krw=job.upfront_cost_krw,
                elapsed_sec=job.upfront_elapsed_sec,
                model=_model_label(job.upfront_models),
                expected_state=lifecycle.STATE_RUNNING,
            )
        except BaseException:  # noqa: BLE001 — 관측 실패가 슬롯 반환을 막으면 안 된다
            logger.exception("종료 중 실행 이력을 마감하지 못했습니다 job_id=%s", job.job_id)
    if job.share_link_hash:
        _finish_link_job(
            job,
            status=share_store.RUN_STATUS_INTERRUPTED,
            stop_step=obs.END_STEP_GENERATE,
            stop_reason="shutdown_timeout",
        )
    try:
        _release_job_slot(job)
    except BaseException:  # noqa: BLE001 — 한 작업 오류가 다른 작업 정리를 막지 않는다
        logger.exception("종료 중 조사 슬롯을 반환하지 못했습니다")
    job.shutdown_cleanup_completed = True


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
    # process가 취소 유예 중 강제 종료돼도 다음 기동에서 원인을 설명할 수 있게
    # provider task를 취소하기 전에 중단 표식을 먼저 커밋한다.
    for task in pending_set:
        job = _TASK_JOBS.get(task)
        if job is None:
            continue
        try:
            job_interruptions.persist(
                job_id=job.job_id,
                interrupted_at=clock.iso_now_kst(),
                reason="shutdown_timeout",
            )
            job.shutdown_interruption_persisted = True
        except BaseException:  # noqa: BLE001
            logger.exception(
                "종료 전 중단 상태를 저장하지 못했습니다 job_id=%s", job.job_id
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
    resolved_track: tuple[share_tracks.Track, str, float | None],
    run_id: str,
    upfront_cost: float,
    upfront_models: tuple[str, ...],
    upfront_elapsed: float,
    slot_bucket_id: str,
    upfront_cost_events: tuple[cost_store.AiCostEvent, ...] = (),
) -> Response:
    """이미 잡은 한 자리를 OCR부터 배경 본조사로 안전하게 넘긴다.

    ★ 업로드 읽기·OCR·DB·작업 등록 어디서 예외가 나도 `finally`가 자리를 돌려준다.
      배경 작업 등록에 성공한 순간부터는 `_run_job()`이 그 자리를 돌려준다.
    """
    handed_off = False
    share_key = resolved_track[1]
    pipeline_phase: Optional[PaidPhase] = None
    admission_recorded = False
    link_history_started = False
    link_history_hash = ""
    member_usage_reserved = False
    member_email = ""
    public_grant: report_access_store.IssuedGrant | None = None
    early_stop_status = share_store.RUN_STATUS_STOPPED
    early_stop_step = obs.END_STEP_GENERATE
    early_stop_reason = "generation_not_started"

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
        if resolved_track[0] is share_tracks.Track.LINK:
            # LINK 꼬리표의 회사·직무는 비교하지 않는다. 다만 capability 자체의
            # 존재·만료·철회는 provider 호출보다 먼저 같은 실행 경계에서 재검사한다.
            link_blocked = request_helpers.require_active_share_link(
                request,
                resolved_track=resolved_track,
            )
            if link_blocked is not None:
                return link_blocked
            try:
                with storage_db.connect() as conn:
                    link = share_store.load(conn, share_key)
                    active = (
                        link is not None
                        and not link.is_revoked
                        and not share_logic.is_share_link_expired(link.created_at)
                    )
                    inserted = active and share_store.start_run(
                        conn,
                        key=share_key,
                        run_id=run_id,
                        started_at=clock.iso_now_kst(),
                        input_company=original_input.company,
                        confirmed_company=card.legal_name,
                        company_id=card.ref or card.legal_name,
                    )
                    if not inserted or link is None:
                        raise RuntimeError("LINK 생성 시작 이력을 저장하지 못했습니다")
                    link_history_hash = link.key_hash
                    link_history_started = True
            except Exception:  # noqa: BLE001 — 이력 없는 유료 호출은 시작하지 않는다
                logger.exception(
                    "LINK 생성 시작 이력을 저장하지 못했습니다 job_id=%s", run_id
                )
                return _storage_unavailable_response(request)
        # 사진으로 올린 공고는 «여기서 처음» 서버에 들어온다 (기획서 D2 — 판정 통과 후).
        # ★ 원본 바이트를 파일·로그·결과 어디에도 남기지 않는다 (S2).
        posting_body = original_input.posting_text
        image_error = ""
        image_failure_kind = ""
        if posting_images:
            image_bytes: list[bytes] = []
            has_named_image = any(upload.filename for upload in posting_images)
            if (
                is_paid
                and has_named_image
                and not bool(
                    getattr(
                        runtime._PIPELINE,
                        "supports_posting_image_input",
                        False,
                    )
                )
            ):
                # UI를 숨겼더라도 옛 화면·직접 요청은 올 수 있다. 현재 pipeline이
                # 실제로 쓰지 않는 입력에 OCR 비용을 쓰기 전에 서버에서 닫는다.
                image_error = _IMAGE_PIPELINE_UNSUPPORTED_ERROR
                image_failure_kind = "input"
                early_stop_step = obs.END_STEP_IMAGE_INPUT
                early_stop_reason = "posting_image_pipeline_unsupported"
            elif has_named_image and not posting_image_consent:
                image_bytes = []
                image_error = _IMAGE_CONSENT_ERROR
                image_failure_kind = "input"
                early_stop_step = obs.END_STEP_IMAGE_INPUT
                early_stop_reason = "posting_image_consent_required"
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
                    early_stop_step = (
                        obs.END_STEP_IMAGE_ERROR
                        if upload_failure.failure_kind == "technical"
                        else obs.END_STEP_IMAGE_INPUT
                    )
                    early_stop_reason = "posting_image_read_failed"
            if image_bytes and not is_paid:
                del image_bytes          # ★ 안 쓸 거면 «바로» 버린다 (S2)
                image_error = _DEMO_IMAGE_NOTICE
                image_failure_kind = "input"
                early_stop_step = obs.END_STEP_IMAGE_INPUT
                early_stop_reason = "posting_image_demo_unsupported"
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
                    early_stop_reason = "daily_budget_unavailable"
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
                    early_stop_step = (
                        obs.END_STEP_IMAGE_ERROR
                        if image_failure_kind == "technical"
                        else obs.END_STEP_IMAGE_INPUT
                    )
                    early_stop_reason = "posting_image_extraction_failed"

                if not image_error:
                    # OCR 비용이 남은 몫을 다 썼다면 본조사 AI는 시작하지 않는다.
                    blocked = request_helpers._guard_run(
                        request,
                        count_start=False,
                        owns_slot=True,
                        resolved_track=resolved_track,
                    )
                    if blocked is not None:
                        early_stop_reason = "daily_budget_exhausted"
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

        member_email = ""
        try:
            with storage_db.connect() as conn:
                if dashboard_store.get_service_state(conn).status == dashboard_store.SERVICE_MAINTENANCE:
                    return request_helpers._throttled(
                        request,
                        "현재 점검 중입니다. 원인 확인과 재검사가 끝난 뒤 운영자가 직접 재시작합니다.",
                        "service-maintenance",
                    )
                if resolved_track[0] is share_tracks.Track.MEMBER:
                    member_email = share_key.removeprefix("user:")
                    member_session = auth_logic.get_session(
                        request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
                    )
                    if (
                        member_session is None
                        or member_session.is_admin
                        or member_session.email != member_email
                        or not auth_logic.is_approval_identity_subject(
                            member_session.subject
                        )
                    ):
                        raise RuntimeError(
                            "MEMBER 실행의 불변 계정 subject를 확인할 수 없습니다"
                        )
                    member_usage_reserved = dashboard_store.reserve_member_run(
                        conn,
                        run_id=run_id,
                        actor_email=member_email,
                        day=clock.today_kst().isoformat(),
                        now_iso=clock.iso_now_kst(),
                    )
                    if member_usage_reserved and not report_access_store.bind_member_run(
                        conn,
                        run_id=run_id,
                        identity_subject=member_session.subject,
                    ):
                        raise RuntimeError("MEMBER 실행 소유권을 결속하지 못했습니다")
        except Exception:  # noqa: BLE001 — 상태·사용량을 모르면 새 provider를 열지 않는다
            logger.exception("전역 상태 또는 MEMBER 성공 보고서 예약을 저장하지 못했습니다")
            return _storage_unavailable_response(request)
        if resolved_track[0] is share_tracks.Track.MEMBER:
            if not member_usage_reserved:
                return request_helpers._throttled(
                    request,
                    "오늘 성공한 보고서 3건을 모두 사용했습니다. 내일 다시 시도해 주세요.",
                    "member-success-limit",
                )

        if is_paid and not _reserved_work_admitted(slot_bucket_id):
            return admission_rejection()
        if is_paid and not bool(
            getattr(runtime._PIPELINE, "supports_deferred_paid_phase", False)
        ):
            # 새 capability를 모르는 교체 pipeline은 무표식 provider로
            # 바뀌지 않게 예전처럼 route에서 예약한다. 운영 RealPipeline은
            # 반드시 DART snapshot→owner→지연 phase 경로를 쓴다.
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
                early_stop_reason = "daily_budget_unavailable"
                return request_helpers._throttled(
                    request, BUSY_MESSAGE, "budget-store"
                )

        # 익명 데모는 주소 자체가 아니라 별도 브라우저 grant로만 이후 화면을
        # 연다. 작업을 scheduler에 넘기기 전에 DB 결속을 확정해야 첫 progress
        # redirect가 권한 없이 떠 버리는 틈이 없다.
        report_audience = _report_audience_for_track(resolved_track)
        requires_public_report_grant = report_audience is ReportAudience.PUBLIC
        if requires_public_report_grant:
            try:
                with storage_db.connect() as conn:
                    public_grant = report_access_store.issue_and_bind(
                        conn,
                        existing_token=request.cookies.get(
                            report_access_constants.PUBLIC_GRANT_COOKIE_NAME
                        ),
                        run_id=run_id,
                    )
            except Exception:  # noqa: BLE001 — grant 없는 공개 작업은 시작하지 않는다
                logger.exception("PUBLIC 보고서 열람 grant를 발급하지 못했습니다")
                return _storage_unavailable_response(request)
        # phase/grant DB 쓰기와 실제 pipeline task 등록 사이의 마지막 fence.
        # PUBLIC에만 두면 MEMBER·LINK·ADMIN은 그 사이 비용 저장소가 닫혀도
        # provider 작업을 등록할 수 있다.
        if is_paid and not _reserved_work_admitted(slot_bucket_id):
            # RealPipeline은 owner 확정 전이라 phase가 아직 없다. ``None``을
            # 취소 함수에 넘기면 원래의 503 대신 AttributeError가 나고, route의
            # 바깥 실패 처리까지 왜곡된다. 실제로 만든 legacy phase만 취소한다.
            if pipeline_phase is not None:
                _cancel_paid_phase(pipeline_phase)
            return admission_rejection()
        # RealPipeline의 본조사 phase는 여기서 미리 예약하지 않는다. 무료
        # DART preflight로 source snapshot을 고정하고 single-flight owner를
        # 결정한 뒤, owner만 첫 Anthropic 호출 직전에 지연 예약한다.

        new_job = Job(
            job_id=run_id,
            user_input=user_input,
            card=card,
            share_key=share_key,
            member_email=member_email,
            share_link_hash=link_history_hash,
            upfront_cost_krw=upfront_cost,
            upfront_models=upfront_models,
            upfront_elapsed_sec=upfront_elapsed,
            upfront_cost_events=upfront_cost_events,
            paid_phase=pipeline_phase,
            is_paid=is_paid,
            paid_cap_krw=resolved_track[2],
            slot_bucket_id=slot_bucket_id,
            report_audience=report_audience,
            public_grant_expires_at=(
                public_grant.expires_at if public_grant is not None else 0.0
            ),
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
            early_stop_step = obs.END_STEP_GENERATE
            early_stop_reason = "job_registration_failed"
            return _storage_unavailable_response(request)
        try:
            _schedule_job(new_job)
        except JobAdmissionClosed:
            if _JOBS.get(run_id) is new_job:
                _JOBS.pop(run_id, None)
            if pipeline_phase is not None:
                _cancel_paid_phase(pipeline_phase)
            early_stop_status = share_store.RUN_STATUS_INTERRUPTED
            early_stop_reason = "server_shutdown"
            return admission_rejection()
        except BaseException:
            if _JOBS.get(run_id) is new_job:
                _JOBS.pop(run_id, None)
            # 작업 등록 전이라 provider를 부르지 않았음이 확실하다.
            if pipeline_phase is not None:
                _cancel_paid_phase(pipeline_phase)
            raise
        handed_off = True
        response = RedirectResponse(f"/progress/{run_id}", status_code=303)
        if public_grant is not None:
            response.set_cookie(
                report_access_constants.PUBLIC_GRANT_COOKIE_NAME,
                public_grant.token,
                max_age=report_access_constants.PUBLIC_GRANT_MAX_AGE_SEC,
                httponly=True,
                secure=request_helpers._cookie_secure(request),
                samesite="lax",
                path="/",
            )
        return response
    except BaseException:
        early_stop_reason = "generation_start_failed"
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
                if link_history_started:
                    try:
                        with storage_db.connect() as conn:
                            share_store.finish_run(
                                conn,
                                run_id=run_id,
                                status=early_stop_status,
                                finished_at=clock.iso_now_kst(),
                                stop_step=early_stop_step,
                                stop_reason=early_stop_reason,
                                internal_ai_cost_krw=upfront_cost,
                            )
                    except Exception:  # noqa: BLE001 — 슬롯 반환을 막지 않는다
                        logger.exception(
                            "LINK 생성 조기 종료 이력을 저장하지 못했습니다 job_id=%s",
                            run_id,
                        )
                _release_run_slot(slot_bucket_id)
                if member_usage_reserved:
                    try:
                        with storage_db.connect() as conn:
                            dashboard_store.settle_member_run(
                                conn,
                                run_id=run_id,
                                succeeded=False,
                                report_id="",
                                now_iso=clock.iso_now_kst(),
                            )
                    except Exception:  # noqa: BLE001 — unknown reservation stays closed safely
                        logger.exception("MEMBER 시작 실패 예약을 반환하지 못했습니다")
        finally:
            try:
                if not handed_off:
                    public_ids.release(run_id)
            finally:
                await _close_posting_images(posting_images)
