"""Render cron이 호출하는 인증된 관리자 정기 작업 경로."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.features.admin_dashboard import maintenance
from src.features.storage import db as storage_db
from src.web import internal_cron


router = APIRouter()
logger = logging.getLogger(__name__)
OPERATION_HEADER = "X-Maintenance-Operation"


def _run(operation: str) -> maintenance.MaintenanceResult:
    return maintenance.run_current_operation(
        storage_db.connect,
        operation=operation,
    )


@router.post("/internal/maintenance/run", include_in_schema=False)
async def run_maintenance(request: Request):
    """cron 비밀과 작업 종류를 검증한 뒤 AI 없는 정기 작업만 실행한다."""
    try:
        expected = maintenance.trigger_secret_from_env()
    except maintenance.MaintenanceConfigurationError:
        logger.exception("정기 작업 호출 비밀이 올바르게 설정되지 않았습니다")
        return JSONResponse({"status": "unavailable"}, status_code=503)

    if not internal_cron.has_valid_bearer(request, expected):
        return internal_cron.unauthorized_response()

    operation = request.headers.get(OPERATION_HEADER, "").strip().lower()
    if operation not in maintenance.OPERATIONS:
        return JSONResponse({"status": "invalid_operation"}, status_code=400)

    try:
        result = await asyncio.to_thread(_run, operation)
    except maintenance.MaintenanceRunError:
        logger.exception("관리자 정기 작업을 완료하지 못했습니다")
        return JSONResponse(
            {"status": "failed", "operation": operation}, status_code=503
        )

    return JSONResponse(
        {
            "status": result.status,
            "operation": result.operation,
            "period_key": result.period_key,
            "weekly_report_saved": result.weekly_report_saved,
            "purged_reports": result.purged_reports,
            "stopped_operations": result.stopped_operations,
        }
    )
