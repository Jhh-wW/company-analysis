"""독립 비용 소비자 검토: 모든 변조는 메모리 DB에서만 실행한다.

소비자가 직접 확인해야 할 손상 경계를 보기 위해 검사 제약은 최소화한다.
최초 검토에서 발견한 세 경계도 보강 이후 일반 실패 차단 회귀로 검증한다.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from src.features.pilot_evaluation.runner import CanonicalPilotRunner, PilotRunnerError


RUN_ID = "1" * 32
PHASE = "본조사"
COST = 712.57
CORP_ID = "00254045"


@pytest.fixture
def ledger_connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE budget_schema_migrations (version TEXT PRIMARY KEY);
        CREATE TABLE budget_phase_accounts (
            run_id TEXT, phase TEXT, state TEXT, reservation_krw REAL,
            lease_owner_id TEXT, lease_expires_at TEXT, PRIMARY KEY(run_id, phase));
        CREATE TABLE budget_provider_attempts (
            attempt_id TEXT PRIMARY KEY, run_id TEXT, phase TEXT, attempt_no INTEGER,
            UNIQUE(run_id, phase, attempt_no));
        CREATE TABLE budget_provider_attempt_events (
            attempt_id TEXT, event_seq INTEGER, transport_state TEXT, billing_state TEXT,
            reservation_krw REAL, known_cost_krw REAL, liability_krw REAL,
            UNIQUE(attempt_id, event_seq));
        CREATE TABLE budget_spend_events (run_id TEXT, cost_krw REAL);
        CREATE TABLE budget_spend_inflight (run_id TEXT);
        CREATE TABLE report_cost_summaries (
            run_id TEXT PRIMARY KEY, outcome TEXT, internal_ai_cost_krw REAL,
            automatic_release_sha256 TEXT);
        CREATE TABLE observability_run_lifecycle (
            run_id TEXT PRIMARY KEY, state TEXT, final_record_json TEXT);
        CREATE TABLE reports (report_id TEXT PRIMARY KEY, corp_id TEXT);
    """)
    conn.execute("INSERT INTO budget_schema_migrations VALUES ('attempt-ledger-v1')")
    conn.execute("INSERT INTO budget_phase_accounts VALUES (?, ?, 'SUCCEEDED', 0, NULL, NULL)",
                 (RUN_ID, PHASE))
    conn.execute("INSERT INTO budget_provider_attempts VALUES ('attempt:paid', ?, ?, 0)", (RUN_ID, PHASE))
    for sequence, transport, billing, reservation, known in (
        (0, "PLANNED", "RESERVED", COST, 0),
        (1, "DISPATCH_INTENT_RECORDED", "RESERVED", COST, 0),
        (2, "RESPONSE_RECEIVED", "KNOWN_COST", 0, COST),
    ):
        conn.execute("INSERT INTO budget_provider_attempt_events VALUES (?, ?, ?, ?, ?, ?, 0)",
                     ("attempt:paid", sequence, transport, billing, reservation, known))
    conn.execute("INSERT INTO report_cost_summaries VALUES (?, '보고서', ?, ?)", (RUN_ID, COST, "f" * 64))
    conn.execute("INSERT INTO observability_run_lifecycle VALUES (?, 'final', ?)",
                 (RUN_ID, json.dumps({"run_id": RUN_ID, "cost_krw": COST})))
    conn.execute("INSERT INTO reports VALUES (?, ?)", (RUN_ID, CORP_ID))
    conn.commit()
    yield conn
    conn.close()


def read_ledger(conn):
    conn.commit()
    runner = object.__new__(CanonicalPilotRunner)
    runner._connect_storage = lambda: conn
    return runner._read_ledger(RUN_ID)


def assert_rejected(conn, code="ledger_spend_invalid"):
    with pytest.raises(PilotRunnerError) as caught:
        read_ledger(conn)
    assert caught.value.code == code


