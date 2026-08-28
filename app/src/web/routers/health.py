"""배포 플랫폼이 호출하는 liveness·readiness 상태 확인 경로."""

from contextlib import closing
import asyncio
import logging
import os
import sqlite3

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.core import clock, deployment_identity
from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.provider_health import constants as provider_health_constants
from src.features.provider_health import store as provider_health_store
from src.features.storage import constants as storage_constants
from src.features.storage import db as storage_db
from src.web import paid_runtime, runtime


router = APIRouter()
logger = logging.getLogger(__name__)

#: 배포된 커밋을 알려 주는 환경변수 이름 — 먼저 찾은 것을 쓴다.
#:
#: ★ 왜 필요한가 (2026-08-26 실측) — 배포된 것이 «어느 커밋인지»를 밖에서 알 방법이
#:   전혀 없었다. /healthz 는 {"status":"ok"} 만 주고 응답 헤더에도 단서가 없다
#:   (rndr-id 는 요청마다 바뀐다). 그래서 「Manual Deploy 를 눌렀는가」를 확인하려면
#:   매번 사람이 Render 대시보드를 열어 봐야 했고, 세션을 넘길 때마다 「배포됐는지
#:   모른다」를 인수인계에 적어야 했다.
#: ★ RENDER_GIT_COMMIT 은 Render 가 자동으로 넣어 준다(선언하지 않는다). 다만 이
#:   서비스는 runtime 이 docker 라 주입 여부를 배포 전에는 확인할 수 없다. 그래서
#:   우리가 직접 넣을 수 있는 APP_GIT_COMMIT 을 뒤에 둔다 — 앞의 것이 비어 있으면
#:   render.yaml 에서 이 이름으로 넣어 주면 된다.
_COMMIT_ENV_NAMES: tuple[str, ...] = deployment_identity.COMMIT_ENV_NAMES

#: 커밋을 짧게 자른다. 사람이 대시보드·git log 와 눈으로 맞추는 데 7자면 충분하고,
#: 전체 40자를 그대로 흘리지 않는다.
_COMMIT_SHORT_LEN: int = deployment_identity.COMMIT_SHORT_LEN

#: 받아들일 최대 길이 — git SHA-1 전체가 40자다. 이보다 길면 커밋이 아니다.
_COMMIT_FULL_LEN: int = deployment_identity.COMMIT_FULL_LEN

#: 값을 못 찾았을 때 쓰는 표시. ★ 키를 «빼지» 않고 이 값을 넣는 이유 —
#: 키가 아예 없으면 「이 기능 이전의 옛 코드가 돌고 있다」와 「새 코드인데 환경변수가
#: 안 들어왔다」를 구분할 수 없다. 키가 보이면 새 코드인 것이고, 값이 unknown 이면
#: 환경변수만 손보면 된다.
_COMMIT_UNKNOWN: str = deployment_identity.UNKNOWN_COMMIT

# readiness 응답 순서도 외부 계약이다. provider 하나의 장애를 다른 provider나
# 전체 서비스 장애로 합치지 않고, 늘 같은 순서의 capability로 내보낸다.
_READINESS_PROVIDERS: tuple[str, ...] = (
    provider_health_constants.PROVIDER_ANTHROPIC,
    provider_health_constants.PROVIDER_GOOGLE_PLACES,
    provider_health_constants.PROVIDER_DART,
)


def _blocked_provider_capabilities() -> list[str]:
    """provider 차단 상태를 SQLite에 단 한 글자도 쓰지 않고 읽는다.

    ``storage_db.connect()``는 정상 요청에 맞춰 스키마를 보장하는 쓰기 가능 연결이다.
    공개 readiness는 플랫폼이 자주 호출하므로 여기서는 SQLite의 ``mode=ro``와
    ``query_only``를 함께 써서 cooldown 만료나 탐색 권한 획득까지도 일으키지 않는다.
    """
    uri = storage_db.default_db_path().resolve().as_uri() + "?mode=ro"
    with closing(
        sqlite3.connect(
            uri,
            uri=True,
            timeout=storage_constants.DB_BUSY_TIMEOUT_SEC,
        )
    ) as conn:
        conn.execute("PRAGMA query_only=ON")
        blocked = provider_health_store.list_blocked(
            conn,
            _READINESS_PROVIDERS,
            now_iso=clock.iso_now_kst(),
        )
    return [
        f"provider:{permission.provider}:{permission.reason_code}"
        for permission in blocked
    ]


def _deployed_commit() -> str:
    """지금 돌고 있는 코드의 커밋(짧은 형태). 모르면 ``"unknown"``.

    ★ 환경변수 값을 그대로 내보내지 않는다 — 이 경로는 **로그인 없이** 열리므로,
      값이 오염돼도 16진수 말고는 밖으로 나갈 수 없게 걸러 낸다.
    ★ **자르기 전에 «전체»를 검사한다.** 먼저 7자로 자르고 그 조각만 보면
      ``"8541a53; rm -rf /"`` 같은 값이 앞 7자만 멀쩡해서 **그럴듯한 커밋으로
      통과한다.** 진짜 RENDER_GIT_COMMIT 은 언제나 깨끗한 16진수이므로, 전체가
      16진수가 아니면 «커밋을 안다»고 말하지 않는 편이 정직하다.
    """
    return deployment_identity.short_deployed_commit()


@router.get("/healthz", include_in_schema=False)
async def healthz():
    """프로세스가 HTTP 요청에 답할 수 있는지만 나타내는 liveness 신호.

    ``commit`` 은 «무엇이 배포됐는지» 확인용이고 liveness 판정에는 쓰지 않는다 —
    이 값이 unknown 이어도 상태는 여전히 ok 다.
    """
    return JSONResponse({"status": "ok", "commit": _deployed_commit()})


@router.get("/readyz", include_in_schema=False)
async def readyz():
    """저장소·로그인·비용 원장이 실제 요청을 받을 준비가 됐는지 확인한다."""
    failed: list[str] = []
    blocked_capabilities: list[str] = []
    try:
        # Uvicorn worker가 하나뿐이다. SQLite의 busy timeout을 async event loop에서
        # 직접 기다리면 readiness 한 건이 /healthz와 모든 사용자 요청까지 멈춘다.
        # 순수 read 검사는 worker thread로 보내되 결과를 받은 뒤에만 ready를 말한다.
        await asyncio.to_thread(runtime._check_storage_read_ready)
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

    if os.environ.get(PIPELINE_ENV, "").strip().lower() == PIPELINE_REAL:
        if not paid_runtime.budget_state_machine_ready():
            blocked_capabilities.append("paid_research:budget_state_cutover")
        elif not paid_runtime._BUDGET_STORE_HEALTHY:
            # 기존 보고서·관리 복구 화면까지 Render가 재시작 루프로 없애지 않는다.
            # 유료 조사만 fail-closed이며 운영 상태에는 degraded로 정확히 드러낸다.
            blocked_capabilities.append("paid_research:budget_store")
        if "storage" not in failed:
            try:
                blocked_capabilities.extend(
                    await asyncio.to_thread(_blocked_provider_capabilities)
                )
            except Exception:  # noqa: BLE001 — 내부 경로·SQL은 응답에 싣지 않는다
                logger.exception("준비 상태 확인에서 provider 상태를 읽지 못했습니다")
                failed.append("storage")

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
