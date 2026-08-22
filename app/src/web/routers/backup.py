"""Render cron이 호출하는 인증된 외부 SQLite 백업 경로."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.core import clock
from src.features.backup import s3 as backup_service
from src.features.backup import status as backup_status
from src.features.storage import db as storage_db
from src.web import internal_cron


router = APIRouter()
logger = logging.getLogger(__name__)


def _record_failure(summary: str) -> None:
    try:
        with storage_db.connect() as conn:
            backup_status.record_failure(
                conn,
                now_iso=clock.iso_now_kst(),
                failure_summary=summary,
            )
    except Exception:
        logger.exception("외부 백업 실패 상태를 기록하지 못했습니다")


@router.post("/internal/backup/run", include_in_schema=False)
async def run_external_backup(request: Request):
    """서버의 영속 디스크를 스냅샷으로 만들어 외부 저장소에서 재검증한다."""

    try:
        expected = backup_service.trigger_secret_from_env()
    except backup_service.BackupConfigurationError:
        logger.exception("외부 백업 요청 비밀이 올바르게 설정되지 않았습니다")
        return JSONResponse({"status": "unavailable"}, status_code=503)

    if not internal_cron.has_valid_bearer(request, expected):
        return internal_cron.unauthorized_response()

    try:
        result = await asyncio.to_thread(backup_service.run_backup)
    except backup_service.BackupAlreadyRunning:
        return JSONResponse(
            {"status": "busy"}, status_code=409, headers={"Retry-After": "60"}
        )
    except backup_service.BackupConfigurationError:
        _record_failure(backup_status.FAILURE_CONFIGURATION)
        logger.exception("외부 SQLite 백업 설정을 확인해야 합니다")
        return JSONResponse({"status": "failed"}, status_code=503)
    except backup_service.ExternalBackupError:
        _record_failure(backup_status.FAILURE_EXECUTION)
        logger.exception("외부 SQLite 백업을 완료하지 못했습니다")
        return JSONResponse({"status": "failed"}, status_code=503)

    try:
        with storage_db.connect() as conn:
            backup_status.record_success(conn, now_iso=clock.iso_now_kst())
    except Exception:
        logger.exception("외부 백업 성공 상태를 기록하지 못했습니다")
        return JSONResponse({"status": "failed"}, status_code=503)

    return JSONResponse(
        {
            "status": "ok",
            "object_key": result.object_key,
            "checksum_key": result.checksum_key,
            "sha256": result.sha256,
            "deleted_objects": result.deleted_objects,
        }
    )
