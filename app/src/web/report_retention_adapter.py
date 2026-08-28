"""관리 휴지통과 불변 Delivery/PDF 정리를 잇는 웹 수명주기 adapter."""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable

from src.features.admin_dashboard import store as dashboard_store
from src.features.report_delivery import retention
from src.features.report_delivery import store as delivery_store
from src.features.storage import db as storage_db
from src.web import report_delivery_adapter


ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


@dataclass(frozen=True)
class ReconcileReport:
    examined: int = 0
    newly_purged: int = 0
    blobs_deleted: int = 0
    blobs_absent: int = 0
    blobs_preserved: int = 0
    blocked: int = 0
    reclaimed_bytes: int = 0

    def plus(self, other: "ReconcileReport") -> "ReconcileReport":
        return ReconcileReport(
            examined=self.examined + other.examined,
            newly_purged=self.newly_purged + other.newly_purged,
            blobs_deleted=self.blobs_deleted + other.blobs_deleted,
            blobs_absent=self.blobs_absent + other.blobs_absent,
            blobs_preserved=self.blobs_preserved + other.blobs_preserved,
            blocked=self.blocked + other.blocked,
            reclaimed_bytes=self.reclaimed_bytes + other.reclaimed_bytes,
        )


def _now(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("휴지통 정리 시각이 올바르지 않습니다") from exc
    if parsed.tzinfo is None:
        raise ValueError("휴지통 정리 시각에는 시간대가 필요합니다")
    return parsed


def _eligible_at(record: dashboard_store.TrashRecord) -> dt.datetime:
    try:
        trashed = dt.datetime.fromisoformat(record.trashed_at)
    except ValueError as exc:
        raise ValueError("휴지통 이동 시각이 올바르지 않습니다") from exc
    if trashed.tzinfo is None:
        raise ValueError("휴지통 이동 시각에는 시간대가 필요합니다")
    return trashed + dt.timedelta(days=30)


def _begin_immediate(conn: sqlite3.Connection) -> None:
    # storage_db.connect()의 schema bootstrap transaction만 먼저 확정한다.
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")


def _prepare(
    connect: ConnectionFactory,
    *,
    backend,
    record: dashboard_store.TrashRecord,
    current: dt.datetime,
) -> retention.RetirementIntent | None:
    eligible = _eligible_at(record)
    if record.status == dashboard_store.TRASH_TRASHED and current < eligible:
        return None
    if record.status not in {
        dashboard_store.TRASH_TRASHED,
        dashboard_store.TRASH_PURGED,
    }:
        return None
    # 이 with 블록이 닫혀 commit된 뒤에만 DB 삭제 단계로 넘어간다.
    with connect() as conn:
        latest = dashboard_store.trash_record(conn, record.report_id)
        if latest is None or latest.status not in {
            dashboard_store.TRASH_TRASHED,
            dashboard_store.TRASH_PURGED,
        }:
            return None
        latest_eligible = _eligible_at(latest)
        if latest.status == dashboard_store.TRASH_TRASHED and current < latest_eligible:
            return None
        return retention.prepare_retirement(
            conn,
            backend,
            public_id=latest.report_id,
            eligible_at=latest_eligible,
            created_at=current,
        )


def _finish_one(
    connect: ConnectionFactory,
    *,
    backend,
    intent: retention.RetirementIntent,
    current: dt.datetime,
) -> ReconcileReport:
    newly_purged = 0
    with connect() as conn:
        _begin_immediate(conn)
        record = dashboard_store.trash_record(conn, intent.public_id)
        state = retention.latest_event(conn, intent.retirement_id)
        if state in {
            retention.EVENT_PREPARED,
            retention.EVENT_PREPARE_BLOCKED,
            retention.EVENT_COMPLETED_PRESERVED,
        }:
            if (
                state != retention.EVENT_COMPLETED_PRESERVED
                and (record is None or record.status == dashboard_store.TRASH_ACTIVE)
            ):
                retention.cancel_retirement(
                    conn,
                    intent=intent,
                    cancelled_at=current,
                )
                return ReconcileReport(examined=1)
            if (
                state != retention.EVENT_COMPLETED_PRESERVED
                and record.status == dashboard_store.TRASH_TRASHED
                and current < _eligible_at(record)
            ):
                retention.cancel_retirement(
                    conn,
                    intent=intent,
                    cancelled_at=current,
                )
                return ReconcileReport(examined=1)
            retired = retention.retire_database_records(
                conn,
                backend,
                intent=intent,
                retired_at=current,
            )
            if retired.blocked:
                return ReconcileReport(examined=1, blocked=1)
            if (
                record is not None
                and record.status == dashboard_store.TRASH_TRASHED
            ):
                if not dashboard_store.purge_expired_trash_item(
                    conn,
                    report_id=intent.public_id,
                    now_iso=current.isoformat(timespec="seconds"),
                ):
                    raise retention.RetirementError(
                        "v2 정리 뒤 휴지통 영구 상태를 마감하지 못했습니다"
                    )
                newly_purged = 1
            if retired.event_type == retention.EVENT_COMPLETED_PRESERVED:
                return ReconcileReport(
                    examined=1,
                    newly_purged=newly_purged,
                    blobs_preserved=1,
                )

    # DB transaction commit 뒤 파일 단계가 시작된다. 이 사이 중단은 정상이며
    # 다음 시작이 db_retired 사건에서 이어받는다.
    with connect() as conn:
        _begin_immediate(conn)
        state = retention.latest_event(conn, intent.retirement_id)
        if state not in {retention.EVENT_DB_RETIRED, retention.EVENT_BLOB_BLOCKED}:
            return ReconcileReport(examined=1, newly_purged=newly_purged)
        result = retention.reconcile_retired_blob(
            conn,
            backend,
            intent=intent,
            reconciled_at=current,
        )
    return ReconcileReport(
        examined=1,
        newly_purged=newly_purged,
        blobs_deleted=(
            1 if result.event_type == retention.EVENT_COMPLETED_DELETED else 0
        ),
        blobs_absent=(
            1 if result.event_type == retention.EVENT_COMPLETED_ABSENT else 0
        ),
        blobs_preserved=(
            1 if result.event_type == retention.EVENT_COMPLETED_PRESERVED else 0
        ),
        blocked=(1 if result.event_type == retention.EVENT_BLOB_BLOCKED else 0),
        reclaimed_bytes=result.reclaimed_bytes,
    )


def reconcile_retirement_intents(
    connect: ConnectionFactory,
    *,
    now: dt.datetime,
    repair_previously_purged: bool = False,
) -> ReconcileReport:
    """별도 commit 뒤 중단된 정리를 이어받고 옛 purged 누락도 복구한다."""

    current = now
    if current.tzinfo is None:
        raise ValueError("보고서 정리 복구 시각에는 시간대가 필요합니다")
    backend = report_delivery_adapter.configured_artifact_backend()
    with connect() as conn:
        intents = retention.list_nonterminal_intents(conn)
    total = ReconcileReport()
    for intent in intents:
        total = total.plus(
            _finish_one(
                connect,
                backend=backend,
                intent=intent,
                current=current,
            )
        )
    if not repair_previously_purged:
        return total
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT trash.report_id, trash.status, trash.trashed_at,
                   trash.restored_at, trash.purged_at
            FROM {dashboard_store.TABLE_REPORT_TRASH} AS trash
            LEFT JOIN {retention.TABLE_RETIRED_PUBLIC_IDS} AS retired
              ON retired.public_id = trash.report_id
            LEFT JOIN {delivery_store.TABLE_DELIVERIES} AS deliveries
              ON deliveries.public_id = trash.report_id
            WHERE trash.status = 'purged'
              AND (retired.public_id IS NULL OR deliveries.public_id IS NOT NULL)
            ORDER BY trash.trashed_at, trash.report_id
            LIMIT 500
            """
        ).fetchall()
        records = tuple(
            dashboard_store.TrashRecord(
                str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])
            )
            for row in rows
        )
    for record in records:
        intent = _prepare(
            connect,
            backend=backend,
            record=record,
            current=current,
        )
        if intent is None:
            continue
        # 이미 위에서 끝낸 intent면 terminal이라 비용 없는 멱등 확인만 한다.
        total = total.plus(
            _finish_one(
                connect,
                backend=backend,
                intent=intent,
                current=current,
            )
        )
    return total


def purge_expired_reports(
    connect: ConnectionFactory,
    *,
    now_iso: str,
) -> int:
    """30일 경과 legacy·current 정본을 intent 경계로 함께 정리한다."""

    current = _now(now_iso)
    total = reconcile_retirement_intents(
        connect,
        now=current,
        repair_previously_purged=True,
    )
    backend = report_delivery_adapter.configured_artifact_backend()
    with connect() as conn:
        candidates = tuple(
            item
            for item in dashboard_store.retention_cleanup_candidates(
                conn,
                now_iso=now_iso,
                status=dashboard_store.TRASH_TRASHED,
            )
        )
    for record in candidates:
        intent = _prepare(
            connect,
            backend=backend,
            record=record,
            current=current,
        )
        if intent is None:
            continue
        total = total.plus(
            _finish_one(
                connect,
                backend=backend,
                intent=intent,
                current=current,
            )
        )
    return total.newly_purged


def reconcile_configured_report_retirements(*, now: dt.datetime) -> ReconcileReport:
    """서버 시작 시 반쪽 정리와 옛 purged 누락만 외부 호출 없이 복구한다."""

    return reconcile_retirement_intents(
        storage_db.connect,
        now=now,
        repair_previously_purged=True,
    )
