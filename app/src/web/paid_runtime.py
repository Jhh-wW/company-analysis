"""유료 호출의 비용 원장, 동시 실행 슬롯과 재시작 복구."""

from __future__ import annotations

import datetime as dt
import logging
import math
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from src.core import clock
from src.core.constants import MODEL_LABEL_SEPARATOR, REPLAY_MODEL_MARK
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.types import (
    BillingDisposition as ProviderBillingDisposition,
    ProviderObservation,
)
from src.features.budget import logic as budget_logic
from src.features.budget import provider_budget
from src.features.budget import spend_store
from src.features.budget import state_machine
from src.features.budget.constants import (
    JOB_KEEP_SEC,
    MAX_CONCURRENT_PER_LINK,
    MAX_CONCURRENT_PER_USER,
    MAX_CONCURRENT_RUNS,
    PAID_PHASE_LEASE_SEC,
    PAID_PHASE_PROVIDER_BUDGET_KRW,
    SPEND_PHASE_OCR,
)
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.observability.records import read_records
from src.features.provider_health import constants as provider_health_constants
from src.features.provider_health import store as provider_health_store
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import LINK_TOTAL_BUDGET_KRW
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
_LEASE_OWNER_ID = f"process:{uuid.uuid4().hex}"
_WorkerResult = TypeVar("_WorkerResult")


class ProviderCircuitOpen(RuntimeError):
    """해당 provider의 유한 cooldown 동안 네트워크 전송을 거부함."""

@dataclass(frozen=True)
class PaidPhase:
    """provider 호출 전 DB에 커밋한 진행 중 비용 표식의 서버 쪽 열쇠."""

    run_id: str
    phase: str
    day: dt.date
    share_key: str
    bucket_id: str
    reserved_krw: float = 0.0
    lease_owner_id: str = ""


def _phase_lease_expires_at(now: dt.datetime | None = None) -> str:
    """한 provider phase의 DB lease 만료 시각을 KST aware ISO로 만든다."""

    captured = clock.now_kst() if now is None else now
    return (captured + dt.timedelta(seconds=PAID_PHASE_LEASE_SEC)).isoformat(
        timespec="seconds"
    )


def _budget_state_machine_enabled(conn) -> bool:  # noqa: ANN001
    """명시적 forward-only cutover가 끝난 DB에서만 새 원장을 사용한다."""

    return state_machine.cutover_applied(conn)


def prepare_budget_state_machine_cutover() -> state_machine.CutoverSummary:
    """real 서비스 시작 시 legacy를 보존한 채 새 attempt 원장으로 전환한다.

    schema bootstrap만으로 행동을 바꾸지 않는다. 같은 write transaction 안에서
    dry-run을 먼저 통과한 뒤 실제 전환을 실행한다. 구 표와 행은 삭제하지 않고,
    전환 뒤 write barrier가 구 코드의 늦은 쓰기를 막는다.
    """

    migrated_at = clock.iso_now_kst()
    with _PAID_PHASE_LOCK:
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            observed_costs: dict[str, float] | None = None
            if not state_machine.cutover_applied(conn):
                read_result = read_records(records_path())
                if read_result.skipped:
                    raise RuntimeError(
                        "깨진 관측 이력이 있어 비용 원장을 안전하게 전환할 수 없습니다"
                    )
                latest = {record.run_id: record for record in read_result.records}
                observed_costs = {
                    run_id: float(record.cost_krw)
                    for run_id, record in latest.items()
                    if REPLAY_MODEL_MARK not in (record.model or "")
                }
            state_machine.prepare_cutover(
                conn,
                migrated_at=migrated_at,
                dry_run=True,
                observed_costs_by_run=observed_costs,
            )
            summary = state_machine.prepare_cutover(
                conn,
                migrated_at=migrated_at,
                dry_run=False,
                observed_costs_by_run=observed_costs,
            )
    logger.info(
        "비용 attempt 원장 전환 완료: phase=%d known=%d unknown=%d already=%s",
        summary.legacy_phases,
        summary.legacy_known_attempts,
        summary.legacy_unknown_attempts,
        summary.already_applied,
    )
    return summary


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
    cap_krw: float | None,
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

def paid_research_block() -> tuple[bool, str]:
    """유료 조사가 막혀 있는지와 «사람이 풀어야 하는 이유»를 돌려준다.

    ★ 왜 여기 있나
      이 판정이 관리자 라우터 안에만 있어서 `/admin/access` 를 «직접 열어야만»
      보였다. 관리자 첫 화면(`/admin`)은 이 상태를 아예 안 읽어서,
      모든 유료 조사가 막힌 날에도 첫 화면은 「문제 없음」이었다.
      상태가 사는 이 모듈에 두고 두 화면이 같은 판정을 쓴다.
    """
    with _PAID_PHASE_LOCK:
        if not _BUDGET_STORE_HEALTHY:
            return (
                True,
                "비용 기록 파일을 읽지 못해 돈이 드는 새 조사를 모두 막아 뒀습니다. "
                "얼마를 썼는지 알 수 없는 상태로는 하루 한도를 지킬 수 없기 때문입니다.",
            )
    with _SLOT_LOCK:
        if _UNRESOLVED_BUCKETS:
            return (
                True,
                "조사 도중 오류가 나서 돈이 얼마 나갔는지 확인되지 않은 건이 남아 있습니다. "
                "그대로 두면 하루에 쓸 수 있는 돈을 넘겨도 알 수 없어 새 조사를 막아 뒀습니다.",
            )
    return False, ""


