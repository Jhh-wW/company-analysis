"""파이프라인 선택과 애플리케이션 시작·저장소 준비."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI

from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.core import clock
from src.features.budget import spend_store
from src.features.observability import constants as obs
from src.features.pipeline.demo import DemoPipeline
from src.features.provenance import sources as provenance_sources
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import evaluation_mode, paid_runtime


logger = logging.getLogger(__name__)


def make_pipeline() -> object:
    """환경변수로 데모 또는 실제 조사 파이프라인을 고른다."""
    if os.environ.get(PIPELINE_ENV, "").strip().lower() == PIPELINE_REAL:
        from src.features.pipeline.real import RealPipeline  # noqa: PLC0415

        if evaluation_mode.enabled() and not evaluation_mode.paid_providers_enabled():
            logger.info(
                "실시간 성능시험 미리보기 — real pipeline은 불러왔지만 외부 호출은 잠겨 있습니다."
            )
        elif evaluation_mode.enabled():
            logger.warning(
                "실시간 성능시험 유료 모드 — 브라우저 동의 뒤 provider 비용이 발생할 수 있습니다."
            )
        else:
            logger.warning("★ 진짜 파이프라인으로 돕니다 — AI 호출마다 비용이 발생합니다.")
        return RealPipeline()
    return DemoPipeline()


_PIPELINE = make_pipeline()


def _current_model() -> str:
    """현재 서비스가 쓰는 AI 모델 이름."""
    if isinstance(_PIPELINE, DemoPipeline):
        return "(데모 — AI 호출 없음)"
    from src.features.pipeline.real import _engine  # noqa: PLC0415

    return getattr(_engine(), "MODEL", "")


def _check_storage_write_ready() -> None:
    """시작할 때 SQLite 쓰기 가능성을 확인한다."""
    path = storage_db.default_db_path().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path), timeout=1.0)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
        finally:
            if conn.in_transaction:
                conn.rollback()


def _check_storage_read_ready() -> None:
    """상태 확인은 SQLite를 읽기 전용으로 연다."""
    uri = storage_db.default_db_path().resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT 1").fetchone()


def _recover_link_run_history() -> None:
    """hard restart로 이어갈 수 없는 LINK 실행을 완료된 비용과 함께 마감한다."""

    with storage_db.connect() as conn:
        # 비용 조회와 상태 전이를 같은 SQLite write transaction에 둔다. 시작 시점에는
        # 이전 프로세스의 작업을 이어갈 수 없으므로 running만 복구 대상이다.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        running = share_store.list_running_runs(conn)
        spend_store.ensure_schema(conn)
        known_costs = spend_store.load_run_history(
            conn, (run.run_id for run in running)
        ).by_run
        recovered = share_store.interrupt_running_runs(
            conn,
            interrupted_at=clock.iso_now_kst(),
            stop_step=obs.END_STEP_GENERATE,
            stop_reason="server_restart",
            known_internal_cost_krw_by_run=known_costs,
        )
    if recovered:
        logger.warning(
            "서버 재시작으로 이어갈 수 없는 LINK 생성 이력 %d건을 중단 처리했습니다",
            recovered,
        )


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """시작 상태를 복구하고 종료 시 실행 중 조사를 안전하게 마감한다."""
    # 실행기를 거치지 않고 환경변수 일부만 흉내 내도 유료 호출이 열리지 않게 한다.
    # 이 검사는 저장소를 만들거나 provider에 접속하기 전에 실패한다.
    evaluation_mode.validate_startup_configuration()
    if not provenance_sources.seal_key_is_persistent():
        if os.environ.get(PIPELINE_ENV, "").strip().lower() == PIPELINE_REAL:
            raise RuntimeError(
                "PIPELINE=real에는 32바이트 이상의 PROVENANCE_SEAL_SECRET이 필요합니다"
            )
        logger.warning(
            "PROVENANCE_SEAL_SECRET이 없어 이번 프로세스에서만 유효한 출처 도장을 씁니다"
        )
    _check_storage_write_ready()
    paid_runtime._seed_ledger()
    paid_runtime._recover_observation_lifecycle()
    _recover_link_run_history()
    # 순환 import를 피하려고 앱 조립이 끝나 실제 lifespan이 열릴 때 가져온다.
    from src.web import job_runtime  # noqa: PLC0415

    job_runtime._start_job_runtime()
    try:
        yield
    finally:
        # drain snapshot보다 먼저 admission을 닫아 종료 중 새 task가 끼어들지 못하게 한다.
        job_runtime._begin_job_shutdown()
        await job_runtime._drain_job_tasks()
