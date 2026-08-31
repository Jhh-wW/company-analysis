"""생성 시작의 빌드 신원이 Job·세션·pipeline 문맥에서 바뀌지 않는지 지킨다."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.core import clock, deployment_identity
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.report_access.models import ReportAudience
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination
from src.web import job_runtime, report_delivery_adapter, runtime


_REAL_REQUIRE_REPORT_DELIVERY = job_runtime._require_report_delivery


def _paid_job(job_id: str) -> job_runtime.Job:
    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company="가나다전자", job="", region="서울"),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울",
            ceo="대표",
            founded="20200101",
            ref="00126380",
        ),
        share_key="cache-identity-test",
        is_paid=True,
        paid_cap_krw=900.0,
        slot_bucket_id="cache-identity-bucket",
        report_audience=ReportAudience.ADMIN,
        delivery_issued_at=clock.now_kst(),
    )


def _enable_deferred_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime,
        "_PIPELINE",
        SimpleNamespace(supports_deferred_paid_phase=True),
    )


def test_Job은_생성시작_A를_세션과_pipeline문맥까지_그대로_운반한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.delenv("APP_GIT_COMMIT", raising=False)
    job = _paid_job("freeze-a")

    job_runtime._prepare_generation_session(job)
    frozen = job.engine_build_identity
    assert frozen is not None and frozen.deployment_revision == "a" * 40
    assert job.generation_session is not None
    # 세션 경계는 전달받은 객체를 그대로 믿지 않고 canonical wire를 다시
    # 파싱한다. 값과 epoch는 같되 caller 객체 alias는 남기지 않는다.
    assert job.generation_session.engine_build_identity == frozen
    assert type(job.generation_session.engine_build_identity) is (
        build_identity_contract.EngineBuildIdentity
    )

    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    job_runtime._prepare_generation_session(job)

    assert job.engine_build_identity is frozen
    assert job.generation_session.engine_build_identity == frozen
    with generation_coordination.activate(job.generation_session.callbacks):
        assert generation_coordination.frozen_engine_build_identity() == frozen


@pytest.mark.parametrize(
    "injected_identity",
    (
        build_identity_contract.EngineBuildIdentity(
            "b" * 40,
            f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{'b' * 40}",
        ),
        build_identity_contract.EngineBuildIdentity("", "unknown"),
        SimpleNamespace(
            deployment_revision="a" * 40,
            build_id=(
                f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:"
                f"{'a' * 40}"
            ),
            wire="forged",
            epoch_digest="a" * 64,
            cache_usable=True,
        ),
    ),
)
def test_조정callback은_다른epoch_unknown_가짜객체로_provider문맥을_열수없다(
    injected_identity,
) -> None:
    build_identity_contract.freeze_process_engine_build_identity(
        build_identity_contract.EngineBuildIdentity(
            "a" * 40,
            f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{'a' * 40}",
        )
    )
    callbacks = generation_coordination.GenerationCallbacks(
        coordinate=lambda *_args: None,
        ensure_paid_phase=lambda: None,
        engine_build_identity=injected_identity,
    )

    with pytest.raises((TypeError, build_identity_contract.EngineBuildIdentityChangedError)):
        with generation_coordination.activate(callbacks):
            raise AssertionError("가짜 생성 callback이 활성화됐습니다")


def test_unknown은_유료세션을_열지않고_뒤에_commit이_생겨도_승격하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    job = _paid_job("freeze-unknown")

    with pytest.raises(TypeError, match="정상 engine epoch"):
        job_runtime._prepare_generation_session(job)
    frozen = job.engine_build_identity
    assert frozen is not None and not frozen.cache_usable
    assert job.generation_session is None

    monkeypatch.setenv("RENDER_GIT_COMMIT", "c" * 40)
    with pytest.raises(TypeError, match="정상 engine epoch"):
        job_runtime._prepare_generation_session(job)

    assert job.engine_build_identity is frozen
    assert not job.engine_build_identity.cache_usable
    assert job.generation_session is None


def test_legacy유료pipeline도_unknown_epoch로_provider를_열수없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    job = _paid_job("legacy-provider-unknown")
    monkeypatch.setattr(
        runtime,
        "_PIPELINE",
        SimpleNamespace(
            supports_deferred_paid_phase=False,
            run=lambda *_args: (_ for _ in ()).throw(
                AssertionError("unknown epoch로 provider를 호출했습니다")
            ),
        ),
    )
    monkeypatch.setattr(
        job_runtime,
        "_begin_paid_phase",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("epoch 검증 전에 비용 phase를 열었습니다")
        ),
    )
    job_runtime._prepare_generation_session(job)

    with pytest.raises(RuntimeError, match="epoch를 캐시·출고에 사용할 수 없습니다"):
        job_runtime._run_pipeline_worker(job)


def test_Job신원을_세션과_다르게_바꾸면_저장경계에서_거절한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("freeze-conflict")
    job_runtime._prepare_generation_session(job)
    assert job.generation_session is not None

    job.engine_build_identity = build_identity_contract.EngineBuildIdentity(
        deployment_revision="b" * 40,
        build_id=(
            f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:"
            f"{'b' * 40}"
        ),
    )

    with pytest.raises(RuntimeError, match="Job과 생성 세션"):
        job_runtime._frozen_job_build_identity(job)


def test_요청도중_raw환경_A에서_B로_바뀌어도_process_A를_유지한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("freeze-before-storage")
    job_runtime._prepare_generation_session(job)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    marker_calls: list[str] = []
    monkeypatch.setattr(
        report_delivery_adapter,
        "require_public_delivery",
        lambda *_args, **_kwargs: marker_calls.append("called"),
    )

    assert _REAL_REQUIRE_REPORT_DELIVERY(job) is True

    assert marker_calls == ["called"]
    assert job.engine_build_identity is not None
    assert job.engine_build_identity.deployment_revision == "a" * 40


@pytest.mark.parametrize("current_commit", ("b" * 40, ""))
def test_legacy_report_insert는_raw환경이_바뀌어도_동결epoch로_저장한다(
    current_commit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    start_commit = "a" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", start_commit)
    start_label = "a"
    current_label = current_commit[:1] or "unknown"
    job = _paid_job(f"legacy-drift-{start_label}-{current_label}")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)
    if current_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", current_commit)
    else:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    assert job_runtime._save_report(job) is True
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) == job.result.report
        assert report_store.engine_epoch_digest(conn, job.job_id) == (
            job.engine_build_identity.epoch_digest
        )


def test_legacy_report는_A가_유지되면_commit한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("legacy-stable-a")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)

    assert job_runtime._save_report(job) is True
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) == job.result.report


def test_legacy_report_commit실패는_모든행을_rollback한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("legacy-commit-failure")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)

    def fail_commit(_conn) -> None:
        raise sqlite3.OperationalError("주입한 commit 실패")

    monkeypatch.setattr(job_runtime, "_commit_report_connection", fail_commit)
    assert job_runtime._save_report(job) is False
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) is None


def test_legacy_report_INSERT도중에도_raw환경을_다시_capture하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("legacy-mid-insert-drift")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)
    assert job.engine_build_identity is not None
    capture_calls: list[str] = []

    def forbidden_capture(*_args, **_kwargs):
        capture_calls.append("called")
        raise AssertionError("요청 도중 raw 환경을 다시 읽었습니다")

    monkeypatch.setattr(
        build_identity_contract,
        "capture_engine_build_identity",
        forbidden_capture,
    )

    assert job_runtime._save_report(job) is True
    assert capture_calls == []
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) == job.result.report
        assert report_store.engine_epoch_digest(conn, job.job_id) == (
            job.engine_build_identity.epoch_digest
        )


def test_legacy_report_commit응답만_잃으면_exact_epoch행을_성공으로_복구한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("legacy-commit-response-loss")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)

    def commit_then_lose_response(conn) -> None:
        conn.commit()
        raise sqlite3.OperationalError("commit 응답만 손실")

    monkeypatch.setattr(
        job_runtime,
        "_commit_report_connection",
        commit_then_lose_response,
    )

    assert job_runtime._save_report(job) is True
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) == job.result.report
        assert report_store.engine_epoch_digest(conn, job.job_id) == (
            job.engine_build_identity.epoch_digest
        )
