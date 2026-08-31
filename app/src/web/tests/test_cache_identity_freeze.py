"""생성 시작의 빌드 신원이 Job·세션·pipeline 문맥에서 바뀌지 않는지 지킨다."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core import deployment_identity
from src.features.composer import build_id as composer_build_id
from src.features.pipeline.port import CompanyCard, UserInput
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

    job.engine_build_identity = composer_build_id.EngineBuildIdentity(
        deployment_revision="b" * 40,
        build_id=(
            f"{composer_build_id.ENGINE_BUILD_ID_CONTRACT_VERSION}:"
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