def budget_state_machine_ready() -> bool:
    """readiness가 새 유료 호출의 DB 정본 전환 완료를 직접 확인한다."""

    try:
        uri = storage_db.default_db_path().resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
            conn.execute("PRAGMA query_only=ON")
            return _budget_state_machine_enabled(conn)
    except Exception:  # noqa: BLE001 — readiness는 상세 DB 오류를 밖에 흘리지 않는다
        logger.exception("비용 상태기계 준비 상태를 읽지 못했습니다")
        return False


def list_unresolved_spend(
    day: dt.date | None = None,
) -> tuple[tuple[object, ...], bool]:
    """그날 마감되지 않은 유료 단계를 «그대로» 돌려준다.

    Args:
        day: 기준 날짜. **호출부가 이미 잡아 둔 「오늘」을 넘겨라** —
            화면 한 장이 시계를 두 번 읽으면 자정을 걸칠 때 스스로 어긋난다
            (`test_admin_access.py` 의 「요청당 한 번 캡처」 시험이 이걸 지킨다).

    Returns:
        (미확정 항목들, 읽는 데 성공했나).

    ★ 왜 필요한가
      화면은 「관리자가 미확정 비용을 대사해야 다시 열립니다」라고 말하는데,
      **무엇이 걸려 있는지 볼 화면이 없었다.** 대사하라면서 대사할 대상을
      안 보여 주면 관리자는 아무것도 할 수 없다.

    읽기에 실패하면 «빈 목록 + False» 를 돌려준다 — 못 읽은 것을 「없다」로
    보이게 하면 안 된다(있는데 없다고 하면 관리자가 손을 뗀다).
    """
    try:
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            if _budget_state_machine_enabled(conn):
                # 새 원장은 어제 이전 보수부채도 숨기지 않는다. 화면이 오늘만
                # 보여 주면 자정이 지난 고아를 영영 정산할 수 없기 때문이다.
                return (tuple(state_machine.list_reconcilable(conn)), True)
            기준일 = clock.today_kst() if day is None else day
            return (spend_store.list_inflight_day(conn, 기준일), True)
    except Exception:  # noqa: BLE001 — 못 읽어도 관리자 화면은 떠야 한다
        logger.exception("미확정 유료 단계를 읽지 못했습니다")
        return ((), False)


def settle_unresolved_spend(run_id: str, phase: str) -> tuple[bool, str]:
    """미확정 단계 하나를 «예약액을 쓴 것으로 확정»해 마감한다.

    Returns:
        (마감했나, 사람에게 보여 줄 한 줄).

    ★ 왜 «예약액»으로 확정하나 — 실제 청구액을 모르기 때문이다.
      모를 때는 **많이 썼다고 가정**해야 하루 상한이 느슨해지지 않는다.
      0원으로 마감하면 「돈은 나갔는데 장부엔 안 남는」 상태가 되어
      상한이 실제보다 헐거워진다. 안전한 쪽으로 기운다.

    ★★ **진행 중인 유료 단계는 건드리지 않는다.** 돌고 있는 조사를 마감하면
      그 조사가 끝났을 때 두 번 적히거나 표식을 못 찾아 실패한다.
    """
    깨끗한_run = str(run_id or "").strip()
    깨끗한_phase = str(phase or "").strip()
    if not 깨끗한_run or not 깨끗한_phase:
        return (False, "마감할 조사와 단계를 지정해 주세요.")

    with _PAID_PHASE_LOCK:
        with _SLOT_LOCK:
            진행중 = {(활성[0], 활성[1]) for 활성 in _ACTIVE_PAID_PHASES}
        if (깨끗한_run, 깨끗한_phase) in 진행중:
            return (
                False,
                "지금 돌고 있는 단계입니다. 끝난 뒤에 다시 확인해 주세요.",
            )
        try:
            오늘 = clock.today_kst()
            with storage_db.connect() as conn:
                spend_store.ensure_schema(conn)
                if _budget_state_machine_enabled(conn):
                    return (
                        False,
                        "새 비용 기록은 실제비용·0원·보수부채 중 무엇을 확인했는지 "
                        "선택해서 마감해야 합니다.",
                    )
                대상 = [
                    항목
                    for 항목 in spend_store.list_inflight_day(conn, 오늘)
                    if 항목.run_id == 깨끗한_run and 항목.phase == 깨끗한_phase
                ]
                if not 대상:
                    return (False, "그 미확정 항목을 찾지 못했습니다. 이미 마감됐을 수 있습니다.")
                항목 = 대상[0]
                # ⚠️ `finish_inflight` 를 쓰면 «지문을 또 지문화»해 항상 어긋난다 —
                #   관리자는 원문 통장을 가질 수 없다(재시작 뒤엔 지문만 남는다).
                spend_store.settle_inflight_as_reserved(
                    conn,
                    run_id=항목.run_id,
                    phase=항목.phase,
                    created_at=clock.iso_now_kst(),
                )
        except Exception:  # noqa: BLE001 — 실패를 성공처럼 보이게 하지 않는다
            logger.exception("미확정 유료 단계를 마감하지 못했습니다")
            return (False, "마감하지 못했습니다. 비용 기록을 사람이 직접 확인해야 합니다.")
        # 마감했으니 장부를 다시 읽어 상태를 새로 정한다.
        _seed_ledger()
        남은 = len(_UNRESOLVED_BUCKETS)
    if 남은:
        return (True, f"한 건을 마감했습니다. 아직 {남은}건이 남아 있습니다.")
    return (True, "마감했습니다. 새 조사를 다시 열었습니다.")


