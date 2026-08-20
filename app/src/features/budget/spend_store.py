"""유료 단계별 비용을 SQLite에 덧붙여 서버 재시작 뒤에도 복원한다.

★ 관측 이력(`runs.jsonl`)만으로는 링크·사용자·관리자 중 어느 통장에서 쓴 돈인지
  알 수 없다. 반대로 이메일·열쇠 원문을 비용 원장에 남기면 필요 없는 식별정보가
  늘어난다. 그래서 통장 이름은 SHA-256 지문으로만 저장한다.

★ 한 요청은 회사 식별 → 이미지 OCR → 본조사처럼 여러 번 돈을 쓸 수 있다.
  단계별 행을 덧붙이고 `(요청 번호, 단계)`를 유일값으로 두어 같은 완료 처리가
  두 번 불려도 돈을 두 번 세지 않는다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from src.features.budget.constants import SPEND_PHASES

TABLE_SPEND_EVENTS = "budget_spend_events"
TABLE_SPEND_INFLIGHT = "budget_spend_inflight"
TABLE_SPEND_OVERRUNS = "budget_spend_overruns"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SPEND_EVENTS} (
    run_id      TEXT NOT NULL,
    phase       TEXT NOT NULL,
    day         TEXT NOT NULL,
    bucket_id   TEXT NOT NULL,
    cost_krw    REAL NOT NULL CHECK(cost_krw >= 0),
    created_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, phase)
)
"""

CREATE_DAY_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_budget_spend_day
    ON {TABLE_SPEND_EVENTS}(day)
"""

CREATE_INFLIGHT_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SPEND_INFLIGHT} (
    run_id      TEXT NOT NULL,
    phase       TEXT NOT NULL,
    day         TEXT NOT NULL,
    bucket_id   TEXT NOT NULL,
    reserved_krw REAL NOT NULL DEFAULT 0 CHECK(reserved_krw >= 0),
    started_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, phase)
)
"""

CREATE_INFLIGHT_DAY_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_budget_spend_inflight_day
    ON {TABLE_SPEND_INFLIGHT}(day)
"""

CREATE_OVERRUN_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SPEND_OVERRUNS} (
    run_id        TEXT NOT NULL,
    phase         TEXT NOT NULL,
    day           TEXT NOT NULL,
    bucket_id     TEXT NOT NULL,
    estimated_krw REAL NOT NULL CHECK(estimated_krw >= 0),
    actual_krw    REAL NOT NULL CHECK(actual_krw >= 0),
    excess_krw    REAL NOT NULL CHECK(excess_krw > 0),
    created_at    TEXT NOT NULL,
    PRIMARY KEY (run_id, phase)
)
"""

CREATE_OVERRUN_DAY_INDEX_SQL = f"""
CREATE INDEX IF NOT EXISTS idx_budget_spend_overrun_day
    ON {TABLE_SPEND_OVERRUNS}(day)
"""


@dataclass(frozen=True)
class SpendSnapshot:
    """하루치 비용 원장을 메모리 장부로 옮길 값."""

    total_krw: float = 0.0
    by_bucket: dict[str, float] = field(default_factory=dict)
    by_run: dict[str, float] = field(default_factory=dict)
    bucket_by_run: dict[str, str] = field(default_factory=dict)
    run_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RunSpendHistory:
    """관측 총액과 대조할 요청별 전 날짜 비용."""

    by_run: dict[str, float] = field(default_factory=dict)
    bucket_by_run: dict[str, str] = field(default_factory=dict)
    days_by_run: dict[str, frozenset[str]] = field(default_factory=dict)
    run_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class MonthlySpend:
    """관리 화면에 보여 줄 월 비용 원장 요약.

    ``unresolved_runs``는 단계 수가 아니라 요청 수다. 한 요청의 OCR과 본조사가
    둘 다 미확정이어도 사용자에게는 미확정 실행 1건으로 보인다.
    """

    total_krw: float = 0.0
    unresolved_runs: int = 0
    ledger_since: str = ""


