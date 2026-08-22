"""관리 대시보드의 정기 XLSX·휴지통 작업 실행 경계.

Render cron은 웹 서비스의 영속 디스크를 직접 읽을 수 없다. 이 모듈은 웹이 가진
SQLite 연결 팩터리를 주입받아, 기존 claim·append-only 사건을 그대로 사용하면서
AI 호출 없이 정기 작업을 한 번만 실행한다.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Final

from src.core import clock
from src.features.admin_dashboard import store, weekly


ENV_TRIGGER_SECRET: Final[str] = "MAINTENANCE_TRIGGER_SECRET"
MIN_SECRET_BYTES: Final[int] = 32
SYSTEM_ACTOR_EMAIL: Final[str] = "scheduler@internal.invalid"
OPERATION_WEEKLY: Final[str] = "weekly"
OPERATION_CLEANUP: Final[str] = "cleanup"
OPERATIONS: Final[frozenset[str]] = frozenset(
    {OPERATION_WEEKLY, OPERATION_CLEANUP}
)
STALE_RUNNING_AFTER: Final[timedelta] = timedelta(minutes=30)

ConnectionFactory = Callable[[], AbstractContextManager[sqlite3.Connection]]


class MaintenanceConfigurationError(RuntimeError):
    """정기 작업 호출 비밀이 안전하게 설정되지 않았다."""


class MaintenanceRunError(RuntimeError):
    """정기 작업을 완료하거나 실패 기록까지 남기지 못했다."""


@dataclass(frozen=True)
class MaintenanceResult:
    operation: str
    period_key: str
    status: str
    weekly_report_saved: bool = False
    purged_reports: int = 0
    stopped_operations: int = 0


def trigger_secret_from_env() -> str:
    """cron 전용 Bearer 비밀을 읽되 짧거나 비어 있으면 실행을 닫는다."""
    secret = os.environ.get(ENV_TRIGGER_SECRET, "").strip()
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise MaintenanceConfigurationError(
            f"{ENV_TRIGGER_SECRET}은 {MIN_SECRET_BYTES}바이트 이상이어야 합니다"
        )
    return secret


def last_completed_week_start(today: date) -> str:
    """현재 KST 날짜를 기준으로 직전 완료 월~일의 월요일을 돌려준다."""
    this_monday = today - timedelta(days=today.weekday())
    return (this_monday - timedelta(days=7)).isoformat()


def run_operation(
    connect: ConnectionFactory,
    *,
    operation: str,
    today: date,
    now_iso: str,
    actor_email: str = SYSTEM_ACTOR_EMAIL,
) -> MaintenanceResult:
    """관리자 버튼과 내부 cron이 공유하는 유일한 정기 작업 실행 진입점."""
    if operation == OPERATION_WEEKLY:
        return run_weekly_xlsx(
            connect,
            today=today,
            now_iso=now_iso,
            actor_email=actor_email,
        )
    if operation == OPERATION_CLEANUP:
        return run_daily_cleanup(
            connect,
            today=today,
            now_iso=now_iso,
            actor_email=actor_email,
        )
    raise ValueError("지원하지 않는 정기 작업입니다")


def run_current_operation(
    connect: ConnectionFactory,
    *,
    operation: str,
    actor_email: str = SYSTEM_ACTOR_EMAIL,
) -> MaintenanceResult:
    """한 번 캡처한 KST 시각으로 기간 판정과 원장 시각을 함께 고정한다."""
    current = clock.now_kst()
    return run_operation(
        connect,
        operation=operation,
        today=current.date(),
        now_iso=current.isoformat(timespec="seconds"),
        actor_email=actor_email,
    )


def run_weekly_xlsx(
    connect: ConnectionFactory,
    *,
    today: date,
    now_iso: str,
    actor_email: str = SYSTEM_ACTOR_EMAIL,
) -> MaintenanceResult:
    """직전 완료 주의 관리자 XLSX를 멱등 생성하고 성공·실패를 기록한다."""
    week_start = last_completed_week_start(today)
    claim, existing_status = _claim(
        connect,
        operation=store.OPERATION_WEEKLY_XLSX,
        period_key=week_start,
        actor_email=actor_email,
        now_iso=now_iso,
    )
    if claim is None:
        return MaintenanceResult(
            OPERATION_WEEKLY,
            week_start,
            "already_done" if existing_status == "succeeded" else "already_running",
        )

    try:
        with connect() as conn:
            workbook = weekly.build_weekly_workbook(conn, week_start=week_start)
        with connect() as conn:
            saved = store.save_weekly_report(
                conn,
                week_start=week_start,
                workbook_blob=workbook,
                actor_email=actor_email,
                now_iso=now_iso,
            )
            # 과거 수동 생성 파일만 있고 claim이 없던 DB도 성공 상태로 수렴한다.
            if not saved and store.load_weekly_report_blob(
                conn, week_start=week_start
            ) is None:
                raise RuntimeError("주간 XLSX 저장을 확인하지 못했습니다")
            if not store.complete_operation(
                conn,
                key=claim,
                status="succeeded",
                detail="정기 관리자 전용 XLSX 생성 완료",
                now_iso=now_iso,
            ):
                raise RuntimeError("주간 XLSX 작업 상태를 마감하지 못했습니다")
    except Exception as exc:
        _fail_operation(
            connect,
            claim=claim,
            detail="정기 주간 XLSX 생성에 실패했습니다.",
            now_iso=now_iso,
            cause=exc,
        )
    return MaintenanceResult(
        OPERATION_WEEKLY,
        week_start,
        "ok",
        weekly_report_saved=saved,
    )


def run_daily_cleanup(
    connect: ConnectionFactory,
    *,
    today: date,
    now_iso: str,
    actor_email: str = SYSTEM_ACTOR_EMAIL,
) -> MaintenanceResult:
    """30일 휴지통과 이전 날짜 멈춘 작업을 AI 호출 없이 멱등 정리한다."""
    day = today.isoformat()
    claim, existing_status = _claim(
        connect,
        operation=store.OPERATION_TRASH_CLEANUP,
        period_key=day,
        actor_email=actor_email,
        now_iso=now_iso,
    )
    if claim is None:
        return MaintenanceResult(
            OPERATION_CLEANUP,
            day,
            "already_done" if existing_status == "succeeded" else "already_running",
        )

    try:
        with connect() as conn:
            stopped = store.fail_stalled_operations(
                conn,
                before_iso=f"{day}T00:00:00+09:00",
                now_iso=now_iso,
            )
            purged = store.purge_expired_trash(conn, now_iso=now_iso)
            if not store.complete_operation(
                conn,
                key=claim,
                status="succeeded",
                detail=(
                    f"30일 경과 휴지통 {purged}건 정리 완료, "
                    f"이전 날짜 멈춘 작업 {stopped}건 실패 처리"
                ),
                now_iso=now_iso,
            ):
                raise RuntimeError("휴지통 정리 작업 상태를 마감하지 못했습니다")
    except Exception as exc:
        _fail_operation(
            connect,
            claim=claim,
            detail="정기 휴지통·멈춘 작업 정리에 실패했습니다.",
            now_iso=now_iso,
            cause=exc,
        )
    return MaintenanceResult(
        OPERATION_CLEANUP,
        day,
        "ok",
        purged_reports=purged,
        stopped_operations=stopped,
    )


def _claim(
    connect: ConnectionFactory,
    *,
    operation: str,
    period_key: str,
    actor_email: str,
    now_iso: str,
) -> tuple[str | None, str]:
    try:
        reclaim_before_iso = _reclaim_before_iso(now_iso)
        with connect() as conn:
            claim = store.claim_operation(
                conn,
                operation=operation,
                period_key=period_key,
                actor_email=actor_email,
                now_iso=now_iso,
                reclaim_before_iso=reclaim_before_iso,
            )
            status = (
                "running"
                if claim is not None
                else store.operation_claim_status(
                    conn,
                    operation=operation,
                    period_key=period_key,
                )
            )
            if claim is None and status not in {"running", "succeeded"}:
                raise RuntimeError("정기 작업 claim 판정이 불완전합니다")
            return claim, status
    except Exception as exc:
        raise MaintenanceRunError("정기 작업 claim을 저장하지 못했습니다") from exc


def _reclaim_before_iso(now_iso: str) -> str:
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError as exc:
        raise ValueError("정기 작업 실행 시각이 올바르지 않습니다") from exc
    if now.tzinfo is None:
        raise ValueError("정기 작업 실행 시각에는 시간대가 필요합니다")
    return (now - STALE_RUNNING_AFTER).isoformat()


def _fail_operation(
    connect: ConnectionFactory,
    *,
    claim: str,
    detail: str,
    now_iso: str,
    cause: Exception,
) -> None:
    try:
        with connect() as conn:
            recorded = store.complete_operation(
                conn,
                key=claim,
                status="failed",
                detail=detail,
                now_iso=now_iso,
            )
    except Exception as record_error:
        raise MaintenanceRunError(
            "정기 작업 실패와 실패 기록 저장을 모두 완료하지 못했습니다"
        ) from record_error
    if not recorded:
        raise MaintenanceRunError("정기 작업 실패 상태를 기록하지 못했습니다") from cause
    raise MaintenanceRunError(detail) from cause
