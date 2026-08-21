"""Render cron이 호출하는 인증된 외부 SQLite 백업 경로."""

from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.features.backup import s3 as backup_service


router = APIRouter()
logger = logging.getLogger(__name__)


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"status": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/internal/backup/run", include_in_schema=False)
async def run_external_backup(request: Request):
    """서버의 영속 디스크를 스냅샷으로 만들어 외부 저장소에서 재검증한다."""

    try:
        expected = backup_service.trigger_secret_from_env()
    except backup_service.BackupConfigurationError:
        logger.exception("외부 백업 요청 비밀이 올바르게 설정되지 않았습니다")
        return JSONResponse({"status": "unavailable"}, status_code=503)

    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not supplied
        or not hmac.compare_digest(supplied, expected)
    ):
        return _unauthorized()

    try:
        result = await asyncio.to_thread(backup_service.run_backup)
    except backup_service.BackupAlreadyRunning:
        return JSONResponse(
            {"status": "busy"}, status_code=409, headers={"Retry-After": "60"}
        )
    except (backup_service.BackupConfigurationError, backup_service.ExternalBackupError):
        logger.exception("외부 SQLite 백업을 완료하지 못했습니다")
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