@dataclass(frozen=True)
class SpendOverrunSummary:
    """provider 실제 usage가 호출 전 예상액을 넘은 관측값."""

    count: int = 0
    excess_krw: float = 0.0


@dataclass(frozen=True)
class InflightSpend:
    """아직 마감하지 않은 유료 단계 한 행.

    웹 계층은 ``(run_id, phase)``를 현재 프로세스의 실행 목록과 대조해 정상 실행과
    재시작 뒤 남은 실행을 가를 수 있다. ``bucket_id``는 원문이 아닌 지문이고,
    ``started_at``은 오래 남은 행을 운영자가 판단할 때 쓴다.
    """

    run_id: str
    phase: str
    day: dt.date
    bucket_id: str
    reserved_krw: float
    started_at: str


class BudgetCapExceeded(RuntimeError):
    """원자 예상예약을 더하면 해당 통장의 운영 중단 기준을 넘음."""


def bucket_id(bucket: str) -> str:
    """이메일·열쇠 원문을 저장하지 않는 안정적인 통장 지문."""
    normalized = (bucket or "").strip().lower().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """비용 원장 표와 날짜 조회 색인을 만든다."""
    conn.execute(CREATE_SQL)
    conn.execute(CREATE_DAY_INDEX_SQL)
    conn.execute(CREATE_INFLIGHT_SQL)
    columns = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({TABLE_SPEND_INFLIGHT})")
    }
    if "reserved_krw" not in columns:
        # 기존 inflight는 재시작 뒤 미확정 표식이다. 0원으로 이관해도 행 자체가
        # 통장을 닫으므로 과금 여부를 거짓으로 확정하지 않는다.
        conn.execute(
            f"ALTER TABLE {TABLE_SPEND_INFLIGHT} "
            "ADD COLUMN reserved_krw REAL NOT NULL DEFAULT 0 "
            "CHECK(reserved_krw >= 0)"
        )
    conn.execute(CREATE_INFLIGHT_DAY_INDEX_SQL)
    conn.execute(CREATE_OVERRUN_SQL)
    conn.execute(CREATE_OVERRUN_DAY_INDEX_SQL)


def _record_overrun(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    stored_bucket: str,
    estimated_krw: float,
    actual_krw: float,
    created_at: str,
) -> None:
    """예상 초과를 비밀 원문 없이 멱등 기록한다."""
    if actual_krw <= estimated_krw:
        return
    conn.execute(
        f"""
        INSERT INTO {TABLE_SPEND_OVERRUNS}
            (run_id, phase, day, bucket_id, estimated_krw, actual_krw,
             excess_krw, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, phase) DO UPDATE SET
            estimated_krw = excluded.estimated_krw,
            actual_krw = excluded.actual_krw,
            excess_krw = excluded.excess_krw,
            created_at = excluded.created_at
        """,
        (
            run_id,
            phase,
            day.isoformat(),
            stored_bucket,
            estimated_krw,
            actual_krw,
            actual_krw - estimated_krw,
            created_at,
        ),
    )


def _clean_run_id(run_id: str) -> str:
    clean = run_id.strip()
    if not clean:
        raise ValueError("비용 원장 요청 번호가 비어 있습니다")
    return clean


def _check_phase(phase: str) -> None:
    if phase not in SPEND_PHASES:
        raise ValueError(f"비용 원장 단계가 올바르지 않습니다: {phase!r}")


def _clean_amount(cost_krw: float) -> float:
    amount = float(cost_krw)
    if not math.isfinite(amount):
        raise ValueError("비용 원장 금액은 유한한 수여야 합니다")
    if amount < 0:
        raise ValueError("비용 원장 금액은 음수일 수 없습니다")
    return amount


def _run_bucket_ids(conn: sqlite3.Connection, run_id: str) -> set[str]:
    """완료·진행 중 표를 합쳐 한 요청에 이미 묶인 통장들을 찾는다."""
    rows = conn.execute(
        f"""
        SELECT bucket_id FROM {TABLE_SPEND_EVENTS} WHERE run_id = ?
        UNION
        SELECT bucket_id FROM {TABLE_SPEND_INFLIGHT} WHERE run_id = ?
        """,
        (run_id, run_id),
    ).fetchall()
    return {str(row[0]) for row in rows}


