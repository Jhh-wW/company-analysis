"""provider별 유한 차단기와 읽기 순수성을 고정한다."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from src.features.provider_health import constants, store


NOW = "2026-08-28T10:00:00+09:00"


@pytest.fixture
def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    store.ensure_schema(conn)
    yield conn
    conn.close()


def _later(seconds: int) -> str:
    return (
        datetime.fromisoformat(NOW) + timedelta(seconds=seconds)
    ).isoformat()


def _event_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_EVENTS}"
        ).fetchone()[0]
    )


def test_absent_provider_is_healthy_and_gets_are_pure(
    connection: sqlite3.Connection,
) -> None:
    before_changes = connection.total_changes

    snapshot = store.get_state(connection, constants.PROVIDER_ANTHROPIC)
    permission = store.peek_permission(
        connection, constants.PROVIDER_ANTHROPIC, now_iso=NOW
    )
    listed = store.list_states(connection)
    blocked = store.list_blocked(
        connection,
        (
            constants.PROVIDER_ANTHROPIC,
            constants.PROVIDER_GOOGLE_PLACES,
        ),
        now_iso=NOW,
    )

    assert snapshot.state is store.ProviderHealthState.HEALTHY
    assert snapshot.version == 0
    assert permission.allowed is True
    assert permission.is_probe is False
    assert listed == ()
    assert blocked == ()
    assert connection.total_changes == before_changes
    assert _event_count(connection) == 0


def test_two_rate_limits_open_only_that_provider_for_a_finite_cooldown(
    connection: sqlite3.Connection,
) -> None:
    first = store.record_failure(
        connection,
        constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.RATE_LIMIT,
        now_iso=NOW,
    )
    second = store.record_failure(
        connection,
        constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.RATE_LIMIT,
        now_iso=_later(1),
    )

    assert first.state is store.ProviderHealthState.DEGRADED
    assert first.consecutive_failures == 1
    assert second.state is store.ProviderHealthState.OPEN
    assert second.open_until == _later(1 + constants.OPEN_COOLDOWN_SEC)
    assert store.peek_permission(
        connection, constants.PROVIDER_DART, now_iso=_later(2)
    ).allowed is False
    assert store.peek_permission(
        connection, constants.PROVIDER_ANTHROPIC, now_iso=_later(2)
    ).allowed is True


def test_cooldown_read_does_not_mutate_and_only_one_probe_is_acquired(
    connection: sqlite3.Connection,
) -> None:
    store.record_failure(
        connection,
        constants.PROVIDER_GOOGLE_PLACES,
        failure_kind=store.ProviderFailureKind.TIMEOUT,
        now_iso=NOW,
    )
    store.record_failure(
        connection,
        constants.PROVIDER_GOOGLE_PLACES,
        failure_kind=store.ProviderFailureKind.TIMEOUT,
        now_iso=_later(1),
    )
    after_cooldown = _later(1 + constants.OPEN_COOLDOWN_SEC)
    changes_before_get = connection.total_changes
    events_before_get = _event_count(connection)

    preview = store.peek_permission(
        connection, constants.PROVIDER_GOOGLE_PLACES, now_iso=after_cooldown
    )

    assert preview.allowed is True
    assert preview.is_probe is True
    assert store.get_state(
        connection, constants.PROVIDER_GOOGLE_PLACES
    ).state is store.ProviderHealthState.OPEN
    assert connection.total_changes == changes_before_get
    assert _event_count(connection) == events_before_get

    first = store.acquire_probe(
        connection, constants.PROVIDER_GOOGLE_PLACES, now_iso=after_cooldown
    )
    second = store.acquire_probe(
        connection, constants.PROVIDER_GOOGLE_PLACES, now_iso=after_cooldown
    )

    assert first.allowed is True
    assert first.is_probe is True
    assert first.state is store.ProviderHealthState.PROBING
    assert second.allowed is False
    assert second.reason_code == constants.REASON_PROBE_IN_PROGRESS


def test_successful_probe_closes_circuit_and_resets_failure_count(
    connection: sqlite3.Connection,
) -> None:
    store.record_failure(
        connection, constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.PROVIDER_RESPONSE, now_iso=NOW,
    )
    store.record_failure(
        connection, constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.PROVIDER_RESPONSE, now_iso=_later(1),
    )
    store.acquire_probe(
        connection, constants.PROVIDER_DART,
        now_iso=_later(1 + constants.OPEN_COOLDOWN_SEC),
    )

    healthy = store.record_success(
        connection, constants.PROVIDER_DART,
        now_iso=_later(2 + constants.OPEN_COOLDOWN_SEC),
    )

    assert healthy.state is store.ProviderHealthState.HEALTHY
    assert healthy.consecutive_failures == 0
    assert healthy.open_until == ""
    assert healthy.probe_lease_until == ""
    assert store.peek_permission(
        connection, constants.PROVIDER_DART,
        now_iso=_later(3 + constants.OPEN_COOLDOWN_SEC),
    ).allowed is True


def test_failed_probe_reopens_with_a_new_finite_deadline(
    connection: sqlite3.Connection,
) -> None:
    store.record_failure(
        connection, constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.CONNECTION, now_iso=NOW,
    )
    store.record_failure(
        connection, constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.CONNECTION, now_iso=_later(1),
    )
    probe_at = 1 + constants.OPEN_COOLDOWN_SEC
    store.acquire_probe(
        connection, constants.PROVIDER_DART, now_iso=_later(probe_at)
    )

    reopened = store.record_failure(
        connection, constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.CONNECTION,
        now_iso=_later(probe_at + 1),
    )

    assert reopened.state is store.ProviderHealthState.OPEN
    assert reopened.open_until == _later(
        probe_at + 1 + constants.OPEN_COOLDOWN_SEC
    )
    assert reopened.probe_lease_until == ""


def test_expired_probe_lease_can_be_reclaimed_once(
    connection: sqlite3.Connection,
) -> None:
    store.record_failure(
        connection, constants.PROVIDER_ANTHROPIC,
        failure_kind=store.ProviderFailureKind.TIMEOUT, now_iso=NOW,
    )
    store.record_failure(
        connection, constants.PROVIDER_ANTHROPIC,
        failure_kind=store.ProviderFailureKind.TIMEOUT, now_iso=_later(1),
    )
    probe_at = 1 + constants.OPEN_COOLDOWN_SEC
    store.acquire_probe(
        connection, constants.PROVIDER_ANTHROPIC, now_iso=_later(probe_at)
    )
    reclaim_at = probe_at + constants.PROBE_LEASE_SEC

    reclaimed = store.acquire_probe(
        connection, constants.PROVIDER_ANTHROPIC, now_iso=_later(reclaim_at)
    )
    blocked = store.acquire_probe(
        connection, constants.PROVIDER_ANTHROPIC, now_iso=_later(reclaim_at)
    )

    assert reclaimed.allowed is True
    assert reclaimed.is_probe is True
    assert blocked.allowed is False


def test_중립관측은_실패횟수를_바꾸지않고_probe만_즉시_반납한다(
    connection: sqlite3.Connection,
) -> None:
    store.record_failure(
        connection, constants.PROVIDER_ANTHROPIC,
        failure_kind=store.ProviderFailureKind.TIMEOUT, now_iso=NOW,
    )
    store.record_failure(
        connection, constants.PROVIDER_ANTHROPIC,
        failure_kind=store.ProviderFailureKind.TIMEOUT, now_iso=_later(1),
    )
    probe_at = 1 + constants.OPEN_COOLDOWN_SEC
    store.acquire_probe(
        connection, constants.PROVIDER_ANTHROPIC, now_iso=_later(probe_at)
    )

    released = store.release_probe_without_health_signal(
        connection,
        constants.PROVIDER_ANTHROPIC,
        now_iso=_later(probe_at + 1),
    )
    permission = store.peek_permission(
        connection,
        constants.PROVIDER_ANTHROPIC,
        now_iso=_later(probe_at + 1),
    )

    assert released.state is store.ProviderHealthState.OPEN
    assert released.consecutive_failures == 2
    assert released.probe_lease_until == ""
    assert permission.allowed is True
    assert permission.is_probe is True


def test_옛_event_check를_자료손실없이_neutral지원으로_전진이관한다() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        legacy_sql = store._CREATE_STATEMENTS[1].replace(
            ", 'neutral'", ""
        )
        conn.execute(legacy_sql)
        conn.execute(
            f"""INSERT INTO {store.TABLE_EVENTS} (
                provider, event_kind, previous_state, next_state, failure_kind,
                consecutive_failures, open_until, probe_lease_until, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                constants.PROVIDER_DART,
                "failure",
                "HEALTHY",
                "DEGRADED",
                "timeout",
                1,
                "",
                "",
                NOW,
            ),
        )

        store.ensure_schema(conn)

        create_sql = str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (store.TABLE_EVENTS,),
            ).fetchone()[0]
        )
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {store.TABLE_EVENTS}"
            ).fetchone()[0]
        )
        assert "'neutral'" in create_sql
        assert count == 1
    finally:
        conn.close()


def test_events_are_append_only(connection: sqlite3.Connection) -> None:
    store.record_failure(
        connection, constants.PROVIDER_DART,
        failure_kind=store.ProviderFailureKind.RATE_LIMIT, now_iso=NOW,
    )

    with pytest.raises(sqlite3.DatabaseError):
        connection.execute(f"UPDATE {store.TABLE_EVENTS} SET provider = 'other'")
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute(f"DELETE FROM {store.TABLE_EVENTS}")


def test_provider_health_schema_has_no_cost_or_liability_columns(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        str(row[1])
        for table in (store.TABLE_STATES, store.TABLE_EVENTS)
        for row in connection.execute(f"PRAGMA table_info({table})")
    }

    assert "cost_krw" not in columns
    assert "liability_krw" not in columns
    assert "reservation_krw" not in columns


@pytest.mark.parametrize("bad_now", ["", "not-a-time", "2026-08-28T10:00:00"])
def test_write_requires_timezone_aware_time(
    connection: sqlite3.Connection, bad_now: str
) -> None:
    with pytest.raises(ValueError):
        store.record_success(
            connection, constants.PROVIDER_DART, now_iso=bad_now
        )
