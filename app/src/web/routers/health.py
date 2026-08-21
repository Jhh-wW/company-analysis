"""배포 플랫폼이 호출하는 liveness·readiness 상태 확인 경로."""

import logging
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.web import paid_runtime, runtime


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/healthz", include_in_schema=False)
async def healthz():
    """프로세스가 HTTP 요청에 답할 수 있는지만 나타내는 liveness 신호."""
    return JSONResponse({"status": "ok"})


@router.get("/readyz", include_in_schema=False)
async def readyz():
    """저장소·로그인·비용 원장이 실제 요청을 받을 준비가 됐는지 확인한다."""
    failed: list[str] = []
    blocked_capabilities: list[str] = []
    try:
        runtime._check_storage_read_ready()
    except Exception:  # noqa: BLE001 — 값·경로는 응답에 싣지 않는다
        logger.exception("준비 상태 확인에서 SQLite를 읽지 못했습니다")
        failed.append("storage")

    if auth_logic.beta_admin_only_from_env():
        for name in (
            auth_constants.ENV_ADMIN_EMAILS,
            auth_constants.ENV_CLIENT_ID,
            auth_constants.ENV_CLIENT_SECRET,
            auth_constants.ENV_REDIRECT_URI,
        ):
            if not os.environ.get(name, "").strip():
                failed.append(name)

    if (
        os.environ.get(PIPELINE_ENV, "").strip().lower() == PIPELINE_REAL
        and not paid_runtime._BUDGET_STORE_HEALTHY
    ):
        # 기존 보고서·관리 복구 화면까지 Render가 재시작 루프로 없애지 않는다.
        # 유료 조사만 fail-closed이며 운영 상태에는 degraded로 정확히 드러낸다.
        blocked_capabilities.append("paid_research:budget_store")

    if failed:
        return JSONResponse(
            {"status": "unready", "failed_checks": failed}, status_code=503
        )
    if blocked_capabilities:
        return JSONResponse(
            {
                "status": "degraded",
                "blocked_capabilities": blocked_capabilities,
            }
        )
    return JSONResponse({"status": "ready"})