def resolve_budget_liability(
    *,
    attempt_id: str,
    action: state_machine.ResolutionAction,
    actual_cost_krw: float | None,
    actor_id: str,
    reason_code: str,
) -> tuple[bool, str]:
    """새 원장의 보수부채 한 건을 명시적인 근거와 행동으로만 마감한다."""

    clean_attempt = str(attempt_id or "").strip()
    if not clean_attempt:
        return False, "확인할 provider 호출 번호를 지정해 주세요."
    try:
        with _PAID_PHASE_LOCK:
            with storage_db.connect() as conn:
                spend_store.ensure_schema(conn)
                if not _budget_state_machine_enabled(conn):
                    return False, "아직 구 비용 기록을 사용 중입니다."
                state_machine.resolve_liability(
                    conn,
                    attempt_id=clean_attempt,
                    action=action,
                    actual_cost_krw=actual_cost_krw,
                    actor_id=actor_id,
                    reason_code=reason_code,
                    resolved_at=clock.iso_now_kst(),
                )
            _seed_ledger()
    except state_machine.ActivePhaseError:
        return False, "지금 실행 중인 호출은 끝난 뒤에만 확인할 수 있습니다."
    except (state_machine.BudgetStateError, ValueError):
        logger.exception("보수부채 확인 값이 원장 계약과 맞지 않습니다")
        return False, "선택한 호출과 확인 결과를 비용 기록에 반영하지 못했습니다."
    except Exception:  # noqa: BLE001 — 저장 실패를 성공처럼 보이지 않는다
        logger.exception("보수부채를 확인하지 못했습니다")
        return False, "비용 기록을 읽거나 쓰지 못했습니다. 잠시 후 다시 확인해 주세요."

    if action is state_machine.ResolutionAction.CONFIRM_ACTUAL:
        return True, "provider 자료에서 확인한 실제 비용을 기록했습니다."
    if action is state_machine.ResolutionAction.CONFIRM_ZERO:
        return True, "provider 자료에서 청구가 없음을 확인해 0원으로 기록했습니다."
    return True, "정확한 비용을 아직 몰라 보수부채를 그대로 유지했습니다."


def recheck_budget_store() -> tuple[bool, str]:
    """관리자가 「원장을 확인했다」고 할 때 상태를 **다시 계산**한다.

    Returns:
        (지금 유료 조사가 열려 있나, 사람에게 보여 줄 한 줄).

    ★ **강제로 열지 않는다.** 서버가 뜰 때와 «같은 검사»(`_seed_ledger`)를 다시
      돌릴 뿐이다. 근본 자료가 여전히 나쁘면 **닫힌 채로 남는다** — 그게 맞는 동작이다.
      돈이 걸린 문을 사람 말 한마디로 여는 길은 만들지 않는다.

    ★★ **진행 중인 유료 단계가 있으면 «하지 않는다».**
      `_seed_ledger()` 는 진행중 표식을 전부 「결과를 모르는 것」으로 다시 분류한다
      (같은 함수 머리말 참고). 돌아가는 조사를 미확정으로 만들면 **오히려 더 막힌다.**
      그래서 비어 있을 때만 돌린다.

    ★ 왜 이 경로가 생겼나 — 화면은 사용자에게
      「비용 기록을 확인할 수 없어 새 조사를 잠시 멈췄습니다. **관리자 확인이 끝나야
      다시 열립니다.**」라고 말하는데, 정작 **관리자가 「확인」을 실행할 방법이 없었다.**
      `_BUDGET_STORE_HEALTHY` 를 True 로 되돌리는 곳이 기동 시 `_seed_ledger()` 한 곳뿐이라,
      운영 중 한 번 꺼지면 **서버를 재시작하기 전까지 모든 유료 조사가 막혔다.**
    """
    with _PAID_PHASE_LOCK:
        with _SLOT_LOCK:
            진행중 = len(_ACTIVE_PAID_PHASES)
        if 진행중:
            return (
                _BUDGET_STORE_HEALTHY,
                f"지금 돌고 있는 조사가 {진행중}건 있어 다시 검사하지 않았습니다. "
                "끝난 뒤에 다시 눌러 주세요.",
            )
        _seed_ledger()
        열렸나 = _BUDGET_STORE_HEALTHY
        미확정 = len(_UNRESOLVED_BUCKETS)
    if 열렸나 and not 미확정:
        return (True, "비용 기록을 다시 읽었습니다. 새 조사를 다시 열었습니다.")
    if 열렸나:
        return (
            True,
            f"비용 기록은 다시 읽었습니다. 다만 돈이 얼마 나갔는지 "
            f"확인되지 않은 건이 {미확정}건 남아 있어 아직 막혀 있습니다. "
            "위 표에서 그 건을 마감해 주세요.",
        )
    return (
        False,
        "다시 읽어 봤지만 비용 기록을 여전히 읽지 못했습니다. "
        "기록 파일을 사람이 직접 확인해야 합니다.",
    )


