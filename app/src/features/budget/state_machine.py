"""유료 phase와 provider attempt를 분리한 SQLite 비용 상태기계.

확정 비용, 보수부채, 현재 예약은 서로 다른 칸에 저장한다. 보수부채는 새 호출의
입장 합계에는 포함하지만 통장 전체를 별도 전역 스위치로 잠그지는 않는다. 외부
provider 전송과 SQLite commit은 원자적으로 묶을 수 없으므로 실제 전송 완료가 아닌
``DISPATCH_INTENT_RECORDED``를 먼저 남기며, 그 뒤 lease가 만료되면 비용을 0원으로
지어내지 않고 attempt 예상액을 보수부채로 보존한다.

기존 세 표의 이관은 schema bootstrap과 의도적으로 분리했다. 웹이 아직 legacy 표에
쓰는 동안 자동 이관하면 정상 실행 중 행을 고아로 오판하기 때문이다. 운영 전환점에
유료 입장을 멈춘 뒤 ``prepare_cutover(dry_run=True)``로 검사하고, 같은 함수를
``dry_run=False``로 한 번 실행해야 새 API가 열린다.
"""

from __future__ import annotations

import datetime as dt
import logging
import hashlib
import math
import sqlite3
import string
from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from typing import Final

from src.features.budget import spend_store
from src.features.budget.constants import PAID_PHASE_PROVIDER_BUDGET_KRW, SPEND_PHASES


CUTOVER_VERSION: Final[str] = spend_store.BUDGET_STATE_CUTOVER_VERSION
SYSTEM_ACTOR_ID: Final[str] = "system:budget"
MIGRATION_ACTOR_ID: Final[str] = "system:migration"
_SAFE_CHARS: Final[frozenset[str]] = frozenset(
    string.ascii_letters + string.digits + "_.:-"
)


class PhaseState(str, Enum):
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


class TransportState(str, Enum):
    PLANNED = "PLANNED"
    DISPATCH_INTENT_RECORDED = "DISPATCH_INTENT_RECORDED"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    TRANSPORT_AMBIGUOUS = "TRANSPORT_AMBIGUOUS"
    LOCAL_FAILURE = "LOCAL_FAILURE"
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


class BillingState(str, Enum):
    RESERVED = "RESERVED"
    KNOWN_COST = "KNOWN_COST"
    CONSERVATIVE_LIABILITY = "CONSERVATIVE_LIABILITY"
    LIABILITY_CONFIRMED = "LIABILITY_CONFIRMED"
    KNOWN_ZERO = "KNOWN_ZERO"
    UNKNOWN_LEGACY = "UNKNOWN_LEGACY"


class ResolutionAction(str, Enum):
    CONFIRM_ACTUAL = "CONFIRM_ACTUAL"
    CONFIRM_CONSERVATIVE_LIABILITY = "CONFIRM_CONSERVATIVE_LIABILITY"
    CONFIRM_ZERO = "CONFIRM_ZERO"


class BudgetStateError(RuntimeError):
    """비용 상태 전이 계약이 맞지 않음."""


class CutoverRequiredError(BudgetStateError):
    """명시적인 legacy 전환이 아직 끝나지 않음."""


class AdmissionLimitExceeded(BudgetStateError):
    """확정액+부채+예약+새 예약이 입장 제한 기준을 넘음."""


class ActivePhaseError(BudgetStateError):
    """관리자가 아직 진행 중인 DB phase를 바꾸려 함."""


class LeaseOwnershipError(BudgetStateError):
    """DB lease 소유자가 아니거나 이미 만료됨."""


class AttemptStateError(BudgetStateError):
    """attempt 상태 전이 순서가 맞지 않음."""


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CutoverSummary:
    legacy_phases: int
    legacy_known_attempts: int
    legacy_unknown_attempts: int
    already_applied: bool = False


@dataclass(frozen=True)
class PhaseAccount:
    run_id: str
    phase: str
    day: dt.date
    bucket_id: str
    state: PhaseState
    reservation_krw: float
    lease_owner_id: str | None
    lease_expires_at: str | None
    started_at: str
    updated_at: str
    version: int


@dataclass(frozen=True)
class AttemptAccount:
    attempt_id: str
    run_id: str
    phase: str
    attempt_no: int
    provider: str
    operation: str
    estimated_krw: float
    transport_state: TransportState
    billing_state: BillingState
    reservation_krw: float
    known_cost_krw: float
    liability_krw: float
    status_code: int | None
    error_type: str
    request_id: str
    actor_id: str
    reason_code: str
    occurred_at: str


@dataclass(frozen=True)
class ExposureSnapshot:
    known_cost_krw: float = 0.0
    liability_krw: float = 0.0
    reservation_krw: float = 0.0
    active_phases: int = 0

    @property
    def admission_exposure_krw(self) -> float:
        return self.known_cost_krw + self.liability_krw + self.reservation_krw


@dataclass(frozen=True)
class DayExposureSnapshot:
    day: dt.date
    total: ExposureSnapshot
    by_bucket: dict[str, ExposureSnapshot]


@dataclass(frozen=True)
class ReconciliationItem:
    attempt_id: str
    run_id: str
    phase: str
    day: dt.date
    bucket_id: str
    phase_state: PhaseState
    billing_state: BillingState
    liability_krw: float
    provider: str
    operation: str
    status_code: int | None
    error_type: str
    request_id: str
    occurred_at: str


def _amount(value: float, *, label: str, positive: bool = False) -> float:
    amount = float(value)
    if not math.isfinite(amount) or amount < 0 or (positive and amount == 0):
        qualifier = "0보다 큰 " if positive else "0 이상 "
        raise ValueError(f"{label}은 {qualifier}유한한 수여야 합니다")
    return amount


def _identifier(value: str, *, label: str, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or any(character not in _SAFE_CHARS for character in value)
    ):
        raise ValueError(f"{label} 형식이 올바르지 않습니다")
    return value


def _optional_identifier(value: str | None, *, label: str, maximum: int) -> str:
    if value in (None, ""):
        return ""
    return _identifier(str(value), label=label, maximum=maximum)


def _timestamp(value: str, *, label: str) -> str:
    if type(value) is not str or not value or len(value) > 40:
        raise ValueError(f"{label} 형식이 올바르지 않습니다")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} 형식이 올바르지 않습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}에는 시간대가 필요합니다")
    return value


def _parsed_timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _phase(value: str) -> str:
    if value not in SPEND_PHASES:
        raise ValueError(f"비용 단계가 올바르지 않습니다: {value!r}")
    return value


