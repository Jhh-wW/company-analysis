"""기업분석 웹 애플리케이션의 조립 지점.

실제 요청 처리는 ``routers`` 아래에 두고, 여기서는 앱 생성·미들웨어·정적 파일과
라우터 연결만 담당한다. 배포 진입점 ``src.web.main:app``은 그대로 유지한다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from src.core import logging_setup, paths
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.sharelink.access_log import (
    CapabilityAccessLogFilter,
    install_uvicorn_access_log_filter,
)
from src.features.storage import db as storage_db
from src.web import deployment_mode, request_helpers, runtime
from src.web.response_security import ResponseSecurityMiddleware
from src.web.routers import (
    admin,
    analysis,
    auth,
    backup,
    dashboard,
    feedback,
    health,
    maintenance,
    reports,
)
from src.web.security import RequestBodyLimitMiddleware


# ★ 로그 설정을 «가장 먼저» 한다. 이게 없으면 최상위 로거가 핸들러 0개·WARNING이라
#   앱의 `logger.info`는 레코드조차 안 만들어진다 (실측 근거는 core/logging_setup.py).
#   비밀 링크 주소를 가리는 필터를 «핸들러»에 함께 걸어, 애플리케이션 로그로 새는
#   길까지 막는다 — 아래 `install_uvicorn_access_log_filter`는 접근 로그만 막는다.
logging_setup.configure_logging(filters=(CapabilityAccessLogFilter(),))
logger = logging.getLogger(__name__)
install_uvicorn_access_log_filter()
app = FastAPI(title="기업분석 도구", lifespan=runtime._lifespan)
app.add_middleware(RequestBodyLimitMiddleware)
app.mount("/static", StaticFiles(directory=str(paths.STATIC_DIR)), name="static")


@app.middleware("http")
async def beta_admin_gate(request: Request, call_next):
    """관리자 전용 배포에서는 로그인한 관리자와 초대 명단 회원만 본문에 들어오게 한다."""
    narrow_admin_no_forwarded = deployment_mode.render_admin_no_forwarded()
    if not auth_logic.beta_admin_only_from_env() and not narrow_admin_no_forwarded:
        return await call_next(request)

    path = request.url.path
    public_prefix = path.startswith(auth_constants.BETA_PUBLIC_PATH_PREFIXES)
    if narrow_admin_no_forwarded and path.startswith(
        auth_constants.BETA_SHARE_ENTRY_PATH_PREFIXES
    ):
        public_prefix = False
    if path in auth_constants.BETA_PUBLIC_PATHS or public_prefix:
        return await call_next(request)

    token = request.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    try:
        session = auth_logic.get_session(token)
    except Exception:  # noqa: BLE001 — 저장소 장애 때 공개 쪽으로 실패하면 안 된다
        logger.exception("시험 공개 관리자 세션을 확인하지 못했습니다")
        return Response("서비스를 준비하고 있습니다.", status_code=503)

    if session is not None and session.is_admin:
        return await call_next(request)
    # 관리자 전용 로그인 벽이 켜져 있어도 «초대 명단(allowlist)에 활성 상태로
    # 있는 회원»은 관리자 화면만 빼고 통과한다 — 로그인만으로는 통과하지
    # 않는다(`sharelink/tracks.py`의 MEMBER 갈래와 정합).
    # ★ 계약과 무관하게 적용한다 — BETA_ADMIN_ONLY는 «로그인 벽을 켤지»를
    #   정할 뿐, 어느 forwarded-header 신뢰 모델을 쓰는지와는 다른 축이다.
    #   admin.py의 LINK 발급·초대 차단, 이 함수 아래의 `/k/`·LINK 쿠키 차단은
    #   여전히 `narrow_admin_no_forwarded` 값 그대로 옛 관리자 두 계약에서만
    #   걸린다 — 이 예외는 그 둘을 바꾸지 않는다.
    is_admin_path = path == auth_constants.ADMIN_PATH_PREFIX or path.startswith(
        f"{auth_constants.ADMIN_PATH_PREFIX}/"
    )
    if session is not None and not is_admin_path:
        try:
            with storage_db.connect() as conn:
                is_member = share_allow.is_allowed(conn, session.email)
        except Exception:  # noqa: BLE001 — 못 읽으면 «초대 안 된 사람»으로 본다
            logger.exception(
                "초대 명단을 못 읽어 시험 공개 회원 통과를 보류했습니다"
            )
            is_member = False
        if is_member:
            return await call_next(request)
    # capability URL을 한 번 통과해 서버가 발급한 쿠키가 있고, 그 열쇠가 DB에서
    # 실제로 살아 있을 때만 결과·진행·PDF 경로를 연다. 관리자 경로는 capability로
    # 절대 열지 않으며, 쿠키가 없는 일반 요청에는 추가 DB 조회 비용을 만들지 않는다.
    if (
        not narrow_admin_no_forwarded
        and (
            path in auth_constants.BETA_SHARE_PATHS
            or path.startswith(auth_constants.BETA_SHARE_PATH_PREFIXES)
        )
        and request.cookies.get(KEY_COOKIE_NAME)
        and request_helpers._raw_share_key(request)
    ):
        return await call_next(request)
    # 초대 링크가 «도중에» 닫힌 손님은 로그인 화면으로 보내지 않는다 — 로그인해도
    # 들어올 수 없는 계정이라 구글 계정 선택 화면이 막다른 길이 된다. 이유를 아는
    # 손님에게만 이유와 연락 안내를 돌려준다.
    closed_link = analysis.closed_link_guest_response(request)
    if closed_link is not None:
        return closed_link
    target = "/auth/not-admin" if session is not None else "/auth/login"
    return RedirectResponse(target, status_code=303)


# beta gate가 직접 돌려주는 303·503에도 같은 보안 헤더를 붙인다.
app.add_middleware(ResponseSecurityMiddleware)

app.include_router(health.router)
app.include_router(backup.router)
app.include_router(maintenance.router)
app.include_router(analysis.router)
app.include_router(reports.router)
app.include_router(feedback.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(admin.router)

# 기존 외부 import 한 곳에서 쓰는 명시적인 권한 의존성만 유지한다.
require_admin = request_helpers.require_admin
