"""웹 화면 — 입력받고 → 확인받고 → 돌리고 → 결과를 보여준다.

★ 알맹이를 직접 부르지 않는다. `features/pipeline/port.py`가 정한 모양으로만 이야기한다.
  지금은 데모(저장된 기록 재생)가 꽂혀 있고, 진짜 파이프라인은 `_PIPELINE` 한 줄만 바꾸면 된다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, TypeVar

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.core import paths
from src.core.citations import citation_number
from src.core.constants import (
    CELL_LABELS,
    COMPANY_LOOKUP_FAILED_MESSAGE,
    MAX_RETRY_INPUT,
    MODEL_LABEL_SEPARATOR,
    PIPELINE_ENV,
    PIPELINE_FAILED_MESSAGE,
    PIPELINE_REAL,
    PROGRESS_STEPS,
    RAW_SOURCE_LABEL,
    RAW_SOURCE_NOTE,
    REPLAY_MODEL_MARK,
)
from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.budget import expiry as link_expiry
from src.features.budget import logic as budget_logic
from src.features.budget import spend_store
from src.features.budget.constants import (
    BUDGET_EXHAUSTED_MESSAGE,
    BUSY_MESSAGE,
    DAILY_BUDGET_KRW,
    JOB_KEEP_SEC,
    JOB_MAX_KEPT,
    MAX_CONCURRENT_PER_LINK,
    MAX_CONCURRENT_PER_USER,
    MAX_CONCURRENT_RUNS,
    RATE_LIMITED_MESSAGE,
    RATE_MAX_RUNS,
    RATE_WINDOW_SEC,
    SPEND_PHASE_IDENTIFY,
    SPEND_PHASE_OCR,
    SPEND_PHASE_PIPELINE,
)
from src.features.budget.sharing import (
    LINK_EXPIRED_MESSAGE,
    REPORT_ID_HEX_CHARS,
    REPORT_LINK_MAX_AGE_DAYS,
    SHARED_LINK_HEADERS,
)
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import issue as share_issue
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_MAX_AGE_SEC,
    KEY_COOKIE_NAME,
    KEY_PATH_PREFIX,
    LINK_BUDGET_EXHAUSTED_MESSAGE,
    PUBLIC_BUCKET,
    PUBLIC_NOT_ALLOWED_MESSAGE,
)
from src.features.export_docx.logic import (
    CONTENT_TYPE_DOCX,
    build_content_disposition,
    build_docx,
    build_download_filename,
)
from src.features.export_notion.notion import send_report_to_notion
from src.features.grading.logic import grade_message
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.observability.metrics import build_dashboard
from src.features.observability.records import read_records
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.posting_image.logic import (
    PostingImageResult,
    default_extract,
    extract_posting_text,
)
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web.recording import (
    record_end,
    record_run,
    records_path,
    safe_observation_job,
)

logger = logging.getLogger(__name__)

# ── 알맹이 꽂는 자리 ─────────────────────────────────────

def make_pipeline() -> object:
    """어느 알맹이를 쓸지 고른다.

    ★ 기본은 **데모(공짜)**다. 진짜 조사는 **환경변수로만** 켠다 —
      코드를 고쳐 켜면 «켜 둔 채 잊어버려» 돈이 계속 나간다.

    켜는 법:
        PIPELINE=real python -m uvicorn src.web.main:app --port 8000
    """
    if os.environ.get(PIPELINE_ENV, "").strip().lower() == PIPELINE_REAL:
        from src.features.pipeline.real import RealPipeline  # noqa: PLC0415

        logger.warning("★ 진짜 파이프라인으로 돕니다 — AI 호출마다 비용이 발생합니다.")
        return RealPipeline()
    return DemoPipeline()


_PIPELINE = make_pipeline()


def _current_model() -> str:
    """지금 서비스가 쓰는 AI 모델. 데모는 AI를 안 부르므로 그렇게 적는다."""
    if isinstance(_PIPELINE, DemoPipeline):
        return "(데모 — AI 호출 없음)"
    from src.features.pipeline.real import _engine  # noqa: PLC0415

    return getattr(_engine(), "MODEL", "")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """서버가 뜰 때 «오늘 이미 쓴 돈»을 이력에서 되살린다 (P-92).

    ★ 이게 없으면 **서버만 껐다 켜면 하루 상한이 풀린다** — 상한이 무의미해진다.
    ⚠️ `@app.on_event("startup")`은 폐기 예정이라 쓰지 않는다.
    """
    _seed_ledger()
    _recover_observation_lifecycle()
    yield


app = FastAPI(title="기업분석 도구", lifespan=_lifespan)

app.mount("/static", StaticFiles(directory=str(paths.STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(paths.TEMPLATES_DIR))


@app.middleware("http")
async def beta_admin_gate(request: Request, call_next):
    """시험 배포에서는 로그인한 관리자만 사이트 본문에 들어오게 한다.

    Google 로그인 왕복과 Render 상태 확인은 예외다. 환경변수가 없으면 기존 공개
    동작을 바꾸지 않으므로, 정식 공개 때는 설정 하나만 내려도 된다.
    """
    if not auth_logic.beta_admin_only_from_env():
        return await call_next(request)

    path = request.url.path
    if path in auth_constants.BETA_PUBLIC_PATHS or path.startswith(
        auth_constants.BETA_PUBLIC_PATH_PREFIXES
    ):
        return await call_next(request)

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    try:
        session = auth_logic.get_session(token)
    except Exception:  # noqa: BLE001 — 저장소 장애 때 공개 쪽으로 실패하면 안 된다
        logger.exception("시험 공개 관리자 세션을 확인하지 못했습니다")
        return Response("서비스를 준비하고 있습니다.", status_code=503)

    if session is not None and session.is_admin:
        return await call_next(request)
    target = "/auth/not-admin" if session is not None else "/auth/login"
    return RedirectResponse(target, status_code=303)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Render가 SQLite 조회와 쓰기 트랜잭션 시작 가능성을 확인하는 상태 신호."""
    try:
        with storage_db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
            # 빈 조회만으로는 쓰기 트랜잭션을 시작할 수 있는지 알 수 없다.
            # BEGIN IMMEDIATE로 쓰기 잠금을 짧게 잡고, 행을 남기지 않고 바로 되돌린다.
            try:
                conn.execute("BEGIN IMMEDIATE")
            finally:
                if conn.in_transaction:
                    conn.rollback()
    except Exception:  # noqa: BLE001 — 경로나 디스크가 깨졌으면 배포를 정상으로 보지 않는다
        logger.exception(
            "상태 확인에서 SQLite를 읽거나 쓰기 트랜잭션을 시작하지 못했습니다"
        )
        return JSONResponse({"status": "unhealthy"}, status_code=503)
    return JSONResponse({"status": "ok"})


@dataclass(frozen=True)
class PaidPhase:
    """provider 호출 전 DB에 커밋한 진행 중 비용 표식의 서버 쪽 열쇠."""

    run_id: str
    phase: str
    day: dt.date
    share_key: str
    bucket_id: str


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


