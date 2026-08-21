"""유료 호출의 비용 원장, 동시 실행 슬롯과 재시작 복구."""

from __future__ import annotations

import datetime as dt
import logging
import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from src.core import clock
from src.core.constants import MODEL_LABEL_SEPARATOR, REPLAY_MODEL_MARK
from src.features.budget import logic as budget_logic
from src.features.budget import provider_budget
from src.features.budget import spend_store
from src.features.budget.constants import (
    JOB_KEEP_SEC,
    MAX_CONCURRENT_PER_LINK,
    MAX_CONCURRENT_PER_USER,
    MAX_CONCURRENT_RUNS,
    PAID_PHASE_PROVIDER_BUDGET_KRW,
    SPEND_PHASE_OCR,
)
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.observability.records import read_records
from src.features.sharelink import logic as share_logic
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.web import evaluation_mode
from src.web.recording import (
    record_end,
    records_path,
    safe_observation_job,
)


logger = logging.getLogger(__name__)


_LEDGER = budget_logic.Ledger(day=clock.today_kst())
_LINK_SPEND = share_logic.DailySpend(day=clock.today_kst())
_RATE_HISTORY = budget_logic.RateHistory()
_RUNNING = 0
_RUNNING_BY_BUCKET: dict[str, int] = {}
_SLOT_LOCK = threading.Lock()
_PAID_PHASE_LOCK = threading.RLock()
_BUDGET_STORE_HEALTHY = False
_UNRESOLVED_BUCKETS: set[tuple[str, str]] = set()
_ACTIVE_PAID_PHASES: set[tuple[str, str, str, str]] = set()
_WorkerResult = TypeVar("_WorkerResult")

@dataclass(frozen=True)
class PaidPhase:
    """provider 호출 전 DB에 커밋한 진행 중 비용 표식의 서버 쪽 열쇠."""

    run_id: str
    phase: str
    day: dt.date
    share_key: str
    bucket_id: str
    reserved_krw: float = 0.0


@dataclass
class PaidPhaseHandle:
    """한 유료 단계가 취소·확정·미확정 중 정확히 한 번만 끝나게 한다."""

    ticket: Optional[PaidPhase]
    provider_started: bool = False
    closed: bool = False

    def mark_provider_started(self) -> None:
        if self.ticket is None or self.closed:
            raise RuntimeError("열리지 않은 비용 단계에서는 provider를 시작할 수 없습니다")
        self.provider_started = True

    def settle(self, *, amount_krw: float, billing_uncertain: bool) -> None:
        if self.ticket is None or self.closed:
            return
        _settle_paid_phase(
            self.ticket,
            amount_krw=amount_krw,
            billing_uncertain=billing_uncertain,
        )
        self.closed = True

    def cancel(self) -> None:
        if self.ticket is None or self.closed:
            return
        _cancel_paid_phase(self.ticket)
        self.closed = True


@contextmanager
def paid_phase(
    *,
    run_id: str,
    phase: str,
    share_key: str,
    cap_krw: float,
    requested_cost_krw: float | None = None,
):
    """provider 경계의 모든 이탈에서 비용 표식을 정확히 한 번 닫는다."""

    ticket = _begin_paid_phase(
        run_id=run_id,
        phase=phase,
        share_key=share_key,
        cap_krw=cap_krw,
        requested_cost_krw=requested_cost_krw,
    )
    handle = PaidPhaseHandle(ticket=ticket, closed=ticket is None)
    try:
        yield handle
    except BaseException:
        if not handle.closed:
            if handle.provider_started:
                handle.settle(amount_krw=0.0, billing_uncertain=True)
            else:
                handle.cancel()
        raise
    finally:
        if not handle.closed:
            if handle.provider_started:
                handle.settle(amount_krw=0.0, billing_uncertain=True)
            else:
                handle.cancel()

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

def _seed_ledger() -> None:
    """서버가 뜰 때 «오늘 이미 쓴 돈»을 이력에서 읽어 장부에 채운다.

    ★ 이게 없으면 서버를 껐다 켜는 것만으로 하루 상한이 풀린다.
    ★ 데모 기록은 빼고 센다 — 데모는 0원이다 (P-84와 같은 규칙).
    """
    global _LEDGER, _LINK_SPEND, _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    today = clock.today_kst()
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
            or clock.business_date_from_iso(record.at) != today
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
    if ticket.day != clock.today_kst():
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

def _begin_paid_phase(
    *,
    run_id: str,
    phase: str,
    share_key: str,
    cap_krw: float,
    requested_cost_krw: float | None = None,
) -> Optional[PaidPhase]:
    """DB 표식과 active 메모리를 요청 스레드 사이에서도 한 전이로 시작한다."""
    with _PAID_PHASE_LOCK:
        return _begin_paid_phase_locked(
            run_id=run_id,
            phase=phase,
            share_key=share_key,
            cap_krw=cap_krw,
            requested_cost_krw=requested_cost_krw,
        )

def _begin_paid_phase_locked(
    *,
    run_id: str,
    phase: str,
    share_key: str,
    cap_krw: float,
    requested_cost_krw: float | None,
) -> Optional[PaidPhase]:
    """유료 provider 호출 전에 표식을 커밋한다. 실패하면 호출 권한을 주지 않는다."""
    global _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    day = clock.today_kst()
    requested = (
        PAID_PHASE_PROVIDER_BUDGET_KRW[phase]
        if requested_cost_krw is None
        else float(requested_cost_krw)
    )
    ticket = PaidPhase(
        run_id=run_id,
        phase=phase,
        day=day,
        share_key=share_key,
        bucket_id=spend_store.bucket_id(share_key),
        reserved_krw=requested,
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
                started_at=clock.iso_now_kst(),
                requested_cost_krw=requested,
                cap_krw=cap_krw,
                run_cap_krw=(
                    evaluation_mode.settings().per_run_cap_krw
                    if evaluation_mode.enabled()
                    else None
                ),
            )
    except spend_store.BudgetCapExceeded:
        # 정상적인 운영 기준 거절이다. 저장소 장애나 미확정 비용으로 오인하지 않는다.
        logger.info("유료 단계 예상예약이 통장 운영 기준에서 거절됐습니다: %s", phase)
        return None
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
                created_at=clock.iso_now_kst(),
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
    return clock.iso_now_kst()


def _call_paid_provider(
    ticket: PaidPhase, call: Callable[..., _WorkerResult], *args: Any, **kwargs: Any
) -> _WorkerResult:
    """worker thread 안에 phase 예약을 설치한 뒤에만 provider 경로를 실행한다."""
    with provider_budget.activate(ticket.reserved_krw):
        return call(*args, **kwargs)

def _begin_observation_pending(
    *,
    run_id: str,
    job: str,
    cost_krw: float,
    elapsed_sec: float,
    model: str,
) -> bool:
    """확인 카드와 최종 이력 사이의 최소·비식별 대기표를 영속 저장한다."""
    started = clock.now_kst()
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
