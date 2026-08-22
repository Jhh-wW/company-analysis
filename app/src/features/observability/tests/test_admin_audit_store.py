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
