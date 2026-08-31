"""완료 시 확정한 보고서 원본을 GET이 다시 만들지 않는지 검증한다."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.cost_tracking import store as cost_store
from src.features.export_pdf import automatic_release as automatic_release_module
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.export_pdf.automatic_release import AutomaticGateStopped
from src.features.export_pdf.release import PdfRenderBlockedError
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import singleflight
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheLookupKey, CacheNamespace
from src.features.report_delivery.models import DeliveryPolicy
from src.features.report_access import constants as report_access_constants
from src.features.report_access import store as report_access_store
from src.features.report_standard import PublishBlockedError, PublishValidation
from src.features.storage import db as storage_db
from src.features.storage import constants as storage_constants
from src.features.storage import reports as report_store
from src.web import generation_singleflight, job_runtime, paid_runtime, runtime
from src.web import report_delivery_adapter
from src.web.main import app
from src.web.routers import reports as reports_router
from src.shared import automatic_release_record as release_contract
from src.shared.report_source_identity import (
    ReportSourceIdentity,
    financial_payload_digest,
)


_REAL_FINALIZE_REPORT_DELIVERY = job_runtime._finalize_report_delivery


def _authorize_current_admin(client: TestClient) -> None:
    """Delivery 내용 시험을 공개 ID bearer가 아닌 현재 관리자 권한으로 연다."""

    session = auth_logic.create_session(
        "admin@example.com", True, subject="test:delivery-integration-admin"
    )
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)


def _demo_report():
    pipeline = DemoPipeline()
    sample = next(item for item in available_companies() if item["is_report"])
    user_input = UserInput(
        company=sample["company"],
        job=sample["job"],
        region="",
        posting_text="",
    )
    result = pipeline.run(user_input, pipeline.find_company(user_input))
    assert result.outcome is Outcome.REPORT and result.report is not None
    return result.report


def _public_job_for_save(report, *, report_id: str) -> job_runtime.Job:
    issued_at = dt.datetime.now(clock.KST)
    expires_at = issued_at + dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS)
    return job_runtime.Job(
        job_id=report_id,
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="demo-corp",
        ),
        result=RunResult(outcome=Outcome.REPORT, report=report),
        requires_public_report_grant=True,
        public_grant_expires_at=(
            expires_at.timestamp()
            + report_access_constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC
            + 1
        ),
        delivery_issued_at=issued_at,
        delivery_expires_at=expires_at,
    )


@pytest.mark.parametrize("binding_failure", ("false", "unavailable"))
def test_PUBLIC_저장은_grant결속실패때_보고서전체를_rollback한다(
    monkeypatch,
    binding_failure: str,
):
    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _public_job_for_save(report, report_id=report_id)

    def fail_binding(*_args, **_kwargs):
        if binding_failure == "false":
            return False
        raise report_access_store.PublicGrantBindingUnavailable("시험 권한 만료")

    monkeypatch.setattr(report_access_store, "bind_report", fail_binding)
    assert job_runtime._save_report(job) is False
    assert job.report_persisted is False
    with storage_db.connect() as conn:
        assert report_store.load(conn, report_id) is None


def test_LINK와ADMIN저장은_grant결속False가_정상이고_MEMBER계약도_바뀌지않는다(
    monkeypatch,
):
    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _public_job_for_save(report, report_id=report_id)
    job.requires_public_report_grant = False
    job.public_grant_expires_at = 0.0
    monkeypatch.setattr(report_access_store, "bind_report", lambda *_a, **_k: False)

    assert job_runtime._save_report(job) is True
    with storage_db.connect() as conn:
        assert report_store.load(conn, report_id) is not None


def test_PUBLIC_cookie가_Delivery보다먼저끝나면_DB쓰기전_거절한다(monkeypatch):
    report = _demo_report()
    report_id = uuid.uuid4().hex
    job = _public_job_for_save(report, report_id=report_id)
    assert job.delivery_expires_at is not None
    job.public_grant_expires_at = (
        job.delivery_expires_at.timestamp()
        + report_access_constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC
    )
    binding_calls = 0

    def should_not_bind(*_args, **_kwargs):
        nonlocal binding_calls
        binding_calls += 1
        return True

    monkeypatch.setattr(report_access_store, "bind_report", should_not_bind)
    assert job_runtime._save_report(job) is False
    assert binding_calls == 0
    with storage_db.connect() as conn:
        assert report_store.load(conn, report_id) is None


def test_PUBLIC_저장실패는_메모리성공과_출고호출과_차감을_모두닫는다(
    monkeypatch,
):
    report = _demo_report()

    class _ReportPipeline:
        @staticmethod
        def run(*_args, **_kwargs):
            return RunResult(outcome=Outcome.REPORT, report=report)

    job = _public_job_for_save(report, report_id=uuid.uuid4().hex)
    job.result = None
    finalized = 0
    failed_intents = 0

    def forbidden_finalize(_job):
        nonlocal finalized
        finalized += 1
        return True

    def failed_delivery(_job):
        nonlocal failed_intents
        failed_intents += 1

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline())
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: False)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", forbidden_finalize)
    monkeypatch.setattr(job_runtime, "_fail_report_delivery", failed_delivery)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert finalized == 0
    assert failed_intents == 1
    assert job.result is not None
    assert job.result.outcome is Outcome.FAILED
    assert job.result.report is None
    assert job.result.charged is False
    assert job.delivery_persisted is False


def test_PUBLIC_최종출고는_실제60일만료보다_grant가짧으면_청구까지rollback한다(
    monkeypatch,
    tmp_path: Path,
):
    report = _demo_report()
    report_id = uuid.uuid4().hex
    completed_at = dt.datetime.now(clock.KST).replace(microsecond=0)
    delivery_expires_at = completed_at + dt.timedelta(
        days=REPORT_LINK_MAX_AGE_DAYS
    )
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "pg"))
    with storage_db.connect() as conn:
        cost_store.record_run_costs(
            conn,
            run_id=report_id,
            outcome=Outcome.REPORT,
            internal_ai_cost_krw=123.0,
        )
        grant = report_access_store.issue_and_bind(
            conn,
            existing_token="",
            run_id=report_id,
            now=completed_at.timestamp(),
        )
        # 정확히 경계면 ``expires_at > required_until``을 만족하지 못한다.
        conn.execute(
            f"UPDATE {report_access_store.TABLE_GRANTS} SET expires_at=? "
            "WHERE grant_hash=?",
            (
                delivery_expires_at.timestamp()
                + report_access_constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC,
                grant.grant_hash,
            ),
        )

    with pytest.raises(report_access_store.PublicGrantBindingUnavailable):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="demo-corp",
            billing_bucket_id="public",
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=False,
            completed_at=completed_at,
            public_access_run_id=report_id,
        )
    with storage_db.connect() as conn:
        assert delivery_store.load_delivery_by_public_id(conn, report_id) is None
        cost_row = conn.execute(
            f"SELECT internal_ai_cost_krw, customer_charge_krw, "
            "charge_eligible, automatic_release_sha256 "
            f"FROM {cost_store.RUN_COST_TABLE} WHERE run_id=?",
            (report_id,),
        ).fetchone()
        assert cost_row is not None
        assert tuple(cost_row) == (123.0, 0.0, 0, "")


@pytest.mark.parametrize("reuse_kind", ("waiter", "cache"))
def test_PUBLIC_waiter와cache도_최종grant결속False면_출고와청구를_rollback한다(
    monkeypatch,
    tmp_path: Path,
    reuse_kind: str,
):
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "reuse"))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {"status": "000", "list": [{"account_nm": "매출액", "thstrm_amount": "100"}]}
    )
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest
    owner = reports_router.finalize_new_report_delivery(
        report_id=f"owner-{uuid.uuid4().hex}",
        corp_id="demo-corp",
        billing_bucket_id="same-bucket",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    assert owner.artifact is not None
    target_id = uuid.uuid4().hex
    monkeypatch.setattr(
        report_access_store,
        "bind_report",
        lambda *_a, **_k: False,
    )
    extra = (
        {
            "cache_namespace": namespace,
            "preflight_identity_digest": preflight_digest,
            "cache_eligible": True,
        }
        if reuse_kind == "cache"
        else {}
    )

    with pytest.raises(report_access_store.PublicGrantBindingUnavailable):
        reports_router.finalize_new_report_delivery(
            report_id=target_id,
            corp_id="demo-corp",
            billing_bucket_id="same-bucket",
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=True,
            dart_receipt_numbers=(receipt,),
            financial_payload_digest=finance_digest,
            reuse_content_snapshot_id=owner.content.content_id,
            reuse_artifact_id=owner.artifact.artifact_id,
            public_access_run_id=target_id,
            **extra,
        )
    with storage_db.connect() as conn:
        assert delivery_store.load_delivery_by_public_id(conn, target_id) is None
        assert conn.execute(
            f"SELECT 1 FROM {cost_store.RUN_COST_TABLE} WHERE run_id=?",
            (target_id,),
        ).fetchone() is None


def _database_dump(path: Path) -> tuple[str, ...]:
    """GET 전후 모든 스키마·행을 같은 정규 SQL 묶음으로 비교한다."""

    with sqlite3.connect(path) as conn:
        return tuple(conn.iterdump())


def test_신선한배포의_공개GET은_DB와artifact폴더를_만들지않는다(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fresh-data" / "analysis.db"
    artifact_root = tmp_path / "fresh-artifacts"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv("APP_DATA_ROOT", str(artifact_root))

    assert report_delivery_adapter.load_public_delivery_intent("missing-id") is None
    assert report_delivery_adapter.load_public_delivery("missing-id") is None

    assert not db_path.exists()
    assert not db_path.parent.exists()
    assert not artifact_root.exists()


def test_job은_delivery확정시도_뒤에만_완료로_열린다(monkeypatch):
    report = _demo_report()

    class _ReportPipeline:
        @staticmethod
        def run(*_args, **_kwargs):
            return RunResult(
                outcome=Outcome.REPORT,
                report=report,
                model="deterministic-demo",
            )

    job = job_runtime.Job(
        job_id=f"completion-{uuid.uuid4().hex}",
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="demo-corp",
        ),
    )
    calls: list[str] = []

    def finalize(current: job_runtime.Job) -> bool:
        assert current.finished is False
        assert current.result is not None and current.result.report is report
        calls.append(current.job_id)
        return True

    monkeypatch.setattr(runtime, "_PIPELINE", _ReportPipeline())
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", finalize)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert calls == [job.job_id]
    assert job.delivery_persisted is True
    assert job.finished is True


def test_completion_adapter는_수집시점의_비민감_출처지문을_그대로받는다(
    monkeypatch,
):
    report = _demo_report()
    receipt = "20260828000123"
    finance_digest = "a" * 64
    job = job_runtime.Job(
        job_id=f"source-{uuid.uuid4().hex}",
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="demo-corp",
        ),
        result=RunResult(
            outcome=Outcome.REPORT,
            report=report,
            dart_receipt_numbers=(receipt,),
            financial_payload_digest=finance_digest,
            generation_cache_eligible=False,
        ),
    )
    captured: dict[str, object] = {}

    def finalize(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=SimpleNamespace(content_id="captured-content-id"),
            artifact=SimpleNamespace(artifact_id="captured-artifact-id"),
        )

    # 공통 웹 fixture는 unrelated route 시험을 빠르게 하려고 worker 경계를
    # 값싼 성공으로 바꾼다. 이 전용 시험만 실제 adapter 호출 계약을 되살린다.
    monkeypatch.setattr(
        job_runtime,
        "_finalize_report_delivery",
        _REAL_FINALIZE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(reports_router, "finalize_new_report_delivery", finalize)

    assert job_runtime._finalize_report_delivery(job) is True
    assert job.delivery_content_id == "captured-content-id"
    assert job.delivery_artifact_id == "captured-artifact-id"
    assert captured["dart_receipt_numbers"] == (receipt,)
    assert captured["financial_payload_digest"] == finance_digest
    assert captured["cache_eligible"] is False


def test_부분출처_bypass는_캐시결속없이도_정상출고한다(monkeypatch):
    report = _demo_report()
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    session = generation_singleflight.GenerationSession(
        run_id=f"partial-{uuid.uuid4().hex}",
        share_key="partial-share",
        billing_bucket_id="partial-bucket",
        cap_krw=900.0,
        on_paid_phase=lambda _ticket: None,
    )
    assert session.coordinate("demo-corp", namespace, "") is None
    assert session.cache_namespace is None
    assert session.preflight_identity_digest == ""
    job = job_runtime.Job(
        job_id=f"partial-delivery-{uuid.uuid4().hex}",
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="demo-corp",
        ),
        result=RunResult(outcome=Outcome.REPORT, report=report),
        generation_session=session,
    )
    captured: dict[str, object] = {}

    def finalize(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=SimpleNamespace(content_id="partial-content"),
            artifact=SimpleNamespace(artifact_id="partial-artifact"),
        )

    monkeypatch.setattr(reports_router, "finalize_new_report_delivery", finalize)

    assert _REAL_FINALIZE_REPORT_DELIVERY(job) is True
    assert captured["cache_namespace"] is None
    assert captured["preflight_identity_digest"] == ""


def test_새_delivery의_결과와_PDF_GET은_저장본만_반복해서_읽는다(
    monkeypatch,
    tmp_path: Path,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifact-data"))
    finance_digest = financial_payload_digest(
        {
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        }
    )

    persisted = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="public",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=("20260828000123",),
        financial_payload_digest=finance_digest,
    )
    assert persisted.inspection is not None
    assert persisted.artifact is not None
    approved_bytes = persisted.inspection.pdf_bytes
    assert approved_bytes is not None
    approved_sha256 = hashlib.sha256(approved_bytes).hexdigest()
    with storage_db.connect() as conn:
        source = delivery_store.load_source_snapshot(
            conn,
            persisted.content.source_snapshot_id,
        )
    assert source is not None and source.cache_usable is True
    assert source.dart_receipt_nos == ("20260828000123",)
    assert source.financial_payload_sha256 == finance_digest
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_COMPLETE

    renderer_calls: list[str] = []

    def forbidden_renderer(*_args, **_kwargs):
        renderer_calls.append("called")
        raise AssertionError("delivery GET이 PDF를 다시 렌더링했습니다")

    # 어느 구형 출고 진입점으로 새더라도 시험이 즉시 실패해야 한다.
    monkeypatch.setattr(reports_router, "_release_state", forbidden_renderer)
    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden_renderer)
    monkeypatch.setattr(reports_router, "automatic_release_pdf", forbidden_renderer)
    job_runtime._JOBS.clear()  # 재시작 뒤에도 public_id 조회만으로 읽어야 한다.

    db_path = storage_db.default_db_path()
    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        before = _database_dump(db_path)
        first_result = client.get(f"/result/{report_id}", follow_redirects=False)
        second_result = client.get(f"/result/{report_id}", follow_redirects=False)
        first_pdf = client.get(
            f"/download/pdf/{report_id}", follow_redirects=False
        )
        second_pdf = client.get(
            f"/download/pdf/{report_id}", follow_redirects=False
        )
        after = _database_dump(db_path)

    assert first_result.status_code == second_result.status_code == 200
    assert first_pdf.status_code == second_pdf.status_code == 200
    assert first_pdf.content == second_pdf.content == approved_bytes
    assert first_pdf.headers["x-pdf-sha256"] == approved_sha256
    assert first_pdf.headers["x-pdf-artifact-id"] == persisted.artifact.artifact_id
    assert second_pdf.headers["x-pdf-artifact-id"] == persisted.artifact.artifact_id
    assert renderer_calls == []
    assert after == before


def test_singleflight_waiter는_owner의_content와_최초PDF를_그대로발급받는다(
    monkeypatch,
    tmp_path: Path,
):
    report = _demo_report()
    owner_id = f"fanout-owner-{uuid.uuid4().hex}"
    waiter_id = f"fanout-waiter-{uuid.uuid4().hex}"
    artifact_root = tmp_path / "fanout-artifacts"
    monkeypatch.setenv("APP_DATA_ROOT", str(artifact_root))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        }
    )
    owner = reports_router.finalize_new_report_delivery(
        report_id=owner_id,
        corp_id="demo-corp",
        billing_bucket_id="same-billing-bucket",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    )
    assert owner.artifact is not None
    assert owner.inspection is not None
    assert owner.inspection.pdf_bytes is not None

    def forbidden_renderer(*_args, **_kwargs):
        raise AssertionError("waiter가 owner PDF를 두고 다시 렌더링했습니다")

    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "automatic_release_pdf", forbidden_renderer)
    with storage_db.connect() as conn:
        before_counts = (
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_SOURCE_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_CONTENT_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_artifact.TABLE_ARTIFACTS}"
            ).fetchone()[0],
        )

    waiter = reports_router.finalize_new_report_delivery(
        report_id=waiter_id,
        corp_id="demo-corp",
        billing_bucket_id="same-billing-bucket",
        report=owner.report,
        actual_models=owner.content.actual_models,
        reused_from_cache=True,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        reuse_content_snapshot_id=owner.content.content_id,
        reuse_artifact_id=owner.artifact.artifact_id,
    )

    assert waiter.content.content_id == owner.content.content_id
    assert waiter.content.source_snapshot_id == owner.content.source_snapshot_id
    assert waiter.artifact is not None
    assert waiter.artifact.artifact_id == owner.artifact.artifact_id
    assert waiter.inspection is not None
    assert waiter.inspection.pdf_bytes == owner.inspection.pdf_bytes
    assert waiter.delivery.delivery_id != owner.delivery.delivery_id
    assert waiter.delivery.public_id == waiter_id
    assert waiter.delivery.cache_origin_content_id == owner.content.content_id
    with storage_db.connect() as conn:
        after_counts = (
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_SOURCE_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_CONTENT_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_artifact.TABLE_ARTIFACTS}"
            ).fetchone()[0],
        )
        assert delivery_store.delivery_count_for_content(
            conn, owner.content.content_id
        ) == 2
    assert after_counts == before_counts


def test_일반캐시_hit은_같은통장안에서_원본content와PDF로_새delivery만발급한다(
    monkeypatch,
    tmp_path: Path,
):
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "ordinary-cache-artifacts"))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        }
    )
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest
    owner = reports_router.finalize_new_report_delivery(
        report_id=f"cache-owner-{uuid.uuid4().hex}",
        corp_id="demo-corp",
        billing_bucket_id="owner-bucket",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    assert owner.artifact is not None
    assert owner.inspection is not None and owner.inspection.pdf_bytes is not None

    session = generation_singleflight.GenerationSession(
        run_id=f"cache-reader-{uuid.uuid4().hex}",
        share_key="owner-share",
        billing_bucket_id="owner-bucket",
        cap_krw=900.0,
        on_paid_phase=lambda _ticket: None,
    )
    reused = session.coordinate("demo-corp", namespace, preflight_digest)
    assert reused is not None
    assert session.paid_phase is None
    assert reused.content_snapshot_id == owner.content.content_id
    assert reused.artifact_id == owner.artifact.artifact_id

    def forbidden_renderer(*_args, **_kwargs):
        raise AssertionError("일반 캐시 hit이 최초 PDF를 두고 다시 렌더링했습니다")

    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "automatic_release_pdf", forbidden_renderer)
    with storage_db.connect() as conn:
        before_counts = (
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_SOURCE_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_CONTENT_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_artifact.TABLE_ARTIFACTS}"
            ).fetchone()[0],
        )

    cached_delivery = reports_router.finalize_new_report_delivery(
        report_id=f"cache-delivery-{uuid.uuid4().hex}",
        corp_id="demo-corp",
        billing_bucket_id="owner-bucket",
        report=reused.report,
        actual_models=reused.actual_models,
        reused_from_cache=True,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        reuse_content_snapshot_id=reused.content_snapshot_id,
        reuse_artifact_id=reused.artifact_id,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )

    assert cached_delivery.content.content_id == owner.content.content_id
    assert cached_delivery.artifact is not None
    assert cached_delivery.artifact.artifact_id == owner.artifact.artifact_id
    assert cached_delivery.inspection is not None
    assert cached_delivery.inspection.pdf_bytes == owner.inspection.pdf_bytes
    assert cached_delivery.delivery.delivery_id != owner.delivery.delivery_id
    assert (
        cached_delivery.delivery.cache_origin_content_id
        == owner.content.content_id
    )
    with storage_db.connect() as conn:
        after_counts = (
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_SOURCE_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_store.TABLE_CONTENT_SNAPSHOTS}"
            ).fetchone()[0],
            conn.execute(
                f"SELECT COUNT(*) FROM {delivery_artifact.TABLE_ARTIFACTS}"
            ).fetchone()[0],
        )
    assert after_counts == before_counts


def test_일시적수집실패_보고서는_전달하되_정식캐시에는_묶지않는다(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """그날의 수집 실패가 60일 동안 다음 사용자에게 재사용되면 안 된다."""

    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "uncacheable-artifacts"))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {"status": "000", "list": [{"account_nm": "매출액", "thstrm_amount": "100"}]}
    )
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest
    bucket = "uncacheable-bucket"
    corp_id = "demo-corp"

    delivered = reports_router.finalize_new_report_delivery(
        report_id=f"uncacheable-{uuid.uuid4().hex}",
        corp_id=corp_id,
        billing_bucket_id=bucket,
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
    )

    assert delivered.artifact is not None
    cache_key = CacheLookupKey.from_preflight(
        billing_bucket_id=bucket,
        corp_id=corp_id,
        namespace=namespace,
        preflight_identity_digest=preflight_digest,
        preflight_cache_usable=True,
    )
    with storage_db.connect() as conn:
        assert delivery_store.load_cache_hit(
            conn,
            key=cache_key,
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            delivered_at=dt.datetime.now(dt.timezone.utc),
        ) is None

    # 캐시에 없더라도 돈을 쓴 현재 사용자에게는 승인 본문·PDF를 그대로 준다.
    reloaded = report_delivery_adapter.load_public_delivery(
        delivered.delivery.public_id
    )
    assert reloaded is not None
    assert reloaded.content.content_id == delivered.content.content_id
    assert reloaded.artifact is not None
    assert reloaded.artifact.artifact_id == delivered.artifact.artifact_id


def test_손상PDF캐시와_남은완료fanout은_격리하고_provider한번으로_재생성한다(
    monkeypatch,
    tmp_path: Path,
):
    report = _demo_report()
    data_root = tmp_path / "poison-cache-artifacts"
    monkeypatch.setenv("APP_DATA_ROOT", str(data_root))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        }
    )
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest
    bucket = "poison-cache-bucket"
    corp_id = "demo-corp"
    owner_id = f"poison-owner-{uuid.uuid4().hex}"
    owner = reports_router.finalize_new_report_delivery(
        report_id=owner_id,
        corp_id=corp_id,
        billing_bucket_id=bucket,
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    assert owner.artifact is not None
    assert owner.artifact.blob_pointer is not None

    lease_key = singleflight.LeaseKey(
        billing_bucket_id=bucket,
        corp_id=corp_id,
        cache_namespace_id=namespace.namespace_id,
        source_identity_digest=preflight_digest,
    )
    completed_at = dt.datetime.now(dt.timezone.utc)
    with storage_db.connect() as conn:
        acquired = singleflight.acquire(
            conn,
            key=lease_key,
            owner_id="old-completed-owner",
            now=completed_at,
            lease_ttl=dt.timedelta(minutes=15),
        )
        assert acquired.handle is not None
        assert singleflight.complete(
            conn,
            handle=acquired.handle,
            content_snapshot_id=owner.content.content_id,
            artifact_id=owner.artifact.artifact_id,
            now=completed_at + dt.timedelta(seconds=1),
            result_fanout_ttl=dt.timedelta(minutes=2),
        )

    blob_path = (
        data_root / "report-artifacts" / owner.artifact.blob_pointer.key
    )
    blob_path.unlink()

    ticket = paid_runtime.PaidPhase(
        run_id="poison-cache-recovery",
        phase="pipeline",
        day=dt.date(2026, 8, 28),
        share_key="share:poison-cache-bucket",
        bucket_id=bucket,
        reserved_krw=900.0,
    )
    monkeypatch.setattr(
        paid_runtime,
        "_begin_paid_phase",
        lambda **_kwargs: ticket,
    )
    monkeypatch.setattr(
        paid_runtime,
        "_activate_paid_provider",
        lambda _ticket: contextlib.nullcontext(),
    )
    session = generation_singleflight.GenerationSession(
        run_id="poison-cache-recovery",
        share_key="share:poison-cache-bucket",
        billing_bucket_id=bucket,
        cap_krw=900.0,
        on_paid_phase=lambda _ticket: None,
    )
    assert session.coordinate(corp_id, namespace, preflight_digest) is None
    assert session.owns_generation
    provider_calls = 0

    def call_provider() -> None:
        nonlocal provider_calls
        session.ensure_paid_phase()
        provider_calls += 1

    call_provider()
    assert provider_calls == 1
    session.close_provider_context()
    session.abandon()

    cache_key = CacheLookupKey.from_preflight(
        billing_bucket_id=bucket,
        corp_id=corp_id,
        namespace=namespace,
        preflight_identity_digest=preflight_digest,
        preflight_cache_usable=True,
    )
    with storage_db.connect() as conn:
        assert delivery_store.load_cache_hit(
            conn,
            key=cache_key,
            policy=DeliveryPolicy(
                content_max_age=dt.timedelta(days=60),
                public_link_lifetime=dt.timedelta(days=60),
            ),
            delivered_at=dt.datetime.now(dt.timezone.utc),
        ) is None
        reasons = tuple(
            str(row[0])
            for row in conn.execute(
                f"SELECT reason_code FROM {delivery_store.TABLE_CACHE_INVALIDATIONS} "
                "WHERE content_snapshot_id = ? AND artifact_id = ?",
                (owner.content.content_id, owner.artifact.artifact_id),
            )
        )
        assert reasons == ("artifact_bytes_unavailable",)
    historical = report_delivery_adapter.load_public_delivery(owner_id)
    assert historical is not None
    assert historical.content.content_id == owner.content.content_id
    assert historical.inspection is not None
    assert historical.inspection.status is delivery_artifact.ArtifactInspectionStatus.MISSING


def test_COMPLETE재시도는_같은통장과_정식cache신원일때만_멱등이다(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "idempotency-artifacts"))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {"status": "000", "list": [{"account_nm": "매출액"}]}
    )
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest
    report_id = f"idempotency-{uuid.uuid4().hex}"
    arguments = dict(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="exact-bucket",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        cache_eligible=True,
    )
    owner = reports_router.finalize_new_report_delivery(**arguments)
    retry = reports_router.finalize_new_report_delivery(**arguments)
    assert retry.delivery.delivery_id == owner.delivery.delivery_id

    with pytest.raises(report_delivery_adapter.DeliveryAdapterError):
        reports_router.finalize_new_report_delivery(
            **{**arguments, "billing_bucket_id": "different-bucket"}
        )
    with pytest.raises(report_delivery_adapter.DeliveryAdapterError):
        reports_router.finalize_new_report_delivery(
            **{**arguments, "preflight_identity_digest": "f" * 64}
        )


def test_다른두통장의_동시miss는_각자content와PDF를_정상확정한다(
    monkeypatch,
    tmp_path: Path,
):
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "two-bucket-artifacts"))
    receipt = "20260828000123"
    finance_digest = financial_payload_digest(
        {
            "status": "000",
            "list": [{"account_nm": "매출액", "thstrm_amount": "100"}],
        }
    )
    revision, image = report_delivery_adapter._release_identity()
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    preflight_digest = ReportSourceIdentity(
        dart_receipt_numbers=(receipt,),
        financial_payload_digest=finance_digest,
    ).cache_digest

    def finalize(bucket: str):
        return reports_router.finalize_new_report_delivery(
            report_id=f"{bucket}-{uuid.uuid4().hex}",
            corp_id="demo-corp",
            billing_bucket_id=bucket,
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=False,
            dart_receipt_numbers=(receipt,),
            financial_payload_digest=finance_digest,
            cache_namespace=namespace,
            preflight_identity_digest=preflight_digest,
            cache_eligible=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(finalize, "bucket-a")
        second_future = pool.submit(finalize, "bucket-b")
        first = first_future.result(timeout=30)
        second = second_future.result(timeout=30)

    assert first.delivery.delivery_id != second.delivery.delivery_id
    assert first.artifact is not None and second.artifact is not None
    # 같은 시각·결정적 출력이면 내용주소 ID가 우연히 같을 수 있다. 독립성의
    # 제품 계약은 ID가 달라야 한다는 것이 아니라 두 통장이 각각 자기 cache
    # handle과 Delivery를 가져 한쪽 INSERT가 다른 쪽을 막지 않는다는 것이다.
    with storage_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT billing_bucket_id, content_snapshot_id, artifact_id
            FROM {delivery_store.TABLE_CACHE_ENTRIES}
            WHERE corp_id = ? AND cache_namespace_id = ?
              AND preflight_identity_digest = ?
            """,
            ("demo-corp", namespace.namespace_id, preflight_digest),
        ).fetchall()
    assert {str(row[0]) for row in rows} == {"bucket-a", "bucket-b"}
    sessions = tuple(
        generation_singleflight.GenerationSession(
            run_id=f"reader-{bucket}-{uuid.uuid4().hex}",
            share_key=f"share-{bucket}",
            billing_bucket_id=bucket,
            cap_krw=900.0,
            on_paid_phase=lambda _ticket: None,
        )
        for bucket in ("bucket-a", "bucket-b")
    )
    hits = tuple(
        session.coordinate("demo-corp", namespace, preflight_digest)
        for session in sessions
    )
    assert hits[0] is not None and hits[1] is not None
    assert hits[0].content_snapshot_id == first.content.content_id
    assert hits[1].content_snapshot_id == second.content.content_id
    assert hits[0].artifact_id == first.artifact.artifact_id
    assert hits[1].artifact_id == second.artifact.artifact_id


