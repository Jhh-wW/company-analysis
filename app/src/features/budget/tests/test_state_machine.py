"""비용 상태기계가 확정액·보수부채·예약을 섞지 않는 계약."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from src.features.budget import spend_store, state_machine
from src.features.budget.constants import (
    SPEND_PHASE_IDENTIFY,
    SPEND_PHASE_PIPELINE,
)


DAY = dt.date(2026, 8, 28)
STARTED_AT = "2026-08-28T09:00:00+09:00"
LEASE_EXPIRES_AT = "2026-08-28T09:05:00+09:00"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    spend_store.ensure_schema(connection)
    state_machine.prepare_cutover(
        connection,
        migrated_at="2026-08-28T08:59:00+09:00",
    )
    yield connection
    connection.close()


def _begin_phase(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    phase: str = SPEND_PHASE_PIPELINE,
    bucket: str = "link:one",
    reservation_krw: float = 900.0,
    owner_id: str = "worker:one",
) -> state_machine.PhaseAccount:
    return state_machine.begin_phase(
        conn,
        run_id=run_id,
        phase=phase,
        day=DAY,
        bucket=bucket,
        reservation_krw=reservation_krw,
        bucket_limit_krw=3_000.0,
        run_limit_krw=1_200.0,
        lease_owner_id=owner_id,
        lease_expires_at=LEASE_EXPIRES_AT,
        started_at=STARTED_AT,
    )


def _known_attempt(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: str,
    actual_krw: float,
) -> None:
    state_machine.begin_attempt(
        conn,
        run_id=run_id,
        phase=SPEND_PHASE_PIPELINE,
        attempt_id=attempt_id,
        provider="anthropic",
        operation="writer",
        estimated_krw=100.0,
        lease_owner_id="worker:one",
        created_at="2026-08-28T09:01:00+09:00",
    )
    state_machine.mark_dispatch_intent(
        conn,
        attempt_id=attempt_id,
        lease_owner_id="worker:one",
        recorded_at="2026-08-28T09:01:01+09:00",
    )
    state_machine.record_attempt_outcome(
        conn,
        attempt_id=attempt_id,
        transport_state=state_machine.TransportState.RESPONSE_RECEIVED,
        billing_state=state_machine.BillingState.KNOWN_COST,
        known_cost_krw=actual_krw,
        liability_krw=0.0,
        close_phase=False,
        phase_succeeded=False,
        recorded_at="2026-08-28T09:01:02+09:00",
        lease_owner_id="worker:one",
    )


def _ambiguous_attempt(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    attempt_id: str,
    estimate_krw: float,
) -> None:
    state_machine.begin_attempt(
        conn,
        run_id=run_id,
        phase=SPEND_PHASE_PIPELINE,
        attempt_id=attempt_id,
        provider="anthropic",
        operation="reviewer",
        estimated_krw=estimate_krw,
        lease_owner_id="worker:one",
        created_at="2026-08-28T09:02:00+09:00",
    )
    state_machine.mark_dispatch_intent(
        conn,
        attempt_id=attempt_id,
        lease_owner_id="worker:one",
        recorded_at="2026-08-28T09:02:01+09:00",
    )
    state_machine.record_attempt_outcome(
        conn,
        attempt_id=attempt_id,
        transport_state=state_machine.TransportState.TRANSPORT_AMBIGUOUS,
        billing_state=state_machine.BillingState.CONSERVATIVE_LIABILITY,
        known_cost_krw=0.0,
        liability_krw=estimate_krw,
        close_phase=True,
        phase_succeeded=False,
        error_type="APITimeoutError",
        recorded_at="2026-08-28T09:02:02+09:00",
        lease_owner_id="worker:one",
    )


def test_ACTIVE_phase의_확정비용과_남은예약을_이중계상하지않는다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(
        conn,
        run_id="active-known",
        reservation_krw=100.0,
    )
    _known_attempt(
        conn,
        run_id="active-known",
        attempt_id="active-known-first",
        actual_krw=20.0,
    )

    exposure = state_machine.load_run_exposure(conn, run_id="active-known")
    assert exposure.known_cost_krw == 20.0
    assert exposure.liability_krw == 0.0
    assert exposure.reservation_krw == 80.0
    assert exposure.admission_exposure_krw == 100.0

    with pytest.raises(state_machine.AdmissionLimitExceeded, match="예약 잔액"):
        state_machine.begin_attempt(
            conn,
            run_id="active-known",
            phase=SPEND_PHASE_PIPELINE,
            attempt_id="active-known-over-budget",
            provider="anthropic",
            operation="reviewer",
            estimated_krw=81.0,
            lease_owner_id="worker:one",
            created_at="2026-08-28T09:03:00+09:00",
        )


def test_부분확정과_모르는비용은_서로다른_attempt로_보존한다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="partial-unknown")
    _known_attempt(
        conn,
        run_id="partial-unknown",
        attempt_id="attempt-known",
        actual_krw=20.0,
    )
    _ambiguous_attempt(
        conn,
        run_id="partial-unknown",
        attempt_id="attempt-unknown",
        estimate_krw=100.0,
    )

    exposure = state_machine.load_exposure(
        conn,
        day=DAY,
        bucket_id=spend_store.bucket_id("link:one"),
    )
    assert exposure.known_cost_krw == 20.0
    assert exposure.liability_krw == 100.0
    assert exposure.reservation_krw == 0.0
    assert exposure.admission_exposure_krw == 120.0
    assert state_machine.load_run_exposure(
        conn, run_id="partial-unknown"
    ) == state_machine.ExposureSnapshot(
        known_cost_krw=20.0,
        liability_krw=100.0,
    )
    reconciliation = state_machine.list_reconcilable(conn)
    assert tuple(item.attempt_id for item in reconciliation) == ("attempt-unknown",)
    assert reconciliation[0].status_code is None
    assert reconciliation[0].error_type == "APITimeoutError"
    assert reconciliation[0].request_id == ""
    assert tuple(
        item.attempt_id
        for item in state_machine.list_attempts(
            conn,
            run_id="partial-unknown",
            phase=SPEND_PHASE_PIPELINE,
        )
    ) == ("attempt-known", "attempt-unknown")

    with pytest.raises(ValueError, match="요청 번호"):
        state_machine.load_run_exposure(conn, run_id="   ")


def test_startup_seed는_날짜전체와_bucket별_세금액을_공개API로_읽는다(
    conn: sqlite3.Connection,
) -> None:
    assert state_machine.cutover_applied(conn) is True
    _begin_phase(
        conn,
        run_id="bucket-a-active",
        bucket="link:a",
        reservation_krw=100.0,
    )
    _begin_phase(
        conn,
        run_id="bucket-b-active",
        bucket="link:b",
        reservation_krw=200.0,
    )

    snapshot = state_machine.load_day_exposures(conn, day=DAY)

    assert snapshot.total.known_cost_krw == 0.0
    assert snapshot.total.liability_krw == 0.0
    assert snapshot.total.reservation_krw == 300.0
    assert snapshot.total.active_phases == 2
    assert snapshot.by_bucket == {
        spend_store.bucket_id("link:a"): state_machine.ExposureSnapshot(
            reservation_krw=100.0,
            active_phases=1,
        ),
        spend_store.bucket_id("link:b"): state_machine.ExposureSnapshot(
            reservation_krw=200.0,
            active_phases=1,
        ),
    }


def test_run_phase목록은_삽입순서가_아니라_시작과_갱신시각순이다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="phase-history")
    state_machine.begin_phase(
        conn,
        run_id="phase-history",
        phase=SPEND_PHASE_IDENTIFY,
        day=DAY,
        bucket="link:one",
        reservation_krw=100.0,
        bucket_limit_krw=3_000.0,
        run_limit_krw=1_200.0,
        lease_owner_id="worker:identify",
        lease_expires_at="2026-08-28T09:04:00+09:00",
        started_at="2026-08-28T08:58:00+09:00",
    )

    phases = state_machine.list_phases(conn, run_id="phase-history")

    assert tuple(item.phase for item in phases) == (
        SPEND_PHASE_IDENTIFY,
        SPEND_PHASE_PIPELINE,
    )
    assert tuple(item.started_at for item in phases) == tuple(
        sorted(item.started_at for item in phases)
    )
    with pytest.raises(ValueError, match="요청 번호"):
        state_machine.list_phases(conn, run_id="")


def test_None_입장기준은_거대숫자로_날조하지않고_검사만_생략한다(
    conn: sqlite3.Connection,
) -> None:
    state_machine.begin_phase(
        conn,
        run_id="unlimited-member",
        phase=SPEND_PHASE_PIPELINE,
        day=DAY,
        bucket="member:one",
        reservation_krw=900.0,
        bucket_limit_krw=None,
        run_limit_krw=None,
        lease_owner_id="worker:one",
        lease_expires_at=LEASE_EXPIRES_AT,
        started_at=STARTED_AT,
    )
    state_machine.begin_attempt(
        conn,
        run_id="unlimited-member",
        phase=SPEND_PHASE_PIPELINE,
        attempt_id="unlimited-unknown",
        provider="anthropic",
        operation="writer",
        estimated_krw=100.0,
        lease_owner_id="worker:one",
        created_at="2026-08-28T09:01:00+09:00",
    )
    state_machine.mark_dispatch_intent(
        conn,
        attempt_id="unlimited-unknown",
        lease_owner_id="worker:one",
        recorded_at="2026-08-28T09:01:01+09:00",
    )
    state_machine.record_attempt_outcome(
        conn,
        attempt_id="unlimited-unknown",
        transport_state=state_machine.TransportState.TRANSPORT_AMBIGUOUS,
        billing_state=state_machine.BillingState.CONSERVATIVE_LIABILITY,
        known_cost_krw=0.0,
        liability_krw=100.0,
        close_phase=True,
        phase_succeeded=False,
        recorded_at="2026-08-28T09:01:02+09:00",
        lease_owner_id="worker:one",
    )

    exposure = state_machine.load_exposure(
        conn,
        day=DAY,
        bucket_id=spend_store.bucket_id("member:one"),
    )
    assert exposure.known_cost_krw == 0.0
    assert exposure.liability_krw == 100.0
    assert exposure.reservation_krw == 0.0


def test_관리자는_DB에서_ACTIVE인_단계를_기억집합없이_정산할수없다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="active-run")
    state_machine.begin_attempt(
        conn,
        run_id="active-run",
        phase=SPEND_PHASE_PIPELINE,
        attempt_id="active-attempt",
        provider="anthropic",
        operation="writer",
        estimated_krw=100.0,
        lease_owner_id="worker:one",
        created_at="2026-08-28T09:01:00+09:00",
    )

    with pytest.raises(state_machine.ActivePhaseError, match="진행 중"):
        state_machine.resolve_liability(
            conn,
            attempt_id="active-attempt",
            action=state_machine.ResolutionAction.CONFIRM_ZERO,
            actual_cost_krw=None,
            actor_id="admin:one",
            reason_code="provider-console-checked",
            resolved_at="2026-08-28T09:03:00+09:00",
        )


def test_한_부채를_확인해도_관계없는_ACTIVE_lease는_그대로다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="orphan-a", reservation_krw=200.0)
    _ambiguous_attempt(
        conn,
        run_id="orphan-a",
        attempt_id="unknown-a",
        estimate_krw=100.0,
    )
    active_before = _begin_phase(
        conn,
        run_id="active-b",
        phase=SPEND_PHASE_IDENTIFY,
        reservation_krw=100.0,
        owner_id="worker:b",
    )

    state_machine.resolve_liability(
        conn,
        attempt_id="unknown-a",
        action=state_machine.ResolutionAction.CONFIRM_ZERO,
        actual_cost_krw=None,
        actor_id="admin:one",
        reason_code="provider-console-zero",
        resolved_at="2026-08-28T09:03:00+09:00",
    )

    active_after = state_machine.get_phase(
        conn,
        run_id="active-b",
        phase=SPEND_PHASE_IDENTIFY,
    )
    assert active_after == active_before
    assert active_after.state is state_machine.PhaseState.ACTIVE


def test_보수부채확인은_확정비용을_부풀려_재대사를_깨지않는다() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        spend_store.ensure_schema(connection)
        spend_store.append_spend(
            connection,
            run_id="legacy-partial",
            phase=SPEND_PHASE_PIPELINE,
            day=DAY,
            bucket="link:legacy",
            cost_krw=20.0,
            created_at="2026-08-28T08:00:00+09:00",
        )
        connection.execute(
            f"""
            INSERT INTO {spend_store.TABLE_SPEND_INFLIGHT}
                (run_id, phase, day, bucket_id, reserved_krw, started_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-partial",
                SPEND_PHASE_PIPELINE,
                DAY.isoformat(),
                spend_store.bucket_id("link:legacy"),
                880.0,
                "2026-08-28T08:01:00+09:00",
            ),
        )
        state_machine.prepare_cutover(
            connection,
            migrated_at="2026-08-28T09:00:00+09:00",
        )
        item = state_machine.list_reconcilable(connection)[0]

        state_machine.resolve_liability(
            connection,
            attempt_id=item.attempt_id,
            action=state_machine.ResolutionAction.CONFIRM_CONSERVATIVE_LIABILITY,
            actual_cost_krw=None,
            actor_id="admin:one",
            reason_code="provider-amount-unavailable",
            resolved_at="2026-08-28T09:03:00+09:00",
        )

        old_known = spend_store.load_run_history(
            connection, {"legacy-partial"}
        ).by_run["legacy-partial"]
        exposure = state_machine.load_exposure(
            connection,
            day=DAY,
            bucket_id=spend_store.bucket_id("link:legacy"),
        )
        assert old_known == 20.0
        assert exposure.known_cost_krw == 20.0
        assert exposure.liability_krw == 880.0
        assert state_machine.list_reconcilable(connection) == ()
    finally:
        connection.close()


