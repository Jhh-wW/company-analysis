"""Append-only AI usage events and separate customer charge decisions.

This schema intentionally has no prompt, response, company, token, or secret
columns.  Monthly server cost lives in a different table and is never added to
per-report AI variable cost.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Final, Iterable

from src.features.export_pdf.release import is_valid_sha256
from src.features.pipeline.port import Outcome


AI_EVENT_TABLE: Final[str] = "ai_variable_cost_events"
RUN_COST_TABLE: Final[str] = "report_cost_summaries"
SERVER_COST_TABLE: Final[str] = "monthly_server_fixed_costs"

CREATE_AI_EVENT_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {AI_EVENT_TABLE} (
    event_id              TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    sequence              INTEGER NOT NULL,
    stage                 TEXT NOT NULL,
    model_id              TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL,
    output_tokens         INTEGER NOT NULL,
    cache_creation_tokens INTEGER NOT NULL,
    cache_read_tokens     INTEGER NOT NULL,
    batch_applied         INTEGER NOT NULL,
    cost_krw              REAL NOT NULL,
    failed_call           INTEGER NOT NULL,
    cache_hit             INTEGER NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE (run_id, sequence)
)
"""
CREATE_RUN_COST_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {RUN_COST_TABLE} (
    run_id                   TEXT PRIMARY KEY,
    outcome                  TEXT NOT NULL,
    internal_ai_cost_krw     REAL NOT NULL,
    customer_charge_krw      REAL NOT NULL DEFAULT 0,
    charge_eligible          INTEGER NOT NULL DEFAULT 0,
    automatic_release_sha256 TEXT NOT NULL DEFAULT '',
    charge_reason            TEXT NOT NULL,
    updated_at               TEXT NOT NULL
)
"""
CREATE_SERVER_COST_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {SERVER_COST_TABLE} (
    month       TEXT PRIMARY KEY,
    amount_krw  REAL NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class AiCostEvent:
    stage: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    batch_applied: bool = False
    cost_krw: float = 0.0
    failed_call: bool = False
    cache_hit: bool = False


@dataclass(frozen=True)
class CustomerChargeDecision:
    eligible: bool
    amount_krw: float
    reason: str


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _validate_event(event: AiCostEvent) -> None:
    if not event.stage.strip() or not event.model_id.strip():
        raise ValueError("비용 이벤트의 stage와 실제 model ID가 필요합니다")
    counts = (
        event.input_tokens,
        event.output_tokens,
        event.cache_creation_tokens,
        event.cache_read_tokens,
    )
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError("비용 이벤트 token 수는 0 이상의 정수여야 합니다")
    if type(event.batch_applied) is not bool or type(event.failed_call) is not bool:
        raise ValueError("비용 이벤트 상태값은 bool이어야 합니다")
    if type(event.cache_hit) is not bool:
        raise ValueError("cache hit 상태는 bool이어야 합니다")
    if event.cost_krw < 0:
        raise ValueError("내부 AI 원가는 음수일 수 없습니다")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_AI_EVENT_SQL)
    conn.execute(CREATE_RUN_COST_SQL)
    conn.execute(CREATE_SERVER_COST_SQL)


def decide_customer_charge(
    *,
    outcome: Outcome,
    automatic_release_sha256: str = "",
    configured_price_krw: float | None = None,
) -> CustomerChargeDecision:
    """Charge only an automatically released complete report.

    No public price is configured yet.  Therefore a valid release is marked
    eligible but remains 0 won until a separately approved price is supplied.
    """

    if outcome is not Outcome.REPORT or not is_valid_sha256(
        automatic_release_sha256
    ):
        return CustomerChargeDecision(False, 0.0, "not_automatically_released")
    if configured_price_krw is None:
        return CustomerChargeDecision(True, 0.0, "price_not_configured")
    if configured_price_krw < 0:
        raise ValueError("고객 청구액은 음수일 수 없습니다")
    return CustomerChargeDecision(True, float(configured_price_krw), "released")


def _event_id(run_id: str, sequence: int, event: AiCostEvent) -> str:
    payload = json.dumps(
        {"run_id": run_id, "sequence": sequence, "event": asdict(event)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_run_costs(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    outcome: Outcome,
    internal_ai_cost_krw: float,
    events: Iterable[AiCostEvent] = (),
) -> None:
    """Persist real provider cost even when generation or the gate failed."""

    clean_run_id = run_id.strip()
    if not clean_run_id or internal_ai_cost_krw < 0:
        raise ValueError("run ID와 0원 이상의 내부 AI 원가가 필요합니다")
    event_values = tuple(events)
    for event in event_values:
        _validate_event(event)
    ensure_schema(conn)
    created_at = _now()
    for sequence, event in enumerate(event_values, start=1):
        conn.execute(
            f"""
            INSERT INTO {AI_EVENT_TABLE} (
                event_id, run_id, sequence, stage, model_id,
                input_tokens, output_tokens, cache_creation_tokens,
                cache_read_tokens, batch_applied, cost_krw, failed_call,
                cache_hit, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                _event_id(clean_run_id, sequence, event),
                clean_run_id,
                sequence,
                event.stage,
                event.model_id,
                event.input_tokens,
                event.output_tokens,
                event.cache_creation_tokens,
                event.cache_read_tokens,
                int(event.batch_applied),
                event.cost_krw,
                int(event.failed_call),
                int(event.cache_hit),
                created_at,
            ),
        )
    decision = decide_customer_charge(outcome=outcome)
    conn.execute(
        f"""
        INSERT INTO {RUN_COST_TABLE} (
            run_id, outcome, internal_ai_cost_krw, customer_charge_krw,
            charge_eligible, automatic_release_sha256, charge_reason, updated_at
        ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            outcome=excluded.outcome,
            internal_ai_cost_krw=excluded.internal_ai_cost_krw,
            customer_charge_krw=CASE
                WHEN {RUN_COST_TABLE}.automatic_release_sha256='' THEN 0
                ELSE {RUN_COST_TABLE}.customer_charge_krw END,
            charge_eligible=CASE
                WHEN {RUN_COST_TABLE}.automatic_release_sha256='' THEN 0
                ELSE {RUN_COST_TABLE}.charge_eligible END,
            charge_reason=CASE
                WHEN {RUN_COST_TABLE}.automatic_release_sha256='' THEN excluded.charge_reason
                ELSE {RUN_COST_TABLE}.charge_reason END,
            updated_at=excluded.updated_at
        """,
        (
            clean_run_id,
            outcome.value,
            float(internal_ai_cost_krw),
            decision.amount_krw,
            int(decision.eligible),
            decision.reason,
            created_at,
        ),
    )


