"""운영 원장의 이관 보존 부채와 실제 ACTIVE 작업을 읽기 전용으로 구분한다."""
from datetime import date
import hashlib
import sqlite3

import pytest

from ops import release_readiness as readiness
from ops.tests.test_release_readiness import _create_database


DAY = date(2026, 8, 28)
STARTED_AT = "2026-08-28T13:54:42+09:00"
MIGRATED_AT = "2026-08-29T09:00:00+09:00"


def _database(tmp_path, *, migrated=True, reserved=900.0, legacy=True):
    readiness._load_storage_db_module()
    from src.features.budget import spend_store, state_machine
    from src.features.budget.constants import SPEND_PHASE_PIPELINE

    path = _create_database(tmp_path / "budget.db")
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE budget_spend_inflight")
        conn.execute("DROP TABLE budget_spend_events")
        spend_store.ensure_schema(conn)
        if legacy:
            spend_store.begin_inflight(
                conn, run_id="old-run", phase=SPEND_PHASE_PIPELINE, day=DAY,
                bucket="link:old", started_at=STARTED_AT, requested_cost_krw=reserved,
            )
        if migrated:
            state_machine.prepare_cutover(conn, migrated_at=MIGRATED_AT)
    return path


def _preflight(path):
    return readiness.preflight(
        path, path.parent, require_maintenance=True,
        min_free_bytes=0, max_disk_used_percent=100,
    )


def _active(conn):
    from src.features.budget import state_machine
    from src.features.budget.constants import SPEND_PHASE_PIPELINE

    state_machine.begin_phase(
        conn, run_id="current-run", phase=SPEND_PHASE_PIPELINE, day=DAY,
        bucket="link:current", reservation_krw=900.0,
        bucket_limit_krw=3_000.0, run_limit_krw=1_200.0,
        lease_owner_id="worker:one", lease_expires_at="2026-08-28T14:30:00+09:00",
        started_at=STARTED_AT,
    )


def test_구형원장과_이관전_schema는_진행예약을_계속_차단한다(tmp_path):
    path = _database(tmp_path, migrated=False)
    result = _preflight(path)
    assert result["status"] == "차단"
    assert result["budget_ledger"]["mode"] == "legacy"
    assert result["counts"]["미정산 비용 예약"] == 1


def test_신원장에만_있는_ACTIVE도_날짜와_lease만료에_관계없이_차단한다(tmp_path):
    path = _database(tmp_path, legacy=False)
    with sqlite3.connect(path) as conn:
        _active(conn)
    result = _preflight(path)
    assert result["status"] == "차단"
    assert result["counts"]["진행 중 비용 단계"] == 1
    assert result["budget_ledger"]["preserved_legacy_inflight"] == 0