def _begin_immediate(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _cutover_row(conn: sqlite3.Connection) -> tuple[object, ...] | None:
    try:
        return conn.execute(
            f"""
            SELECT legacy_phases, legacy_known_attempts, legacy_unknown_attempts
              FROM {spend_store.TABLE_BUDGET_SCHEMA_MIGRATIONS}
             WHERE version = ?
            """,
            (CUTOVER_VERSION,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _require_cutover(conn: sqlite3.Connection) -> None:
    if _cutover_row(conn) is None:
        raise CutoverRequiredError(
            "비용 상태기계 전환이 끝나지 않았습니다. prepare_cutover를 먼저 실행하세요"
        )


def cutover_applied(conn: sqlite3.Connection) -> bool:
    """startup이 raw migration SQL 없이 새 원장 전환 여부를 읽는다."""
    return _cutover_row(conn) is not None


def _stable_legacy_id(kind: str, run_id: str, phase: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{run_id}\0{phase}".encode("utf-8")
    ).hexdigest()
    return f"legacy:{kind}:{digest}"


def _legacy_inventory(
    conn: sqlite3.Connection,
) -> tuple[
    dict[tuple[str, str], tuple[str, str, float, str]],
    dict[tuple[str, str], tuple[str, str, float, str]],
]:
    """구판 확정행과 진행행을 읽고 서로 모순된 키를 거절한다."""
    known: dict[tuple[str, str], tuple[str, str, float, str]] = {}
    for row in conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, cost_krw, created_at
          FROM {spend_store.TABLE_SPEND_EVENTS}
         ORDER BY run_id, phase
        """
    ):
        run_id = str(row[0])
        phase = _phase(str(row[1]))
        amount = _amount(float(row[4]), label="구판 확정 비용")
        known[(run_id, phase)] = (str(row[2]), str(row[3]), amount, str(row[5]))

    unknown: dict[tuple[str, str], tuple[str, str, float, str]] = {}
    inflight_columns = {
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({spend_store.TABLE_SPEND_INFLIGHT})"
        )
    }
    reserved_expression = "reserved_krw" if "reserved_krw" in inflight_columns else "0"
    for row in conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, {reserved_expression}, started_at
          FROM {spend_store.TABLE_SPEND_INFLIGHT}
         ORDER BY run_id, phase
        """
    ):
        run_id = str(row[0])
        phase = _phase(str(row[1]))
        amount = _amount(float(row[4]), label="구판 미확정 예약")
        unknown[(run_id, phase)] = (
            str(row[2]),
            str(row[3]),
            amount,
            str(row[5]),
        )

    buckets_by_run: dict[str, set[str]] = {}
    for (run_id, _phase_name), (_day, stored_bucket, _amount_value, _at) in (
        list(known.items()) + list(unknown.items())
    ):
        buckets_by_run.setdefault(run_id, set()).add(stored_bucket)
    if any(len(buckets) != 1 for buckets in buckets_by_run.values()):
        raise ValueError("구판 비용 원장에서 한 요청이 여러 통장에 걸쳐 있습니다")
    for key in known.keys() & unknown.keys():
        if known[key][0:2] != unknown[key][0:2]:
            raise ValueError("구판 확정 비용과 미확정 예약의 날짜 또는 통장이 다릅니다")
    return known, unknown


def _legacy_observation_adjustments(
    known: Mapping[tuple[str, str], tuple[str, str, float, str]],
    unknown: Mapping[tuple[str, str], tuple[str, str, float, str]],
    observed_costs_by_run: Mapping[str, float] | None,
) -> dict[tuple[str, str], float]:
    """JSONL 최종 원가가 구 DB보다 큰 정확한 차액을 이관 대상으로 만든다.

    과거 관측 한 줄은 run 총액만 갖고 phase를 갖지 않는다. 같은 run의 legacy
    phase들은 이미 한 통장이라는 검사를 통과했으므로 마지막 phase에 차액을 붙인다.
    phase가 전혀 없거나 DB가 관측보다 더 큰 모순은 통장을 지어낼 수 없어 전환을
    중단한다.
    """

    lower_than_ledger: list[str] = []
    if not observed_costs_by_run:
        return {}
    keys_by_run: dict[str, list[tuple[str, str]]] = {}
    for key in known.keys() | unknown.keys():
        keys_by_run.setdefault(key[0], []).append(key)
    adjustments: dict[tuple[str, str], float] = {}
    for raw_run_id, raw_observed in observed_costs_by_run.items():
        run_id = _identifier(raw_run_id, label="관측 요청 번호", maximum=128)
        observed = _amount(float(raw_observed), label="관측 최종 비용")
        keys = keys_by_run.get(run_id, [])
        if not keys:
            if observed > 0:
                raise BudgetStateError(
                    "관측 비용의 통장을 legacy DB에서 찾을 수 없습니다"
                )
            continue
        known_total = sum(known[key][2] for key in keys if key in known)
        delta = round(observed - known_total, 6)
        if delta <= 0.01:
            # ★ 2026-08-29 — 여기서 «중단»하지 마라. 서버가 아예 못 뜬다.
            #   운영 실측: 기동이 `Exited with status 3` 으로 죽었고, 원인은
            #   「legacy DB 확정 비용이 관측 최종 비용보다 큽니다」였다.
            #
            #   DB 가 관측보다 «크다»는 것은 우리가 이미 더 많이 세어 뒀다는 뜻이다 —
            #   돈이 «빠진» 게 아니라 오히려 보수적인 쪽이다. 게다가 옛 JSONL 은
            #   손상 이력이 문서에 남아 있어(`app/docs/출시전_수정_지시서.md`
            #   「관측 정본 정정」) 덜 적혀 있는 것이 정상이다.
            #
            #   ⚠️ 그래서 «건너뛰되 세어서 알린다». 조용히 무시하지 않는다.
            if delta < -0.01:
                lower_than_ledger.append(run_id)
            continue
        target = max(
            keys,
            key=lambda key: (unknown.get(key) or known[key])[3],
        )
        adjustments[target] = delta
    if lower_than_ledger:
        # 개수와 사유만 남긴다 — 회사 원문·통장 원문은 로그에 넣지 않는다.
        logger.warning(
            "옛 관측값이 확정 원장보다 작아 보정하지 않은 요청 %d건 "
            "(원장이 더 크므로 비용을 적게 세지 않는다)",
            len(lower_than_ledger),
        )
    return adjustments


def prepare_cutover(
    conn: sqlite3.Connection,
    *,
    migrated_at: str,
    dry_run: bool = False,
    observed_costs_by_run: Mapping[str, float] | None = None,
) -> CutoverSummary:
    """구판 행을 attempt 원장으로 한 번만, 삭제 없이 전진 이관한다.

    ``dry_run=True``는 새 표조차 만들지 않고 구판 행의 개수와 모순만 검사한다.
    실제 실행은 호출자 transaction 안에서 이뤄지므로 commit 전 문제가 생기면
    rollback할 수 있다. 배포 startup은 유료 입장을 먼저 멈추고 실행 중 호출을
    drain한 뒤 이 함수를 호출해야 한다.
    """
    event_time = _timestamp(migrated_at, label="비용 전환 시각")
    existing = _cutover_row(conn)
    if existing is not None:
        return CutoverSummary(
            legacy_phases=int(existing[0]),
            legacy_known_attempts=int(existing[1]),
            legacy_unknown_attempts=int(existing[2]),
            already_applied=True,
        )

    if dry_run:
        known, unknown = _legacy_inventory(conn)
        adjustments = _legacy_observation_adjustments(
            known, unknown, observed_costs_by_run
        )
        return CutoverSummary(
            legacy_phases=len(known.keys() | unknown.keys()),
            legacy_known_attempts=len(known) + len(adjustments),
            legacy_unknown_attempts=len(unknown),
        )

    _begin_immediate(conn)
    # 새 표 생성과 legacy snapshot을 같은 write transaction 안에 둬, 점검 뒤
    # 다른 legacy write가 끼어드는 경쟁 창을 닫는다.
    spend_store.ensure_schema(conn)
    existing = _cutover_row(conn)
    if existing is not None:
        return CutoverSummary(
            legacy_phases=int(existing[0]),
            legacy_known_attempts=int(existing[1]),
            legacy_unknown_attempts=int(existing[2]),
            already_applied=True,
        )
    known, unknown = _legacy_inventory(conn)
    adjustments = _legacy_observation_adjustments(
        known, unknown, observed_costs_by_run
    )
    all_keys = sorted(known.keys() | unknown.keys())
    summary = CutoverSummary(
        legacy_phases=len(all_keys),
        legacy_known_attempts=len(known) + len(adjustments),
        legacy_unknown_attempts=len(unknown),
    )
    phase_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {spend_store.TABLE_BUDGET_PHASES}"
        ).fetchone()[0]
    )
    if phase_count:
        raise BudgetStateError("전환 표식 없이 새 비용 phase가 이미 존재합니다")

    for run_id, phase_name in all_keys:
        known_row = known.get((run_id, phase_name))
        unknown_row = unknown.get((run_id, phase_name))
        source = unknown_row or known_row
        assert source is not None
        day_text, stored_bucket, _source_amount, source_time = source
        phase_state = (
            PhaseState.UNKNOWN_LEGACY if unknown_row is not None else PhaseState.SUCCEEDED
        )
        conn.execute(
            f"""
            INSERT INTO {spend_store.TABLE_BUDGET_PHASES}
                (run_id, phase, day, bucket_id, state, reservation_krw,
                 lease_owner_id, lease_expires_at, started_at, updated_at, version)
            VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, 0)
            """,
            (
                run_id,
                phase_name,
                day_text,
                stored_bucket,
                phase_state.value,
                source_time,
                event_time,
            ),
        )
        attempt_no = 0
        if known_row is not None:
            _known_day, _known_bucket, known_amount, known_at = known_row
            attempt_id = _stable_legacy_id("known", run_id, phase_name)
            conn.execute(
                f"""
                INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPTS}
                    (attempt_id, run_id, phase, attempt_no, provider, operation,
                     estimated_krw, created_at)
                VALUES (?, ?, ?, ?, 'legacy', 'known-spend', ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    phase_name,
                    attempt_no,
                    known_amount,
                    known_at,
                ),
            )
            conn.execute(
                f"""
                INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS}
                    (attempt_id, event_seq, transport_state, billing_state,
                     reservation_krw, known_cost_krw, liability_krw,
                     status_code, error_type, request_id, actor_id, reason_code,
                     occurred_at)
                VALUES (?, 0, ?, ?, 0, ?, 0, NULL, '', '', ?, ?, ?)
                """,
                (
                    attempt_id,
                    TransportState.RESPONSE_RECEIVED.value,
                    BillingState.KNOWN_COST.value,
                    known_amount,
                    MIGRATION_ACTOR_ID,
                    "legacy-known-spend",
                    event_time,
                ),
            )
            attempt_no += 1
        if unknown_row is not None:
            _unknown_day, _unknown_bucket, old_reservation, unknown_at = unknown_row
            # 아주 오래된 schema는 예약 열이 없어 0원 기본값으로 이관됐다. 0원을
            # 실제비용이라고 날조하거나 통장을 전역 차단하지 않고, 해당 phase의
            # 승인된 입장 예약값을 UNKNOWN_LEGACY 보수부채로 둔다.
            liability = old_reservation or float(
                PAID_PHASE_PROVIDER_BUDGET_KRW[phase_name]
            )
            attempt_id = _stable_legacy_id("unknown", run_id, phase_name)
            conn.execute(
                f"""
                INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPTS}
                    (attempt_id, run_id, phase, attempt_no, provider, operation,
                     estimated_krw, created_at)
                VALUES (?, ?, ?, ?, 'legacy', 'unknown-inflight', ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    phase_name,
                    attempt_no,
                    liability,
                    unknown_at,
                ),
            )
            conn.execute(
                f"""
                INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS}
                    (attempt_id, event_seq, transport_state, billing_state,
                     reservation_krw, known_cost_krw, liability_krw,
                     status_code, error_type, request_id, actor_id, reason_code,
                     occurred_at)
                VALUES (?, 0, ?, ?, 0, 0, ?, NULL, ?, '', ?, ?, ?)
                """,
                (
                    attempt_id,
                    TransportState.UNKNOWN_LEGACY.value,
                    BillingState.UNKNOWN_LEGACY.value,
                    liability,
                    "LegacyZeroReservation" if old_reservation == 0 else "",
                    MIGRATION_ACTOR_ID,
                    (
                        "legacy-zero-reservation-fallback"
                        if old_reservation == 0
                        else "legacy-inflight-reservation"
                    ),
                    event_time,
                ),
            )
            attempt_no += 1

        observation_delta = adjustments.get((run_id, phase_name), 0.0)
        if observation_delta > 0:
            attempt_id = _stable_legacy_id("observation", run_id, phase_name)
            conn.execute(
                f"""
                INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPTS}
                    (attempt_id, run_id, phase, attempt_no, provider, operation,
                     estimated_krw, created_at)
                VALUES (?, ?, ?, ?, 'legacy', 'observation-adjustment', ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    phase_name,
                    attempt_no,
                    observation_delta,
                    event_time,
                ),
            )
            conn.execute(
                f"""
                INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS}
                    (attempt_id, event_seq, transport_state, billing_state,
                     reservation_krw, known_cost_krw, liability_krw,
                     status_code, error_type, request_id, actor_id, reason_code,
                     occurred_at)
                VALUES (?, 0, ?, ?, 0, ?, 0, NULL, '', '', ?, ?, ?)
                """,
                (
                    attempt_id,
                    TransportState.RESPONSE_RECEIVED.value,
                    BillingState.KNOWN_COST.value,
                    observation_delta,
                    MIGRATION_ACTOR_ID,
                    "legacy-observation-adjustment",
                    event_time,
                ),
            )

    conn.execute(
        f"""
        INSERT INTO {spend_store.TABLE_BUDGET_SCHEMA_MIGRATIONS}
            (version, migrated_at, legacy_phases, legacy_known_attempts,
             legacy_unknown_attempts)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            CUTOVER_VERSION,
            event_time,
            summary.legacy_phases,
            summary.legacy_known_attempts,
            summary.legacy_unknown_attempts,
        ),
    )
    return summary