def _seed_attempt_ledger(
    today: dt.date, *, verify_observation_history: bool = False
) -> bool:
    """전환된 DB면 새 attempt 원장으로 메모리의 빠른 조회값을 다시 만든다.

    반환값 ``False``는 아직 cutover 전이라는 뜻이지 장애가 아니다. 확정비용만
    ``_LEDGER``의 실제 지출로 표시하고, 입장 판단용 ``_LINK_SPEND``에는 확정비용,
    보수부채, ACTIVE 예약을 모두 넣는다. 따라서 모르는 호출을 0원으로 만들지도,
    별도 전역 스위치로 모든 통장을 닫지도 않는다.
    """

    global _LEDGER, _LINK_SPEND, _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    observed_at = clock.iso_now_kst()
    observation_mismatch_count = 0
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        if not _budget_state_machine_enabled(conn):
            return False
        state_machine.expire_due_phase_leases(conn, observed_at=observed_at)
        snapshot = state_machine.load_day_exposures(conn, day=today)
        if verify_observation_history:
            # cutover 뒤에는 SQLite current 행과 변경 불가 audit가 정본이다. 옛
            # append-only JSONL을 매 재시작마다 통째로 읽으면 파일 성장에 따라
            # OOM이 나고, 같은 최종값을 두 저장소에 영원히 중복하게 된다.
            lifecycle.ensure_schema(conn)
            for record in lifecycle.iter_final(conn):
                if (
                    record.cost_krw <= 0
                    or REPLAY_MODEL_MARK in (record.model or "")
                ):
                    continue
                exposure = state_machine.load_run_exposure(
                    conn, run_id=record.run_id
                )
                # 새 attempt DB가 비용 정본이다. JSONL 최종 이력이 더 크다면
                # 차액을 메모리로만 덧대지 않고 paid capability를 닫아 조용한
                # 과소계상을 드러낸다. 반대 방향은 관리자 실제비용 확인 뒤 생길
                # 수 있으므로 DB 정본을 줄이지 않는다.
                if float(record.cost_krw) - exposure.known_cost_krw > 0.01:
                    observation_mismatch_count += 1

    _LEDGER = budget_logic.Ledger(
        day=today,
        spent_krw=snapshot.total.known_cost_krw,
    )
    _LINK_SPEND = share_logic.DailySpend(
        day=today,
        by_key={
            bucket_id: exposure.admission_exposure_krw
            for bucket_id, exposure in snapshot.by_bucket.items()
        },
    )
    # 새 원장은 미확정 호출을 해당 금액의 보수부채로 이미 입장 합계에 넣는다.
    # 과거의 별도 bucket 영구잠금 집합을 함께 쓰면 같은 위험을 두 번 세게 된다.
    _UNRESOLVED_BUCKETS = set()
    _BUDGET_STORE_HEALTHY = observation_mismatch_count == 0
    if observation_mismatch_count:
        logger.error(
            "관측 최종 비용보다 attempt 원장이 작은 요청 %d건이 있어 유료 호출을 닫습니다",
            observation_mismatch_count,
        )
    logger.info(
        "새 비용 원장으로 시작합니다: 확정 %.1f원, 보수부채 %.1f원, "
        "진행예약 %.1f원",
        snapshot.total.known_cost_krw,
        snapshot.total.liability_krw,
        snapshot.total.reservation_krw,
    )
    return True


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
        if _seed_attempt_ledger(today, verify_observation_history=True):
            return
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
    cap_krw: float | None,
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


def _reap_expired_attempt_phases_locked(*, day: dt.date) -> bool:
    """만료 lease를 입장 판단보다 먼저 닫고 메모리 빠른 조회값도 다시 만든다.

    ``begin_phase``가 상한 초과로 거절된 뒤에 정리하면 그 예외와 함께 정리 작업도
    rollback된다. 그래서 정리 transaction을 먼저 끝낸 뒤 새 예약 transaction을
    연다. 전송 전 만료는 예약을 풀고, 전송 의도 뒤 만료는 보수부채를 남긴다.
    """

    observed_at = clock.iso_now_kst()
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        if not _budget_state_machine_enabled(conn):
            return True
        due = state_machine.list_active_phases(
            conn,
            expired_at_or_before=observed_at,
        )
        if not due:
            return True
        state_machine.expire_due_phase_leases(conn, observed_at=observed_at)
    _seed_attempt_ledger(day)
    return True


def reap_expired_paid_phases() -> bool:
    """POST 입장 사전검사에서 만료 예약을 스스로 회복한다.

    저장소를 읽거나 정리하지 못하면 ``False``를 반환하고 유료 기능 건강 상태를
    내린다. 호출부는 이 값을 무시하고 provider로 진행하면 안 된다.
    """

    global _BUDGET_STORE_HEALTHY
    with _PAID_PHASE_LOCK:
        try:
            return _reap_expired_attempt_phases_locked(day=clock.today_kst())
        except Exception:  # noqa: BLE001 — 비용 정리 실패는 fail-closed다
            logger.exception("만료된 비용 예약을 정리하지 못해 진짜 조사를 닫습니다")
            _BUDGET_STORE_HEALTHY = False
            return False