def test_singleflight_content는_다른비용통장에_넘겨주지않는다(
    monkeypatch,
    tmp_path: Path,
):
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "bucket-artifacts"))
    finance_digest = financial_payload_digest(
        {"status": "000", "list": [{"account_nm": "매출액"}]}
    )
    owner = reports_router.finalize_new_report_delivery(
        report_id=f"bucket-owner-{uuid.uuid4().hex}",
        corp_id="demo-corp",
        billing_bucket_id="owner-bucket",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        dart_receipt_numbers=("20260828000123",),
        financial_payload_digest=finance_digest,
    )
    assert owner.artifact is not None

    with pytest.raises(report_delivery_adapter.DeliveryAdapterError):
        reports_router.finalize_new_report_delivery(
            report_id=f"bucket-cross-{uuid.uuid4().hex}",
            corp_id="demo-corp",
            billing_bucket_id="different-bucket",
            report=owner.report,
            actual_models=owner.content.actual_models,
            reused_from_cache=True,
            dart_receipt_numbers=("20260828000123",),
            financial_payload_digest=finance_digest,
            reuse_content_snapshot_id=owner.content.content_id,
            reuse_artifact_id=owner.artifact.artifact_id,
        )


def test_향후_checker_v2배포를흉내내도_v1승인artifact는_그계약으로조회한다(
    monkeypatch,
    tmp_path: Path,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifact-data"))

    persisted = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="public",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
    )
    assert persisted.artifact is not None
    assert persisted.inspection is not None
    approved_bytes = persisted.inspection.pdf_bytes
    assert approved_bytes is not None
    historical_version = persisted.artifact.version.checker_version
    assert historical_version == "automatic-release-v1"

    # 다음 배포에서 새 출고 기본값이 v2가 됐다고 흉내 낸다. 조회 경로가
    # 현재 기본값을 쓰면 지원 여부/행 조회에서 닫히므로 이 시험이 깨진다.
    future_version = "automatic-release-v2"
    monkeypatch.setattr(
        automatic_release_module,
        "AUTOMATIC_CHECKER_VERSION",
        future_version,
    )
    monkeypatch.setattr(
        pdf_release_store,
        "AUTOMATIC_CHECKER_VERSION",
        future_version,
    )
    monkeypatch.setattr(
        report_delivery_adapter,
        "AUTOMATIC_CHECKER_VERSION",
        future_version,
    )
    monkeypatch.setattr(
        release_contract,
        "AUTOMATIC_CHECKER_VERSION",
        future_version,
    )

    renderer_calls: list[str] = []

    def forbidden_renderer(*_args, **_kwargs):
        renderer_calls.append("called")
        raise AssertionError("역사적 artifact GET이 현재 renderer를 호출했습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden_renderer)
    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden_renderer)
    monkeypatch.setattr(reports_router, "automatic_release_pdf", forbidden_renderer)
    job_runtime._JOBS.clear()

    db_path = storage_db.default_db_path()
    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        before = _database_dump(db_path)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        after = _database_dump(db_path)

    assert result.status_code == pdf.status_code == 200
    assert pdf.content == approved_bytes
    assert renderer_calls == []
    assert after == before