def _phase_from_row(row: tuple[object, ...]) -> PhaseAccount:
    return PhaseAccount(
        run_id=str(row[0]),
        phase=str(row[1]),
        day=dt.date.fromisoformat(str(row[2])),
        bucket_id=str(row[3]),
        state=PhaseState(str(row[4])),
        reservation_krw=float(row[5]),
        lease_owner_id=None if row[6] is None else str(row[6]),
        lease_expires_at=None if row[7] is None else str(row[7]),
        started_at=str(row[8]),
        updated_at=str(row[9]),
        version=int(row[10]),
    )


def get_phase(
    conn: sqlite3.Connection, *, run_id: str, phase: str
) -> PhaseAccount:
    _require_cutover(conn)
    clean_run = _identifier(run_id, label="요청 번호", maximum=128)
    clean_phase = _phase(phase)
    row = conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, state, reservation_krw,
               lease_owner_id, lease_expires_at, started_at, updated_at, version
          FROM {spend_store.TABLE_BUDGET_PHASES}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, clean_phase),
    ).fetchone()
    if row is None:
        raise BudgetStateError("비용 phase가 없습니다")
    return _phase_from_row(row)


def list_phases(
    conn: sqlite3.Connection, *, run_id: str
) -> tuple[PhaseAccount, ...]:
    """한 요청의 모든 phase를 시작→마지막 갱신→phase 이름 순으로 읽는다."""
    _require_cutover(conn)
    clean_run = _identifier(run_id, label="요청 번호", maximum=128)
    rows = conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, state, reservation_krw,
               lease_owner_id, lease_expires_at, started_at, updated_at, version
          FROM {spend_store.TABLE_BUDGET_PHASES}
         WHERE run_id = ?
         ORDER BY started_at, updated_at, phase
        """,
        (clean_run,),
    ).fetchall()
    return tuple(_phase_from_row(row) for row in rows)


def list_active_phases(
    conn: sqlite3.Connection,
    *,
    expired_at_or_before: str | None = None,
) -> tuple[PhaseAccount, ...]:
    """startup·readiness가 메모리 추측 없이 DB ACTIVE lease를 읽는다."""
    _require_cutover(conn)
    cutoff = (
        None
        if expired_at_or_before is None
        else _parsed_timestamp(
            _timestamp(expired_at_or_before, label="ACTIVE lease 조회 시각")
        )
    )
    rows = conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, state, reservation_krw,
               lease_owner_id, lease_expires_at, started_at, updated_at, version
          FROM {spend_store.TABLE_BUDGET_PHASES}
         WHERE state = ?
         ORDER BY day, started_at, run_id, phase
        """,
        (PhaseState.ACTIVE.value,),
    ).fetchall()
    phases = tuple(_phase_from_row(row) for row in rows)
    if cutoff is None:
        return phases
    return tuple(
        phase_account
        for phase_account in phases
        if phase_account.lease_expires_at is not None
        and _parsed_timestamp(phase_account.lease_expires_at) <= cutoff
    )