def test_lease_만료는_dispatch_intent만_보수부채로_바꾼다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="expired", reservation_krw=100.0)
    state_machine.begin_attempt(
        conn,
        run_id="expired",
        phase=SPEND_PHASE_PIPELINE,
        attempt_id="expired-attempt",
        provider="anthropic",
        operation="writer",
        estimated_krw=80.0,
        lease_owner_id="worker:one",
        created_at="2026-08-28T09:01:00+09:00",
    )
    state_machine.mark_dispatch_intent(
        conn,
        attempt_id="expired-attempt",
        lease_owner_id="worker:one",
        recorded_at="2026-08-28T09:01:01+09:00",
    )

    expired = state_machine.expire_phase_lease(
        conn,
        run_id="expired",
        phase=SPEND_PHASE_PIPELINE,
        observed_at="2026-08-28T09:05:01+09:00",
    )

    assert expired.state is state_machine.PhaseState.FAILED
    exposure = state_machine.load_exposure(
        conn,
        day=DAY,
        bucket_id=spend_store.bucket_id("link:one"),
    )
    assert exposure.liability_krw == 80.0
    assert exposure.reservation_krw == 0.0


def test_lease는_만료시각과_같아진_순간부터_한번에_회수한다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="expired-at-boundary", reservation_krw=100.0)

    expired = state_machine.expire_due_phase_leases(
        conn,
        observed_at="2026-08-28T09:05:00+09:00",
    )

    assert len(expired) == 1
    assert expired[0].state is state_machine.PhaseState.FAILED
    exposure = state_machine.load_exposure(
        conn,
        day=DAY,
        bucket_id=spend_store.bucket_id("link:one"),
    )
    assert exposure.reservation_krw == 0.0
    assert exposure.liability_krw == 0.0