def _require_same_run_bucket(
    conn: sqlite3.Connection, run_id: str, stored_bucket: str
) -> None:
    known = _run_bucket_ids(conn, run_id)
    if known and known != {stored_bucket}:
        raise ValueError("같은 요청의 비용이 서로 다른 통장에 기록될 수 없습니다")


def append_spend(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
    cost_krw: float,
    created_at: str,
) -> bool:
    """유료 단계 한 건을 적는다. 이미 있으면 다시 더하지 않는다.

    Returns:
        새 행을 실제로 적었으면 True, 같은 요청·단계가 이미 있으면 False.
    """
    clean_run = _clean_run_id(run_id)
    _check_phase(phase)
    amount = _clean_amount(cost_krw)
    # 0원은 저장하지 않는다. 데모·코드 경로까지 행을 만들면 실제 유료 호출 수를
    # 셀 수 없고, 원장 크기만 계속 늘어난다.
    if amount == 0:
        return False

    stored_bucket = bucket_id(bucket)
    _require_same_run_bucket(conn, clean_run, stored_bucket)
    existing = conn.execute(
        f"""
        SELECT day, bucket_id, cost_krw
          FROM {TABLE_SPEND_EVENTS}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, phase),
    ).fetchone()
    if existing is not None:
        same = (
            str(existing[0]) == day.isoformat()
            and str(existing[1]) == stored_bucket
            and float(existing[2]) == amount
        )
        if same:
            return False
        # INSERT OR IGNORE만 쓰면 같은 요청·단계의 다른 금액도 조용히 버려져
        # 과소계상을 숨긴다. 완전히 같은 행만 멱등으로 인정한다.
        raise ValueError("같은 요청·단계의 비용 원장 값이 기존 기록과 다릅니다")

    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_SPEND_EVENTS}
            (run_id, phase, day, bucket_id, cost_krw, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            clean_run,
            phase,
            day.isoformat(),
            stored_bucket,
            amount,
            created_at,
        ),
    )
    if cursor.rowcount == 1:
        return True
    # 다른 연결이 같은 순간 먼저 쓴 경우도 완전히 같은 값일 때만 멱등이다.
    raced = conn.execute(
        f"""
        SELECT day, bucket_id, cost_krw
          FROM {TABLE_SPEND_EVENTS}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, phase),
    ).fetchone()
    if raced is not None and (
        str(raced[0]) == day.isoformat()
        and str(raced[1]) == stored_bucket
        and float(raced[2]) == amount
    ):
        return False
    raise ValueError("같은 요청·단계의 비용 원장 값이 기존 기록과 다릅니다")


