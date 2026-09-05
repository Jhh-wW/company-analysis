"""DART 재무 API 자료가 없는 비상장사의 worker 완료 회귀 시험.

2026-09-05 인이지 실측 사고의 재현 방지선이다. 감사보고서만 제출하는
비상장사는 DART 주요계정 API가 세 사업연도 모두 「조회된 데이터 없음(013)」을
답한다. 그러면 ``RunResult.financial_payload_digest``가 비고
``ReportSourceIdentity.cache_usable``이 False가 된다. 그 상태로도 AI 작성이
끝난 보고서는 저장·Delivery·PDF·publish까지 한 거래로 확정되고 결과 화면이
열려야 한다.

``test_report_delivery_integration.py``의 같은 회귀 시험은
``finalize_new_report_delivery``만 직접 부른다. 이 파일은 그보다 한 겹 위인
``job_runtime._run_job`` worker 전체를 돌려, 파이프라인 결과가 실제 worker
합성 순서를 지나 ``/api/progress``·``/result``까지 도달하는지 확인한다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.report_access.models import ReportAudience
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.storage import db as storage_db
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_source_identity import ReportSourceIdentity
from src.web import job_runtime, report_delivery_adapter, runtime
from src.web.main import app


#: 실측 회사와 같은 모양의 14자리 접수번호. 원문·회사명은 싣지 않는다.
_RECEIPT = "20260406001240"
#: 공식 자료 snapshot SHA-256 자리. 값 자체는 시험 안에서만 의미가 있다.
_OFFICIAL_SNAPSHOT_SHA256 = "b" * 64


@pytest.fixture(autouse=True)
def _검증된_배포에서_worker를_돌린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """출고 권위는 정상 full commit에서만 발급된다 — 로컬 shell 환경에 기대지 않는다."""

    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


def _재무자료없는_보고서():
    """실측 회사와 같은 갈래(SHADOW·비상장 외감)로 맞춘 데모 본문."""

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
    return dataclasses.replace(
        result.report,
        release_mode=ReleaseMode.SHADOW.value,
        corp_type="비상장 외감",
    )


def _생성_전_지문() -> str:
    """접수번호와 공식 snapshot만으로 세운 「재무 없음」 생성 신원."""

    identity = ReportSourceIdentity(dart_receipt_numbers=(_RECEIPT,))
    # 재무 도장이 없으므로 캐시 재사용 열쇠는 서지 않는다 — 이게 이 부류의 정의다.
    assert identity.cache_usable is False
    assert identity.cache_digest == ""
    digest = identity.generation_digest_without_financials(_OFFICIAL_SNAPSHOT_SHA256)
    assert len(digest) == 64
    return digest


def _생성세션(report, preflight_digest: str):
    """worker가 출고에 운반하는 namespace·지문만 가진 최소 세션 대역.

    실제 ``GenerationSession``은 lease·유료 phase까지 소유한다. 이 시험은
    유료 경로가 아니라 «완료 뒤 출고 결속»을 보므로, worker가 실제로 읽는
    속성과 마감 호출만 그대로 흉내 낸다.
    """

    revision, image = report_delivery_adapter._release_identity(
        build_identity_contract.process_engine_build_identity()
    )
    namespace = CacheNamespace.create(
        product="company-analysis",
        schema_version=report.schema_version or "legacy-report-schema",
        deployment_revision=revision,
        image_digest=image,
        requested_models={"pipeline": "deterministic-demo"},
        output_settings={"temperature": 0},
    )
    완료호출: list[tuple[str, str, bool]] = []

    def complete(content_id: str, artifact_id: str, *, cache_eligible: bool) -> None:
        완료호출.append((content_id, artifact_id, cache_eligible))

    session = SimpleNamespace(
        cache_namespace=namespace,
        preflight_identity_digest=preflight_digest,
        completed_reuse_key=None,
        engine_build_identity=(
            build_identity_contract.process_engine_build_identity()
        ),
        owns_generation=False,
        complete=complete,
        fail=lambda _reason: None,
        abandon=lambda: None,
        cancel_waiter=lambda: None,
        close_provider_context=lambda: None,
        완료호출=완료호출,
    )
    return session, namespace


def test_재무자료없는_비상장사도_worker가_결과화면까지_연다(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """재무 도장 없는 RunResult로 worker를 끝내도 결과가 공개돼야 한다.

    1차 고침 뒤 실패가 사전검사에서 출고 직전으로 옮겨 갔던 사고를 worker
    합성 순서 전체로 막는다. AI 비용을 다 쓴 뒤 「보고서 저장의 마지막 확인이
    끝나지 않아」로 닫히면 이 시험이 깨진다.
    """

    report = _재무자료없는_보고서()
    report_id = uuid.uuid4().hex
    preflight_digest = _생성_전_지문()

    class _재무없는_파이프라인:
        """실측 회사의 RunResult 모양을 그대로 돌려주는 가짜 파이프라인."""

        @staticmethod
        def run(*_args, **_kwargs) -> RunResult:
            return RunResult(
                outcome=Outcome.REPORT,
                report=report,
                corp_type=report.corp_type,
                dart_receipt_numbers=(_RECEIPT,),
                # DART 주요계정 API가 세 사업연도 모두 자료 없음을 답했다.
                financial_payload_digest="",
                # 재무·실적표가 없으니 장기 캐시에 묶을 자격도 없다.
                generation_cache_eligible=False,
            )

    job = job_runtime.Job(
        job_id=report_id,
        user_input=UserInput(company=report.company, job=report.job, region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
            ref="absent-financials-corp",
        ),
        report_audience=ReportAudience.ADMIN,
        engine_build_identity=(
            build_identity_contract.process_engine_build_identity()
        ),
    )
    session, namespace = _생성세션(report, preflight_digest)
    job.generation_session = session

    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "absent-financials-worker"))
    monkeypatch.setattr(runtime, "_PIPELINE", _재무없는_파이프라인())
    monkeypatch.setattr(job_runtime, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    # ── worker 자체가 성공으로 닫혔는가 ────────────────────
    assert job.result is not None
    assert job.result.outcome is Outcome.REPORT, job.result.message
    assert job.result.financial_payload_digest == ""
    assert job.report_persisted is True
    assert job.delivery_persisted is True
    assert session.완료호출 == [
        (job.delivery_content_id, job.delivery_artifact_id, False)
    ]

    # ── 출고물이 실제로 저장됐는가 ────────────────────────
    stored = report_delivery_adapter.load_public_delivery(report_id)
    intent = report_delivery_adapter.load_public_delivery_intent(report_id)
    assert stored is not None
    assert intent is not None
    assert intent.state == delivery_store.DELIVERY_INTENT_COMPLETE

    with storage_db.connect() as conn:
        source = delivery_store.load_source_snapshot(
            conn, stored.content.source_snapshot_id
        )
    assert source is not None
    # 생성 전 지문은 그대로 봉인하되, 캐시 재사용 열쇠는 계속 닫혀 있어야 한다.
    assert source.preflight_identity_digest == preflight_digest
    assert source.financial_payload_sha256 == ""
    assert source.dart_receipt_nos == (_RECEIPT,)
    assert source.cache_usable is False
    assert stored.content.cache_namespace_id == namespace.namespace_id

    # ── 화면 경계가 「출고 완료」로 읽는가 ──────────────────
    assert job_runtime._load_saved_report(report_id) is not None

    job_runtime._JOBS[report_id] = job
    try:
        with TestClient(app, base_url="https://testserver") as client:
            login = auth_logic.create_session(
                "admin@example.com",
                True,
                subject="test:absent-financials-worker",
            )
            client.cookies.set(auth_constants.SESSION_COOKIE_NAME, login.token)
            progress = client.get(
                f"/api/progress/{report_id}", follow_redirects=False
            )
            result = client.get(f"/result/{report_id}", follow_redirects=False)
    finally:
        job_runtime._JOBS.pop(report_id, None)

    assert progress.status_code == 200, progress.text
    assert progress.json().get("code") != "report_not_published"
    assert progress.json()["finished"] is True
    assert progress.json()["next_url"] == f"/result/{report_id}"
    assert result.status_code == 200, result.text