def test_heartbeat는_DB_lease_소유자만_연장한다(conn: sqlite3.Connection) -> None:
    _begin_phase(conn, run_id="heartbeat")

    with pytest.raises(state_machine.LeaseOwnershipError):
        state_machine.heartbeat_phase(
            conn,
            run_id="heartbeat",
            phase=SPEND_PHASE_PIPELINE,
            lease_owner_id="worker:other",
            lease_expires_at="2026-08-28T09:10:00+09:00",
            heartbeat_at="2026-08-28T09:04:00+09:00",
        )

    renewed = state_machine.heartbeat_phase(
        conn,
        run_id="heartbeat",
        phase=SPEND_PHASE_PIPELINE,
        lease_owner_id="worker:one",
        lease_expires_at="2026-08-28T09:10:00+09:00",
        heartbeat_at="2026-08-28T09:04:00+09:00",
    )
    assert renewed.lease_expires_at == "2026-08-28T09:10:00+09:00"
    assert state_machine.list_active_phases(
        conn,
        expired_at_or_before="2026-08-28T09:09:59+09:00",
    ) == ()
    assert state_machine.list_active_phases(
        conn,
        expired_at_or_before="2026-08-28T09:10:00+09:00",
    ) == (renewed,)


def test_heartbeat는_만료시각에_도달한_lease를_되살리지_못한다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="heartbeat-at-expiry")

    with pytest.raises(state_machine.LeaseOwnershipError, match="이미 만료"):
        state_machine.heartbeat_phase(
            conn,
            run_id="heartbeat-at-expiry",
            phase=SPEND_PHASE_PIPELINE,
            lease_owner_id="worker:one",
            lease_expires_at="2026-08-28T09:10:00+09:00",
            heartbeat_at="2026-08-28T09:05:00+09:00",
        )