def begin_inflight(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
    started_at: str,
    requested_cost_krw: float = 0.0,
    cap_krw: float | None = None,
    run_cap_krw: float | None = None,
) -> bool:
    """provider 호출 전에 진행 중 표식을 커밋할 행을 만든다.

    같은 표식이나 이미 끝난 단계가 있으면 False다. 호출자는 False일 때 provider를
    다시 부르면 안 된다.
    """
    clean_run = _clean_run_id(run_id)
    _check_phase(phase)
    requested = _clean_amount(requested_cost_krw)
    cap = math.inf if cap_krw is None else _clean_amount(cap_krw)
    run_cap = math.inf if run_cap_krw is None else _clean_amount(run_cap_krw)
    stored_bucket = bucket_id(bucket)
    # 다른 프로세스도 같은 SQLite write lock을 거쳐야 한다. 검사와 INSERT 사이에
    # 경쟁 창이 생기지 않도록 읽기 전에 RESERVED write transaction을 잡는다.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    _require_same_run_bucket(conn, clean_run, stored_bucket)

    completed = conn.execute(
        f"SELECT 1 FROM {TABLE_SPEND_EVENTS} WHERE run_id = ? AND phase = ?",
        (clean_run, phase),
    ).fetchone()
    if completed is not None:
        return False

    existing = conn.execute(
        f"""
        SELECT day, bucket_id, reserved_krw FROM {TABLE_SPEND_INFLIGHT}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, phase),
    ).fetchone()
    if existing is not None:
        if (
            str(existing[0]) != day.isoformat()
            or str(existing[1]) != stored_bucket
            or float(existing[2]) != requested
        ):
            raise ValueError("같은 요청·단계의 진행 중 표식 값이 기존 기록과 다릅니다")
        return False

    spent_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(cost_krw), 0)
          FROM {TABLE_SPEND_EVENTS}
         WHERE day = ? AND bucket_id = ?
        """,
        (day.isoformat(), stored_bucket),
    ).fetchone()
    reserved_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(reserved_krw), 0)
          FROM {TABLE_SPEND_INFLIGHT}
         WHERE day = ? AND bucket_id = ?
        """,
        (day.isoformat(), stored_bucket),
    ).fetchone()
    spent = float(spent_row[0]) if spent_row is not None else 0.0
    reserved = float(reserved_row[0]) if reserved_row is not None else 0.0
    if not math.isfinite(spent) or not math.isfinite(reserved):
        raise ValueError("비용 원장의 확정액 또는 예약액이 유한한 수가 아닙니다")
    if spent + reserved + requested > cap:
        raise BudgetCapExceeded(
            "확정 비용과 진행 중 예상예약을 합치면 통장 운영 기준을 넘습니다"
        )

    run_spent_row = conn.execute(
        f"SELECT COALESCE(SUM(cost_krw), 0) FROM {TABLE_SPEND_EVENTS} "
        "WHERE run_id = ?",
        (clean_run,),
    ).fetchone()
    run_reserved_row = conn.execute(
        f"SELECT COALESCE(SUM(reserved_krw), 0) FROM {TABLE_SPEND_INFLIGHT} "
        "WHERE run_id = ?",
        (clean_run,),
    ).fetchone()
    run_spent = float(run_spent_row[0]) if run_spent_row is not None else 0.0
    run_reserved = (
        float(run_reserved_row[0]) if run_reserved_row is not None else 0.0
    )
    if not math.isfinite(run_spent) or not math.isfinite(run_reserved):
        raise ValueError("요청별 확정액 또는 예약액이 유한한 수가 아닙니다")
    if run_spent + run_reserved + requested > run_cap:
        raise BudgetCapExceeded("이 요청의 예상예약을 합치면 건당 운영 기준을 넘습니다")

    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_SPEND_INFLIGHT}
            (run_id, phase, day, bucket_id, reserved_krw, started_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            clean_run,
            phase,
            day.isoformat(),
            stored_bucket,
            requested,
            started_at,
        ),
    )
    if cursor.rowcount == 1:
        return True
    raced = conn.execute(
        f"""
        SELECT day, bucket_id, reserved_krw FROM {TABLE_SPEND_INFLIGHT}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, phase),
    ).fetchone()
    if raced is not None and (
        str(raced[0]) == day.isoformat()
        and str(raced[1]) == stored_bucket
        and float(raced[2]) == requested
    ):
        return False
    raise ValueError("같은 요청·단계의 진행 중 표식 값이 기존 기록과 다릅니다")


def _require_inflight(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
) -> tuple[str, str, float]:
    clean_run = _clean_run_id(run_id)
    _check_phase(phase)
    stored_bucket = bucket_id(bucket)
    row = conn.execute(
        f"""
        SELECT day, bucket_id, reserved_krw FROM {TABLE_SPEND_INFLIGHT}
         WHERE run_id = ? AND phase = ?
        """,
        (clean_run, phase),
    ).fetchone()
    if row is None:
        raise ValueError("마감할 진행 중 비용 표식이 없습니다")
    if str(row[0]) != day.isoformat() or str(row[1]) != stored_bucket:
        raise ValueError("진행 중 비용 표식의 날짜 또는 통장이 다릅니다")
    reserved = _clean_amount(float(row[2]))
    return clean_run, stored_bucket, reserved


