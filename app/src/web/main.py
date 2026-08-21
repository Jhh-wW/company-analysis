"""기업분석 웹 애플리케이션의 조립 지점.

실제 요청 처리는 ``routers`` 아래에 두고, 여기서는 앱 생성·미들웨어·정적 파일과
라우터 연결만 담당한다. 배포 진입점 ``src.web.main:app``은 그대로 유지한다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from src.core import paths
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.web import request_helpers, runtime
from src.web.response_security import ResponseSecurityMiddleware
from src.web.routers import admin, analysis, auth, backup, health, reports
from src.web.security import RequestBodyLimitMiddleware


logger = logging.getLogger(__name__)
app = FastAPI(title="기업분석 도구", lifespan=runtime._lifespan)
app.add_middleware(RequestBodyLimitMiddleware)
app.mount("/static", StaticFiles(directory=str(paths.STATIC_DIR)), name="static")


@app.middleware("http")
async def beta_admin_gate(request: Request, call_next):
    """시험 배포에서는 로그인한 관리자만 사이트 본문에 들어오게 한다."""
    if not auth_logic.beta_admin_only_from_env():
        return await call_next(request)

    path = request.url.path
    if path in auth_constants.BETA_PUBLIC_PATHS or path.startswith(
        auth_constants.BETA_PUBLIC_PATH_PREFIXES
    ):
        return await call_next(request)

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    try:
        session = auth_logic.get_session(token)
    except Exception:  # noqa: BLE001 — 저장소 장애 때 공개 쪽으로 실패하면 안 된다
        logger.exception("시험 공개 관리자 세션을 확인하지 못했습니다")
        return Response("서비스를 준비하고 있습니다.", status_code=503)

    if session is not None and session.is_admin:
        return await call_next(request)
    # capability URL을 한 번 통과해 서버가 발급한 쿠키가 있고, 그 열쇠가 DB에서
    # 실제로 살아 있을 때만 결과·진행·PDF 경로를 연다. 관리자 경로는 capability로
    # 절대 열지 않으며, 쿠키가 없는 일반 요청에는 추가 DB 조회 비용을 만들지 않는다.
    if (
        (
            path in auth_constants.BETA_SHARE_PATHS
            or path.startswith(auth_constants.BETA_SHARE_PATH_PREFIXES)
        )
        and request.cookies.get(KEY_COOKIE_NAME)
        and request_helpers._raw_share_key(request)
    ):
        return await call_next(request)
    target = "/auth/not-admin" if session is not None else "/auth/login"
    return RedirectResponse(target, status_code=303)


# beta gate가 직접 돌려주는 303·503에도 같은 보안 헤더를 붙인다.
app.add_middleware(ResponseSecurityMiddleware)

app.include_router(health.router)
app.include_router(backup.router)
app.include_router(analysis.router)
app.include_router(reports.router)
app.include_router(auth.router)
app.include_router(admin.router)

# 기존 외부 import 한 곳에서 쓰는 명시적인 권한 의존성만 유지한다.
require_admin = request_helpers.require_admin