def test_provider에_보내기전_로컬실패만_known_zero로_닫는다(
    conn: sqlite3.Connection,
) -> None:
    _begin_phase(conn, run_id="local-validation", reservation_krw=100.0)
    state_machine.begin_attempt(
        conn,
        run_id="local-validation",
        phase=SPEND_PHASE_PIPELINE,
        attempt_id="local-attempt",
        provider="anthropic",
        operation="request-build",
        estimated_krw=80.0,
        lease_owner_id="worker:one",
        created_at="2026-08-28T09:01:00+09:00",
    )

    attempt = state_machine.record_pre_dispatch_failure(
        conn,
        attempt_id="local-attempt",
        lease_owner_id="worker:one",
        error_type="LocalSchemaError",
        close_phase=True,
        recorded_at="2026-08-28T09:01:01+09:00",
    )

    assert attempt.transport_state is state_machine.TransportState.LOCAL_FAILURE
    assert attempt.billing_state is state_machine.BillingState.KNOWN_ZERO
    assert state_machine.get_phase(
        conn,
        run_id="local-validation",
        phase=SPEND_PHASE_PIPELINE,
    ).state is state_machine.PhaseState.FAILED
    assert state_machine.list_reconcilable(conn) == ()


def test_cutover_dry_run과_rollback은_legacy_DB를_바꾸지않는다() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        # 새 schema bootstrap을 부르지 않고 실제 구판의 세 표만 만든다.
        connection.execute(spend_store.CREATE_SQL)
        connection.execute(
            f"""
            CREATE TABLE {spend_store.TABLE_SPEND_INFLIGHT} (
                run_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                day TEXT NOT NULL,
                bucket_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY (run_id, phase)
            )
            """
        )
        connection.execute(spend_store.CREATE_OVERRUN_SQL)
        connection.execute(
            f"""
            INSERT INTO {spend_store.TABLE_SPEND_INFLIGHT}
                (run_id, phase, day, bucket_id, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "legacy-zero-reservation",
                SPEND_PHASE_IDENTIFY,
                DAY.isoformat(),
                spend_store.bucket_id("link:old"),
                STARTED_AT,
            ),
        )
        connection.commit()

        summary = state_machine.prepare_cutover(
            connection,
            migrated_at="2026-08-28T09:00:00+09:00",
            dry_run=True,
        )
        assert summary.legacy_phases == 1
        assert summary.legacy_unknown_attempts == 1
        names_after_dry_run = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert spend_store.TABLE_BUDGET_PHASES not in names_after_dry_run

        connection.execute("BEGIN")
        state_machine.prepare_cutover(
            connection,
            migrated_at="2026-08-28T09:00:00+09:00",
        )
        migrated = state_machine.load_exposure(
            connection,
            day=DAY,
            bucket_id=spend_store.bucket_id("link:old"),
        )
        # 구판 0원은 0원으로 날조하지 않고 그 phase의 승인된 예약 기준을 부채로 둔다.
        assert migrated.liability_krw == 100.0
        connection.rollback()

        names_after_rollback = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert spend_store.TABLE_BUDGET_PHASES not in names_after_rollback
        assert connection.execute(
            f"SELECT COUNT(*) FROM {spend_store.TABLE_SPEND_INFLIGHT}"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_cutover는_JSONL에만_있던_확정차액을_조용히_버리지않는다() -> None:
    connection = sqlite3.connect(":memory:")
    spend_store.ensure_schema(connection)
    assert spend_store.append_spend(
        connection,
        run_id="legacy-observed",
        phase=SPEND_PHASE_PIPELINE,
        day=DAY,
        bucket="link:one",
        cost_krw=20.0,
        created_at=STARTED_AT,
    )

    dry = state_machine.prepare_cutover(
        connection,
        migrated_at="2026-08-28T09:10:00+09:00",
        dry_run=True,
        observed_costs_by_run={"legacy-observed": 40.0},
    )
    assert dry.legacy_known_attempts == 2
    state_machine.prepare_cutover(
        connection,
        migrated_at="2026-08-28T09:10:00+09:00",
        observed_costs_by_run={"legacy-observed": 40.0},
    )

    exposure = state_machine.load_run_exposure(
        connection, run_id="legacy-observed"
    )
    attempts = state_machine.list_attempts(
        connection,
        run_id="legacy-observed",
        phase=SPEND_PHASE_PIPELINE,
    )
    assert exposure.known_cost_krw == 40.0
    assert [attempt.operation for attempt in attempts] == [
        "known-spend",
        "observation-adjustment",
    ]
    connection.close()


def test_cutover는_옛관측이_원장보다_작아도_서버를_못뜨게_하지_않는다(caplog) -> None:
    """★ 운영 사고 — 이 검사가 서버 기동을 통째로 막았다.

    Render 배포가 `Exited with status 3` 로 죽었고, 원인은
    「legacy DB 확정 비용이 관측 최종 비용보다 큽니다」였다.

    ★ 왜 중단이 틀렸나
      원장(구 DB)이 옛 관측값보다 «크다»는 것은 우리가 이미 더 많이 세어 뒀다는 뜻이다 —
      돈이 «빠진» 게 아니라 오히려 보수적인 쪽이다. 게다가 옛 JSONL 은 손상 이력이
      문서에 남아 있어(`app/docs/출시전_수정_지시서.md` 「관측 정본 정정」)
      덜 적혀 있는 것이 정상이다.

    ⚠️ 그래도 «조용히» 넘기지는 않는다 — 개수를 로그에 남긴다.
    """
    import logging

    connection = sqlite3.connect(":memory:")
    spend_store.ensure_schema(connection)
    assert spend_store.append_spend(
        connection,
        run_id="legacy-bigger",
        phase=SPEND_PHASE_PIPELINE,
        day=DAY,
        bucket="link:one",
        cost_krw=40.0,
        created_at=STARTED_AT,
    )

    with caplog.at_level(logging.WARNING, logger=state_machine.__name__):
        # 옛 관측값(20원)이 확정 원장(40원)보다 «작다» — 예전엔 여기서 죽었다.
        dry = state_machine.prepare_cutover(
            connection,
            migrated_at="2026-08-28T09:10:00+09:00",
            dry_run=True,
            observed_costs_by_run={"legacy-bigger": 20.0},
        )

    # ★ 예외 없이 «끝까지» 와야 한다. 여기서 죽으면 서버가 못 뜬다.
    assert dry.legacy_known_attempts == 1, "차액을 지어내면 안 된다"
    assert any(
        "보정하지 않은 요청" in 기록.getMessage() for 기록 in caplog.records
    ), "★ 조용히 넘기면 안 된다 — 개수를 남겨야 한다"

    # 실제 실행도 막히지 않는다.
    state_machine.prepare_cutover(
        connection,
        migrated_at="2026-08-28T09:10:00+09:00",
        observed_costs_by_run={"legacy-bigger": 20.0},
    )
    exposure = state_machine.load_run_exposure(connection, run_id="legacy-bigger")
    # 원장 값이 그대로 남는다 — 관측이 작다고 «깎지» 않는다.
    assert exposure.known_cost_krw == 40.0
    connection.close()


def test_cutover는_통장을_모르는_JSONL비용을_임의통장에_넣지않는다() -> None:
    connection = sqlite3.connect(":memory:")
    spend_store.ensure_schema(connection)
    with pytest.raises(
        state_machine.BudgetStateError,
        match="통장을 legacy DB에서 찾을 수 없습니다",
    ):
        state_machine.prepare_cutover(
            connection,
            migrated_at="2026-08-28T09:10:00+09:00",
            dry_run=True,
            observed_costs_by_run={"orphan-observation": 40.0},
        )
    connection.close()


def test_cutover뒤_구판원장_write는_split_brain전에_거절한다(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="disabled after cutover"):
        spend_store.append_spend(
            conn,
            run_id="late-legacy-write",
            phase=SPEND_PHASE_IDENTIFY,
            day=DAY,
            bucket="link:one",
            cost_krw=10.0,
            created_at="2026-08-28T09:04:00+09:00",
        )


@pytest.mark.parametrize(
    ("action", "actual", "expected_billing", "expected_known", "expected_liability"),
    (
        (
            state_machine.ResolutionAction.CONFIRM_ACTUAL,
            12.0,
            state_machine.BillingState.KNOWN_COST,
            12.0,
            0.0,
        ),
        (
            state_machine.ResolutionAction.CONFIRM_ZERO,
            None,
            state_machine.BillingState.KNOWN_ZERO,
            0.0,
            0.0,
        ),
    ),
)
def test_관리자_실제비용과_known_zero는_서로다른_감사사건이다(
    conn: sqlite3.Connection,
    action: state_machine.ResolutionAction,
    actual: float | None,
    expected_billing: state_machine.BillingState,
    expected_known: float,
    expected_liability: float,
) -> None:
    run_id = f"resolution-{action.value.lower()}"
    attempt_id = f"attempt-{action.value.lower()}"
    _begin_phase(conn, run_id=run_id, reservation_krw=100.0)
    _ambiguous_attempt(
        conn,
        run_id=run_id,
        attempt_id=attempt_id,
        estimate_krw=80.0,
    )

    resolved = state_machine.resolve_liability(
        conn,
        attempt_id=attempt_id,
        action=action,
        actual_cost_krw=actual,
        actor_id="admin:cost-reviewer",
        reason_code="provider-console-checked",
        resolved_at="2026-08-28T09:04:00+09:00",
    )

    assert resolved.billing_state is expected_billing
    assert resolved.known_cost_krw == expected_known
    assert resolved.liability_krw == expected_liability
    assert resolved.actor_id == "admin:cost-reviewer"
    assert resolved.reason_code == "provider-console-checked"
    assert resolved.occurred_at == "2026-08-28T09:04:00+09:00"
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            f"UPDATE {spend_store.TABLE_BUDGET_ATTEMPT_EVENTS} "
            "SET reason_code = 'rewritten' WHERE attempt_id = ?",
            (attempt_id,),
        )
