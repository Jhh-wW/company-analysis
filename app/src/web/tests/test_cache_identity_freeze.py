"""생성 시작의 빌드 신원이 Job·세션·pipeline 문맥에서 바뀌지 않는지 지킨다."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.core import deployment_identity
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
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
    assert job.generation_session.engine_build_identity is frozen

    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)
    job_runtime._prepare_generation_session(job)

    assert job.engine_build_identity is frozen
    assert job.generation_session.engine_build_identity is frozen
    with generation_coordination.activate(job.generation_session.callbacks):
        assert generation_coordination.frozen_engine_build_identity() is frozen


def test_unknown도_뒤에_commit이_생겨도_Job과세션에서_승격하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    job = _paid_job("freeze-unknown")

    job_runtime._prepare_generation_session(job)
    frozen = job.engine_build_identity
    assert frozen is not None and not frozen.cache_usable

    monkeypatch.setenv("RENDER_GIT_COMMIT", "c" * 40)
    job_runtime._prepare_generation_session(job)

    assert job.engine_build_identity is frozen
    assert job.generation_session.engine_build_identity is frozen
    assert not job.engine_build_identity.cache_usable


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


def test_구형보고서_저장전에도_A에서_B로_바뀐_배포를_거절한다(
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

    with pytest.raises(
        report_delivery_adapter.DeliveryAdapterError,
        match="생성 시작과 출고 시점",
    ):
        _REAL_REQUIRE_REPORT_DELIVERY(job)

    assert marker_calls == []


@pytest.mark.parametrize(
    ("start_commit", "current_commit"),
    (("a" * 40, "b" * 40), ("a" * 40, ""), ("", "b" * 40)),
)
def test_legacy_report_insert는_신원drift때_행을_남기지_않는다(
    start_commit: str,
    current_commit: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    if start_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", start_commit)
    start_label = start_commit[:1] or "unknown"
    current_label = current_commit[:1] or "unknown"
    job = _paid_job(f"legacy-drift-{start_label}-{current_label}")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)
    if current_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", current_commit)
    else:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    assert job_runtime._save_report(job) is False
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) is None


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


def test_legacy_report_INSERT도중_A에서_B로_바뀌면_모두_rollback한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_deferred_pipeline(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    job = _paid_job("legacy-mid-insert-drift")
    job.result = RunResult(outcome=Outcome.REPORT, report=build_demo_report())
    job_runtime._prepare_generation_session(job)
    assert job.engine_build_identity is not None
    build_b = build_identity_contract.EngineBuildIdentity(
        "b" * 40,
        f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{'b' * 40}",
    )
    captures = iter((job.engine_build_identity, build_b))
    monkeypatch.setattr(
        build_identity_contract,
        "capture_engine_build_identity",
        lambda: next(captures),
    )

    assert job_runtime._save_report(job) is False
    with storage_db.connect() as conn:
        assert report_store.load(conn, job.job_id) is None