@dataclass(frozen=True)
class PaidAttempt:
    """비용 검사를 통과한 회사 확인과 본조사를 잇는 일회용 서버 기록.

    ★ 숨은 입력칸의 비용·권한·회사 식별값을 믿지 않는다. 브라우저에는 추측할 수
      없는 `token`만 보내고 실제 값은 서버 메모리에 둔다. 토큰은 `/run`에서 한 번
      꺼내면 바로 없어져 같은 식별 결과로 OCR·본조사를 반복 호출할 수 없다.
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


#: 돌아가는 요청들. 1단계는 메모리에 둔다 (저장은 2단계에서).
#: ⚠️ **반드시 치워야 한다** — 안 치우면 서버가 죽을 때까지 쌓인다 (P-92).
#:   치우는 것은 `_sweep_jobs()`. 보고서 자체는 DB에 있으므로 «화면은 안 죽는다».
_JOBS: dict[str, Job] = {}
#: 확인 카드에서 [맞습니다]를 기다리는 유료 요청. 끝난 작업과 같은 시간 뒤 치운다.
_PAID_ATTEMPTS: dict[str, PaidAttempt] = {}

# ══════════════════════════════════════════════════════════
# 돈·횟수 막기 (배포 안전 — 문제로그 P-92)
# ══════════════════════════════════════════════════════════
# ★ 왜 여기(전역)에 두나 — 서버 하나에 하나만 있어야 하는 값이다.
#   요청마다 새로 만들면 아무것도 못 센다.
# ⚠️ **서버를 껐다 켜면 오늘 쓴 돈이 0으로 보인다.** 실제 지출은 이력에 남아 있으므로,
#   서버가 뜰 때 «오늘 몫»을 이력에서 다시 읽어 채운다 (`_seed_ledger`).
#   안 그러면 서버만 재시작하면 상한이 계속 풀리는 구멍이 된다.

_LEDGER = budget_logic.Ledger(day=dt.date.today())
#: ★ 열쇠(링크)별 «오늘 쓴 돈». 전체 하나가 아니라 **링크마다 따로** 센다 (P-94).
#:   한 회사 인사팀이 다 써도 다른 회사 링크는 멀쩡히 돌아야 한다.
_LINK_SPEND = share_logic.DailySpend(day=dt.date.today())
_RATE_HISTORY = budget_logic.RateHistory()
#: 지금 돌고 있는 조사 수.
_RUNNING = 0
#: 같은 로그인 계정·초대 링크가 차지한 자리 수. 열쇠 원문이나 이메일 대신
#: 비용 원장과 같은 SHA-256 통장 지문만 메모리에 둔다.
_RUNNING_BY_BUCKET: dict[str, int] = {}
#: 여러 요청 스레드가 검사와 자리잡기 사이에 끼어들지 못하게 한다.
_SLOT_LOCK = threading.Lock()
#: DB inflight와 메모리 active를 한 전이처럼 바꾼다. SQLite 쓰기 자체도 한 번에
#: 하나이므로 이 짧은 구간을 직렬화해 다섯 요청의 오래된 스냅샷이 섞이지 않게 한다.
_PAID_PHASE_LOCK = threading.RLock()
#: 시작 훅이 성공적으로 원장을 읽기 전에는 빈 장부를 믿지 않는다.
#: ★ lifespan 없이 앱을 부르는 환경도 있으므로 True로 시작하면 안 된다.
_BUDGET_STORE_HEALTHY = False
#: provider 호출 전 표식은 있는데 확정 비용으로 마감되지 않은 오늘의 통장들.
#: 같은 링크만 닫아 다른 링크까지 함께 멈추지 않는다(P-94).
_UNRESOLVED_BUCKETS: set[tuple[str, str]] = set()
#: 지금 프로세스가 정상적으로 돌리고 있는 비용 표식. DB의 inflight 행 중 여기에
#: 없는 것만 재시작·API 예외로 결과를 모르는 표식이다.
_ACTIVE_PAID_PHASES: set[tuple[str, str, str, str]] = set()

_WorkerResult = TypeVar("_WorkerResult")


def _bucket_concurrency_limit(track: share_tracks.Track) -> int:
    """한 비용 통장이 동시에 차지할 수 있는 조사 자리 수."""
    if track is share_tracks.Track.LINK:
        return MAX_CONCURRENT_PER_LINK
    if track in (share_tracks.Track.ADMIN, share_tracks.Track.MEMBER):
        return MAX_CONCURRENT_PER_USER
    return MAX_CONCURRENT_RUNS


def _slot_is_full(
    track: share_tracks.Track, bucket: str, *, owns_slot: bool = False
) -> bool:
    """전역 자리나 이 통장의 자리가 꽉 찼는지 한 잠금 안에서 본다."""
    stored_bucket = spend_store.bucket_id(bucket)
    own = 1 if owns_slot else 0
    with _SLOT_LOCK:
        running = max(0, _RUNNING - own)
        bucket_running = max(0, _RUNNING_BY_BUCKET.get(stored_bucket, 0) - own)
        return (
            running >= MAX_CONCURRENT_RUNS
            or bucket_running >= _bucket_concurrency_limit(track)
        )


def _reserve_run_slot(track: share_tracks.Track, bucket: str) -> str | None:
    """전역·통장별 상한을 다시 확인하고 한 자리를 원자적으로 잡는다."""
    global _RUNNING
    stored_bucket = spend_store.bucket_id(bucket)
    with _SLOT_LOCK:
        bucket_running = _RUNNING_BY_BUCKET.get(stored_bucket, 0)
        if (
            _RUNNING >= MAX_CONCURRENT_RUNS
            or bucket_running >= _bucket_concurrency_limit(track)
        ):
            return None
        _RUNNING += 1
        _RUNNING_BY_BUCKET[stored_bucket] = bucket_running + 1
    return stored_bucket


def _release_run_slot(stored_bucket: str) -> None:
    """성공·실패와 상관없이 잡았던 한 자리를 정확히 한 번 돌려준다."""
    global _RUNNING
    if not stored_bucket:
        return
    with _SLOT_LOCK:
        current = _RUNNING_BY_BUCKET.get(stored_bucket, 0)
        if current <= 0:
            return
        _RUNNING = max(0, _RUNNING - 1)
        left = current - 1
        if left > 0:
            _RUNNING_BY_BUCKET[stored_bucket] = left
        else:
            _RUNNING_BY_BUCKET.pop(stored_bucket, None)


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


def _seed_ledger() -> None:
    """서버가 뜰 때 «오늘 이미 쓴 돈»을 이력에서 읽어 장부에 채운다.

    ★ 이게 없으면 서버를 껐다 켜는 것만으로 하루 상한이 풀린다.
    ★ 데모 기록은 빼고 센다 — 데모는 0원이다 (P-84와 같은 규칙).
    """
    global _LEDGER, _LINK_SPEND, _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    today = dt.date.today()
    # 서버 시작 시 DB에 남아 있는 inflight는 이 프로세스가 돌리는 정상 작업일 수
    # 없다. 전부 재시작 뒤 결과를 모르는 표식으로 다시 분류한다.
    with _SLOT_LOCK:
        _ACTIVE_PAID_PHASES.clear()
    try:
        read_result = read_records(records_path())
        records = read_result.records
        latest = {}
        for record in records:
            latest[record.run_id] = record
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            snapshot = spend_store.load_day(conn, today)
            history = spend_store.load_run_history(conn, latest)
            unresolved = spend_store.load_unresolved_day(conn, today)
    except Exception:  # noqa: BLE001 — 이력을 못 읽어도 서버는 떠야 한다
        # ★ 0원으로 열어 두면 재시작 한 번으로 모든 상한이 풀린다. 화면은 살리되
        # 진짜 유료 호출은 `_guard_run()`이 닫도록 건강 상태를 내린다.
        logger.exception("비용 원장을 못 읽어 진짜 조사를 닫습니다")
        _LEDGER = budget_logic.Ledger(day=today)
        _LINK_SPEND = share_logic.DailySpend(day=today)
        _UNRESOLVED_BUCKETS = set()
        _BUDGET_STORE_HEALTHY = False
        return

    # 이력은 같은 run_id의 마지막 줄이 «그 요청 총비용»이다. 다만 자정을 걸친
    # 요청은 어제 식별비+오늘 본조사비가 한 줄에 합쳐지므로 전 날짜 원장과 대조한다.
    # 모든 알려진 단계가 오늘인 요청만 양의 차이를 오늘 같은 통장에 보충할 수 있다.
    by_bucket = dict(snapshot.by_bucket)
    supplemental = 0.0
    legacy_spent = 0.0
    ambiguous_spent = 0.0
    today_text = today.isoformat()
    for run_id, record in latest.items():
        if (
            record.cost_krw <= 0
            or REPLAY_MODEL_MARK in (record.model or "")
            or record.at[:10] != today.isoformat()
        ):
            continue
        if run_id not in history.run_ids:
            # 새 원장 전의 옛 이력은 통장을 알 수 없어 전체 합계에만 보탠다.
            legacy_spent += record.cost_krw
            continue
        known_total = history.by_run.get(run_id, 0.0)
        missing = round(record.cost_krw - known_total, 2)
        if missing > 0:
            if history.days_by_run.get(run_id) == frozenset({today_text}):
                bucket = history.bucket_by_run[run_id]
                by_bucket[bucket] = by_bucket.get(bucket, 0.0) + missing
                supplemental += missing
            else:
                # 어느 날 빠진 단계인지 알 수 없는데 오늘로 옮기면 숫자를 지어낸다.
                # 금액은 보충하지 않고 유료 호출을 닫아 사람이 원장을 확인하게 한다.
                ambiguous_spent += missing
        elif missing < 0:
            # 단계 원장이 관측 총액보다 큰 것도 정상 상태가 아니다. 실제 단계 원장은
            # 그대로 세되 다음 유료 호출은 닫는다.
            ambiguous_spent += abs(missing)

    spent = snapshot.total_krw + supplemental + legacy_spent
    _LEDGER = budget_logic.Ledger(day=today, spent_krw=spent)
    _LINK_SPEND = share_logic.DailySpend(day=today, by_key=by_bucket)
    _UNRESOLVED_BUCKETS = {(today_text, bucket) for bucket in unresolved}
    # 통장을 알 수 없는 오늘의 옛 비용이나 깨진 이력이 있으면 링크별 상한을
    # 사실대로 복원할 수 없다. 전체 합계만 맞춰 놓고 유료 호출은 안전하게 닫는다.
    _BUDGET_STORE_HEALTHY = (
        legacy_spent == 0
        and ambiguous_spent == 0
        and read_result.skipped == 0
    )
    if not _BUDGET_STORE_HEALTHY:
        logger.error(
            "통장별로 복원할 수 없는 비용 이력이 있어 진짜 조사를 닫습니다 "
            "(옛 비용 %.1f원, 날짜 불명 비용 %.1f원, 깨진 줄 %d개)",
            legacy_spent,
            ambiguous_spent,
            read_result.skipped,
        )
    logger.info(
        "오늘 이미 쓴 돈 %.1f원·미확정 통장 %d개로 시작합니다",
        spent,
        len(_UNRESOLVED_BUCKETS),
    )


def _add_memory_spend(ticket: PaidPhase, amount_krw: float) -> None:
    """DB에 새로 들어간 확정 비용을 오늘 메모리 장부에도 한 번 더한다."""
    global _LEDGER, _LINK_SPEND
    if not math.isfinite(amount_krw) or amount_krw <= 0:
        return
    if ticket.day != dt.date.today():
        # 자정 전에 시작한 요청이 뒤늦게 끝나도 오늘 장부를 어제로 되감으면 안 된다.
        # 영속 원장에는 ticket 날짜로 이미 정확히 들어갔고 오늘 메모리에는 더하지 않는다.
        return
    _LEDGER = budget_logic.add_spend(_LEDGER, ticket.day, amount_krw)
    _LINK_SPEND = share_logic.add_spend(
        _LINK_SPEND, ticket.bucket_id, ticket.day, amount_krw
    )


def _paid_phase_key(ticket: PaidPhase) -> tuple[str, str, str, str]:
    return (
        ticket.day.isoformat(),
        ticket.bucket_id,
        ticket.run_id,
        ticket.phase,
    )


def _inflight_phase_key(
    row: spend_store.InflightSpend,
) -> tuple[str, str, str, str]:
    return (row.day.isoformat(), row.bucket_id, row.run_id, row.phase)


def _finish_active_phase(
    ticket: PaidPhase, remaining: tuple[spend_store.InflightSpend, ...]
) -> None:
    """현재 실행 표식을 빼고, DB에 남은 진짜 미확정 표식만 통장을 닫는다."""
    unresolved_key = (ticket.day.isoformat(), ticket.bucket_id)
    with _SLOT_LOCK:
        _ACTIVE_PAID_PHASES.discard(_paid_phase_key(ticket))
        has_unknown = any(
            row.bucket_id == ticket.bucket_id
            and _inflight_phase_key(row) not in _ACTIVE_PAID_PHASES
            for row in remaining
        )
        if has_unknown:
            _UNRESOLVED_BUCKETS.add(unresolved_key)
        else:
            _UNRESOLVED_BUCKETS.discard(unresolved_key)


def _begin_paid_phase(*, run_id: str, phase: str, share_key: str) -> Optional[PaidPhase]:
    """DB 표식과 active 메모리를 요청 스레드 사이에서도 한 전이로 시작한다."""
    with _PAID_PHASE_LOCK:
        return _begin_paid_phase_locked(
            run_id=run_id,
            phase=phase,
            share_key=share_key,
        )


def _begin_paid_phase_locked(
    *, run_id: str, phase: str, share_key: str
) -> Optional[PaidPhase]:
    """유료 provider 호출 전에 표식을 커밋한다. 실패하면 호출 권한을 주지 않는다."""
    global _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    day = dt.date.today()
    ticket = PaidPhase(
        run_id=run_id,
        phase=phase,
        day=day,
        share_key=share_key,
        bucket_id=spend_store.bucket_id(share_key),
    )
    try:
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            inserted = spend_store.begin_inflight(
                conn,
                run_id=run_id,
                phase=phase,
                day=day,
                bucket=share_key,
                started_at=dt.datetime.now().isoformat(timespec="seconds"),
            )
    except Exception:  # noqa: BLE001 — 표식이 없으면 provider를 절대 부르지 않는다
        logger.exception("비용 진행 중 표식을 쓰지 못해 진짜 조사를 닫습니다")
        _BUDGET_STORE_HEALTHY = False
        return None
    if not inserted:
        # 이미 끝났거나 미확정인 같은 단계다. 어느 쪽이든 다시 부르면 이중 과금이다.
        with _SLOT_LOCK:
            _UNRESOLVED_BUCKETS.add((ticket.day.isoformat(), ticket.bucket_id))
        return None
    # 정상 진행 중인 표식은 장애가 아니다. 같은 초대 링크의 세 자리를 허용하려면
    # 이것을 재시작·API 예외 표식과 섞어 같은 통장을 즉시 닫으면 안 된다.
    with _SLOT_LOCK:
        _ACTIVE_PAID_PHASES.add(_paid_phase_key(ticket))
    return ticket


def _settle_paid_phase(
    ticket: PaidPhase, *, amount_krw: float, billing_uncertain: bool
) -> None:
    """동시 마감끼리 오래된 inflight 스냅샷을 서로 장애로 오인하지 않게 한다."""
    with _PAID_PHASE_LOCK:
        _settle_paid_phase_locked(
            ticket,
            amount_krw=amount_krw,
            billing_uncertain=billing_uncertain,
        )


def _settle_paid_phase_locked(
    ticket: PaidPhase, *, amount_krw: float, billing_uncertain: bool
) -> None:
    """확정 응답은 원자적으로 마감하고, API 예외면 알려진 돈과 표식을 함께 남긴다."""
    global _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    inserted = False
    remaining: tuple[spend_store.InflightSpend, ...] = ()
    try:
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            kwargs = dict(
                run_id=ticket.run_id,
                phase=ticket.phase,
                day=ticket.day,
                bucket=ticket.share_key,
                cost_krw=amount_krw,
                created_at=dt.datetime.now().isoformat(timespec="seconds"),
            )
            if billing_uncertain:
                inserted = spend_store.keep_inflight_with_known_spend(conn, **kwargs)
            else:
                inserted = spend_store.finish_inflight(conn, **kwargs)
            remaining = spend_store.list_inflight_day(conn, ticket.day)
    except Exception:  # noqa: BLE001 — 과금 뒤 저장 실패는 전역도 함께 닫는다
        logger.exception("비용 단계를 마감하지 못해 진짜 조사를 닫습니다")
        _BUDGET_STORE_HEALTHY = False
        with _SLOT_LOCK:
            _ACTIVE_PAID_PHASES.discard(_paid_phase_key(ticket))
            _UNRESOLVED_BUCKETS.add((ticket.day.isoformat(), ticket.bucket_id))
        # DB 커밋 여부를 확정할 수 없으므로 메모리에는 보수적으로 한 번 센다.
        _add_memory_spend(ticket, amount_krw)
        return
    try:
        if inserted:
            _add_memory_spend(ticket, amount_krw)
        _finish_active_phase(ticket, remaining)
    except Exception:  # noqa: BLE001 — DB 마감 뒤 메모리 정리도 fail-closed다
        logger.exception("비용 원장 마감 뒤 메모리 장부를 맞추지 못했습니다")
        _BUDGET_STORE_HEALTHY = False
        with _SLOT_LOCK:
            _ACTIVE_PAID_PHASES.discard(_paid_phase_key(ticket))
            _UNRESOLVED_BUCKETS.add((ticket.day.isoformat(), ticket.bucket_id))


def _cancel_paid_phase(ticket: PaidPhase) -> None:
    """시작 취소도 다른 시작·마감과 같은 순서열 안에서 처리한다."""
    with _PAID_PHASE_LOCK:
        _cancel_paid_phase_locked(ticket)


def _cancel_paid_phase_locked(ticket: PaidPhase) -> None:
    """provider를 아직 부르지 않았음이 확실한 작업 등록 실패에서만 표식을 취소한다."""
    global _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    try:
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            spend_store.cancel_inflight(
                conn,
                run_id=ticket.run_id,
                phase=ticket.phase,
                day=ticket.day,
                bucket=ticket.share_key,
            )
            remaining = spend_store.list_inflight_day(conn, ticket.day)
    except Exception:  # noqa: BLE001 — 취소 실패 표식은 지우지 않은 쪽으로 본다
        logger.exception("시작 전 비용 표식을 취소하지 못해 진짜 조사를 닫습니다")
        _BUDGET_STORE_HEALTHY = False
        with _SLOT_LOCK:
            _ACTIVE_PAID_PHASES.discard(_paid_phase_key(ticket))
            _UNRESOLVED_BUCKETS.add((ticket.day.isoformat(), ticket.bucket_id))
        return
    _finish_active_phase(ticket, remaining)


def _model_tuple(*models: str) -> tuple[str, ...]:
    """빈 값과 중복을 빼고 실제로 쓴 모델 순서만 남긴다."""
    pieces = (
        piece.strip()
        for label in models
        if label
        for piece in label.split(MODEL_LABEL_SEPARATOR)
        if piece.strip()
    )
    return tuple(dict.fromkeys(pieces))


def _model_label(models: tuple[str, ...]) -> str:
    """고정된 이력 한 칸에 여러 유료 단계의 모델을 빠짐없이 표시한다."""
    return MODEL_LABEL_SEPARATOR.join(models)


def _observation_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _begin_observation_pending(
    *,
    run_id: str,
    job: str,
    cost_krw: float,
    elapsed_sec: float,
    model: str,
) -> bool:
    """확인 카드와 최종 이력 사이의 최소·비식별 대기표를 영속 저장한다."""
    started = dt.datetime.now()
    try:
        with storage_db.connect() as conn:
            lifecycle.ensure_schema(conn)
            lifecycle.begin_pending(
                conn,
                run_id=run_id,
                at=started.isoformat(timespec="seconds"),
                job=safe_observation_job(job),
                confirmed_cost_krw=cost_krw,
                elapsed_sec=round(elapsed_sec, 1),
                model=model,
                expires_at=(started + dt.timedelta(seconds=JOB_KEEP_SEC)).isoformat(
                    timespec="seconds"
                ),
            )
        return True
    except Exception:  # noqa: BLE001 — 대기표 없이는 뒤 유료 단계를 이어가지 않는다
        logger.exception("관측 대기표를 저장하지 못해 확인 이후 유료 단계를 닫습니다")
        return False


def _mark_observation_running(run_id: str) -> bool:
    """한 확인 대기표를 정확히 한 실행 흐름만 소비한다."""
    try:
        with storage_db.connect() as conn:
            lifecycle.ensure_schema(conn)
            return lifecycle.consume_pending(
                conn,
                run_id,
                event_at=_observation_now(),
            )
    except Exception:  # noqa: BLE001 — 상태 소비 실패 뒤 provider를 부르면 중복 과금이다
        logger.exception("관측 대기표를 소비하지 못해 유료 단계를 시작하지 않습니다")
        return False


def _finalize_observation_entry(
    entry: lifecycle.LifecycleEntry,
    *,
    end_step: str,
) -> bool:
    """저장된 최소 정보만으로 만료·재시작 종료를 사실대로 마감한다."""
    return record_end(
        run_id=entry.run_id,
        job=entry.job,
        end_step=end_step,
        cost_krw=entry.confirmed_cost_krw,
        elapsed_sec=entry.elapsed_sec,
        model=entry.model,
        expected_state=entry.state,
    )


def _expire_observation_pending() -> None:
    """벽시계 기준으로 만료된 확인 대기표를 다음 요청 때 마감한다."""
    try:
        with storage_db.connect() as conn:
            lifecycle.ensure_schema(conn)
            expired = lifecycle.list_expired_pending(conn, now=_observation_now())
    except Exception:  # noqa: BLE001 — 다음 요청이나 관리자 조회에서 다시 시도한다
        logger.exception("만료된 관측 대기표를 읽지 못했습니다")
        return
    for entry in expired:
        _finalize_observation_entry(entry, end_step=obs.END_STEP_CONFIRM)


def _recover_observation_lifecycle() -> None:
    """서버 재시작으로 이어갈 수 없어진 확인·실행 상태를 최종 마감한다."""
    try:
        with storage_db.connect() as conn:
            lifecycle.ensure_schema(conn)
            spend_store.ensure_schema(conn)
            candidates = lifecycle.list_restart_candidates(conn)
            running_phases: dict[str, str | None] = {}
            for entry in candidates:
                if entry.state != lifecycle.STATE_RUNNING:
                    continue
                try:
                    running_phases[entry.run_id] = spend_store.get_inflight_phase(
                        conn, entry.run_id
                    )
                except ValueError:
                    # 한 run에 진행 단계가 여러 개인 손상 상태처럼 어느 단계였는지
                    # 단정할 수 없으면 일반 생성 실패로 보수적으로 마감한다.
                    logger.exception(
                        "재시작 비용 단계를 판별하지 못해 생성 실패로 마감합니다: %s",
                        entry.run_id,
                    )
                    running_phases[entry.run_id] = None
    except Exception:  # noqa: BLE001 — 서버는 띄우되 다음 정리 기회에 다시 시도한다
        logger.exception("재시작 관측·비용 상태를 읽지 못했습니다")
        return
    for entry in candidates:
        end_step = (
            obs.END_STEP_CONFIRM
            if entry.state == lifecycle.STATE_PENDING
            else (
                obs.END_STEP_IMAGE_ERROR
                if running_phases.get(entry.run_id) == SPEND_PHASE_OCR
                else obs.END_STEP_GENERATE
            )
        )
        _finalize_observation_entry(entry, end_step=end_step)



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
            record_end(
                run_id=attempt.run_id,
                job=attempt.user_input.job,
                end_step=obs.END_STEP_CONFIRM,
                cost_krw=attempt.lookup_cost_krw,
                elapsed_sec=attempt.elapsed_sec,
                model=_model_label(attempt.models),
                expected_state=lifecycle.STATE_PENDING,
            )
            del _PAID_ATTEMPTS[token]

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


def _ctx(request: Request, **kwargs) -> dict:
    """모든 화면이 공통으로 쓰는 값.

    ★ 로그인 상태를 «여기서» 싣는다. 화면마다 따로 넣으면 하나쯤 빠뜨리고,
      그 화면만 「로그인 안 한 것처럼」 보이게 된다.
    """
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    base = {
        "cell_labels": CELL_LABELS,
        # 내부 ``조각 N·종류``를 템플릿에서 직접 자르면 다른 출력과 다시 갈린다(P-127).
        "citation_number": citation_number,
        # 검증된 본문 아래의 근거 원문 문구 — 세 출력 형태가 core 값을 같이 쓴다(P-117).
        "raw_source_label": RAW_SOURCE_LABEL,
        "raw_source_note": RAW_SOURCE_NOTE,
        "max_retry": MAX_RETRY_INPUT,
        # 화면 머리의 배지가 «지금 진짜로 도는지»를 정직하게 말하게 한다.
        "is_real": not isinstance(_PIPELINE, DemoPipeline),
        # ⚠️ 이 값은 «보여주기»용이다. 진짜 차단은 require_admin()이 매 요청마다 한다.
        "auth_email": auth_logic.current_email(token),
        "auth_is_admin": auth_logic.is_admin_session(token),
        # 설정값 자체는 절대 싣지 않는다. 로그인 버튼이 지금 동작 가능한지만 알린다.
        "auth_login_available": auth_google.credentials_configured(),
    }
    base.update(kwargs)
    return base


def _retry_screen(
    request: Request,
    user_input: UserInput,
    retry: int,
    rejected: bool,
) -> HTMLResponse:
    """회사명을 다시 받는 화면.

    「못 찾음」과 「[아닙니다]」 둘 다 같은 화면으로 보낸다 — 사용자가 할 일이 같기 때문이다.
    ★ 여기서 「대상 아님」이라고 단정하지 않는다. 이름을 아직 못 맞춘 것뿐이다.
    """
    return templates.TemplateResponse(
        request=request,
        name="not_found.html",
        context=_ctx(
            request,
            user_input=user_input,
            retry=retry,
            rejected=rejected,
            # retry는 「지금까지 몇 번 찾아봤나」. 다음 시도가 상한을 넘으면 폼을 닫는다.
            exhausted=retry + 1 >= MAX_RETRY_INPUT,
            demo_companies=available_companies(),
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


# ══════════════════════════════════════════════════════════
# 1. 입력 화면
# ══════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def input_page(request: Request):
    """회사명·직무·주소·공고를 받는 첫 화면."""
    return templates.TemplateResponse(
        request=request,
        name="input.html",
        context=_ctx(request, demo_companies=available_companies(),
            demo_available=paths.demo_data_available(),
            share_link=_current_share_link(request),
        ),
    )


def _current_share_link(request: Request):
    """이 손님이 «어느 회사 링크»로 들어왔는지 (P-94).

    Args:
        request: 들어온 요청.

    Returns:
        발급해 둔 링크 정보. 열쇠가 없거나 못 찾으면 None.

    ★ 첫 화면이 그 회사를 «크게» 보여주기 위한 것이다 —
      인사팀이 자기 회사 이름을 바로 보는 것이 이 방식의 핵심이다.
    ★ 못 찾아도 **조용히 None**을 돌려준다. 링크가 닫혔다고 첫 화면까지
      막으면, 인사팀에게는 그냥 「안 되는 사이트」가 된다.
    """
    key = (request.cookies.get(KEY_COOKIE_NAME) or "").strip().lower()
    if not share_logic.is_valid_key(key):
        return None
    try:
        with storage_db.connect() as conn:
            return share_store.load(conn, key)
    except Exception:  # noqa: BLE001 — 링크를 못 읽어도 화면은 떠야 한다
        logger.exception("열쇠 링크를 못 읽었습니다")
        return None


@app.get(KEY_PATH_PREFIX + "/{key}")
async def open_share_link(request: Request, key: str):
    """회사별 «열쇠 링크»로 들어왔을 때 (P-94).

    하는 일 세 가지:
      ① 열쇠를 쿠키에 심는다 → 그다음부터 주소를 안 달고 다녀도 된다
      ② 「열어봤다」를 기록한다 → **인사팀이 내 포폴을 봤는지** 알 수 있다
      ③ 미리 구운 보고서가 있으면 **거기로 바로** 보낸다 (0원·즉시)

    ★ 열쇠가 이상하거나 없는 링크여도 **첫 화면으로 보낸다.** 오류를 띄우지 않는다 —
      인사팀 눈에는 그냥 「안 되는 사이트」로 보이고, 그게 가장 나쁜 결과다.
    """
    clean = (key or "").strip().lower()
    if not share_logic.is_valid_key(clean):
        return RedirectResponse("/", status_code=303)

    link = None
    try:
        with storage_db.connect() as conn:
            link = share_store.load(conn, clean)
            if link is not None:
                share_store.mark_opened(conn, clean, dt.datetime.now().isoformat())
    except Exception:  # noqa: BLE001 — 기록 실패가 손님을 막으면 안 된다
        logger.exception("열쇠 링크 기록 실패")

    if link is None:
        return RedirectResponse("/", status_code=303)

    # ★ 미리 구운 보고서가 있으면 거기로 바로 보낸다 — 파이프라인을 안 거치므로
    #   **0원이고, 링크 예산이 다 됐어도 열린다** (2026-08-16 사용자 결정).
    target = f"/result/{link.report_id}" if link.is_baked else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        KEY_COOKIE_NAME,
        clean,
        max_age=KEY_COOKIE_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return response


# ══════════════════════════════════════════════════════════
# 2. 확인 카드 — 회사 1곳만 제시하고 사람이 확인한다
# ══════════════════════════════════════════════════════════

def _find_company_metered(user_input: UserInput) -> CompanyLookupResult:
    """유료 알맹이는 계량 약속이 없으면 호출 자체를 하지 않는다."""
    metered = getattr(_PIPELINE, "find_company_metered", None)
    if callable(metered):
        result = metered(user_input)
        if isinstance(result, CompanyLookupResult):
            return result
        raise TypeError("find_company_metered()가 약속한 결과 모양을 돌려주지 않았습니다")
    if isinstance(_PIPELINE, DemoPipeline):
        return CompanyLookupResult(card=_PIPELINE.find_company(user_input))
    logger.error("유료 알맹이에 find_company_metered()가 없어 회사 식별을 막았습니다")
    return CompanyLookupResult(card=None, failed=True)

@app.post("/confirm", response_class=HTMLResponse)
async def confirm_page(
    request: Request,
    company: str = Form(...),
    job: str = Form(...),
    region: str = Form(...),
    posting_text: str = Form(""),
    retry: int = Form(0),
):
    """찾은 회사 하나를 보여주고 [맞습니다]/[아닙니다]를 받는다.

    ★ 이 화면이 「AI가 실재하는 다른 회사를 답하는 것」의 유일한 방어선이다.
    """
    user_input = UserInput(
        company=company.strip(),
        job=job.strip(),
        region=region.strip(),
        posting_text=posting_text,
    )
    # 재입력 상한 — 없으면 재입력마다 층2 AI 5회가 새로 돌아 비용이 무한히 곱해진다.
    if retry >= MAX_RETRY_INPUT:
        return _retry_screen(request, user_input, retry, rejected=False)

    is_paid = not isinstance(_PIPELINE, DemoPipeline)
    share_key = PUBLIC_BUCKET
    run_id = uuid.uuid4().hex[:REPORT_ID_HEX_CHARS]
    lookup_started = time.perf_counter()
    resolved_track: Optional[tuple[share_tracks.Track, str, float]] = None
    identify_phase: Optional[PaidPhase] = None
    slot_bucket_id = ""
    if is_paid:
        # ★ P-125 — 회사 식별 AI보다 먼저 기존 네 문(갈래·횟수·동시실행·예산)을 지난다.
        # 권한·통장을 한 번만 읽는다. 가드 뒤 DB가 잠깐 실패해 PUBLIC으로 바뀌어도
        # 이미 허용한 유료 호출을 엉뚱한 통장에 적는 TOCTOU 틈을 만들지 않는다.
        resolved_track = _track_of(request)
        blocked = _guard_run(request, resolved_track=resolved_track)
        if blocked is not None:
            return blocked
        share_key = resolved_track[1]
        slot_bucket_id = _reserve_run_slot(resolved_track[0], share_key) or ""
        if not slot_bucket_id:
            return _throttled(request, BUSY_MESSAGE, "busy")
        identify_phase = _begin_paid_phase(
            run_id=run_id,
            phase=SPEND_PHASE_IDENTIFY,
            share_key=share_key,
        )
        if identify_phase is None:
            _release_run_slot(slot_bucket_id)
            return _throttled(request, BUSY_MESSAGE, "budget-store")

    lookup_task: Optional[asyncio.Task[CompanyLookupResult]] = None
    try:
        try:
            # 회사 식별도 AI를 부를 수 있다. 본조사와 마찬가지로 별도 스레드에서
            # 돌려야 다섯 사용자의 화면이 한 요청 때문에 차례로 멈추지 않는다.
            lookup_task = asyncio.create_task(
                asyncio.to_thread(_find_company_metered, user_input)
            )
            lookup = await asyncio.shield(lookup_task)
            if not isinstance(lookup, CompanyLookupResult):
                raise TypeError("회사 식별 결과 계약이 올바르지 않습니다")
        except asyncio.CancelledError:
            # 실제 provider 스레드가 끝날 때까지 자리를 쥔다. 정리 중 다시 취소돼도
            # helper가 shield를 반복해 실동시성이 5를 넘지 않게 한다.
            try:
                if lookup_task is None:
                    raise RuntimeError("회사 식별 worker가 만들어지지 않았습니다")
                lookup = await _await_worker_after_cancel(lookup_task)
                if not isinstance(lookup, CompanyLookupResult):
                    raise TypeError("회사 식별 결과 계약이 올바르지 않습니다")
            except BaseException:  # noqa: BLE001 — 결과를 끝내 모르면 통장만 닫는다
                if identify_phase is not None:
                    _settle_paid_phase(
                        identify_phase, amount_krw=0.0, billing_uncertain=True
                    )
            else:
                if identify_phase is not None:
                    _settle_paid_phase(
                        identify_phase,
                        amount_krw=lookup.cost_krw,
                        billing_uncertain=lookup.billing_uncertain,
                    )
            raise
        except Exception:  # noqa: BLE001 — 계량 계약 오류도 회사 없음으로 바꾸지 않는다
            logger.exception("회사 식별 연결이 실패했습니다")
            lookup = CompanyLookupResult(
                card=None,
                failed=True,
                billing_uncertain=is_paid,
            )

        lookup_elapsed = time.perf_counter() - lookup_started
        if identify_phase is not None:
            _settle_paid_phase(
                identify_phase,
                amount_krw=lookup.cost_krw,
                billing_uncertain=lookup.billing_uncertain,
            )
    finally:
        if is_paid:
            _release_run_slot(slot_bucket_id)

    card = lookup.card
    if lookup.failed or lookup.billing_uncertain:
        if is_paid:
            record_end(
                run_id=run_id,
                job=user_input.job,
                end_step=obs.END_STEP_IDENTIFY_ERROR,
                cost_krw=lookup.cost_krw,
                elapsed_sec=lookup_elapsed,
                model=lookup.model,
            )
        return _lookup_failed_screen(request)
    if card is None:
        if is_paid:
            # 여기서 실제로 끝났으므로 기존 `01_식별` 종료값과 뜻이 정확히 맞는다.
            record_run(
                user_input,
                RunResult(
                    outcome=Outcome.NOT_FOUND,
                    cost_krw=lookup.cost_krw,
                    model=lookup.model,
                ),
                lookup_elapsed,
                run_id=run_id,
            )
        return _retry_screen(request, user_input, retry, rejected=False)

    attempt_token = ""
    if is_paid:
        lookup_models = _model_tuple(lookup.model)
        if not _begin_observation_pending(
            run_id=run_id,
            job=user_input.job,
            cost_krw=lookup.cost_krw,
            elapsed_sec=lookup_elapsed,
            model=_model_label(lookup_models),
        ):
            return _throttled(request, BUSY_MESSAGE, "observation-store")
        attempt_token = uuid.uuid4().hex
        _PAID_ATTEMPTS[attempt_token] = PaidAttempt(
            token=attempt_token,
            run_id=run_id,
            user_input=user_input,
            card=card,
            share_key=share_key,
            bucket_id=spend_store.bucket_id(share_key),
            lookup_cost_krw=lookup.cost_krw,
            models=lookup_models,
            elapsed_sec=lookup_elapsed,
            created_at=time.monotonic(),
        )

    return templates.TemplateResponse(
        request=request,
        name="confirm.html",
        context=_ctx(
            request,
            user_input=user_input,
            card=card,
            retry=retry,
            paid_attempt_token=attempt_token,
        ),
    )


@app.post("/reject", response_class=HTMLResponse)
async def reject_card(
    request: Request,
    company: str = Form(...),
    job: str = Form(...),
    region: str = Form(...),
    posting_text: str = Form(""),
    retry: int = Form(0),
    paid_attempt_token: str = Form(""),
):
    """확인 카드에서 [아닙니다]를 눌렀을 때 — 회사명을 다시 받는다.

    같은 이름을 다시 넣으면 같은 답이 나오므로 다른 이름을 권한다.
    """
    user_input = UserInput(
        company=company.strip(),
        job=job.strip(),
        region=region.strip(),
        posting_text=posting_text,
    )
    if paid_attempt_token:
        attempt = _PAID_ATTEMPTS.pop(paid_attempt_token, None)
        if attempt is not None:
            record_end(
                run_id=attempt.run_id,
                job=attempt.user_input.job,
                end_step=obs.END_STEP_CONFIRM,
                cost_krw=attempt.lookup_cost_krw,
                elapsed_sec=attempt.elapsed_sec,
                model=_model_label(attempt.models),
                expected_state=lifecycle.STATE_PENDING,
            )
    return _retry_screen(request, user_input, retry, rejected=True)


# ══════════════════════════════════════════════════════════
# 3. 진행 화면 — 최대 5분 걸리므로 단계를 보여준다
# ══════════════════════════════════════════════════════════

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
        worker = asyncio.create_task(
            asyncio.to_thread(
                _PIPELINE.run, job.user_input, job.card, _mark_step(job)
            )
        )
        job.result = await asyncio.shield(worker)
        if not isinstance(job.result, RunResult):
            raise TypeError("파이프라인 결과 계약이 올바르지 않습니다")
    except asyncio.CancelledError:
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
            if job.paid_phase is not None:
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
            _release_run_slot(spend_store.bucket_id(job.share_key))


def _save_report(job: Job) -> None:
    """만든 보고서를 파일 저장소에 남긴다.

    ★ 이게 없으면 **서버를 끄는 순간 보고서가 사라진다** (메모리에만 있었다).
    ★ 저장이 실패해도 사용자가 화면을 못 보게 만들지 않는다 — 화면은 메모리에도 있다.
    """
    if job.result is None or job.result.report is None:
        return
    try:
        with storage_db.connect() as conn:
            report_store.save(
                conn,
                report_id=job.job_id,
                corp_id=job.card.ref or job.card.legal_name,
                job=job.user_input.job,
                report=job.result.report,
            )
    except Exception:  # noqa: BLE001 — 저장 실패가 사용자를 막으면 안 된다
        logger.exception("보고서 저장 실패 (화면은 정상)")


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
        templates.TemplateResponse(
            request=request,
            name="expired.html",
            context=_ctx(request, expired_message=LINK_EXPIRED_MESSAGE),
            status_code=410,                  # 410 Gone = 「있었는데 이제 없다」
        )
    )


def _link_expired(report: Optional[Report]) -> bool:
    """이 보고서 링크가 기간이 지났는가."""
    if report is None:
        return False
    return link_expiry.is_expired(
        report.generated_at, dt.date.today(), REPORT_LINK_MAX_AGE_DAYS
    )


def _load_saved_report(report_id: str) -> Optional[Report]:
    """서버를 껐다 켠 뒤에도 옛 보고서를 다시 볼 수 있게 한다."""
    try:
        with storage_db.connect() as conn:
            return report_store.load(conn, report_id)
    except Exception:  # noqa: BLE001
        logger.exception("보고서 불러오기 실패")
        return None


#: 데모에서 사진을 올렸을 때 보여줄 말. ★ 데모는 AI를 안 부른다 (돈).
_DEMO_IMAGE_NOTICE = (
    "지금은 데모라 사진을 읽지 않습니다. "
    "공고 «글자»를 붙여넣어 주세요 — 진짜 조사 모드에서는 사진도 읽습니다."
)


def _schedule_job(job: Job) -> None:
    """배경 작업 등록 한 곳. 등록 실패 때 슬롯·비용 표식을 시험할 수 있게 분리한다."""
    asyncio.create_task(_run_job(job))


async def _start_with_reserved_slot(
    *,
    request: Request,
    original_input: UserInput,
    card: CompanyCard,
    posting_images: list[UploadFile],
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
    try:
        # 사진으로 올린 공고는 «여기서 처음» 서버에 들어온다 (기획서 D2 — 판정 통과 후).
        # ★ 원본 바이트를 파일·로그·결과 어디에도 남기지 않는다 (S2).
        posting_body = original_input.posting_text
        image_error = ""
        image_failure_kind = ""
        if posting_images:
            image_bytes = [await f.read() for f in posting_images if f.filename]
            if image_bytes and not is_paid:
                del image_bytes          # ★ 안 쓸 거면 «바로» 버린다 (S2)
                image_error = _DEMO_IMAGE_NOTICE
                image_failure_kind = "input"
            elif image_bytes:
                ocr_phase = _begin_paid_phase(
                    run_id=run_id,
                    phase=SPEND_PHASE_OCR,
                    share_key=share_key,
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
                    return _throttled(request, BUSY_MESSAGE, "budget-store")
                ocr_started = time.perf_counter()
                ocr_task = asyncio.create_task(
                    asyncio.to_thread(
                        extract_posting_text, image_bytes, extract=default_extract
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
                if outcome.ok and not outcome.billing_uncertain:
                    posting_body = outcome.text
                else:
                    image_error = outcome.error
                    image_failure_kind = outcome.failure_kind

                if not image_error:
                    # OCR 비용이 남은 몫을 다 썼다면 본조사 AI는 시작하지 않는다.
                    blocked = _guard_run(
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
            return templates.TemplateResponse(
                request=request,
                name="input.html",
                context=_ctx(
                    request,
                    image_error=image_error,
                    prefill=user_input,
                    demo_companies=available_companies(),
                    demo_available=paths.demo_data_available(),
                ),
            )

        if is_paid:
            # route가 죽어도 첫 provider 호출 전 표식은 이미 커밋돼 있어야 한다.
            pipeline_phase = _begin_paid_phase(
                run_id=run_id,
                phase=SPEND_PHASE_PIPELINE,
                share_key=share_key,
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
                return _throttled(request, BUSY_MESSAGE, "budget-store")

        new_job = Job(
            job_id=run_id,
            user_input=user_input,
            card=card,
            share_key=share_key,
            upfront_cost_krw=upfront_cost,
            upfront_models=upfront_models,
            upfront_elapsed_sec=upfront_elapsed,
            paid_phase=pipeline_phase,
        )
        _JOBS[run_id] = new_job
        try:
            _schedule_job(new_job)
        except BaseException:
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
        if not handed_off:
            _release_run_slot(slot_bucket_id)


@app.post("/run")
async def start_run(
    request: Request,
    company: str = Form(...),
    job: str = Form(...),
    region: str = Form(...),
    posting_text: str = Form(""),
    legal_name: str = Form(""),
    ref: str = Form(""),
    address: str = Form(""),
    paid_attempt_token: str = Form(""),
    posting_images: list[UploadFile] = File(default=[]),
):
    """[맞습니다]를 누르면 작업을 시작하고 진행 화면으로 보낸다.

    ★ 회사를 **다시 찾지 않는다.** 확인 카드가 이미 답(`ref`)을 들고 왔다.
      다시 찾으면 이름 대조 AI가 최대 5회 통째로 또 나간다.
    """
    original_input = UserInput(
        company=company.strip(),
        job=job.strip(),
        region=region.strip(),
        posting_text=posting_text,
    )
    is_paid = not isinstance(_PIPELINE, DemoPipeline)
    attempt: Optional[PaidAttempt] = None
    slot_bucket_id = ""

    if not ref:
        # 확인 카드를 거치지 않고 들어온 요청 — 처음부터 다시 하게 한다.
        return RedirectResponse("/", status_code=303)

    # 권한·통장·상한을 이 요청에서 딱 한 번 정한다. 가드와 원장·토큰 대조가
    # 서로 다른 순간의 DB 결과를 쓰면 허용한 갈래와 돈을 적는 갈래가 달라진다.
    resolved_track = _track_of(request)
    share_key = resolved_track[1]
    if is_paid:
        # 주소를 직접 부른 요청도 먼저 현재 갈래·동시실행·예산으로 막는다.
        # 확인 토큰이 없으면 아래에서 끝나므로 횟수는 새로 깎지 않는다.
        blocked = _guard_run(
            request, count_start=False, resolved_track=resolved_track
        )
        if blocked is not None:
            return blocked
        attempt = _PAID_ATTEMPTS.get(paid_attempt_token)
        current_bucket = spend_store.bucket_id(share_key)
        # ★ 숨은 입력값은 사용자가 바꿀 수 있다. 서버에 기억한 입력·회사·통장과
        # 하나라도 다르면 일회용 확인을 거친 요청이 아니다. AI를 부르지 않는다.
        if (
            attempt is None
            or attempt.user_input != original_input
            or attempt.card.ref != ref
            or attempt.bucket_id != current_bucket
        ):
            return RedirectResponse("/", status_code=303)

        slot_bucket_id = _reserve_run_slot(resolved_track[0], share_key) or ""
        if not slot_bucket_id:
            return _throttled(request, BUSY_MESSAGE, "busy")

        # 같은 조사 안에서 회사 식별 때 횟수는 이미 한 번 셌다. OCR/본조사 앞에서는
        # 동시실행·현재 예산·갈래만 다시 확인해 그 한 건을 두 번 세지 않는다.
        if not _mark_observation_running(attempt.run_id):
            _release_run_slot(slot_bucket_id)
            _PAID_ATTEMPTS.pop(paid_attempt_token, None)
            return RedirectResponse("/", status_code=303)
        _PAID_ATTEMPTS.pop(paid_attempt_token, None)  # 한 번만 쓸 수 있다
        card = attempt.card
        run_id = attempt.run_id
        upfront_cost = attempt.lookup_cost_krw
        upfront_models = attempt.models
        upfront_elapsed = attempt.elapsed_sec
    else:
        # 데모는 식별이 0원이라 여기서 기존 횟수·동시실행 검사를 한 번만 한다.
        blocked = _guard_run(request, resolved_track=resolved_track)
        if blocked is not None:
            return blocked
        slot_bucket_id = _reserve_run_slot(resolved_track[0], share_key) or ""
        if not slot_bucket_id:
            return _throttled(request, BUSY_MESSAGE, "busy")
        card = CompanyCard(
            legal_name=legal_name or company.strip(),
            typed_name=company.strip(),
            address=address,
            ceo="",
            founded="",
            ref=ref,
        )
        run_id = uuid.uuid4().hex[:REPORT_ID_HEX_CHARS]
        upfront_cost = 0.0
        upfront_models = ()
        upfront_elapsed = 0.0

    # 검사 직후 잡은 자리는 업로드 읽기부터 본조사 완료까지 이어진다. 아래 helper의
    # finally 또는 `_run_job()` 둘 중 정확히 한 곳이 돌려준다.
    return await _start_with_reserved_slot(
        request=request,
        original_input=original_input,
        card=card,
        posting_images=posting_images,
        is_paid=is_paid,
        resolved_track=resolved_track,
        run_id=run_id,
        upfront_cost=upfront_cost,
        upfront_models=upfront_models,
        upfront_elapsed=upfront_elapsed,
        slot_bucket_id=slot_bucket_id,
    )


def _raw_share_key(request: Request) -> str:
    """쿠키 열쇠가 실제 발급돼 지금도 살아 있을 때만 돌려준다.

    ★ 16진수 모양만 보면 공격자가 쿠키를 바꿀 때마다 새 3,000원 통장을 만들 수
      있다. 발급 저장소에 없거나 이미 삭제된 키는 PUBLIC(0원)으로 되돌린다.
    """
    key = (request.cookies.get(KEY_COOKIE_NAME) or "").strip().lower()
    if not share_logic.is_valid_key(key):
        return ""
    try:
        with storage_db.connect() as conn:
            return key if share_store.load(conn, key) is not None else ""
    except Exception:  # noqa: BLE001 — 링크 확인 실패는 권한을 주지 않는 쪽으로
        logger.exception("열쇠 링크를 확인하지 못해 공개 손님으로 봅니다")
        return ""


def _track_of(request: Request) -> tuple[share_tracks.Track, str, float]:
    """이 손님이 «어느 갈래»이고, «어느 통장»에서, «얼마»까지 쓸 수 있는가.

    Args:
        request: 들어온 요청.

    Returns:
        (갈래, 통장 이름, 하루 상한).

    ★ **로그인만으로는 아무 권한도 안 준다** (P-95).
      구글 로그인은 「누구인가」만 알려준다. 「써도 되는가」는 **초대 명단**이 정한다.
      이걸 안 나누면 인터넷의 아무나 로그인해서 돈을 쓴다.
    ★ 명단을 못 읽으면 «초대 안 된 사람»으로 본다 — 안전한 쪽으로 틀린다.
    """
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    email = auth_logic.current_email(token) or ""
    is_admin = auth_logic.is_admin_session(token)

    is_member = False
    if email and not is_admin:
        try:
            with storage_db.connect() as conn:
                is_member = share_allow.is_allowed(conn, email)
        except Exception:  # noqa: BLE001 — 못 읽으면 «안 된 사람»으로 본다
            logger.exception("초대 명단을 못 읽었습니다 — 초대 안 된 것으로 봅니다")

    key = _raw_share_key(request)
    track = share_tracks.decide_track(
        email=email, is_admin=is_admin, is_member=is_member, share_key=key
    )
    bucket = share_tracks.bucket_of(track, email=email, share_key=key)
    return track, bucket, share_tracks.budget_of(track)


def _client_key(request: Request) -> str:
    """횟수를 셀 때 사람을 가르는 열쇠.

    Args:
        request: 들어온 요청.

    Returns:
        보통은 접속한 IP. 못 알아내면 `"unknown"`.

    ⚠️ **완벽한 신원이 아니다.** 같은 공유기를 쓰면 여러 사람이 한 열쇠로 묶이고,
      마음먹으면 IP를 바꿀 수도 있다. 이 값의 목적은 「신원 확인」이 아니라
      **「한 사람이 몰아치는 것을 늦추기」**다. 진짜 상한은 «하루 예산»이 잡는다.
    """
    client = request.client
    return client.host if client is not None else "unknown"


def _guard_run(
    request: Request,
    *,
    count_start: bool = True,
    owns_slot: bool = False,
    resolved_track: Optional[tuple[share_tracks.Track, str, float]] = None,
) -> Optional[HTMLResponse]:
    """조사를 시작해도 되는지 보고, 안 되면 «막는 화면»을 돌려준다.

    Args:
        request: 들어온 요청.

    Returns:
        막아야 하면 보여줄 화면. 시작해도 되면 None.

    ★ 통과할 때만 횟수를 적는다 — 거절당한 요청까지 세면
      「돈도 안 썼는데 차단」이 된다 (budget/logic.py 참고).
    """
    now = time.monotonic()
    _sweep_jobs(now)

    ip = _client_key(request)
    # ★ 예산은 «링크마다» 센다 (P-94, 2026-08-16 사용자 결정).
    #   전체 상한은 두지 않는다 — 대신 링크 하나가 하루에 쓸 수 있는 몫을 정했다.
    #   ⚠️ 그러므로 **최악의 하루 지출 = 링크당 상한 × 살아 있는 링크 수**다.
    #     링크를 몇 개 뿌렸는지가 곧 예산이다 (관리 화면에서 확인).
    costs_money = not isinstance(_PIPELINE, DemoPipeline)
    track, bucket, cap = resolved_track or _track_of(request)
    stored_bucket = spend_store.bucket_id(bucket)
    with _SLOT_LOCK:
        unresolved = (
            dt.date.today().isoformat(), stored_bucket
        ) in _UNRESOLVED_BUCKETS
    # 메모리 장부 모양을 바꾸기 전부터 있던 호출부·운영 중 갱신을 안전하게 잇는다.
    # 새 원장 복원값은 지문 키만 쓰고, 원문 키가 실제로 있을 때만 옛 값을 읽는다.
    if (
        share_logic.spent_for(_LINK_SPEND, stored_bucket, dt.date.today()) <= 0
        and share_logic.spent_for(_LINK_SPEND, bucket, dt.date.today()) > 0
    ):
        stored_bucket = bucket
    budget_exhausted = costs_money and not share_logic.can_start_new_run(
        _LINK_SPEND, stored_bucket, dt.date.today(), cap
    )
    if count_start and not budget_logic.rate_ok(
        _RATE_HISTORY, ip, now, window_sec=RATE_WINDOW_SEC, max_runs=RATE_MAX_RUNS
    ):
        return _throttled(request, RATE_LIMITED_MESSAGE, "rate")
    if _slot_is_full(track, bucket, owns_slot=owns_slot):
        return _throttled(request, BUSY_MESSAGE, "busy")
    if costs_money and not _BUDGET_STORE_HEALTHY:
        # 원장이 고장 난 채 열어 두면 재시작 뒤 상한을 보장할 수 없다.
        return _throttled(request, BUSY_MESSAGE, "budget-store")
    if costs_money and unresolved:
        # provider 응답 전에 서버가 죽었거나 API 예외로 과금 여부가 불명확하다.
        # 다른 통장은 살리고 이 통장만 사람이 원장을 확인할 때까지 닫는다.
        return _throttled(request, BUSY_MESSAGE, "budget-unresolved")
    if budget_exhausted:
        # ★ 예산이 다 돼도 **이미 만들어 둔 보고서는 계속 열린다** —
        #   그건 파이프라인을 안 거치고 저장소에서 바로 꺼내므로 0원이다.
        #   막는 것은 «새로 AI를 부르는 일»뿐이다 (2026-08-16 사용자 결정).
        # ★ 모르는 손님(상한 0원)에게는 «다른 말»을 한다 — 「다 썼다」가 아니라
        #   「이 기능은 초대받은 분만」이다. 사실이 다르면 안내도 달라야 한다.
        message = (
            PUBLIC_NOT_ALLOWED_MESSAGE
            if track is share_tracks.Track.PUBLIC
            else LINK_BUDGET_EXHAUSTED_MESSAGE
        )
        return _throttled(request, message, f"budget:{track.value}")

    # ★ 통과할 때만 횟수를 적는다 — 거절당한 요청까지 세면
    #   「돈도 안 썼는데 차단」이 된다 (budget/logic.py 참고).
    if count_start:
        budget_logic.record_start(_RATE_HISTORY, ip, now)
    return None


def _throttled(request: Request, message: str, kind: str) -> HTMLResponse:
    """조사를 막았을 때 보여줄 화면.

    Args:
        request: 들어온 요청.
        message: 사용자에게 보여줄 말.
        kind: 왜 막았는지 (`rate` | `busy` | `budget`). 로그·화면 구분용.

    ★ 429는 「지금은 안 된다」이지 「고장」이 아니다. 화면이 그걸 분명히 말한다.
    """
    logger.info("조사를 막았습니다: %s", kind)
    return templates.TemplateResponse(
        request=request,
        name="throttled.html",
        context=_ctx(request, throttle_message=message, throttle_kind=kind),
        status_code=429,
    )


@app.get("/progress/{job_id}", response_class=HTMLResponse)
async def progress_page(request: Request, job_id: str):
    """단계별 진행을 보여주는 화면."""
    job = _JOBS.get(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="progress.html",
        context=_ctx(request, job=job, steps=PROGRESS_STEPS),
    )


@app.get("/api/progress/{job_id}")
async def progress_api(job_id: str):
    """진행 화면이 물어보는 곳. 끝났으면 어디로 갈지도 알려준다."""
    job = _JOBS.get(job_id)
    if job is None:
        return JSONResponse({"error": "없는 작업입니다"}, status_code=404)
    return JSONResponse(
        {
            "done": job.done_steps,
            "current": job.current_step,
            "finished": job.finished,
            "next_url": f"/result/{job_id}" if job.finished else "",
        }
    )


# ══════════════════════════════════════════════════════════
# 4. 결과 화면
# ══════════════════════════════════════════════════════════

@app.get("/result/{job_id}", response_class=HTMLResponse)
async def result_page(request: Request, job_id: str):
    """보고서 또는 「왜 못 만들었는지」를 보여준다.

    ★ 메모리에 없으면 «저장소»에서 찾는다 — 서버를 껐다 켜도 옛 보고서를 볼 수 있다.
    """
    job = _JOBS.get(job_id)
    if job is None or job.result is None:
        saved = _load_saved_report(job_id)
        if saved is None:
            return RedirectResponse("/", status_code=303)
        if _link_expired(saved):
            return _expired_screen(request)
        # 저장소에서 되살린 보고서 — 진행 상태는 이미 끝난 것으로 본다.
        return _shared(templates.TemplateResponse(
            request=request,
            name="result.html",
            context=_ctx(
                request,
                job=Job(
                    job_id=job_id,
                    user_input=UserInput(company=saved.company, job=saved.job, region=""),
                    card=CompanyCard(
                        legal_name=saved.company, typed_name=saved.company,
                        address="", ceo="", founded="",
                    ),
                    finished=True,
                ),
                result=RunResult(outcome=Outcome.REPORT, report=saved),
                report=saved,
                grade_note=grade_message(saved.grade, saved.filled_count),
            ),
        ))

    result = job.result

    # 보고서가 안 나온 경우 — 사유와 수집 현황은 보여준다.
    if result.outcome is not Outcome.REPORT or result.report is None:
        return templates.TemplateResponse(
        request=request,
        name="stopped.html",
        context=_ctx(request, job=job, result=result),
        )

    report = result.report
    if _link_expired(report):
        return _expired_screen(request)
    return _shared(templates.TemplateResponse(
        request=request,
        name="result.html",
        context=_ctx(request, job=job,
            result=result,
            report=report,
            grade_note=grade_message(report.grade, report.filled_count),
        ),
    ))


# ══════════════════════════════════════════════════════════
# 5. 로그인 — 구글이 「누구인가」, 우리 목록이 「관리자인가」 (결정기록 D15)
# ══════════════════════════════════════════════════════════

def _cookie_secure() -> bool:
    """쿠키를 HTTPS에서만 보낼지. ★ 기본은 «켜짐(안전)»이다.

    로컬 http 시험에서만 `AUTH_COOKIE_INSECURE=1`로 잠시 끈다.
    끄는 쪽을 명시하게 만든 이유 — 기본을 꺼 두면 배포할 때 켜는 걸 잊는다.
    """
    return os.environ.get(auth_constants.ENV_COOKIE_INSECURE, "").strip() != "1"


def require_admin(request: Request) -> Optional[RedirectResponse]:
    """관리자가 아니면 되돌릴 응답, 관리자면 None.

    ★ **매 요청마다 서버에서 다시 판정한다.** 버튼을 숨기는 것은 권한이 아니다
      (기획서 07_출력/4_근거 §4 · 성공기준 P4 「0건 고정」).
    """
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    if not auth_logic.is_admin_session(token):
        return RedirectResponse("/auth/not-admin", status_code=303)
    return None


@app.get("/auth/login")
async def auth_login(request: Request):
    """구글 로그인 화면으로 보낸다. 비밀번호는 구글이 받고 우리는 보지 않는다."""
    try:
        started = auth_google.start_login()
    except auth_google.MissingCredentialError as exc:
        # ★ 로그에는 빠진 설정 이름을 남기되 화면에는 이름이나 값을 노출하지 않는다.
        logger.error("구글 로그인 설정 오류: %s", exc)
        return templates.TemplateResponse(
            request=request,
            name="login_unavailable.html",
            context=_ctx(request),
            status_code=503,
        )
    response = RedirectResponse(started.auth_url, status_code=303)
    response.set_cookie(
        auth_constants.STATE_COOKIE_NAME,
        started.state,
        max_age=auth_constants.STATE_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return response


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = ""):
    """구글에서 돌아왔을 때. state를 반드시 대조한다 (CSRF 방어)."""
    expected = request.cookies.get(auth_constants.STATE_COOKIE_NAME)
    try:
        result = auth_google.handle_callback(code, state, expected)
    except Exception as exc:  # noqa: BLE001 — 실패 이유를 화면에 흘리지 않는다
        # ★ 화면에는 안 보여주지만 «로그에는» 이유를 남긴다.
        #   예전에는 예외 «이름»만 남겨서(`GoogleAuthError`) 원인을 못 찾았다.
        #   실제로 로그인이 조용히 실패했는데 로그만 보고는 아무것도 알 수 없었다.
        #   메시지에는 열쇠·코드가 안 담기게 `auth/google.py`가 이미 막아 두었다.
        logger.warning("로그인 실패: %s — %s", type(exc).__name__, exc)
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(auth_constants.STATE_COOKIE_NAME)
        return response

    response = RedirectResponse(
        "/" if result.is_admin else "/auth/not-admin", status_code=303
    )
    response.delete_cookie(auth_constants.STATE_COOKIE_NAME)
    response.set_cookie(
        auth_constants.SESSION_COOKIE_NAME,
        result.session.token,
        max_age=auth_constants.SESSION_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return response


@app.get("/auth/not-admin", response_class=HTMLResponse)
async def auth_not_admin(request: Request):
    """로그인은 됐지만 관리자가 아닐 때."""
    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    return templates.TemplateResponse(
        request=request,
        name="not_admin.html",
        context=_ctx(request),
    )


@app.get("/download/{job_id}")
async def download_docx(request: Request, job_id: str):
    """보고서를 워드 파일로 내려준다.

    ★ 화면과 «같은 내용»이어야 한다 (기획서 07_출력 P3). 그래서 워드 쪽도
      화면과 같은 상수·같은 함수를 쓴다 — 따로 그리면 한쪽만 고쳤을 때 갈린다.
    """
    # ★ 메모리에 없으면 저장소에서 찾는다 — 서버를 껐다 켜도 내려받을 수 있어야 한다.
    job = _JOBS.get(job_id)
    report = (
        job.result.report
        if job is not None and job.result is not None
        else _load_saved_report(job_id)
    )
    if report is None:
        return RedirectResponse("/", status_code=303)
    if _link_expired(report):
        return _expired_screen(request)
    return Response(
        content=build_docx(report),
        media_type=CONTENT_TYPE_DOCX,
        headers={
            "Content-Disposition": build_content_disposition(
                build_download_filename(report)
            ),
            **SHARED_LINK_HEADERS,
        },
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    """관리자 첫 화면. ★ 권한은 여기서 «서버가» 다시 판정한다."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    return templates.TemplateResponse(
        request=request, name="admin_home.html", context=_ctx(request)
    )


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """운영자 품질 대시보드. ★ 관리자만 — 매 요청마다 서버가 다시 판정한다."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    _sweep_jobs(time.monotonic())
    read = read_records(records_path())
    final_records = []
    try:
        with storage_db.connect() as conn:
            lifecycle.ensure_schema(conn)
            final_records = lifecycle.list_final(conn)
    except Exception:  # noqa: BLE001 — JSONL 옛 이력은 계속 보여 주되 손상을 알린다
        logger.exception("SQLite 최종 관측값을 읽지 못했습니다")

    month_spend = None
    try:
        with _PAID_PHASE_LOCK:
            with _SLOT_LOCK:
                known_active = frozenset(_ACTIVE_PAID_PHASES)
            with storage_db.connect() as conn:
                spend_store.ensure_schema(conn)
                month_spend = spend_store.load_month(
                    conn,
                    dt.date.today(),
                    known_active=known_active,
                )
    except Exception:  # noqa: BLE001 — 0원으로 꾸미지 않고 화면에 확인 불가로 표시한다
        logger.exception("월 비용 원장을 읽지 못했습니다")

    dashboard = build_dashboard(
        [*read.records, *final_records],
        today=dt.date.today(),
        model=_current_model(),
        cost_month_krw_override=(
            month_spend.total_krw if month_spend is not None else None
        ),
        cost_ledger_error=month_spend is None,
        unresolved_cost_runs=(
            month_spend.unresolved_runs if month_spend is not None else 0
        ),
        cost_ledger_since=(month_spend.ledger_since if month_spend is not None else ""),
    )
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=_ctx(request, dashboard=dashboard, skipped=read.skipped),
    )


# ══════════════════════════════════════════════════════════
# 관리 — 누가 유료 기능을 쓸 수 있나 (문제로그 P-96)
# ══════════════════════════════════════════════════════════
# ★ 이 화면이 없으면 기능이 있어도 «쓸 방법»이 없다 — DB에 손으로 넣어야 했다.
# ★ 모든 경로가 require_admin()을 «먼저» 부른다. 화면에서 버튼을 숨기는 것은 방어가 아니다.


def _access_context(request: Request) -> dict:
    """초대·링크 관리 화면이 쓸 값을 모은다."""
    links: list = []
    members: list = []
    try:
        with storage_db.connect() as conn:
            links = share_store.list_all(conn)
            members = share_allow.list_all(conn)
    except Exception:  # noqa: BLE001 — 못 읽어도 화면은 떠야 한다
        logger.exception("초대·링크 목록을 못 읽었습니다")

    # ★ 「최악의 하루 지출」을 계산해 보여준다. 전체 상한을 두지 않기로 했으므로
    #   (사용자 결정), **뿌린 개수가 곧 예산**이다. 안 보여주면 얼마가 나갈지 모른다.
    worst = (
        len(links) * share_tracks.budget_of(share_tracks.Track.LINK)
        + len(members) * share_tracks.budget_of(share_tracks.Track.MEMBER)
        + share_tracks.budget_of(share_tracks.Track.ADMIN)
    )
    return _ctx(
        request,
        links=links,
        members=members,
        spent_today=share_logic.total_spent(_LINK_SPEND, dt.date.today()),
        worst_case_krw=worst,
    )


@app.get("/admin/access", response_class=HTMLResponse)
async def admin_access(request: Request):
    """초대한 친구와 회사별 링크를 관리하는 화면."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    return templates.TemplateResponse(
        request=request, name="admin_access.html", context=_access_context(request)
    )