def finish_inflight(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
    cost_krw: float,
    created_at: str,
) -> bool:
    """확정 비용 저장과 진행 중 표식 삭제를 같은 트랜잭션에서 한다."""
    clean_run, _stored_bucket, reserved = _require_inflight(
        conn, run_id=run_id, phase=phase, day=day, bucket=bucket
    )
    amount = _clean_amount(cost_krw)
    inserted = append_spend(
        conn,
        run_id=clean_run,
        phase=phase,
        day=day,
        bucket=bucket,
        cost_krw=amount,
        created_at=created_at,
    )
    _record_overrun(
        conn,
        run_id=clean_run,
        phase=phase,
        day=day,
        stored_bucket=_stored_bucket,
        estimated_krw=reserved,
        actual_krw=amount,
        created_at=created_at,
    )
    conn.execute(
        f"DELETE FROM {TABLE_SPEND_INFLIGHT} WHERE run_id = ? AND phase = ?",
        (clean_run, phase),
    )
    return inserted


def keep_inflight_with_known_spend(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
    cost_krw: float,
    created_at: str,
) -> bool:
    """API 예외 때 확인된 앞 호출 비용만 적고 미확정 표식은 남긴다."""
    clean_run, _stored_bucket, reserved = _require_inflight(
        conn, run_id=run_id, phase=phase, day=day, bucket=bucket
    )
    amount = _clean_amount(cost_krw)
    inserted = append_spend(
        conn,
        run_id=clean_run,
        phase=phase,
        day=day,
        bucket=bucket,
        cost_krw=amount,
        created_at=created_at,
    )
    _record_overrun(
        conn,
        run_id=clean_run,
        phase=phase,
        day=day,
        stored_bucket=_stored_bucket,
        estimated_krw=reserved,
        actual_krw=amount,
        created_at=created_at,
    )
    if inserted and amount > 0:
        conn.execute(
            f"""
            UPDATE {TABLE_SPEND_INFLIGHT}
               SET reserved_krw = MAX(0, reserved_krw - ?)
             WHERE run_id = ? AND phase = ?
            """,
            (amount, clean_run, phase),
        )
    return inserted


def load_overrun_day(
    conn: sqlite3.Connection, day: dt.date
) -> SpendOverrunSummary:
    """관리 화면에 보여줄 오늘 예상 초과 횟수·차액."""
    row = conn.execute(
        f"""
        SELECT COUNT(*), COALESCE(SUM(excess_krw), 0)
          FROM {TABLE_SPEND_OVERRUNS}
         WHERE day = ?
        """,
        (day.isoformat(),),
    ).fetchone()
    if row is None:
        return SpendOverrunSummary()
    count, excess = int(row[0]), float(row[1])
    if count < 0 or not math.isfinite(excess) or excess < 0:
        raise ValueError("예상 초과 비용 원장이 올바르지 않습니다")
    return SpendOverrunSummary(count=count, excess_krw=excess)


def cancel_inflight(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str,
    day: dt.date,
    bucket: str,
) -> None:
    """provider를 부르기 전임이 확실할 때만 진행 중 표식을 지운다."""
    clean_run, _stored_bucket, _reserved = _require_inflight(
        conn, run_id=run_id, phase=phase, day=day, bucket=bucket
    )
    completed = conn.execute(
        f"SELECT 1 FROM {TABLE_SPEND_EVENTS} WHERE run_id = ? AND phase = ?",
        (clean_run, phase),
    ).fetchone()
    if completed is not None:
        raise ValueError("비용이 기록된 진행 중 표식은 취소할 수 없습니다")
    conn.execute(
        f"DELETE FROM {TABLE_SPEND_INFLIGHT} WHERE run_id = ? AND phase = ?",
        (clean_run, phase),
    )