def test_정상_이관부채는_진행0_경고이며_원장전체를_보존한다(tmp_path):
    path = _database(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = _preflight(path)
    assert result["status"] == "통과" and not result["blockers"]
    assert result["counts"]["진행 중 비용 단계"] == 0
    assert result["budget_ledger"] == {
        "mode": "attempt-ledger-v1", "active_phases": 0, "preserved_legacy_inflight": 1,
        "liabilities": {"UNKNOWN_LEGACY": {"attempts": 1, "liability_krw": 900.0}},
    }
    assert any("900원" in value for value in result["warnings"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_옛_0원예약도_이관된_보수부채를_0으로_지우지_않는다(tmp_path):
    path = _database(tmp_path, reserved=0)
    result = _preflight(path)
    assert result["status"] == "통과"
    assert result["budget_ledger"]["liabilities"]["UNKNOWN_LEGACY"]["liability_krw"] > 0


def test_보수부채_확인뒤에도_구행은_남고_현재부채만_한번_집계한다(tmp_path):
    path = _database(tmp_path)
    from src.features.budget import state_machine

    with sqlite3.connect(path) as conn:
        item = state_machine.list_reconcilable(conn)[0]
        state_machine.resolve_liability(
            conn, attempt_id=item.attempt_id,
            action=state_machine.ResolutionAction.CONFIRM_CONSERVATIVE_LIABILITY,
            actual_cost_krw=None, actor_id="admin:fixture", reason_code="provider-amount-unavailable",
            resolved_at="2026-08-29T10:00:00+09:00",
        )
        assert conn.execute("SELECT COUNT(*) FROM budget_spend_inflight").fetchone()[0] == 1
    result = _preflight(path)
    assert result["status"] == "통과"
    assert result["budget_ledger"]["liabilities"] == {
        "LIABILITY_CONFIRMED": {"attempts": 1, "liability_krw": 900.0},
    }


def test_신규_보수부채도_진행종료후_금액보존_경고로_남긴다(tmp_path):
    path = _database(tmp_path, legacy=False)
    from src.features.budget import state_machine
    from src.features.budget.constants import SPEND_PHASE_PIPELINE

    with sqlite3.connect(path) as conn:
        _active(conn)
        state_machine.begin_attempt(
            conn, run_id="current-run", phase=SPEND_PHASE_PIPELINE, attempt_id="new-attempt",
            provider="anthropic", operation="writer", estimated_krw=400.0,
            lease_owner_id="worker:one", created_at="2026-08-28T13:54:43+09:00",
        )
        state_machine.mark_dispatch_intent(
            conn, attempt_id="new-attempt", lease_owner_id="worker:one",
            recorded_at="2026-08-28T13:54:44+09:00",
        )
        state_machine.expire_due_phase_leases(conn, observed_at="2026-08-29T10:00:00+09:00")
    result = _preflight(path)
    assert result["status"] == "통과"
    assert result["budget_ledger"]["active_phases"] == 0
    assert result["budget_ledger"]["liabilities"] == {
        "CONSERVATIVE_LIABILITY": {"attempts": 1, "liability_krw": 400.0},
    }


def test_부분확정비용과_별도부채가_같은_phase에_이관되어도_중복합산하지_않는다(tmp_path):
    path = _database(tmp_path, migrated=False)
    from src.features.budget import spend_store, state_machine
    from src.features.budget.constants import SPEND_PHASE_PIPELINE

    with sqlite3.connect(path) as conn:
        spend_store.append_spend(
            conn, run_id="old-run", phase=SPEND_PHASE_PIPELINE, day=DAY,
            bucket="link:old", cost_krw=20.0, created_at=STARTED_AT,
        )
        state_machine.prepare_cutover(conn, migrated_at=MIGRATED_AT)
    result = _preflight(path)
    assert result["status"] == "통과"
    assert result["budget_ledger"]["liabilities"]["UNKNOWN_LEGACY"]["liability_krw"] == 900.0


@pytest.mark.parametrize("damage", ["phase", "amount", "latest", "orphan", "migration", "barrier", "forged_barrier", "table", "summary"])
def test_불완전이관과_결속누락과_불일치는_계속_차단한다(tmp_path, damage):
    path = _database(tmp_path)
    # 공격 fixture만 변조한다. 검사기는 읽기 전용이며 운영 원장은 접근하지 않는다.
    with sqlite3.connect(path) as conn:
        if damage == "phase":
            conn.execute("UPDATE budget_phase_accounts SET bucket_id='different-bucket'")
        elif damage in ("amount", "latest"):
            conn.execute("DROP TRIGGER budget_provider_attempt_events_no_update")
            if damage == "latest":
                conn.execute("INSERT INTO budget_provider_attempt_events "
                             "(attempt_id,event_seq,transport_state,billing_state,reservation_krw,known_cost_krw,liability_krw,actor_id,reason_code,occurred_at) "
                             "SELECT attempt_id,1,transport_state,billing_state,0,0,901,actor_id,reason_code,occurred_at "
                             "FROM budget_provider_attempt_events WHERE event_seq=0")
            else:
                conn.execute("UPDATE budget_provider_attempt_events SET liability_krw=901")
        elif damage == "orphan":
            conn.execute("DROP TRIGGER budget_provider_attempts_no_delete")
            conn.execute("DROP TRIGGER budget_provider_attempt_events_no_delete")
            conn.execute("DELETE FROM budget_provider_attempt_events")
            conn.execute("DELETE FROM budget_provider_attempts")
        elif damage == "migration":
            conn.execute("DROP TRIGGER budget_schema_migrations_no_delete")
            conn.execute("DELETE FROM budget_schema_migrations")
        elif damage == "barrier":
            conn.execute("DROP TRIGGER budget_spend_inflight_delete_after_cutover")
        elif damage == "forged_barrier":
            conn.execute("DROP TRIGGER budget_spend_inflight_delete_after_cutover")
            conn.execute("CREATE TRIGGER budget_spend_inflight_delete_after_cutover "
                         "BEFORE DELETE ON budget_spend_inflight BEGIN SELECT 1; END")
        elif damage == "summary":
            conn.execute("DROP TRIGGER budget_schema_migrations_no_update")
            conn.execute("UPDATE budget_schema_migrations SET legacy_unknown_attempts=0")
        else:
            conn.execute("DROP TABLE budget_provider_attempt_events")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = _preflight(path)
    assert result["status"] == "차단"
    assert result["blockers"]
    assert result["budget_ledger"]["mode"] == "검증 실패"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