def test_artifact_checker버전변조는_현재버전으로_보정하지않고_닫는다(
    monkeypatch,
    tmp_path: Path,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifact-data"))
    persisted = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="public",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
    )
    assert persisted.artifact is not None

    with storage_db.connect() as conn:
        conn.execute(
            "UPDATE report_delivery_artifacts "
            "SET checker_version=? WHERE artifact_id=?",
            ("automatic-release-v999", persisted.artifact.artifact_id),
        )

    renderer_calls: list[str] = []

    def forbidden_renderer(*_args, **_kwargs):
        renderer_calls.append("called")
        raise AssertionError("변조된 artifact를 현재 renderer로 복구했습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden_renderer)
    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden_renderer)
    monkeypatch.setattr(reports_router, "automatic_release_pdf", forbidden_renderer)
    job_runtime._JOBS.clear()

    db_path = storage_db.default_db_path()
    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        before = _database_dump(db_path)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        after = _database_dump(db_path)

    assert result.status_code == pdf.status_code == 503
    assert renderer_calls == []
    assert after == before


@pytest.mark.parametrize("tampered_part", ("report_body", "pdf_bytes"))
def test_저장본문이나_PDF_hash불일치는_재생성없이_닫는다(
    monkeypatch,
    tmp_path: Path,
    tampered_part: str,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    data_root = tmp_path / "artifact-data"
    monkeypatch.setenv("APP_DATA_ROOT", str(data_root))
    persisted = reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="public",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
    )
    assert persisted.artifact is not None
    assert persisted.artifact.blob_pointer is not None

    if tampered_part == "report_body":
        with storage_db.connect() as conn:
            conn.execute(
                f"UPDATE {delivery_store.TABLE_CONTENT_SNAPSHOTS} "
                "SET payload=? WHERE content_id=?",
                (b"{}", persisted.content.content_id),
            )
    else:
        blob_path = (
            data_root
            / "report-artifacts"
            / persisted.artifact.blob_pointer.key
        )
        blob_path.write_bytes(blob_path.read_bytes() + b"\ntampered")

    renderer_calls: list[str] = []

    def forbidden_renderer(*_args, **_kwargs):
        renderer_calls.append("called")
        raise AssertionError("손상된 저장본을 다시 렌더링해 덮었습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden_renderer)
    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden_renderer)
    monkeypatch.setattr(reports_router, "automatic_release_pdf", forbidden_renderer)
    job_runtime._JOBS.clear()

    db_path = storage_db.default_db_path()
    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        before = _database_dump(db_path)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        after = _database_dump(db_path)

    assert result.status_code == pdf.status_code == 503
    assert renderer_calls == []
    assert after == before