@app.post("/admin/link/new")
async def admin_link_new(
    request: Request,
    company: str = Form(...),
    job: str = Form(...),
    note: str = Form(""),
):
    """회사별 링크를 새로 발급한다."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    key = share_issue.new_key()
    try:
        with storage_db.connect() as conn:
            share_store.save(
                conn, key=key, company=company.strip(), job=job.strip(),
                note=note.strip(), now_iso=dt.datetime.now().isoformat(),
            )
    except Exception:  # noqa: BLE001
        logger.exception("링크 발급 실패")
        return RedirectResponse("/admin/access", status_code=303)
    # ★ 발급 «직후» 주소·QR 화면으로 보낸다 — 목록으로 돌려보내면
    #   방금 만든 것을 다시 찾아 눌러야 한다.
    return RedirectResponse(f"/admin/link/{key}", status_code=303)


@app.get("/admin/link/{key}", response_class=HTMLResponse)
async def admin_link_detail(request: Request, key: str):
    """링크 하나의 주소와 QR."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    if not share_logic.is_valid_key(key):
        return RedirectResponse("/admin/access", status_code=303)
    try:
        with storage_db.connect() as conn:
            link = share_store.load(conn, key.lower())
    except Exception:  # noqa: BLE001
        logger.exception("링크를 못 읽었습니다")
        link = None
    if link is None:
        return RedirectResponse("/admin/access", status_code=303)

    # ★ 서비스 주소를 «지금 접속한 주소»에서 뽑는다 — 상수로 박으면
    #   배포한 뒤에도 localhost를 가리키는 링크가 나온다.
    base = share_issue.base_url_of(str(request.url))
    url = share_issue.link_url(base or "", link.key)
    return templates.TemplateResponse(
        request=request,
        name="admin_link.html",
        context=_ctx(request, link=link, url=url, base_url=base,
                     is_deployed=share_issue.looks_deployed(url),
                     qr_svg=share_issue.qr_svg(url)),
    )


