"""배포 플랫폼이 호출하는 서비스 상태 확인 경로."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.web import runtime


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz", include_in_schema=False)
async def healthz():
    """Render가 서버와 SQLite를 읽을 수 있는지 확인하는 가벼운 상태 신호."""
    try:
        runtime._check_storage_read_ready()
    except Exception:  # noqa: BLE001 — 경로나 디스크가 깨졌으면 배포를 정상으로 보지 않는다
        logger.exception("상태 확인에서 SQLite를 읽지 못했습니다")
        return JSONResponse({"status": "unhealthy"}, status_code=503)
    return JSONResponse({"status": "ok"})