def load_unresolved_day(
    conn: sqlite3.Connection, day: dt.date
) -> frozenset[str]:
    """그날 마감되지 않은 통장 지문들. 다른 날 표식은 오늘을 막지 않는다."""
    rows = conn.execute(
        f"SELECT DISTINCT bucket_id FROM {TABLE_SPEND_INFLIGHT} WHERE day = ?",
        (day.isoformat(),),
    ).fetchall()
    return frozenset(str(row[0]) for row in rows)


def get_inflight_phase(conn: sqlite3.Connection, run_id: str) -> str | None:
    """요청 하나의 현재 미마감 유료 단계만 읽는다.

    통장 지문이나 시작 시각은 호출부에 내보내지 않는다. 정상 요청에는 현재 단계가
    하나뿐이어야 하므로 둘 이상이면 임의로 최신값을 고르지 않고 깨진 상태로 알린다.
    """
    clean_run = _clean_run_id(run_id)
    rows = conn.execute(
        f"""
        SELECT phase
          FROM {TABLE_SPEND_INFLIGHT}
         WHERE run_id = ?
         ORDER BY phase
         LIMIT 2
        """,
        (clean_run,),
    ).fetchall()
    phases = tuple(str(row[0]) for row in rows)
    for phase in phases:
        _check_phase(phase)
    if len(phases) > 1:
        raise ValueError("한 요청에 미마감 비용 단계가 둘 이상 있습니다")
    return phases[0] if phases else None


def list_inflight_day(
    conn: sqlite3.Connection, day: dt.date
) -> tuple[InflightSpend, ...]:
    """특정 날짜의 미마감 단계를 시작 시각·요청·단계 순으로 돌려준다.

    통장 지문만 돌려주는 ``load_unresolved_day``와 달리 각 행의 요청과 단계를
    보존한다. 같은 시각의 행도 기본키까지 정렬하므로 재시작과 반복 조회에서 순서가
    흔들리지 않는다.
    """
    rows = conn.execute(
        f"""
        SELECT run_id, phase, day, bucket_id, reserved_krw, started_at
          FROM {TABLE_SPEND_INFLIGHT}
         WHERE day = ?
         ORDER BY started_at, run_id, phase, bucket_id
        """,
        (day.isoformat(),),
    ).fetchall()
    return tuple(
        InflightSpend(
            run_id=str(row[0]),
            phase=str(row[1]),
            day=dt.date.fromisoformat(str(row[2])),
            bucket_id=str(row[3]),
            reserved_krw=float(row[4]),
            started_at=str(row[5]),
        )
        for row in rows
    )


def load_day(conn: sqlite3.Connection, day: dt.date) -> SpendSnapshot:
    """그날의 전체 비용과 통장별 비용을 복원한다."""
    rows = conn.execute(
        f"""
        SELECT run_id, bucket_id, cost_krw
          FROM {TABLE_SPEND_EVENTS}
         WHERE day = ?
        """,
        (day.isoformat(),),
    ).fetchall()
    by_bucket: dict[str, float] = {}
    by_run: dict[str, float] = {}
    bucket_by_run: dict[str, str] = {}
    run_ids: set[str] = set()
    total = 0.0
    for row in rows:
        run_id, stored_bucket, amount = str(row[0]), str(row[1]), float(row[2])
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("비용 원장에 유효하지 않은 금액이 있습니다")
        previous_bucket = bucket_by_run.get(run_id)
        if previous_bucket is not None and previous_bucket != stored_bucket:
            raise ValueError("한 요청이 여러 통장에 걸친 비용 원장이 있습니다")
        run_ids.add(run_id)
        by_bucket[stored_bucket] = by_bucket.get(stored_bucket, 0.0) + amount
        by_run[run_id] = by_run.get(run_id, 0.0) + amount
        bucket_by_run.setdefault(run_id, stored_bucket)
        total += amount
    return SpendSnapshot(
        total_krw=total,
        by_bucket=by_bucket,
        by_run=by_run,
        bucket_by_run=bucket_by_run,
        run_ids=frozenset(run_ids),
    )