@app.post("/admin/link/delete")
async def admin_link_delete(request: Request, key: str = Form(...)):
    """링크를 닫는다. ★ 되돌릴 방법이 있어야 한다 — 잘못 보냈거나 지원이 끝났을 때."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            share_store.delete(conn, key.strip().lower())
    except Exception:  # noqa: BLE001
        logger.exception("링크 삭제 실패")
    return RedirectResponse("/admin/access", status_code=303)


@app.post("/admin/invite")
async def admin_invite(
    request: Request, email: str = Form(...), note: str = Form("")
):
    """친구를 초대 명단에 넣는다 (P-95)."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            share_allow.invite(
                conn, email=email, note=note,
                now_iso=dt.datetime.now().isoformat(),
            )
    except Exception:  # noqa: BLE001
        logger.exception("초대 실패")
    return RedirectResponse("/admin/access", status_code=303)


@app.post("/admin/revoke")
async def admin_revoke(request: Request, email: str = Form(...)):
    """친구를 초대 명단에서 뺀다."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    try:
        with storage_db.connect() as conn:
            share_allow.revoke(conn, email)
    except Exception:  # noqa: BLE001
        logger.exception("초대 취소 실패")
    return RedirectResponse("/admin/access", status_code=303)


@app.post("/notion/{job_id}", response_class=HTMLResponse)
async def send_to_notion(request: Request, job_id: str):
    """보고서를 노션으로 보낸다. ★ 관리자만 (기획서 D10 · P4)."""
    blocked = require_admin(request)
    if blocked is not None:
        return blocked
    job = _JOBS.get(job_id)
    report = (
        job.result.report
        if job is not None and job.result is not None
        else _load_saved_report(job_id)
    )
    if report is None:
        return RedirectResponse("/", status_code=303)
    # 화면에 낸 것과 «같은 문자열»을 넘겨야 세 형태 내용이 갈리지 않는다 (P3).
    result = send_report_to_notion(
        report, grade_note=grade_message(report.grade, report.filled_count)
    )
    if not result.success:
        logger.warning("노션 전송 실패: %s", result.error)
    return templates.TemplateResponse(
        request=request,
        name="notion_result.html",
        context=_ctx(request, notion=result, report=report, job_id=job_id),
    )


@app.get("/auth/logout")
async def auth_logout(request: Request):
    """우리 쪽 세션만 지운다. 구글 계정 로그인 상태는 건드리지 않는다."""
    auth_logic.delete_session(request.cookies.get(auth_constants.SESSION_COOKIE_NAME))
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth_constants.SESSION_COOKIE_NAME)
    return response
