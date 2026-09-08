"""전환 표식이 지정한 비용 정본을 호출자의 읽기 snapshot에서 검증한다.

budget 상태기계의 최신 event_seq별 확정액 계약을 소비한다. 스키마 생성,
전환, lease 만료, 관리자 정산은 평가기의 권한이 아니므로 수행하지 않는다.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from src.features.pilot_evaluation.constants import (
    ATTEMPT_EVENT_TABLE,
    ATTEMPT_LEDGER_VERSION,
    ATTEMPT_TABLE,
    LEGACY_INFLIGHT_TABLE,
    LEGACY_SPEND_TABLE,
    LIABILITY_BILLING_STATES,
    MIGRATION_TABLE,
    PHASE_STATES,
    PHASE_TABLE,
    SETTLED_BILLING_STATES,
    TRANSPORT_STATES,
)


class SpendLedgerError(ValueError):
    """정본을 선택하거나 비용 상태를 검증하지 못함."""


@dataclass(frozen=True)
class SpendLedger:
    known_cost_krw: float
    billing_uncertain: bool


def _amount(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise SpendLedgerError("비용 원장의 금액이 숫자가 아닙니다")
    amount = float(value)
    if not math.isfinite(amount) or amount < 0:
        raise SpendLedgerError("비용 원장의 금액이 유한한 0 이상 수가 아닙니다")
    return amount


def _attempt_ledger_applied(conn: sqlite3.Connection) -> bool:
    tables = {
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if MIGRATION_TABLE in tables:
        versions = {
            str(row[0]) for row in conn.execute(f"SELECT version FROM {MIGRATION_TABLE}")
        }
        if versions - {ATTEMPT_LEDGER_VERSION}:
            raise SpendLedgerError("지원하는 비용 원장 전환 표식이 없습니다")
        if ATTEMPT_LEDGER_VERSION in versions:
            return True
    # 표만 생성되고 아직 전환되지 않은 DB는 legacy 계약을 따른다. 새 원장에
    # 기록이 있는데 표식이 없으면 legacy로 후퇴해 손상을 숨기지 않는다.
    for table in (PHASE_TABLE, ATTEMPT_TABLE, ATTEMPT_EVENT_TABLE):
        if table in tables and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise SpendLedgerError("전환 표식 없이 provider 비용 기록이 존재합니다")
    return False


def read_spend_ledger(conn: sqlite3.Connection, run_id: str) -> SpendLedger:
    """읽기 연결을 재사용하며 비용 정본을 요약값으로 대체하지 않는다."""
    try:
        if _attempt_ledger_applied(conn):
            return _read_attempt_ledger(conn, run_id)
        costs = [_amount(row[0]) for row in conn.execute(
            f"SELECT cost_krw FROM {LEGACY_SPEND_TABLE} WHERE run_id=?", (run_id,)
        )]
        inflight = conn.execute(
            f"SELECT 1 FROM {LEGACY_INFLIGHT_TABLE} WHERE run_id=? LIMIT 1", (run_id,)
        ).fetchone()
        return SpendLedger(_amount(math.fsum(costs)), inflight is not None)
    except (sqlite3.Error, OverflowError) as exc:
        raise SpendLedgerError("비용 정본의 스키마 또는 금액을 읽지 못했습니다") from exc


def _read_attempt_ledger(conn: sqlite3.Connection, run_id: str) -> SpendLedger:
    # 부모가 사라진 정산은 실행 ID조차 복원할 수 없다. JOIN에서 숨기지 말고
    # 비용 정본의 손상으로 처리하며 어떤 실행에도 확정된 0원으로 간주하지 않는다.
    orphan = conn.execute(
        f"SELECT 1 FROM {ATTEMPT_EVENT_TABLE} e LEFT JOIN {ATTEMPT_TABLE} a "
        "ON a.attempt_id=e.attempt_id WHERE a.attempt_id IS NULL LIMIT 1"
    ).fetchone()
    if orphan is not None:
        raise SpendLedgerError("provider 시도에 연결되지 않은 정산 기록이 있습니다")
    phases: dict[str, str] = {}
    billing_uncertain = False
    for phase, state, reservation, owner, expiry in conn.execute(
        f"SELECT phase, state, reservation_krw, lease_owner_id, lease_expires_at "
        f"FROM {PHASE_TABLE} WHERE run_id=?", (run_id,)
    ):
        amount = _amount(reservation)
        if phase in phases or state not in PHASE_STATES:
            raise SpendLedgerError("비용 phase 상태가 올바르지 않습니다")
        if state == "ACTIVE":
            if not owner or not expiry:
                raise SpendLedgerError("진행 중 비용 phase의 lease가 없습니다")
            billing_uncertain = True
        elif amount != 0 or owner is not None or expiry is not None:
            raise SpendLedgerError("마감한 비용 phase에 예약 또는 lease가 남았습니다")
        phases[phase] = state

    rows = conn.execute(
        f"SELECT a.attempt_id, a.phase, a.attempt_no, e.event_seq, e.transport_state, "
        f"e.billing_state, e.reservation_krw, e.known_cost_krw, e.liability_krw "
        f"FROM {ATTEMPT_TABLE} a LEFT JOIN {ATTEMPT_EVENT_TABLE} e "
        "ON e.attempt_id=a.attempt_id AND e.event_seq=("
        f"SELECT MAX(latest.event_seq) FROM {ATTEMPT_EVENT_TABLE} latest "
        "WHERE latest.attempt_id=a.attempt_id) WHERE a.run_id=?", (run_id,)
    ).fetchall()
    costs: list[float] = []
    seen: set[str] = set()
    seen_logical_attempts: set[tuple[str, int]] = set()
    phases_with_attempts: set[str] = set()
    for attempt, phase, attempt_no, sequence, transport, billing, reservation, known, liability in rows:
        # LEFT JOIN으로 정산 event나 phase가 없는 attempt도 발견한다.
        if phase not in phases or sequence is None or attempt in seen:
            raise SpendLedgerError("provider 시도의 phase 또는 최신 정산 기록이 없습니다")
        if type(sequence) is not int or sequence < 0 or transport not in TRANSPORT_STATES:
            raise SpendLedgerError("provider 시도의 최신 전송 기록이 올바르지 않습니다")
        if (type(attempt_no) is not int or attempt_no < 0
                or (phase, attempt_no) in seen_logical_attempts):
            raise SpendLedgerError("provider 시도 번호가 올바르지 않거나 중복됐습니다")
        seen.add(attempt)
        seen_logical_attempts.add((phase, attempt_no))
        phases_with_attempts.add(phase)
        reserved, cost, debt = map(_amount, (reservation, known, liability))
        if billing in SETTLED_BILLING_STATES:
            if reserved != 0 or debt != 0 or (billing == "KNOWN_ZERO" and cost != 0):
                raise SpendLedgerError("확정된 provider 비용 상태와 금액이 다릅니다")
            if transport in {"PLANNED", "DISPATCH_INTENT_RECORDED"}:
                raise SpendLedgerError("provider 전송 미완료 상태에 확정 비용이 있습니다")
        elif billing == "RESERVED":
            if cost != 0 or debt != 0 or reserved <= 0:
                raise SpendLedgerError("provider 예약 상태와 금액이 다릅니다")
            billing_uncertain = True
        elif billing in LIABILITY_BILLING_STATES:
            if reserved != 0 or cost != 0 or debt <= 0:
                raise SpendLedgerError("provider 부채 상태와 금액이 다릅니다")
            # 관리자 보수부채 확인도 실제비용 확정과 다르다.
            billing_uncertain = True
        else:
            raise SpendLedgerError("provider 비용 상태가 올바르지 않습니다")
        costs.append(cost)
    if any(state == "UNKNOWN_LEGACY" and phase not in phases_with_attempts
           for phase, state in phases.items()):
        raise SpendLedgerError("이관된 미확정 phase의 provider 기록이 없습니다")
    return SpendLedger(_amount(math.fsum(costs)), billing_uncertain)