def test_delivery없는_과거보고서는_현재renderer로_PDF를_새로만들지않는다(monkeypatch):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "legacy-corp", report.job, report)

    calls: list[str] = []

    def forbidden_legacy_release(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("과거 PDF를 오늘 renderer로 다시 만들었습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden_legacy_release)
    job_runtime._JOBS.clear()

    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        response = client.get(
            f"/download/pdf/{report_id}", follow_redirects=False
        )

    assert response.status_code == 410
    assert calls == []
    assert "PDF 원본은 확인할 수 없습니다" in response.text
    assert response.headers["x-pdf-artifact-status"] == "legacy-original-unknown"
    assert "content-disposition" not in response.headers
    assert "x-pdf-artifact-id" not in response.headers


def test_실패한_새보고서는_재시작뒤에도_legacy재렌더로_우회하지않는다(
    monkeypatch,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "new-corp", report.job, report)

    monkeypatch.setattr(
        reports_router,
        "_candidate_for_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("시험용 최초 PDF 확정 실패")
        ),
    )
    with pytest.raises(RuntimeError, match="최초 PDF 확정 실패"):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="new-corp",
            billing_bucket_id="public",
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=False,
        )
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_FAILED

    renderer_calls: list[str] = []

    def forbidden_renderer(*_args, **_kwargs):
        renderer_calls.append("called")
        raise AssertionError("실패한 새 보고서를 legacy PDF로 다시 만들었습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden_renderer)
    monkeypatch.setattr(reports_router, "_candidate_for_report", forbidden_renderer)
    monkeypatch.setattr(reports_router, "prepare_pdf_release", forbidden_renderer)
    job_runtime._JOBS.clear()

    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)

    assert result.status_code == pdf.status_code == 503
    assert renderer_calls == []