def _link_total_budget_inputs(
    conn: sqlite3.Connection, share_key: str
) -> tuple[Optional[float], float]:
    """LINK 통장이면 «수명 전체» 상한과 이미 끝낸 실행의 실측 원가를 돌려준다.

    Args:
        conn: 예약을 커밋할 연결. 같은 연결로 읽어 다른 저장소를 섞지 않는다.
        share_key: 통장 원문.

    Returns:
        ``(수명 전체 상한, 지난 실측 원가)``. LINK가 아니면 ``(None, 0.0)``.

    ★ LINK 갈래에서만 값을 준다 — MEMBER·ADMIN·PUBLIC은 사람 통장이거나 전체
      통장이라 「링크 수명」이라는 개념 자체가 없다.
      갈래를 가르는 것은 열쇠 모양(32자리 16진수)이다. 사람 통장에는 `user:`
      접두어가 붙어 열쇠와 절대 겹치지 않는다 (`sharelink/constants.py` 참고).
    ★ 진행 중 예약은 여기서 세지 않는다 — 그건 예약을 커밋하는 transaction 안에서
      `begin_phase`가 다시 센다. 여기서 같이 세면 동시 요청이 옛 숫자를 공유한다.
    """
    if not share_logic.is_valid_key(share_key):
        return None, 0.0
    key_hash = share_store.key_hash_of(share_key)
    link = share_store.load_by_hash(conn, key_hash)
    prior_cost = share_store.link_run_cost_sum_krw(conn, key_hash=key_hash)
    limit = (
        link.effective_total_budget_krw
        if link is not None
        else LINK_TOTAL_BUDGET_KRW
    )
    return limit, prior_cost