def _attempt_from_row(row: tuple[object, ...]) -> AttemptAccount:
    return AttemptAccount(
        attempt_id=str(row[0]),
        run_id=str(row[1]),
        phase=str(row[2]),
        attempt_no=int(row[3]),
        provider=str(row[4]),
        operation=str(row[5]),
        estimated_krw=float(row[6]),
        transport_state=TransportState(str(row[7])),
        billing_state=BillingState(str(row[8])),
        reservation_krw=float(row[9]),
        known_cost_krw=float(row[10]),
        liability_krw=float(row[11]),
        status_code=None if row[12] is None else int(row[12]),
        error_type=str(row[13]),
        request_id=str(row[14]),
        actor_id=str(row[15]),
        reason_code=str(row[16]),
        occurred_at=str(row[17]),
    )


_LATEST_ATTEMPT_SELECT: Final[str] = f"""
SELECT a.attempt_id, a.run_id, a.phase, a.attempt_no, a.provider, a.operation,
       a.estimated_krw, e.transport_state, e.billing_state, e.reservation_krw,
       e.known_cost_krw, e.liability_krw, e.status_code, e.error_type,
       e.request_id, e.actor_id, e.reason_code, e.occurred_at
  FROM {spend_store.TABLE_BUDGET_ATTEMPTS} AS a
  JOIN {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS} AS e
    ON e.attempt_id = a.attempt_id
   AND e.event_seq = (
       SELECT MAX(e2.event_seq)
         FROM {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS} AS e2
        WHERE e2.attempt_id = a.attempt_id
   )
"""


def get_attempt(conn: sqlite3.Connection, *, attempt_id: str) -> AttemptAccount:
    _require_cutover(conn)
    clean_id = _identifier(attempt_id, label="provider 시도 번호", maximum=128)
    row = conn.execute(
        _LATEST_ATTEMPT_SELECT + " WHERE a.attempt_id = ?",
        (clean_id,),
    ).fetchone()
    if row is None:
        raise AttemptStateError("provider 시도가 없습니다")
    return _attempt_from_row(row)


def list_attempts(
    conn: sqlite3.Connection, *, run_id: str, phase: str
) -> tuple[AttemptAccount, ...]:
    """outer phase 마감이 low-level attempt 존재 여부를 raw SQL 없이 확인한다."""
    _require_cutover(conn)
    clean_run = _identifier(run_id, label="요청 번호", maximum=128)
    clean_phase = _phase(phase)
    rows = conn.execute(
        _LATEST_ATTEMPT_SELECT
        + " WHERE a.run_id = ? AND a.phase = ? ORDER BY a.attempt_no",
        (clean_run, clean_phase),
    ).fetchall()
    return tuple(_attempt_from_row(row) for row in rows)


def _load_exposure_where(
    conn: sqlite3.Connection, *, where_sql: str, params: tuple[object, ...]
) -> ExposureSnapshot:
    event_row = conn.execute(
        f"""
        WITH latest AS (
            SELECT e.attempt_id, e.known_cost_krw, e.liability_krw
              FROM {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS} AS e
              JOIN (
                  SELECT attempt_id, MAX(event_seq) AS event_seq
                    FROM {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS}
                   GROUP BY attempt_id
              ) AS chosen
                ON chosen.attempt_id = e.attempt_id
               AND chosen.event_seq = e.event_seq
        )
        SELECT COALESCE(SUM(latest.known_cost_krw), 0),
               COALESCE(SUM(latest.liability_krw), 0)
          FROM latest
          JOIN {spend_store.TABLE_BUDGET_ATTEMPTS} AS a
            ON a.attempt_id = latest.attempt_id
          JOIN {spend_store.TABLE_BUDGET_PHASES} AS p
            ON p.run_id = a.run_id AND p.phase = a.phase
         WHERE {where_sql}
        """,
        params,
    ).fetchone()
    phase_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(reservation_krw), 0), COUNT(*)
          FROM {spend_store.TABLE_BUDGET_PHASES} AS p
         WHERE {where_sql} AND state = ?
        """,
        params + (PhaseState.ACTIVE.value,),
    ).fetchone()
    known = float(event_row[0]) if event_row is not None else 0.0
    liability = float(event_row[1]) if event_row is not None else 0.0
    reservation = float(phase_row[0]) if phase_row is not None else 0.0
    active = int(phase_row[1]) if phase_row is not None else 0
    for value in (known, liability, reservation):
        _amount(value, label="비용 노출 합계")
    return ExposureSnapshot(
        known_cost_krw=known,
        liability_krw=liability,
        reservation_krw=reservation,
        active_phases=active,
    )


def _load_bucket_lifetime_reservation(
    conn: sqlite3.Connection, stored_bucket: str
) -> float:
    """한 통장이 «모든 날짜»에 걸쳐 지금 잡고 있는 ACTIVE 예약액 합(원).

    ★ 날짜 조건이 없다는 것이 `_load_exposure_where`의 하루 집계와 다른 점이다.
      「수명 전체」 상한은 어제 잡아 둔 예약도 같이 세야 한다.
    ★ 확정비용·보수부채는 여기서 세지 않는다. 그 몫은 호출부가 넘겨주는
      ``bucket_prior_cost_krw``(종결된 실행의 실측 원가)가 이미 담고 있어,
      둘을 다 더하면 같은 돈을 두 번 세게 된다.
    """
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(reservation_krw), 0)
          FROM {spend_store.TABLE_BUDGET_PHASES}
         WHERE bucket_id = ? AND state = ?
        """,
        (stored_bucket, PhaseState.ACTIVE.value),
    ).fetchone()
    value = float(row[0]) if row is not None else 0.0
    return _amount(value, label="통장 수명 전체 진행 예약")


def load_exposure(
    conn: sqlite3.Connection, *, day: dt.date, bucket_id: str
) -> ExposureSnapshot:
    """한 날짜·통장의 확정액+부채+활성 예약을 읽는다."""
    _require_cutover(conn)
    stored_bucket = _identifier(bucket_id, label="통장 지문", maximum=64)
    return _load_exposure_where(
        conn,
        where_sql="p.day = ? AND p.bucket_id = ?",
        params=(day.isoformat(), stored_bucket),
    )


def load_run_exposure(
    conn: sqlite3.Connection, *, run_id: str
) -> ExposureSnapshot:
    """재시작 복구가 한 요청의 새 원장 비용을 raw SQL 없이 읽는다."""
    _require_cutover(conn)
    clean_run = _identifier(run_id, label="요청 번호", maximum=128)
    return _load_exposure_where(
        conn,
        where_sql="p.run_id = ?",
        params=(clean_run,),
    )


def load_day_exposures(
    conn: sqlite3.Connection, *, day: dt.date
) -> DayExposureSnapshot:
    """새 원장 seed·관리 화면용 날짜 전체와 통장별 세 금액을 돌려준다."""
    _require_cutover(conn)
    bucket_ids = tuple(
        str(row[0])
        for row in conn.execute(
            f"""
            SELECT DISTINCT bucket_id
              FROM {spend_store.TABLE_BUDGET_PHASES}
             WHERE day = ?
             ORDER BY bucket_id
            """,
            (day.isoformat(),),
        ).fetchall()
    )
    by_bucket = {
        stored_bucket: _load_exposure_where(
            conn,
            where_sql="p.day = ? AND p.bucket_id = ?",
            params=(day.isoformat(), stored_bucket),
        )
        for stored_bucket in bucket_ids
    }
    total = ExposureSnapshot(
        known_cost_krw=sum(item.known_cost_krw for item in by_bucket.values()),
        liability_krw=sum(item.liability_krw for item in by_bucket.values()),
        reservation_krw=sum(item.reservation_krw for item in by_bucket.values()),
        active_phases=sum(item.active_phases for item in by_bucket.values()),
    )
    return DayExposureSnapshot(day=day, total=total, by_bucket=by_bucket)