def update_summary(conn, cost):
    conn.execute("UPDATE report_cost_summaries SET internal_ai_cost_krw=?", (cost,))
    conn.execute("UPDATE observability_run_lifecycle SET final_record_json=?",
                 (json.dumps({"run_id": RUN_ID, "cost_krw": cost}),))


def add_unsettled(conn, billing="CONSERVATIVE_LIABILITY"):
    conn.execute("INSERT INTO budget_provider_attempts VALUES ('attempt:unknown', ?, ?, 1)", (RUN_ID, PHASE))
    conn.execute("INSERT INTO budget_provider_attempt_events VALUES ('attempt:unknown', 0, ?, ?, ?, 0, ?)",
                 ("PLANNED" if billing == "RESERVED" else "TRANSPORT_AMBIGUOUS", billing,
                  1 if billing == "RESERVED" else 0, 0 if billing == "RESERVED" else 1))


def test_attempt_cost_remains_authoritative_after_migration(ledger_connection):
    conn = ledger_connection
    conn.execute("INSERT INTO budget_spend_events VALUES (?, 999)", (RUN_ID,))
    conn.execute("INSERT INTO budget_spend_inflight VALUES (?)", (RUN_ID,))
    ledger = read_ledger(conn)
    assert ledger.cost_krw == COST and ledger.billing_uncertain is False


@pytest.mark.parametrize("change", [
    "DELETE FROM budget_schema_migrations",
    "DROP TABLE budget_schema_migrations",
    "UPDATE budget_schema_migrations SET version='unsupported-version'",
    "DROP TABLE budget_provider_attempt_events",
    "DROP TABLE budget_phase_accounts",
    "DELETE FROM budget_provider_attempt_events",
    "DELETE FROM budget_phase_accounts",
    "UPDATE budget_provider_attempt_events SET event_seq=-1 WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET transport_state='invalid' WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET billing_state='invalid' WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET known_cost_krw=-1 WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET known_cost_krw=1e999 WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET known_cost_krw='broken' WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET billing_state='KNOWN_ZERO' WHERE event_seq=2",
    "UPDATE budget_provider_attempt_events SET liability_krw=1 WHERE event_seq=2",
    "UPDATE budget_phase_accounts SET reservation_krw=1",
    "UPDATE budget_phase_accounts SET lease_owner_id='orphan-owner'",
])
def test_corrupt_or_missing_required_state_fails_closed(ledger_connection, change):
    ledger_connection.execute(change)
    # 최신 순번 2를 -1로 바꾸면 1번 RESERVED가 정본이 되어 요약 비용과 달라진다.
    if "event_seq=-1" in change:
        assert_rejected(ledger_connection, "ledger_cost_mismatch")
    else:
        assert_rejected(ledger_connection)


@pytest.mark.parametrize("billing", ["RESERVED", "CONSERVATIVE_LIABILITY", "UNKNOWN_LEGACY", "LIABILITY_CONFIRMED"])
def test_unsettled_attempt_is_not_hidden_by_zero_legacy_inflight(ledger_connection, billing):
    add_unsettled(ledger_connection, billing)
    ledger = read_ledger(ledger_connection)
    assert ledger.cost_krw == COST and ledger.billing_uncertain is True


def test_zero_reservation_active_phase_is_still_uncertain(ledger_connection):
    ledger_connection.execute("UPDATE budget_phase_accounts SET state='ACTIVE', lease_owner_id='owner', "
                              "lease_expires_at='2020-01-01T00:00:00Z'")
    assert read_ledger(ledger_connection).billing_uncertain is True