def _begin_paid_phase_locked(
    *,
    run_id: str,
    phase: str,
    share_key: str,
    cap_krw: float | None,
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
    using_attempt_ledger = False
    try:
        # 만료 예약이 상한을 먼저 먹어 새 예약을 거절하기 전에 별도 transaction으로
        # 정리한다. 거절 뒤 seed하는 옛 순서는 영원히 자기 회복하지 못했다.
        _reap_expired_attempt_phases_locked(day=day)
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            if _budget_state_machine_enabled(conn):
                using_attempt_ledger = True
                started_at = clock.iso_now_kst()
                ticket = PaidPhase(
                    run_id=run_id,
                    phase=phase,
                    day=day,
                    share_key=share_key,
                    bucket_id=spend_store.bucket_id(share_key),
                    reserved_krw=requested,
                    lease_owner_id=_LEASE_OWNER_ID,
                )
                # LINK만 «수명 전체» 상한을 하나 더 받는다. 값이 None이면
                # begin_phase가 누적 검사를 아예 건너뛴다 (다른 갈래의 동작 불변).
                link_total_limit, link_prior_cost = _link_total_budget_inputs(
                    conn, share_key
                )
                state_machine.begin_phase(
                    conn,
                    run_id=run_id,
                    phase=phase,
                    day=day,
                    bucket=share_key,
                    reservation_krw=requested,
                    bucket_limit_krw=cap_krw,
                    run_limit_krw=(
                        evaluation_mode.settings().per_run_cap_krw
                        if evaluation_mode.enabled()
                        else None
                    ),
                    bucket_total_limit_krw=link_total_limit,
                    bucket_prior_cost_krw=link_prior_cost,
                    lease_owner_id=ticket.lease_owner_id,
                    lease_expires_at=_phase_lease_expires_at(),
                    started_at=started_at,
                )
                inserted = True
            else:
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
    except (spend_store.BudgetCapExceeded, state_machine.AdmissionLimitExceeded):
        # 정상적인 운영 기준 거절이다. 저장소 장애나 미확정 비용으로 오인하지 않는다.
        logger.info("유료 단계 예상예약이 통장 운영 기준에서 거절됐습니다: %s", phase)
        return None
    except state_machine.BudgetStateError:
        # 같은 run/phase 재사용이나 전환 계약 위반은 이중 과금을 막는 정상 거절이다.
        logger.warning("유료 단계 상태가 이미 존재해 다시 시작하지 않습니다: %s", phase)
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
    if using_attempt_ledger:
        # ACTIVE 여부와 통장별 예약은 이제 DB lease가 정본이다. 메모리 tuple과
        # 미확정 bucket 집합에 중복 기록하면 재시작 때 다시 영구잠금이 생긴다.
        _seed_attempt_ledger(day)
        return ticket
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
    if ticket.lease_owner_id:
        _settle_attempt_ledger_phase(
            ticket,
            amount_krw=amount_krw,
            billing_uncertain=billing_uncertain,
        )
        return
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


def _record_transition_attempt(
    conn,  # noqa: ANN001
    *,
    ticket: PaidPhase,
    estimated_krw: float,
    known_cost_krw: float,
    liability_krw: float,
    close_phase: bool,
) -> None:
    """저수준 gateway 전환 중 누락된 외곽 관측을 한 번만 보수적으로 남긴다."""

    attempt_id = f"transition:{uuid.uuid4().hex}"
    at = clock.iso_now_kst()
    state_machine.begin_attempt(
        conn,
        run_id=ticket.run_id,
        phase=ticket.phase,
        attempt_id=attempt_id,
        provider="transition-wrapper",
        operation="phase-summary",
        estimated_krw=estimated_krw,
        lease_owner_id=ticket.lease_owner_id,
        created_at=at,
    )
    state_machine.mark_dispatch_intent(
        conn,
        attempt_id=attempt_id,
        lease_owner_id=ticket.lease_owner_id,
        recorded_at=at,
    )
    is_liability = liability_krw > 0
    state_machine.record_attempt_outcome(
        conn,
        attempt_id=attempt_id,
        transport_state=(
            state_machine.TransportState.TRANSPORT_AMBIGUOUS
            if is_liability
            else state_machine.TransportState.RESPONSE_RECEIVED
        ),
        billing_state=(
            state_machine.BillingState.CONSERVATIVE_LIABILITY
            if is_liability
            else state_machine.BillingState.KNOWN_COST
        ),
        known_cost_krw=known_cost_krw,
        liability_krw=liability_krw,
        close_phase=close_phase,
        phase_succeeded=not is_liability,
        recorded_at=at,
        lease_owner_id=ticket.lease_owner_id,
        error_type="TransitionBoundaryUnknown" if is_liability else "",
    )


def _settle_attempt_ledger_phase(
    ticket: PaidPhase, *, amount_krw: float, billing_uncertain: bool
) -> None:
    """attempt 정본을 중복 없이 마감하고 메모리 조회값을 DB에서 다시 만든다."""

    global _BUDGET_STORE_HEALTHY
    try:
        already_closed = False
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            phase_account = state_machine.get_phase(
                conn,
                run_id=ticket.run_id,
                phase=ticket.phase,
            )
            if phase_account.state is not state_machine.PhaseState.ACTIVE:
                # liability 관측은 결과 기록과 같은 transaction에서 phase를 닫는다.
                # 바깥 finally가 두 번째로 불려도 같은 돈을 다시 쓰지 않는다.
                already_closed = True

            if not already_closed:
                attempts = state_machine.list_attempts(
                    conn,
                    run_id=ticket.run_id,
                    phase=ticket.phase,
                )
                active_attempts = tuple(
                    item
                    for item in attempts
                    if item.billing_state is state_machine.BillingState.RESERVED
                )
                if len(active_attempts) > 1:
                    raise state_machine.AttemptStateError(
                        "한 phase에 진행 중 provider 시도가 여러 개입니다"
                    )
                if active_attempts:
                    active = active_attempts[0]
                    at = clock.iso_now_kst()
                    if active.transport_state is state_machine.TransportState.PLANNED:
                        # dispatch-intent commit 전 실패했으므로 provider send는 0회다.
                        state_machine.record_pre_dispatch_failure(
                            conn,
                            attempt_id=active.attempt_id,
                            lease_owner_id=ticket.lease_owner_id,
                            error_type="OuterBoundaryBeforeDispatch",
                            close_phase=True,
                            recorded_at=at,
                        )
                    elif (
                        active.transport_state
                        is state_machine.TransportState.DISPATCH_INTENT_RECORDED
                    ):
                        # 전송 의도 뒤 관측 저장이 끊겼다. 이 호출의 예약액만
                        # 보수부채로 남기고 phase를 닫는다.
                        state_machine.record_attempt_outcome(
                            conn,
                            attempt_id=active.attempt_id,
                            transport_state=(
                                state_machine.TransportState.TRANSPORT_AMBIGUOUS
                            ),
                            billing_state=(
                                state_machine.BillingState.CONSERVATIVE_LIABILITY
                            ),
                            known_cost_krw=0.0,
                            liability_krw=active.estimated_krw,
                            close_phase=True,
                            phase_succeeded=False,
                            recorded_at=at,
                            lease_owner_id=ticket.lease_owner_id,
                            error_type="ObservationMissingAfterDispatchIntent",
                        )
                    else:
                        raise state_machine.AttemptStateError(
                            "진행 중 provider 시도의 전송 상태가 올바르지 않습니다"
                        )
                    already_closed = True

            if not already_closed:
                # 저수준 attempt 결과가 정본이다. 전환 기간에만 바깥 usage 합계가
                # 더 큰 경우 그 확정 차액을 보존하며, 모른다는 이유로 phase 전체
                # 잔액을 가짜 부채로 만들지는 않는다.
                attempts = state_machine.list_attempts(
                    conn,
                    run_id=ticket.run_id,
                    phase=ticket.phase,
                )
                known = sum(item.known_cost_krw for item in attempts)
                transition_known = max(0.0, float(amount_krw) - known)
                if transition_known > 0:
                    estimate = min(transition_known, phase_account.reservation_krw)
                    if estimate <= 0:
                        raise state_machine.BudgetStateError(
                            "확정 비용은 늘었지만 phase 예약 잔액이 없습니다"
                        )
                    # actual은 admission 추정값을 넘을 수 있다. 이미 생긴 비용은
                    # estimate에 맞춰 자르지 않고 전액 기록한다.
                    _record_transition_attempt(
                        conn,
                        ticket=ticket,
                        estimated_krw=estimate,
                        known_cost_krw=transition_known,
                        liability_krw=0.0,
                        close_phase=False,
                    )
                    phase_account = state_machine.get_phase(
                        conn,
                        run_id=ticket.run_id,
                        phase=ticket.phase,
                    )

                state_machine.complete_phase(
                    conn,
                    run_id=ticket.run_id,
                    phase=ticket.phase,
                    lease_owner_id=ticket.lease_owner_id,
                    succeeded=not billing_uncertain,
                    completed_at=clock.iso_now_kst(),
                )
        _seed_attempt_ledger(clock.today_kst())
    except Exception:  # noqa: BLE001 — provider 뒤 원장 실패는 성공으로 가장하지 않는다
        logger.exception("새 비용 attempt phase를 마감하지 못했습니다")
        _BUDGET_STORE_HEALTHY = False


def _cancel_paid_phase(ticket: PaidPhase) -> None:
    """시작 취소도 다른 시작·마감과 같은 순서열 안에서 처리한다."""
    with _PAID_PHASE_LOCK:
        _cancel_paid_phase_locked(ticket)

def _cancel_paid_phase_locked(ticket: PaidPhase) -> None:
    """provider를 아직 부르지 않았음이 확실한 작업 등록 실패에서만 표식을 취소한다."""
    global _BUDGET_STORE_HEALTHY, _UNRESOLVED_BUCKETS
    if ticket.lease_owner_id:
        try:
            with storage_db.connect() as conn:
                spend_store.ensure_schema(conn)
                account = state_machine.get_phase(
                    conn,
                    run_id=ticket.run_id,
                    phase=ticket.phase,
                )
                if account.state is state_machine.PhaseState.ACTIVE:
                    state_machine.complete_phase(
                        conn,
                        run_id=ticket.run_id,
                        phase=ticket.phase,
                        lease_owner_id=ticket.lease_owner_id,
                        succeeded=False,
                        completed_at=clock.iso_now_kst(),
                    )
            _seed_attempt_ledger(clock.today_kst())
        except Exception:  # noqa: BLE001 — 실패 phase는 lease 만료가 다시 회수한다
            logger.exception("새 비용 phase의 전송 전 취소를 기록하지 못했습니다")
            _BUDGET_STORE_HEALTHY = False
        return
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


def _provider_failure_kind(
    observation: ProviderObservation,
) -> provider_health_store.ProviderFailureKind | None:
    """provider 전체 가용성에 관한 증거만 실패 종류로 돌려준다.

    400은 요청 형식과 Anthropic 조직 지출 한도가 겹쳐 status만으로 원인을
    확정할 수 없다. 그런 4xx는 성공/실패 어느 쪽에도 넣지 않는다. 반면
    401·402·403은 현재 계정으로 provider를 운영할 수 없다는 신호이므로
    별도 account/config 종류로 누적한다.
    """

    if observation.status_code == 429:
        return provider_health_store.ProviderFailureKind.RATE_LIMIT
    error_type = observation.error_type.casefold()
    if "timeout" in error_type or observation.status_code in {408, 504}:
        return provider_health_store.ProviderFailureKind.TIMEOUT
    if any(
        marker in error_type
        for marker in ("connection", "connect", "network", "socket")
    ):
        return provider_health_store.ProviderFailureKind.CONNECTION
    if observation.status_code in {401, 402, 403}:
        return provider_health_store.ProviderFailureKind.ACCOUNT_CONFIGURATION
    if observation.status_code is not None and observation.status_code >= 500:
        return provider_health_store.ProviderFailureKind.PROVIDER_RESPONSE
    return None


def _provider_health_response_is_healthy(
    observation: ProviderObservation,
) -> bool:
    """2xx는 usage 누락·본문 해석 실패여도 provider 건강상 성공이다."""

    status = observation.status_code
    return status is not None and 200 <= status < 300


def _provider_attempt_callbacks(
    ticket: PaidPhase,
) -> attempt_context.ProviderAttemptCallbacks:
    """저수준 provider 한 번과 영속 attempt 원장을 잇는 요청 로컬 경계."""

    if not ticket.lease_owner_id:
        raise state_machine.CutoverRequiredError(
            "attempt 원장 전환 전에는 새 provider gateway를 열 수 없습니다"
        )

    def begin_attempt(provider: str, operation: str, reserved_krw: float) -> str:
        attempt_id = f"attempt:{uuid.uuid4().hex}"
        recorded_at = clock.iso_now_kst()
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            provider_health_store.ensure_schema(conn)
            state_machine.begin_attempt(
                conn,
                run_id=ticket.run_id,
                phase=ticket.phase,
                attempt_id=attempt_id,
                provider=provider,
                operation=operation,
                estimated_krw=reserved_krw,
                lease_owner_id=ticket.lease_owner_id,
                created_at=recorded_at,
            )
            # 여기서는 순수 조회만 한다. OPEN의 cooldown이 끝났더라도 probe
            # 소유권은 실제 전송 의도를 기록하는 transaction에서만 잡는다.
            # 먼저 잡고 heartbeat/dispatch DB 쓰기가 실패하면 300초 동안
            # PROBING에 갇히는 반쪽 상태가 생기기 때문이다.
            permission = provider_health_store.peek_permission(
                conn,
                provider,
                now_iso=recorded_at,
            )
            if not permission.allowed:
                # attempt를 먼저 만든 뒤 같은 transaction에서 0원으로 닫는다.
                # 따라서 차단기 거부가 비용 누락이나 provider 전송으로 바뀔 수 없다.
                state_machine.record_pre_dispatch_failure(
                    conn,
                    attempt_id=attempt_id,
                    lease_owner_id=ticket.lease_owner_id,
                    error_type="ProviderCircuitOpen",
                    close_phase=True,
                    recorded_at=recorded_at,
                )
        if not permission.allowed:
            raise ProviderCircuitOpen(
                f"{provider} provider가 잠시 쉬는 중입니다({permission.reason_code})"
            )
        return attempt_id

    def heartbeat(_attempt_id: Any) -> None:
        # 같은 초에 여러 호출이 이어져도 기존 만료시각보다 반드시 뒤로 간다.
        now = clock.now_kst()
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            phase_account = state_machine.get_phase(
                conn,
                run_id=ticket.run_id,
                phase=ticket.phase,
            )
            proposed = (now + dt.timedelta(seconds=PAID_PHASE_LEASE_SEC)).replace(
                microsecond=0
            )
            if phase_account.lease_expires_at:
                current_expiry = dt.datetime.fromisoformat(
                    phase_account.lease_expires_at
                )
                if proposed <= current_expiry:
                    proposed = current_expiry + dt.timedelta(seconds=1)
            state_machine.heartbeat_phase(
                conn,
                run_id=ticket.run_id,
                phase=ticket.phase,
                lease_owner_id=ticket.lease_owner_id,
                lease_expires_at=proposed.isoformat(timespec="seconds"),
                heartbeat_at=now.isoformat(timespec="seconds"),
            )

    def mark_dispatch_intent(attempt_id: Any) -> None:
        recorded_at = clock.iso_now_kst()
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            provider_health_store.ensure_schema(conn)
            attempt_account = state_machine.get_attempt(
                conn, attempt_id=str(attempt_id)
            )
            permission = provider_health_store.acquire_probe(
                conn,
                attempt_account.provider,
                now_iso=recorded_at,
            )
            if permission.allowed:
                # probe 획득과 전송 의도 기록은 같은 SQLite transaction이다.
                # 어느 한쪽이라도 실패하면 둘 다 rollback되어 provider가
                # 가짜 PROBING 상태로 남지 않는다.
                state_machine.mark_dispatch_intent(
                    conn,
                    attempt_id=str(attempt_id),
                    lease_owner_id=ticket.lease_owner_id,
                    recorded_at=recorded_at,
                )
            else:
                state_machine.record_pre_dispatch_failure(
                    conn,
                    attempt_id=str(attempt_id),
                    lease_owner_id=ticket.lease_owner_id,
                    error_type="ProviderCircuitOpen",
                    close_phase=True,
                    recorded_at=recorded_at,
                )
        if not permission.allowed:
            raise ProviderCircuitOpen(
                f"{attempt_account.provider} provider가 잠시 쉬는 중입니다"
                f"({permission.reason_code})"
            )

    def record_observation(
        attempt_id: Any, observation: ProviderObservation
    ) -> None:
        is_liability = (
            observation.billing_disposition
            is ProviderBillingDisposition.CONSERVATIVE_LIABILITY
        )
        recorded_at = clock.iso_now_kst()
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            provider_health_store.ensure_schema(conn)
            attempt_account = state_machine.record_attempt_outcome(
                conn,
                attempt_id=str(attempt_id),
                transport_state=state_machine.TransportState(
                    observation.transport_state.value
                ),
                billing_state=state_machine.BillingState(
                    observation.billing_disposition.value
                ),
                known_cost_krw=observation.known_cost_krw,
                liability_krw=observation.liability_krw,
                # 모르는 청구는 더 쌓지 않도록 이 phase만 닫는다. 다른 통장과
                # 서비스 전체는 닫지 않고 이 부채만 입장 합계에 남긴다.
                close_phase=is_liability,
                phase_succeeded=False,
                recorded_at=recorded_at,
                lease_owner_id=ticket.lease_owner_id,
                status_code=observation.status_code,
                error_type=observation.error_type,
                request_id=observation.request_id,
            )
            failure_kind = _provider_failure_kind(observation)
            if _provider_health_response_is_healthy(observation):
                # MissingUsage는 비용상 보수부채여도 provider 응답 성공이다.
                provider_health_store.record_success(
                    conn,
                    provider=attempt_account.provider,
                    now_iso=recorded_at,
                )
            elif failure_kind is not None:
                provider_health_store.record_failure(
                    conn,
                    provider=attempt_account.provider,
                    failure_kind=failure_kind,
                    now_iso=recorded_at,
                )
            else:
                # 요청별 4xx·분류할 수 없는 로컬 오류는 provider 전체의
                # 성공/실패로 날조하지 않는다. 단, probe를 잡았다면 즉시
                # 반납해 300초짜리 가짜 PROBING만 남지 않게 한다.
                provider_health_store.release_probe_without_health_signal(
                    conn,
                    provider=attempt_account.provider,
                    now_iso=recorded_at,
                )

    return attempt_context.ProviderAttemptCallbacks(
        begin_attempt=begin_attempt,
        heartbeat=heartbeat,
        mark_dispatch_intent=mark_dispatch_intent,
        record_observation=record_observation,
    )


@contextmanager
def _activate_paid_provider(ticket: PaidPhase):
    """worker 문맥에 phase의 호출별 예산·attempt callback을 설치한다.

    즉시 provider를 부르는 OCR·회사 식별과, source snapshot으로
    single-flight owner를 고른 뒤 지연해 phase를 여는 본조사가 같은
    기계 경계를 쓴다.
    """

    with provider_budget.activate(ticket.reserved_krw):
        if not getattr(ticket, "lease_owner_id", ""):
            # legacy 모드는 배포 전 기존 단위시험과 데모 호환만 유지한다. real
            # lifespan은 provider보다 먼저 forward-only cutover를 끝낸다.
            yield
            return
        callbacks = _provider_attempt_callbacks(ticket)
        with attempt_context.activate(callbacks):
            yield


def _call_paid_provider(
    ticket: PaidPhase, call: Callable[..., _WorkerResult], *args: Any, **kwargs: Any
) -> _WorkerResult:
    """worker thread 안에 phase 예약을 설치한 뒤에만 provider 경로를 실행한다."""

    with _activate_paid_provider(ticket):
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
                    if _budget_state_machine_enabled(conn):
                        phases = state_machine.list_phases(
                            conn, run_id=entry.run_id
                        )
                        # startup seed가 만료 lease를 이미 FAILED로 닫았어도 마지막
                        # 실행 단계의 정체는 phase 이력에 남는다.
                        running_phases[entry.run_id] = (
                            phases[-1].phase if phases else None
                        )
                    else:
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