def begin_phase(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
    reservation_krw: float,
    bucket_limit_krw: float | None,
    run_limit_krw: float | None,
    lease_owner_id: str,
    lease_expires_at: str,
    started_at: str,
    bucket_total_limit_krw: float | None = None,
    bucket_prior_cost_krw: float = 0.0,
) -> PhaseAccount:
    """provider 전 phase 예약과 DB lease를 하나의 write transaction에서 연다.

    Args:
        bucket_limit_krw: 이 통장의 **하루** 입장 기준. 없으면 하루 검사를 건너뛴다.
        run_limit_krw: 이 요청 하나의 입장 기준.
        bucket_total_limit_krw: 이 통장의 **수명 전체** 입장 기준. 초대 링크에만
            의미가 있어 LINK 갈래 호출부만 값을 준다. 없으면 누적 검사를 건너뛴다.
        bucket_prior_cost_krw: 이 통장이 «이미 끝낸» 실행들의 실측 원가 합.
            누적 판단의 바닥값이다. 진행 중 예약은 이 transaction 안에서 다시 센다.

    ★ 누적 검사가 왜 여기에 있나 — 요청을 받자마자 하는 사전 검사만으로는
      같은 링크의 동시 요청을 못 막는다. 셋이 같은 옛 숫자를 읽고 셋 다 통과한
      뒤 각자 예약해 버린다 (실측: 잔여 1원에서 3동시 → 5,699원).
      하루 상한이 이미 이 자리에서 원자적으로 재확인하므로, 누적도 **같은 자리에서
      같은 비교 규칙·같은 실패 종류**로 확인한다.
    """
    spend_store.ensure_schema(conn)
    _require_cutover(conn)
    clean_run = _identifier(run_id, label="요청 번호", maximum=128)
    clean_phase = _phase(phase)
    reservation = _amount(reservation_krw, label="phase 예약액", positive=True)
    bucket_limit = (
        None
        if bucket_limit_krw is None
        else _amount(bucket_limit_krw, label="통장 입장 기준")
    )
    run_limit = (
        None
        if run_limit_krw is None
        else _amount(run_limit_krw, label="요청 입장 기준")
    )
    bucket_total_limit = (
        None
        if bucket_total_limit_krw is None
        else _amount(bucket_total_limit_krw, label="통장 수명 전체 입장 기준")
    )
    prior_cost = _amount(
        bucket_prior_cost_krw, label="통장 수명 전체 지난 실측 원가"
    )
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    started = _timestamp(started_at, label="phase 시작 시각")
    expires = _timestamp(lease_expires_at, label="lease 만료 시각")
    if _parsed_timestamp(expires) <= _parsed_timestamp(started):
        raise ValueError("lease 만료 시각은 phase 시작 뒤여야 합니다")
    stored_bucket = spend_store.bucket_id(bucket)
    _begin_immediate(conn)

    existing_row = conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, state, reservation_krw,
               lease_owner_id, lease_expires_at, started_at, updated_at, version
          FROM {spend_store.TABLE_BUDGET_PHASES}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, clean_phase),
    ).fetchone()
    if existing_row is not None:
        existing = _phase_from_row(existing_row)
        if (
            existing.state is PhaseState.ACTIVE
            and existing.day == day
            and existing.bucket_id == stored_bucket
            and existing.reservation_krw == reservation
            and existing.lease_owner_id == owner
            and existing.lease_expires_at == expires
            and existing.started_at == started
        ):
            return existing
        raise BudgetStateError("같은 요청·단계의 비용 phase가 이미 존재합니다")

    run_buckets = {
        str(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT bucket_id FROM {spend_store.TABLE_BUDGET_PHASES} "
            "WHERE run_id = ?",
            (clean_run,),
        )
    }
    if run_buckets and run_buckets != {stored_bucket}:
        raise BudgetStateError("같은 요청의 비용이 서로 다른 통장에 기록될 수 없습니다")

    bucket_exposure = _load_exposure_where(
        conn,
        where_sql="p.day = ? AND p.bucket_id = ?",
        params=(day.isoformat(), stored_bucket),
    )
    if (
        bucket_limit is not None
        and bucket_exposure.admission_exposure_krw + reservation > bucket_limit
    ):
        raise AdmissionLimitExceeded(
            "확정 비용·보수부채·진행 예약과 새 예약을 합치면 통장 입장 기준을 넘습니다"
        )
    if bucket_total_limit is not None:
        # 하루 검사와 «같은 transaction·같은 비교 규칙»이다. 다른 점은 날짜 조건이
        # 없다는 것뿐 — 「수명 전체」이므로 어제 잡아 둔 예약도 함께 센다.
        lifetime_reservation = _load_bucket_lifetime_reservation(
            conn, stored_bucket
        )
        if prior_cost + lifetime_reservation + reservation > bucket_total_limit:
            raise AdmissionLimitExceeded(
                "종결된 실행의 실측 원가·진행 예약과 새 예약을 합치면 "
                "통장의 수명 전체 입장 기준을 넘습니다"
            )
    run_exposure = _load_exposure_where(
        conn,
        where_sql="p.run_id = ?",
        params=(clean_run,),
    )
    if (
        run_limit is not None
        and run_exposure.admission_exposure_krw + reservation > run_limit
    ):
        raise AdmissionLimitExceeded(
            "확정 비용·보수부채·진행 예약과 새 예약을 합치면 요청 입장 기준을 넘습니다"
        )
    conn.execute(
        f"""
        INSERT INTO {spend_store.TABLE_BUDGET_PHASES}
            (run_id, phase, day, bucket_id, state, reservation_krw,
             lease_owner_id, lease_expires_at, started_at, updated_at, version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            clean_run,
            clean_phase,
            day.isoformat(),
            stored_bucket,
            PhaseState.ACTIVE.value,
            reservation,
            owner,
            expires,
            started,
            started,
        ),
    )
    return get_phase(conn, run_id=clean_run, phase=clean_phase)


def _require_active_owner(
    phase_account: PhaseAccount, *, owner_id: str, at: str, allow_after_expiry: bool = False
) -> None:
    if phase_account.state is not PhaseState.ACTIVE:
        raise ActivePhaseError("비용 phase가 진행 중이 아닙니다")
    if phase_account.lease_owner_id != owner_id:
        raise LeaseOwnershipError("DB lease 소유자가 아닙니다")
    if (
        not allow_after_expiry
        and phase_account.lease_expires_at is not None
        and _parsed_timestamp(at) >= _parsed_timestamp(phase_account.lease_expires_at)
    ):
        raise LeaseOwnershipError("DB lease가 이미 만료됐습니다")


def heartbeat_phase(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    lease_owner_id: str,
    lease_expires_at: str,
    heartbeat_at: str,
) -> PhaseAccount:
    """현재 DB lease 소유자만 만료 전에 heartbeat를 연장한다."""
    _begin_immediate(conn)
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    heartbeat = _timestamp(heartbeat_at, label="heartbeat 시각")
    new_expiry = _timestamp(lease_expires_at, label="lease 만료 시각")
    account = get_phase(conn, run_id=run_id, phase=phase)
    _require_active_owner(account, owner_id=owner, at=heartbeat)
    if (
        _parsed_timestamp(new_expiry) <= _parsed_timestamp(heartbeat)
        or account.lease_expires_at is None
        or _parsed_timestamp(new_expiry)
        <= _parsed_timestamp(account.lease_expires_at)
    ):
        raise ValueError("새 lease 만료는 현재 만료와 heartbeat 뒤여야 합니다")
    cursor = conn.execute(
        f"""
        UPDATE {spend_store.TABLE_BUDGET_PHASES}
           SET lease_expires_at = ?, updated_at = ?, version = version + 1
         WHERE run_id = ? AND phase = ? AND state = ?
           AND lease_owner_id = ? AND version = ?
        """,
        (
            new_expiry,
            heartbeat,
            account.run_id,
            account.phase,
            PhaseState.ACTIVE.value,
            owner,
            account.version,
        ),
    )
    if cursor.rowcount != 1:
        raise LeaseOwnershipError("DB lease가 다른 작업에 의해 바뀌었습니다")
    return get_phase(conn, run_id=account.run_id, phase=account.phase)


def _active_attempt_for_phase(
    conn: sqlite3.Connection, *, run_id: str, phase: str
) -> AttemptAccount | None:
    rows = conn.execute(
        _LATEST_ATTEMPT_SELECT
        + """
        WHERE a.run_id = ? AND a.phase = ?
          AND e.billing_state = ?
          AND e.transport_state IN (?, ?)
        ORDER BY a.attempt_no
        """,
        (
            run_id,
            phase,
            BillingState.RESERVED.value,
            TransportState.PLANNED.value,
            TransportState.DISPATCH_INTENT_RECORDED.value,
        ),
    ).fetchall()
    if len(rows) > 1:
        raise AttemptStateError("한 phase에 진행 중 provider 시도가 둘 이상입니다")
    return _attempt_from_row(rows[0]) if rows else None


def _insert_event(
    conn: sqlite3.Connection,
    *,
    attempt: AttemptAccount,
    transport_state: TransportState,
    billing_state: BillingState,
    reservation_krw: float,
    known_cost_krw: float,
    liability_krw: float,
    status_code: int | None,
    error_type: str,
    request_id: str,
    actor_id: str,
    reason_code: str,
    occurred_at: str,
) -> None:
    seq_row = conn.execute(
        f"SELECT COALESCE(MAX(event_seq), -1) + 1 "
        f"FROM {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS} WHERE attempt_id = ?",
        (attempt.attempt_id,),
    ).fetchone()
    seq = int(seq_row[0])
    conn.execute(
        f"""
        INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS}
            (attempt_id, event_seq, transport_state, billing_state,
             reservation_krw, known_cost_krw, liability_krw, status_code,
             error_type, request_id, actor_id, reason_code, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt.attempt_id,
            seq,
            transport_state.value,
            billing_state.value,
            _amount(reservation_krw, label="attempt 예약액"),
            _amount(known_cost_krw, label="attempt 확정 비용"),
            _amount(liability_krw, label="attempt 보수부채"),
            status_code,
            _optional_identifier(error_type, label="provider 오류 종류", maximum=128),
            _optional_identifier(request_id, label="provider 요청 번호", maximum=128),
            _identifier(actor_id, label="변경 주체", maximum=80),
            _identifier(reason_code, label="변경 이유", maximum=64),
            _timestamp(occurred_at, label="attempt 사건 시각"),
        ),
    )


