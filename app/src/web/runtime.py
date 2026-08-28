"""파이프라인 선택과 애플리케이션 시작·저장소 준비."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing

from fastapi import FastAPI

from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.core import clock
from src.features.budget import spend_store, state_machine as budget_state_machine
from src.features.admin_dashboard import store as dashboard_store
from src.features.observability import constants as obs
from src.features.pipeline.demo import DemoPipeline
from src.features.provenance import sources as provenance_sources
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import store as delivery_store
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import (
    evaluation_mode,
    paid_runtime,
    report_delivery_adapter,
    report_retention_adapter,
)


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
    """현재 bootstrap한 바로 그 SQLite가 읽기 가능한지 무쓰기 대조한다."""

    path = storage_db.default_db_path().resolve()
    uri = path.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
        conn.execute("PRAGMA query_only=ON")
        current_identity = storage_db._read_database_identity(conn)  # noqa: SLF001
        cached_identity = storage_db._cached_identity(path)  # noqa: SLF001
        if current_identity is None or current_identity != cached_identity:
            raise RuntimeError(
                "readiness SQLite가 현재 process가 bootstrap한 저장소와 다릅니다"
            )
        if (
            storage_db._current_journal_mode(conn)  # noqa: SLF001
            != storage_db.preferred_journal_mode()
        ):
            raise RuntimeError("readiness SQLite journal mode가 안전 계약과 다릅니다")
        conn.execute("SELECT 1 FROM storage_database_identity").fetchone()


def _recover_link_run_history() -> None:
    """hard restart로 이어갈 수 없는 LINK 실행을 완료된 비용과 함께 마감한다."""

    with storage_db.connect() as conn:
        # 비용 조회와 상태 전이를 같은 SQLite write transaction에 둔다. 시작 시점에는
        # 이전 프로세스의 작업을 이어갈 수 없으므로 running만 복구 대상이다.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        running = share_store.list_running_runs(conn)
        spend_store.ensure_schema(conn)
        if budget_state_machine.cutover_applied(conn):
            # 보수부채는 실제 지출로 확정된 값이 아니다. LINK 이력의 내부 원가에는
            # attempt 원장의 확정 비용만 쓰고, 부채는 관리자 비용 화면에 따로 둔다.
            known_costs = {
                run.run_id: budget_state_machine.load_run_exposure(
                    conn, run_id=run.run_id
                ).known_cost_krw
                for run in running
            }
        else:
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


def _recover_member_run_history() -> None:
    """hard restart에 남은 MEMBER 성공 예약을 실제 불변 출고물과 대조해 마감한다.

    성공 건수와 비용은 서로 다른 안전장치다. 보고서가 끝나지 않았으면 성공 예약은
    반환하되, provider 비용의 확정액·보수부채는 비용 원장이 계속 보존한다. 반대로
    delivery와 최초 승인 PDF가 모두 남았으면 실제 성공이므로 ``used``로 확정한다.
    """

    recovered_used = 0
    recovered_returned = 0
    recovered_at = clock.iso_now_kst()
    with storage_db.connect() as conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        for reservation in dashboard_store.list_reserved_member_runs(conn):
            intent = delivery_store.load_delivery_intent(conn, reservation.run_id)
            delivery = (
                delivery_store.load_delivery_by_public_id(conn, reservation.run_id)
                if intent is not None
                and intent.state == delivery_store.DELIVERY_INTENT_COMPLETE
                else None
            )
            artifact = (
                delivery_artifact.artifact_for_delivery(
                    conn,
                    delivery_id=delivery.delivery_id,
                )
                if delivery is not None
                else None
            )
            succeeded = artifact is not None
            settled = dashboard_store.settle_member_run(
                conn,
                run_id=reservation.run_id,
                succeeded=succeeded,
                report_id=reservation.run_id if succeeded else "",
                now_iso=recovered_at,
                outcome=(
                    "server_restart_report_available"
                    if succeeded
                    else "server_restart_interrupted"
                ),
                # 성공 건수 복구에서 비용을 0원으로 추측하지 않는다. 금액은 별도
                # attempt 원장이 정본이며 MEMBER 요약에는 불확실로 표시한다.
                cost_krw=0.0,
                cost_uncertain=True,
            )
            if not settled:
                raise RuntimeError("MEMBER 성공 건수 예약을 재시작 뒤 마감하지 못했습니다")
            if succeeded:
                recovered_used += 1
            else:
                recovered_returned += 1
    if recovered_used or recovered_returned:
        logger.warning(
            "서버 재시작 MEMBER 예약을 마감했습니다: 성공 %d건, 반환 %d건",
            recovered_used,
            recovered_returned,
        )


def _reconcile_artifact_blob_intents() -> None:
    """시작 전 유예 기간이 끝난 미결속 PDF blob만 보수적으로 정리한다."""

    report = report_delivery_adapter.reconcile_configured_artifact_blob_intents(
        now=clock.now_kst(),
    )
    if report.examined:
        logger.warning(
            "artifact blob intent 복구를 끝냈습니다: "
            "검토 %d, 삭제 %d, 이미 없음 %d, 기존 artifact 결속 %d, "
            "활성 보존 %d, 불일치 보존 %d",
            report.examined,
            report.deleted,
            report.absent,
            report.bound_existing,
            report.kept_active,
            report.kept_mismatch,
        )


def _reconcile_report_retirements() -> None:
    """DB·PDF 사이에서 중단된 30일 정리와 옛 누락을 시작 전에 복구한다."""

    report = report_retention_adapter.reconcile_configured_report_retirements(
        now=clock.now_kst(),
    )
    if report.examined:
        logger.warning(
            "보고서 휴지통 정리 복구를 끝냈습니다: 검토 %d, 새 영구 정리 %d, "
            "PDF 삭제 %d, 이미 없음 %d, 공유 보존 %d, 차단 %d, 회수 %d bytes",
            report.examined,
            report.newly_purged,
            report.blobs_deleted,
            report.blobs_absent,
            report.blobs_preserved,
            report.blocked,
            report.reclaimed_bytes,
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
    # provider·job을 열기 전에만 실행한다. 다른 실행 중 writer가
    # 없는 시점이며, 실패하면 서비스를 열지 않는 fail-closed다.
    _reconcile_artifact_blob_intents()
    _reconcile_report_retirements()
    if os.environ.get(PIPELINE_ENV, "").strip().lower() == PIPELINE_REAL:
        # 새 호출부와 같은 배포에서만 forward-only cutover를 켠다. dry-run과
        # 실제 전환은 paid_runtime 안의 한 write 경계에서 실행되고 legacy 행은
        # 삭제하지 않는다. 실패하면 lifespan이 열리지 않아 provider도 못 나간다.
        paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()
    paid_runtime._recover_observation_lifecycle()
    _recover_link_run_history()
    _recover_member_run_history()
    # 순환 import를 피하려고 앱 조립이 끝나 실제 lifespan이 열릴 때 가져온다.
    from src.web import job_runtime  # noqa: PLC0415

    job_runtime._start_job_runtime()
    try:
        yield
    finally:
        # drain snapshot보다 먼저 admission을 닫아 종료 중 새 task가 끼어들지 못하게 한다.
        job_runtime._begin_job_shutdown()
        await job_runtime._drain_job_tasks()