@pytest.mark.parametrize("latest_cost", [0, 600.0])
def test_latest_refund_or_adjustment_replaces_prior_cost(ledger_connection, latest_cost):
    conn = ledger_connection
    conn.execute("INSERT INTO budget_provider_attempt_events VALUES "
                 "('attempt:paid', 3, 'RESPONSE_RECEIVED', ?, 0, ?, 0)",
                 ("KNOWN_ZERO" if latest_cost == 0 else "KNOWN_COST", latest_cost))
    # 정산 변경만 먼저 보이면 오래된 요약과 불일치하므로 차단해야 한다.
    assert_rejected(conn, "ledger_cost_mismatch")
    update_summary(conn, latest_cost)
    ledger = read_ledger(conn)
    assert ledger.cost_krw == latest_cost and ledger.billing_uncertain is False


def test_unknown_settlement_resolved_by_latest_event(ledger_connection):
    conn = ledger_connection
    add_unsettled(conn, "UNKNOWN_LEGACY")
    conn.execute("INSERT INTO budget_provider_attempt_events VALUES "
                 "('attempt:unknown', 1, 'TRANSPORT_AMBIGUOUS', 'KNOWN_ZERO', 0, 0, 0)")
    assert read_ledger(conn).billing_uncertain is False


@pytest.mark.parametrize("table,code", [
    ("budget_provider_attempt_events", "ledger_cost_mismatch"),
    ("observability_run_lifecycle", "ledger_lifecycle_cost_mismatch"),
])
def test_one_cent_mismatch_is_rejected(ledger_connection, table, code):
    if table == "budget_provider_attempt_events":
        ledger_connection.execute("UPDATE budget_provider_attempt_events SET known_cost_krw=712.56 WHERE event_seq=2")
    else:
        ledger_connection.execute("UPDATE observability_run_lifecycle SET final_record_json=?",
                                  (json.dumps({"run_id": RUN_ID, "cost_krw": 712.56}),))
    assert_rejected(ledger_connection, code)


def test_unmigrated_empty_attempt_tables_use_legacy(ledger_connection):
    conn = ledger_connection
    for table in ("budget_schema_migrations", "budget_provider_attempt_events", "budget_provider_attempts",
                  "budget_phase_accounts"):
        conn.execute("DELETE FROM " + table)
    conn.execute("INSERT INTO budget_spend_events VALUES (?, ?)", (RUN_ID, COST))
    assert read_ledger(conn).cost_krw == COST


@pytest.mark.parametrize("table", ["budget_provider_attempts", "budget_provider_attempt_events"])
def test_duplicate_attempt_id_or_latest_event_is_rejected(ledger_connection, table):
    conn = ledger_connection
    conn.execute("CREATE TABLE copy AS SELECT * FROM " + table)
    conn.execute("DROP TABLE " + table)
    conn.execute("ALTER TABLE copy RENAME TO " + table)
    suffix = " WHERE event_seq=2" if table.endswith("events") else ""
    conn.execute("INSERT INTO " + table + " SELECT * FROM " + table + suffix)
    assert_rejected(conn)


def test_orphan_liability_event_must_fail_closed(ledger_connection):
    conn = ledger_connection
    add_unsettled(conn)
    conn.execute("DELETE FROM budget_provider_attempts WHERE attempt_id='attempt:unknown'")
    assert_rejected(conn)


def test_duplicate_logical_attempt_must_fail_closed(ledger_connection):
    conn = ledger_connection
    conn.execute("CREATE TABLE copy AS SELECT * FROM budget_provider_attempts")
    conn.execute("DROP TABLE budget_provider_attempts")
    conn.execute("ALTER TABLE copy RENAME TO budget_provider_attempts")
    conn.execute("INSERT INTO budget_provider_attempts VALUES ('attempt:duplicate', ?, ?, 0)", (RUN_ID, PHASE))
    conn.execute("INSERT INTO budget_provider_attempt_events VALUES "
                 "('attempt:duplicate', 0, 'LOCAL_FAILURE', 'KNOWN_ZERO', 0, 0, 0)")
    assert_rejected(conn)


def test_unsupported_followup_migration_must_fail_closed(ledger_connection):
    ledger_connection.execute("INSERT INTO budget_schema_migrations VALUES ('attempt-ledger-v2')")
    assert_rejected(ledger_connection)