def mark_automatic_release(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    automatic_release_sha256: str,
    configured_price_krw: float | None = None,
) -> CustomerChargeDecision:
    """Attach the shared auto-release object and decide customer billing."""

    clean_run_id = run_id.strip()
    if not clean_run_id or not is_valid_sha256(automatic_release_sha256):
        raise ValueError("run ID와 자동출고 지문이 필요합니다")
    ensure_schema(conn)
    decision = decide_customer_charge(
        outcome=Outcome.REPORT,
        automatic_release_sha256=automatic_release_sha256,
        configured_price_krw=configured_price_krw,
    )
    now = _now()
    conn.execute(
        f"""
        INSERT INTO {RUN_COST_TABLE} (
            run_id, outcome, internal_ai_cost_krw, customer_charge_krw,
            charge_eligible, automatic_release_sha256, charge_reason, updated_at
        ) VALUES (?, ?, 0, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            customer_charge_krw=excluded.customer_charge_krw,
            charge_eligible=excluded.charge_eligible,
            automatic_release_sha256=excluded.automatic_release_sha256,
            charge_reason=excluded.charge_reason,
            updated_at=excluded.updated_at
        """,
        (
            clean_run_id,
            Outcome.REPORT.value,
            decision.amount_krw,
            int(decision.eligible),
            automatic_release_sha256,
            decision.reason,
            now,
        ),
    )
    return decision


def record_monthly_server_cost(
    conn: sqlite3.Connection,
    *,
    month: str,
    amount_krw: float,
    note: str = "",
) -> None:
    """Record fixed server cost separately from report AI variable cost."""

    if len(month) != 7 or month[4] != "-" or amount_krw < 0:
        raise ValueError("월은 YYYY-MM, 서버 비용은 0원 이상이어야 합니다")
    ensure_schema(conn)
    conn.execute(
        f"""
        INSERT INTO {SERVER_COST_TABLE} (month, amount_krw, note, recorded_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(month) DO UPDATE SET
            amount_krw=excluded.amount_krw,
            note=excluded.note,
            recorded_at=excluded.recorded_at
        """,
        (month, float(amount_krw), note.strip(), _now()),
    )
