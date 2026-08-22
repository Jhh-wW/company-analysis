"""관리자 변경 감사 원장의 append-only·비식별 경계."""

from __future__ import annotations

import sqlite3

import pytest

from src.features.observability import admin_audit_store


def _append(connection: sqlite3.Connection, *, actor_id: str = "anonymous") -> None:
    admin_audit_store.append_success(
        connection,
        event_time="2026-08-23T10:00:00+09:00",
        request_id="request-1",
        actor_id=actor_id,
        action="admin.member.invite",
        target_id="member:fixed-target",
        reason_code="invited",
    )


def test_append_is_transactional_and_update_delete_are_blocked() -> None:
    with sqlite3.connect(":memory:") as connection:
        admin_audit_store.ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN")
        _append(connection)
        connection.rollback()
        assert connection.execute(
            "SELECT COUNT(*) FROM admin_audit_events"
        ).fetchone()[0] == 0

        _append(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE admin_audit_events SET outcome='forged' WHERE id=1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM admin_audit_events WHERE id=1")


def test_public_store_rejects_unsanitized_actor_without_persisting_value() -> None:
    with sqlite3.connect(":memory:") as connection:
        admin_audit_store.ensure_schema(connection)
        with pytest.raises(ValueError) as captured:
            _append(connection, actor_id="private@example.invalid")
        count = connection.execute(
            "SELECT COUNT(*) FROM admin_audit_events"
        ).fetchone()[0]

    assert count == 0
    assert "private@example.invalid" not in str(captured.value)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("event_time", "2026-08-23 10:00:00"),
        ("request_id", "request/private"),
        ("actor_id", "a" * 81),
        ("action", "admin member invite"),
        ("target_id", "private@example.invalid"),
        ("outcome", "forged"),
        ("reason_code", "r" * 49),
    ),
)
def test_table_checks_reject_bypassing_public_store(column: str, value: str) -> None:
    with sqlite3.connect(":memory:") as connection:
        admin_audit_store.ensure_schema(connection)
        fields = {
            "event_time": "2026-08-23T10:00:00+09:00",
            "request_id": "request-1",
            "actor_id": "anonymous",
            "action": "admin.member.invite",
            "target_id": "member:fixed-target",
            "outcome": "success",
            "reason_code": "invited",
        }
        fields[column] = value

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO admin_audit_events (
                    event_time, request_id, actor_id, action,
                    target_id, outcome, reason_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(fields.values()),
            )


@pytest.mark.parametrize(
    ("event_time", "actor_id", "outcome"),
    (
        ("2026-08-23T10:00:00", "anonymous", "success"),
        ("2026-08-23T10:00:00+09:00", "private@example.invalid", "success"),
        ("2026-08-23T10:00:00+09:00", "anonymous", "forged"),
    ),
)
def test_persisted_validator_rejects_rows_even_if_checks_were_bypassed(
    event_time: str,
    actor_id: str,
    outcome: str,
) -> None:
    with sqlite3.connect(":memory:") as connection:
        admin_audit_store.ensure_schema(connection)
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO admin_audit_events (
                event_time, request_id, actor_id, action,
                target_id, outcome, reason_code
            ) VALUES (?, 'request-1', ?, 'admin.member.invite',
                      'member:fixed-target', ?, 'invited')
            """,
            (event_time, actor_id, outcome),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

        with pytest.raises(ValueError):
            admin_audit_store.validate_persisted_events(connection)


def test_persisted_validator_accepts_publicly_written_rows() -> None:
    with sqlite3.connect(":memory:") as connection:
        _append(connection)

        admin_audit_store.validate_persisted_events(connection)