def test_blob저장후_후속DB장애는_별도intent로_시작복구된다(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report_id = f"blob-rollback-{uuid.uuid4().hex}"
    report = _demo_report()
    artifact_data_root = tmp_path / "artifact-data"
    monkeypatch.setenv("APP_DATA_ROOT", str(artifact_data_root))

    def fail_after_artifact(*_args, **_kwargs):
        raise RuntimeError("시험용 artifact 후속 DB 장애")

    monkeypatch.setattr(
        reports_router.cost_store,
        "mark_automatic_release",
        fail_after_artifact,
    )
    with pytest.raises(RuntimeError, match="artifact 후속 DB 장애"):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="demo-corp",
            billing_bucket_id="public",
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=False,
        )

    with storage_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT intents.intent_id, intents.blob_key, intents.created_at,
                   events.event_type
            FROM {delivery_artifact.TABLE_BLOB_INTENTS} AS intents
            JOIN {delivery_artifact.TABLE_BLOB_INTENT_EVENTS} AS events
              ON events.event_id = (
                  SELECT MAX(latest.event_id)
                  FROM {delivery_artifact.TABLE_BLOB_INTENT_EVENTS} AS latest
                  WHERE latest.intent_id = intents.intent_id
              )
            """
        ).fetchall()
        assert conn.execute(
            f"SELECT COUNT(*) FROM {delivery_artifact.TABLE_ARTIFACTS}"
        ).fetchone()[0] == 0
    assert len(rows) == 1
    assert str(rows[0][3]) == "created"
    blob_path = artifact_data_root / "report-artifacts" / str(rows[0][1])
    assert blob_path.is_file()
    intent_created_at = delivery_artifact.datetime_from_utc_text(
        str(rows[0][2]),
        label="시험 intent",
    )

    recovered = report_delivery_adapter.reconcile_configured_artifact_blob_intents(
        now=intent_created_at + dt.timedelta(days=1, microseconds=1),
    )

    assert recovered.deleted == 1
    assert not blob_path.exists()


def test_출고본문계약차단은_저장장애503으로_바뀌지않고_GET도409를_읽기만한다(
    monkeypatch,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "new-corp", report.job, report)

    validation_calls: list[str] = []

    def publication_blocked(_report):
        validation_calls.append("called")
        raise PublishBlockedError(
            PublishValidation(False, reasons=("시험용 공개 계약 차단",))
        )

    monkeypatch.setattr(reports_router, "_report_for_output", publication_blocked)
    with pytest.raises(PublishBlockedError):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="new-corp",
            billing_bucket_id="public",
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=False,
        )
    assert validation_calls == ["called"]
    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_FAILED
    assert intent.failure_code == "publication_contract_blocked"

    # GET은 실패 원인을 다시 계산하지 않고 기계 코드만 읽어 같은 409를 낸다.
    validation_calls.clear()
    job_runtime._JOBS.clear()
    db_path = storage_db.default_db_path()
    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        before = _database_dump(db_path)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
        after = _database_dump(db_path)

    assert result.status_code == pdf.status_code == 409
    assert "현재 보고서 기준을 통과한 근거가 충분하지 않아" in result.text
    assert "현재 보고서 기준을 통과한 근거가 충분하지 않아" in pdf.text
    assert validation_calls == []
    assert after == before


@pytest.mark.parametrize(
    ("blocked_error", "expected_code"),
    (
        (
            PdfRenderBlockedError("시험용 PDF 후보 생성 차단"),
            "pdf_render_contract_blocked",
        ),
        (
            AutomaticGateStopped(("시험용 자동검사 차단",)),
            "automatic_release_gate_blocked",
        ),
    ),
)
def test_PDF후보생성과_자동검사차단은_서로다른_영속코드로_남는다(
    monkeypatch,
    blocked_error,
    expected_code: str,
):
    report_id = uuid.uuid4().hex
    report = _demo_report()

    monkeypatch.setattr(reports_router, "_report_for_output", lambda value: value)
    if isinstance(blocked_error, PdfRenderBlockedError):
        monkeypatch.setattr(
            reports_router,
            "_candidate_for_report",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(blocked_error),
        )
    else:
        monkeypatch.setattr(
            reports_router,
            "_candidate_for_report",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(
            reports_router,
            "automatic_release_pdf",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(blocked_error),
        )

    with pytest.raises(type(blocked_error)):
        reports_router.finalize_new_report_delivery(
            report_id=report_id,
            corp_id="new-corp",
            billing_bucket_id="public",
            report=report,
            actual_models=("deterministic-demo",),
            reused_from_cache=False,
        )

    with storage_db.connect() as conn:
        intent = delivery_store.load_delivery_intent(conn, report_id)
    assert intent is not None
    assert intent.failure_code == expected_code

    monkeypatch.setattr(
        reports_router,
        "_report_for_output",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET이 PDF 차단을 다시 계산했습니다")
        ),
    )
    job_runtime._JOBS.clear()
    with TestClient(app, base_url="https://testserver") as client:
        _authorize_current_admin(client)
        result = client.get(f"/result/{report_id}", follow_redirects=False)
        pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)

    assert result.status_code == pdf.status_code == 409