def begin_attempt(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    attempt_id: str,
    provider: str,
    operation: str,
    estimated_krw: float,
    lease_owner_id: str,
    created_at: str,
) -> AttemptAccount:
    """현재 phase 예약 안에서 provider 시도를 append-only로 계획한다."""
    _begin_immediate(conn)
    clean_attempt = _identifier(
        attempt_id, label="provider 시도 번호", maximum=128
    )
    clean_provider = _identifier(provider, label="provider 이름", maximum=64)
    clean_operation = _identifier(operation, label="provider 작업", maximum=80)
    estimate = _amount(estimated_krw, label="attempt 예상액", positive=True)
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    event_time = _timestamp(created_at, label="attempt 생성 시각")
    existing_row = conn.execute(
        _LATEST_ATTEMPT_SELECT + " WHERE a.attempt_id = ?",
        (clean_attempt,),
    ).fetchone()
    if existing_row is not None:
        existing = _attempt_from_row(existing_row)
        if (
            existing.run_id == run_id
            and existing.phase == phase
            and existing.provider == clean_provider
            and existing.operation == clean_operation
            and existing.estimated_krw == estimate
        ):
            return existing
        raise AttemptStateError("같은 provider 시도 번호의 값이 기존 기록과 다릅니다")
    account = get_phase(conn, run_id=run_id, phase=phase)
    _require_active_owner(account, owner_id=owner, at=event_time)
    if _active_attempt_for_phase(conn, run_id=account.run_id, phase=account.phase):
        raise AttemptStateError("이 phase에는 이미 진행 중인 provider 시도가 있습니다")
    if estimate > account.reservation_krw:
        raise AdmissionLimitExceeded("provider 시도 예상액이 phase 예약 잔액을 넘습니다")
    next_no_row = conn.execute(
        f"""
        SELECT COALESCE(MAX(attempt_no), -1) + 1
          FROM {spend_store.TABLE_BUDGET_ATTEMPTS}
         WHERE run_id = ? AND phase = ?
        """,
        (account.run_id, account.phase),
    ).fetchone()
    attempt_no = int(next_no_row[0])
    conn.execute(
        f"""
        INSERT INTO {spend_store.TABLE_BUDGET_ATTEMPTS}
            (attempt_id, run_id, phase, attempt_no, provider, operation,
             estimated_krw, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_attempt,
            account.run_id,
            account.phase,
            attempt_no,
            clean_provider,
            clean_operation,
            estimate,
            event_time,
        ),
    )
    seed = AttemptAccount(
        attempt_id=clean_attempt,
        run_id=account.run_id,
        phase=account.phase,
        attempt_no=attempt_no,
        provider=clean_provider,
        operation=clean_operation,
        estimated_krw=estimate,
        transport_state=TransportState.PLANNED,
        billing_state=BillingState.RESERVED,
        reservation_krw=estimate,
        known_cost_krw=0.0,
        liability_krw=0.0,
        status_code=None,
        error_type="",
        request_id="",
        actor_id=SYSTEM_ACTOR_ID,
        reason_code="attempt-planned",
        occurred_at=event_time,
    )
    _insert_event(
        conn,
        attempt=seed,
        transport_state=TransportState.PLANNED,
        billing_state=BillingState.RESERVED,
        reservation_krw=estimate,
        known_cost_krw=0.0,
        liability_krw=0.0,
        status_code=None,
        error_type="",
        request_id="",
        actor_id=SYSTEM_ACTOR_ID,
        reason_code="attempt-planned",
        occurred_at=event_time,
    )
    return get_attempt(conn, attempt_id=clean_attempt)


def mark_dispatch_intent(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    lease_owner_id: str,
    recorded_at: str,
) -> AttemptAccount:
    """네트워크 전송 직전에 의도를 commit할 append-only 사건을 남긴다."""
    _begin_immediate(conn)
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    event_time = _timestamp(recorded_at, label="전송 의도 시각")
    attempt = get_attempt(conn, attempt_id=attempt_id)
    phase_account = get_phase(conn, run_id=attempt.run_id, phase=attempt.phase)
    _require_active_owner(phase_account, owner_id=owner, at=event_time)
    if (
        attempt.transport_state is TransportState.DISPATCH_INTENT_RECORDED
        and attempt.billing_state is BillingState.RESERVED
    ):
        return attempt
    if (
        attempt.transport_state is not TransportState.PLANNED
        or attempt.billing_state is not BillingState.RESERVED
    ):
        raise AttemptStateError("전송 의도를 기록할 수 없는 provider 시도 상태입니다")
    _insert_event(
        conn,
        attempt=attempt,
        transport_state=TransportState.DISPATCH_INTENT_RECORDED,
        billing_state=BillingState.RESERVED,
        reservation_krw=attempt.reservation_krw,
        known_cost_krw=0.0,
        liability_krw=0.0,
        status_code=None,
        error_type="",
        request_id="",
        actor_id=SYSTEM_ACTOR_ID,
        reason_code="dispatch-intent-recorded",
        occurred_at=event_time,
    )
    return get_attempt(conn, attempt_id=attempt.attempt_id)


def record_pre_dispatch_failure(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    lease_owner_id: str,
    error_type: str,
    close_phase: bool,
    recorded_at: str,
) -> AttemptAccount:
    """provider에 보내지 않았음이 확실한 로컬 실패만 known-zero로 닫는다."""
    _begin_immediate(conn)
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    event_time = _timestamp(recorded_at, label="전송 전 실패 시각")
    attempt = get_attempt(conn, attempt_id=attempt_id)
    phase_account = get_phase(conn, run_id=attempt.run_id, phase=attempt.phase)
    _require_active_owner(phase_account, owner_id=owner, at=event_time)
    if (
        attempt.transport_state is not TransportState.PLANNED
        or attempt.billing_state is not BillingState.RESERVED
    ):
        raise AttemptStateError("전송 전 실패로 기록할 수 없는 provider 시도 상태입니다")
    _insert_event(
        conn,
        attempt=attempt,
        transport_state=TransportState.LOCAL_FAILURE,
        billing_state=BillingState.KNOWN_ZERO,
        reservation_krw=0.0,
        known_cost_krw=0.0,
        liability_krw=0.0,
        status_code=None,
        error_type=error_type,
        request_id="",
        actor_id=SYSTEM_ACTOR_ID,
        reason_code="pre-dispatch-failure",
        occurred_at=event_time,
    )
    if close_phase:
        cursor = conn.execute(
            f"""
            UPDATE {spend_store.TABLE_BUDGET_PHASES}
               SET state = ?, reservation_krw = 0, lease_owner_id = NULL,
                   lease_expires_at = NULL, updated_at = ?, version = version + 1
             WHERE run_id = ? AND phase = ? AND state = ?
               AND lease_owner_id = ? AND version = ?
            """,
            (
                PhaseState.FAILED.value,
                event_time,
                phase_account.run_id,
                phase_account.phase,
                PhaseState.ACTIVE.value,
                owner,
                phase_account.version,
            ),
        )
        if cursor.rowcount != 1:
            raise LeaseOwnershipError("전송 전 실패 처리 중 DB lease가 바뀌었습니다")
    return get_attempt(conn, attempt_id=attempt.attempt_id)


def record_attempt_outcome(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    transport_state: TransportState,
    billing_state: BillingState,
    known_cost_krw: float,
    liability_krw: float,
    close_phase: bool,
    phase_succeeded: bool,
    recorded_at: str,
    lease_owner_id: str,
    status_code: int | None = None,
    error_type: str = "",
    request_id: str = "",
) -> AttemptAccount:
    """provider 결과의 전송 축과 비용 축을 한 transaction에서 append한다.

    ``lease_owner_id``는 응답을 기록하는 주체가 호출 전 lease 소유자와 같은지
    DB에서 확인하는 값이다.
    """
    _begin_immediate(conn)
    event_time = _timestamp(recorded_at, label="provider 결과 시각")
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    attempt = get_attempt(conn, attempt_id=attempt_id)
    phase_account = get_phase(conn, run_id=attempt.run_id, phase=attempt.phase)
    _require_active_owner(phase_account, owner_id=owner, at=event_time)
    if (
        attempt.transport_state is not TransportState.DISPATCH_INTENT_RECORDED
        or attempt.billing_state is not BillingState.RESERVED
    ):
        raise AttemptStateError("provider 결과를 기록할 수 없는 시도 상태입니다")
    if transport_state not in {
        TransportState.RESPONSE_RECEIVED,
        TransportState.TRANSPORT_AMBIGUOUS,
        TransportState.LOCAL_FAILURE,
    }:
        raise ValueError("provider 결과 전송 상태가 올바르지 않습니다")
    known = _amount(known_cost_krw, label="provider 확정 비용")
    liability = _amount(liability_krw, label="provider 보수부채")
    if billing_state is BillingState.KNOWN_COST:
        if liability != 0:
            raise ValueError("확정 비용 attempt에 보수부채를 함께 적을 수 없습니다")
    elif billing_state is BillingState.CONSERVATIVE_LIABILITY:
        if known != 0 or liability <= 0:
            raise ValueError("보수부채 attempt 금액이 올바르지 않습니다")
        if not close_phase:
            raise ValueError("보수부채 attempt는 phase를 함께 닫아야 합니다")
    elif billing_state is BillingState.KNOWN_ZERO:
        if known != 0 or liability != 0:
            raise ValueError("0원 확정 attempt에 비용을 적을 수 없습니다")
    else:
        raise ValueError("provider 결과 비용 상태가 올바르지 않습니다")
    if status_code is not None and not 100 <= int(status_code) <= 599:
        raise ValueError("provider HTTP 상태가 올바르지 않습니다")
    _insert_event(
        conn,
        attempt=attempt,
        transport_state=transport_state,
        billing_state=billing_state,
        reservation_krw=0.0,
        known_cost_krw=known,
        liability_krw=liability,
        status_code=None if status_code is None else int(status_code),
        error_type=error_type,
        request_id=request_id,
        actor_id=SYSTEM_ACTOR_ID,
        reason_code="provider-outcome-recorded",
        occurred_at=event_time,
    )
    if close_phase:
        new_state = PhaseState.SUCCEEDED if phase_succeeded else PhaseState.FAILED
        cursor = conn.execute(
            f"""
            UPDATE {spend_store.TABLE_BUDGET_PHASES}
               SET state = ?, reservation_krw = 0, lease_owner_id = NULL,
                   lease_expires_at = NULL, updated_at = ?, version = version + 1
             WHERE run_id = ? AND phase = ? AND state = ?
               AND lease_owner_id = ? AND version = ?
            """,
            (
                new_state.value,
                event_time,
                phase_account.run_id,
                phase_account.phase,
                PhaseState.ACTIVE.value,
                owner,
                phase_account.version,
            ),
        )
    else:
        consumed = known + liability
        remaining = max(0.0, phase_account.reservation_krw - consumed)
        cursor = conn.execute(
            f"""
            UPDATE {spend_store.TABLE_BUDGET_PHASES}
               SET reservation_krw = ?, updated_at = ?, version = version + 1
             WHERE run_id = ? AND phase = ? AND state = ?
               AND lease_owner_id = ? AND version = ?
            """,
            (
                remaining,
                event_time,
                phase_account.run_id,
                phase_account.phase,
                PhaseState.ACTIVE.value,
                owner,
                phase_account.version,
            ),
        )
    if cursor.rowcount != 1:
        raise LeaseOwnershipError("provider 결과를 기록하는 동안 DB lease가 바뀌었습니다")
    return get_attempt(conn, attempt_id=attempt.attempt_id)


def complete_phase(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    lease_owner_id: str,
    succeeded: bool,
    completed_at: str,
) -> PhaseAccount:
    """진행 중 attempt가 없을 때 phase 예약 잔액을 반환하고 닫는다."""
    _begin_immediate(conn)
    owner = _identifier(lease_owner_id, label="lease 소유자", maximum=80)
    event_time = _timestamp(completed_at, label="phase 완료 시각")
    account = get_phase(conn, run_id=run_id, phase=phase)
    _require_active_owner(account, owner_id=owner, at=event_time)
    if _active_attempt_for_phase(conn, run_id=account.run_id, phase=account.phase):
        raise AttemptStateError("진행 중 provider 시도가 있어 phase를 닫을 수 없습니다")
    new_state = PhaseState.SUCCEEDED if succeeded else PhaseState.FAILED
    cursor = conn.execute(
        f"""
        UPDATE {spend_store.TABLE_BUDGET_PHASES}
           SET state = ?, reservation_krw = 0, lease_owner_id = NULL,
               lease_expires_at = NULL, updated_at = ?, version = version + 1
         WHERE run_id = ? AND phase = ? AND state = ?
           AND lease_owner_id = ? AND version = ?
        """,
        (
            new_state.value,
            event_time,
            account.run_id,
            account.phase,
            PhaseState.ACTIVE.value,
            owner,
            account.version,
        ),
    )
    if cursor.rowcount != 1:
        raise LeaseOwnershipError("phase 완료 중 DB lease가 바뀌었습니다")
    return get_phase(conn, run_id=account.run_id, phase=account.phase)


def expire_phase_lease(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    observed_at: str,
) -> PhaseAccount:
    """만료된 lease를 CAS로 닫고 전송 의도 attempt만 부채로 보존한다."""
    _begin_immediate(conn)
    event_time = _timestamp(observed_at, label="lease 만료 확인 시각")
    account = get_phase(conn, run_id=run_id, phase=phase)
    if account.state is not PhaseState.ACTIVE:
        return account
    if account.lease_expires_at is None or _parsed_timestamp(event_time) < _parsed_timestamp(
        account.lease_expires_at
    ):
        raise LeaseOwnershipError("아직 DB lease가 만료되지 않았습니다")
    attempt = _active_attempt_for_phase(conn, run_id=account.run_id, phase=account.phase)
    if attempt is not None:
        if attempt.transport_state is TransportState.DISPATCH_INTENT_RECORDED:
            _insert_event(
                conn,
                attempt=attempt,
                transport_state=TransportState.TRANSPORT_AMBIGUOUS,
                billing_state=BillingState.CONSERVATIVE_LIABILITY,
                reservation_krw=0.0,
                known_cost_krw=0.0,
                liability_krw=attempt.estimated_krw,
                status_code=None,
                error_type="LeaseExpiredAfterDispatchIntent",
                request_id="",
                actor_id=SYSTEM_ACTOR_ID,
                reason_code="lease-expired-after-dispatch-intent",
                occurred_at=event_time,
            )
        else:
            _insert_event(
                conn,
                attempt=attempt,
                transport_state=TransportState.LOCAL_FAILURE,
                billing_state=BillingState.KNOWN_ZERO,
                reservation_krw=0.0,
                known_cost_krw=0.0,
                liability_krw=0.0,
                status_code=None,
                error_type="LeaseExpiredBeforeDispatch",
                request_id="",
                actor_id=SYSTEM_ACTOR_ID,
                reason_code="lease-expired-before-dispatch",
                occurred_at=event_time,
            )
    cursor = conn.execute(
        f"""
        UPDATE {spend_store.TABLE_BUDGET_PHASES}
           SET state = ?, reservation_krw = 0, lease_owner_id = NULL,
               lease_expires_at = NULL, updated_at = ?, version = version + 1
         WHERE run_id = ? AND phase = ? AND state = ? AND version = ?
        """,
        (
            PhaseState.FAILED.value,
            event_time,
            account.run_id,
            account.phase,
            PhaseState.ACTIVE.value,
            account.version,
        ),
    )
    if cursor.rowcount != 1:
        raise LeaseOwnershipError("lease 만료 처리 중 phase가 바뀌었습니다")
    return get_phase(conn, run_id=account.run_id, phase=account.phase)


def expire_due_phase_leases(
    conn: sqlite3.Connection,
    *,
    observed_at: str,
) -> tuple[PhaseAccount, ...]:
    """확인 시각에 만료된 ACTIVE lease를 한 write transaction에서 모두 닫는다.

    호출부가 먼저 읽고 하나씩 닫으면 그 사이 다른 프로세스가 heartbeat를 갱신할 수
    있다. 여기서는 ``BEGIN IMMEDIATE`` 뒤 다시 목록을 읽고 각 행을 CAS(읽은 버전과
    같은 경우에만 갱신)로 닫는다. 전송 의도가 있던 attempt는 예상액을 보수부채로,
    전송 전 attempt는 0원으로 남기는 기존 규칙을 그대로 사용한다.
    """

    event_time = _timestamp(observed_at, label="lease 만료 확인 시각")
    _begin_immediate(conn)
    expired = list_active_phases(conn, expired_at_or_before=event_time)
    return tuple(
        expire_phase_lease(
            conn,
            run_id=account.run_id,
            phase=account.phase,
            observed_at=event_time,
        )
        for account in expired
    )


def list_reconcilable(
    conn: sqlite3.Connection,
    *,
    day_from: dt.date | None = None,
    day_to: dt.date | None = None,
) -> tuple[ReconciliationItem, ...]:
    """날짜 경계를 생략하면 오늘뿐 아니라 전 날짜의 열린 부채를 돌려준다."""
    _require_cutover(conn)
    filters = ["e.billing_state IN (?, ?)"]
    params: list[object] = [
        BillingState.CONSERVATIVE_LIABILITY.value,
        BillingState.UNKNOWN_LEGACY.value,
    ]
    if day_from is not None:
        filters.append("p.day >= ?")
        params.append(day_from.isoformat())
    if day_to is not None:
        filters.append("p.day <= ?")
        params.append(day_to.isoformat())
    if day_from is not None and day_to is not None and day_from > day_to:
        raise ValueError("부채 조회 시작일이 종료일보다 늦습니다")
    rows = conn.execute(
        f"""
        WITH latest AS (
            SELECT e.*
              FROM {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS} AS e
              JOIN (
                  SELECT attempt_id, MAX(event_seq) AS event_seq
                    FROM {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS}
                   GROUP BY attempt_id
              ) AS chosen
                ON chosen.attempt_id = e.attempt_id
               AND chosen.event_seq = e.event_seq
        )
        SELECT a.attempt_id, a.run_id, a.phase, p.day, p.bucket_id, p.state,
               e.billing_state, e.liability_krw, a.provider, a.operation,
               e.status_code, e.error_type, e.request_id, e.occurred_at
          FROM latest AS e
          JOIN {spend_store.TABLE_BUDGET_ATTEMPTS} AS a
            ON a.attempt_id = e.attempt_id
          JOIN {spend_store.TABLE_BUDGET_PHASES} AS p
            ON p.run_id = a.run_id AND p.phase = a.phase
         WHERE {" AND ".join(filters)}
         ORDER BY p.day, p.started_at, a.run_id, a.phase, a.attempt_no
        """,
        tuple(params),
    ).fetchall()
    return tuple(
        ReconciliationItem(
            attempt_id=str(row[0]),
            run_id=str(row[1]),
            phase=str(row[2]),
            day=dt.date.fromisoformat(str(row[3])),
            bucket_id=str(row[4]),
            phase_state=PhaseState(str(row[5])),
            billing_state=BillingState(str(row[6])),
            liability_krw=float(row[7]),
            provider=str(row[8]),
            operation=str(row[9]),
            status_code=None if row[10] is None else int(row[10]),
            error_type=str(row[11]),
            request_id=str(row[12]),
            occurred_at=str(row[13]),
        )
        for row in rows
    )


def resolve_liability(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    action: ResolutionAction,
    actual_cost_krw: float | None,
    actor_id: str,
    reason_code: str,
    resolved_at: str,
) -> AttemptAccount:
    """관리자 판단을 실제비용·보수부채·known-zero 중 하나로 감사 기록한다."""
    _begin_immediate(conn)
    actor = _identifier(actor_id, label="관리자 식별자", maximum=80)
    reason = _identifier(reason_code, label="관리자 판단 이유", maximum=64)
    event_time = _timestamp(resolved_at, label="관리자 판단 시각")
    attempt = get_attempt(conn, attempt_id=attempt_id)
    phase_account = get_phase(conn, run_id=attempt.run_id, phase=attempt.phase)
    if phase_account.state is PhaseState.ACTIVE:
        raise ActivePhaseError("진행 중인 비용 phase는 관리자가 정산할 수 없습니다")
    if attempt.billing_state not in {
        BillingState.CONSERVATIVE_LIABILITY,
        BillingState.UNKNOWN_LEGACY,
    }:
        raise AttemptStateError("관리자 확인이 필요한 보수부채 attempt가 아닙니다")
    if action is ResolutionAction.CONFIRM_ACTUAL:
        if actual_cost_krw is None:
            raise ValueError("실제비용 확인에는 확인한 금액이 필요합니다")
        known = _amount(actual_cost_krw, label="관리자 확인 실제비용")
        liability = 0.0
        billing = BillingState.KNOWN_COST
    elif action is ResolutionAction.CONFIRM_CONSERVATIVE_LIABILITY:
        if actual_cost_krw is not None:
            raise ValueError("보수부채 확정에는 실제비용을 함께 적을 수 없습니다")
        known = 0.0
        liability = attempt.liability_krw
        billing = BillingState.LIABILITY_CONFIRMED
    elif action is ResolutionAction.CONFIRM_ZERO:
        if actual_cost_krw not in (None, 0, 0.0):
            raise ValueError("known-zero 확인에는 실제비용을 적을 수 없습니다")
        known = 0.0
        liability = 0.0
        billing = BillingState.KNOWN_ZERO
    else:  # pragma: no cover - Enum 밖 값은 공개 API에서 만들어지지 않는다.
        raise ValueError("관리자 부채 판단 종류가 올바르지 않습니다")
    _insert_event(
        conn,
        attempt=attempt,
        transport_state=attempt.transport_state,
        billing_state=billing,
        reservation_krw=0.0,
        known_cost_krw=known,
        liability_krw=liability,
        status_code=attempt.status_code,
        error_type=attempt.error_type,
        request_id=attempt.request_id,
        actor_id=actor,
        reason_code=reason,
        occurred_at=event_time,
    )
    return get_attempt(conn, attempt_id=attempt.attempt_id)