def load_month(
    conn: sqlite3.Connection,
    day: dt.date,
    *,
    known_active: Iterable[tuple[str, str, str, str]] = (),
) -> MonthlySpend:
    """``day``가 속한 달의 확정 비용과 미확정 요청 수를 원장에서 읽는다.

    관측 JSONL은 품질 퍼널용이고 비용의 정본은 이 SQLite 원장이다. 원장 도입
    전 기간이 월 합계에 섞였다고 오해하지 않도록 최초 기록일도 함께 돌려준다.
    현재 프로세스가 정상 실행 중이라고 아는 ``(day, bucket, run, phase)`` 표식은
    청구 미확정 수에서 뺀다. 같은 요청에 다른 고아 표식도 있으면 그 요청은 센다.
    """
    month_prefix = f"{day.year:04d}-{day.month:02d}-%"
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(cost_krw), 0), COUNT(DISTINCT run_id)
          FROM {TABLE_SPEND_EVENTS}
         WHERE day LIKE ?
        """,
        (month_prefix,),
    ).fetchone()
    total = float(row[0]) if row is not None else 0.0
    if not math.isfinite(total) or total < 0:
        raise ValueError("비용 원장에 유효하지 않은 월 합계가 있습니다")

    inflight_rows = conn.execute(
        f"""
        SELECT day, bucket_id, run_id, phase
          FROM {TABLE_SPEND_INFLIGHT}
         WHERE day LIKE ?
        """,
        (month_prefix,),
    ).fetchall()
    active = set(known_active)
    unresolved_run_ids = {
        str(row[2])
        for row in inflight_rows
        if (str(row[0]), str(row[1]), str(row[2]), str(row[3])) not in active
    }
    since_row = conn.execute(
        f"""
        SELECT MIN(day)
          FROM (
                SELECT day FROM {TABLE_SPEND_EVENTS}
                UNION ALL
                SELECT day FROM {TABLE_SPEND_INFLIGHT}
          )
        """
    ).fetchone()
    return MonthlySpend(
        total_krw=total,
        unresolved_runs=len(unresolved_run_ids),
        ledger_since=str(since_row[0]) if since_row and since_row[0] is not None else "",
    )


def load_run_history(
    conn: sqlite3.Connection, run_ids: Iterable[str]
) -> RunSpendHistory:
    """지정한 요청의 전 날짜 원장 합계·통장·날짜를 읽는다.

    ★ 완료 이력이 오늘 찍혀도 식별은 어제였을 수 있다. 오늘 행만 보고 차액을
    보충하면 어제 돈을 오늘 돈으로 지어내므로 전 날짜를 함께 대조한다.
    """
    wanted = {run_id.strip() for run_id in run_ids if run_id.strip()}
    if not wanted:
        return RunSpendHistory()
    # 요청 수가 SQLite 바인딩 상한을 넘어도 동작하도록 행을 한 번 읽고 Python에서
    # 거른다. 한 요청당 최대 세 행이라 원장 크기는 관측 파일과 같은 차수다.
    rows = conn.execute(
        f"SELECT run_id, day, bucket_id, cost_krw FROM {TABLE_SPEND_EVENTS}"
    ).fetchall()
    by_run: dict[str, float] = {}
    bucket_by_run: dict[str, str] = {}
    mutable_days: dict[str, set[str]] = {}
    for row in rows:
        run_id = str(row[0])
        if run_id not in wanted:
            continue
        day, stored_bucket, amount = str(row[1]), str(row[2]), float(row[3])
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("비용 원장에 유효하지 않은 금액이 있습니다")
        previous_bucket = bucket_by_run.get(run_id)
        if previous_bucket is not None and previous_bucket != stored_bucket:
            raise ValueError("한 요청이 여러 통장에 걸친 비용 원장이 있습니다")
        bucket_by_run.setdefault(run_id, stored_bucket)
        by_run[run_id] = by_run.get(run_id, 0.0) + amount
        mutable_days.setdefault(run_id, set()).add(day)
    return RunSpendHistory(
        by_run=by_run,
        bucket_by_run=bucket_by_run,
        days_by_run={key: frozenset(value) for key, value in mutable_days.items()},
        run_ids=frozenset(by_run),
    )
