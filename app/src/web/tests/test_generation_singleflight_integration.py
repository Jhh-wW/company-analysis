"""실제 Job→pipeline→delivery 경로의 billing-bucket 단일 실행 계약."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import threading
from types import SimpleNamespace

import pytest

from src.core.constants import MAX_AI_CALLS_PER_REQUEST
from src.features.budget.constants import PAID_PHASE_LEASE_SEC
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline.constants import ANTHROPIC_TIMEOUT_SEC
from src.features.pipeline.port import (
    CompanyCard,
    Grade,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery import singleflight
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery.cache_identity import CacheLookupKey, CacheNamespace
from src.features.report_delivery.models import ContentSnapshot, Delivery, DeliveryPolicy
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import generation_coordination
from src.shared.report_source_identity import ReportSourceIdentity
from src.web import generation_singleflight, job_runtime, paid_runtime, runtime


_RECEIPT = "20260828000123"
_FINANCIAL_DIGEST = "b" * 64
_NAMESPACE = CacheNamespace.create(
    product="company-analysis",
    schema_version="company-report-v2-composer",
    deployment_revision="05dfb49",
    requested_models={"pipeline": "claude-test"},
    output_settings={"temperature": 0},
)
_NAMESPACE_ID = _NAMESPACE.namespace_id


def _report() -> Report:
    return Report(
        company="테스트전자",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
        generated_at="2026-08-28",
        schema_version="company-report-v2-composer",
        as_of_date="2026-08-28",
    )


def _source_digest() -> str:
    return ReportSourceIdentity(
        dart_receipt_numbers=(_RECEIPT,),
        financial_payload_digest=_FINANCIAL_DIGEST,
    ).cache_digest


def _session(
    run_id: str,
    bucket: str,
    *,
    on_paid_phase=lambda _ticket: None,
) -> generation_singleflight.GenerationSession:
    return generation_singleflight.GenerationSession(
        run_id=run_id,
        share_key=f"share:{bucket}",
        billing_bucket_id=bucket,
        cap_krw=900.0,
        on_paid_phase=on_paid_phase,
    )


def _persist_shared_content(
    report: Report,
    *,
    artifact_root,
) -> tuple[ContentSnapshot, delivery_artifact.ArtifactMetadata]:
    captured = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    source = SourceSnapshot.capture(
        dart_receipt_nos=(_RECEIPT,),
        financial_payload=None,
        financial_payload_sha256=_FINANCIAL_DIGEST,
        captured_at=captured,
        source_as_of=captured.date(),
        adapter_versions={"report_delivery": "test-v1"},
    )
    content = ContentSnapshot.create(
        payload=report_store.report_to_json(report).encode("utf-8"),
        source_snapshot=source,
        cache_namespace=_NAMESPACE,
        content_generated_at=captured,
        actual_models=("claude-test",),
    )
    with storage_db.connect() as conn:
        delivery_store.save_source_snapshot(conn, source)
        delivery_store.save_cache_namespace(conn, _NAMESPACE)
        delivery_store.save_content_snapshot(conn, content)
        backend = delivery_artifact.FilesystemArtifactBlobBackend(artifact_root)
        pdf_bytes = b"%PDF-1.4\n% single-flight immutable test\n"
        intent = delivery_artifact.create_blob_write_intent(
            conn,
            backend,
            pdf_bytes=pdf_bytes,
            created_at=captured,
        )
        artifact = delivery_artifact.store_approved_pdf(
            conn,
            backend,
            blob_intent=intent,
            content_snapshot_id=content.content_id,
            pdf_bytes=pdf_bytes,
            version=delivery_artifact.ArtifactVersion(
                renderer_version="test-renderer-v1",
                font_bundle_version="test-font-v1",
                checker_version="automatic-checks-v1",
            ),
            created_at=captured,
            retention=delivery_artifact.ArtifactRetention(
                policy_id="test-retention-v1",
                retain_until=None,
            ),
        )
    return content, artifact


def test_같은통장의_동시job은_provider한번만_쓰고_각자delivery를_받는다(
    monkeypatch,
    tmp_path,
) -> None:
    report = _report()
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        pdf_release_store,
        "load_automatic_release_record",
        lambda *_args, **_kwargs: object(),
    )
    content, artifact = _persist_shared_content(
        report,
        artifact_root=tmp_path / "report-artifacts",
    )
    provider_calls = 0
    phase_begins: list[str] = []
    phase_settles: list[str] = []
    owner_entered = threading.Event()
    release_owner = threading.Event()
    lock = threading.Lock()

    class DeferredPipeline:
        supports_deferred_paid_phase = True

        def run(self, *_args, **_kwargs) -> RunResult:
            nonlocal provider_calls
            reused = generation_coordination.coordinate(
                corp_id="00126380",
                cache_namespace=_NAMESPACE,
                preflight_identity_digest=_source_digest(),
            )
            if reused is not None:
                return RunResult(
                    outcome=Outcome.REPORT,
                    report=reused.report,
                    cache_hit="1층",
                    model=" + ".join(reused.actual_models),
                    dart_receipt_numbers=(_RECEIPT,),
                    financial_payload_digest=_FINANCIAL_DIGEST,
                    reused_content_snapshot_id=reused.content_snapshot_id,
                    reused_artifact_id=reused.artifact_id,
                    generation_cache_eligible=(
                        reused.generation_cache_eligible
                    ),
                )
            generation_coordination.ensure_paid_phase()
            with lock:
                provider_calls += 1
            owner_entered.set()
            if not release_owner.wait(timeout=3):
                raise AssertionError("owner provider 가짜 호출을 제때 풀지 못했습니다")
            return RunResult(
                outcome=Outcome.REPORT,
                report=report,
                cost_krw=7.0,
                model="claude-test",
                dart_receipt_numbers=(_RECEIPT,),
                financial_payload_digest=_FINANCIAL_DIGEST,
                generation_cache_eligible=True,
            )

    def begin_phase(**kwargs):
        phase_begins.append(kwargs["run_id"])
        return SimpleNamespace(
            run_id=kwargs["run_id"],
            phase=kwargs["phase"],
            day=dt.date(2026, 8, 28),
            share_key=kwargs["share_key"],
            bucket_id="bucket-a",
            reserved_krw=900.0,
            lease_owner_id="",
        )

    def finalize(job: job_runtime.Job) -> bool:
        delivered_at = dt.datetime.now(dt.timezone.utc)
        delivery = Delivery.issue(
            public_id=job.job_id,
            billing_bucket_id=job.slot_bucket_id,
            content=content,
            delivered_at=delivered_at,
            policy=DeliveryPolicy(dt.timedelta(days=60), dt.timedelta(days=60)),
            reused_from_cache=bool(job.result and job.result.cache_hit),
        )
        with storage_db.connect() as conn:
            delivery_store.save_delivery(conn, delivery)
            delivery_artifact.bind_artifact_to_delivery(
                conn,
                delivery_id=delivery.delivery_id,
                artifact_id=artifact.artifact_id,
            )
            delivery_store.bind_cache_entry(
                conn,
                key=CacheLookupKey.from_preflight(
                    billing_bucket_id=job.slot_bucket_id,
                    corp_id="00126380",
                    namespace=_NAMESPACE,
                    preflight_identity_digest=_source_digest(),
                    preflight_cache_usable=True,
                ),
                content=content,
                artifact_id=artifact.artifact_id,
                cached_at=delivered_at,
            )
        job.delivery_content_id = content.content_id
        job.delivery_artifact_id = artifact.artifact_id
        return True

    monkeypatch.setattr(runtime, "_PIPELINE", DeferredPipeline())
    monkeypatch.setattr(paid_runtime, "_begin_paid_phase", begin_phase)
    monkeypatch.setattr(
        paid_runtime,
        "_activate_paid_provider",
        lambda _ticket: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        job_runtime,
        "_settle_paid_phase",
        lambda ticket, **_kwargs: phase_settles.append(ticket.run_id),
    )
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_runtime, "_require_report_delivery", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", finalize)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    def make_job(run_id: str) -> job_runtime.Job:
        return job_runtime.Job(
            job_id=run_id,
            user_input=UserInput(company="테스트전자", job="", region="서울"),
            card=CompanyCard(
                legal_name="테스트전자",
                typed_name="테스트전자",
                address="서울",
                ceo="대표",
                founded="20200101",
                ref="00126380",
            ),
            share_key="same-share",
            is_paid=True,
            paid_cap_krw=900.0,
            slot_bucket_id="bucket-a",
        )

    owner = make_job("singleflight-owner")
    waiter = make_job("singleflight-waiter")

    async def scenario() -> None:
        first = asyncio.create_task(job_runtime._run_job(owner))
        assert await asyncio.to_thread(owner_entered.wait, 2)
        second = asyncio.create_task(job_runtime._run_job(waiter))
        await asyncio.sleep(0.1)
        assert not second.done(), "waiter가 owner content 확정 전에 독립 생성했습니다"
        release_owner.set()
        await asyncio.gather(first, second)

    asyncio.run(scenario())

    assert provider_calls == 1
    assert phase_begins == [owner.job_id]
    assert phase_settles == [owner.job_id]
    assert owner.delivery_persisted is True
    assert waiter.delivery_persisted is True
    assert owner.result is not None and waiter.result is not None
    assert owner.result.report == waiter.result.report == report
    with storage_db.connect() as conn:
        assert delivery_store.delivery_count_for_content(conn, content.content_id) == 2
        assert {
            delivery_artifact.artifact_for_delivery(
                conn,
                delivery_id=delivery_store.load_delivery_by_public_id(
                    conn, public_id
                ).delivery_id,
            ).artifact_id
            for public_id in (owner.job_id, waiter.job_id)
        } == {artifact.artifact_id}
        assert (
            delivery_store.load_delivery_by_public_id(conn, owner.job_id)
            is not None
        )
        assert (
            delivery_store.load_delivery_by_public_id(conn, waiter.job_id)
            is not None
        )


def test_다른통장은_같은회사와출처여도_각자owner가된다() -> None:
    first = _session("owner-a", "bucket-a")
    second = _session("owner-b", "bucket-b")

    assert first.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert second.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert first.owns_generation
    assert second.owns_generation
    first.abandon()
    second.abandon()


def test_캐시불가_owner결과는_동시waiter에게만_짧게공유하고_다음조사는_새owner가된다(
    monkeypatch,
    tmp_path,
) -> None:
    """중복 과금은 막되 일시적 수집 실패를 장기 정상 캐시로 승격하지 않는다."""

    report = _report()
    content, artifact = _persist_shared_content(
        report,
        artifact_root=tmp_path / "uncacheable-fanout-artifacts",
    )
    owner = _session("uncacheable-owner", "bucket-a")
    assert owner.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert owner.owns_generation

    owner.complete(
        content.content_id,
        artifact.artifact_id,
        cache_eligible=False,
    )

    waiter = _session("uncacheable-waiter", "bucket-a")
    reused = waiter.coordinate("00126380", _NAMESPACE, _source_digest())
    assert reused is not None
    assert reused.content_snapshot_id == content.content_id
    assert reused.artifact_id == artifact.artifact_id

    cache_key = CacheLookupKey.from_preflight(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        namespace=_NAMESPACE,
        preflight_identity_digest=_source_digest(),
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

    # 2분짜리 동시 fan-out이 끝나면 같은 결과를 장기 재사용하지 않고 새 owner가 된다.
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=3)
    monkeypatch.setattr(generation_singleflight.clock, "now_kst", lambda: future)
    fresh = _session("uncacheable-fresh-owner", "bucket-a")
    assert fresh.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert fresh.owns_generation
    fresh.abandon()


@pytest.mark.parametrize(
    ("corp_id", "namespace", "source_digest"),
    (
        ("", _NAMESPACE, _source_digest()),
        ("00126380", None, _source_digest()),
        ("00126380", _NAMESPACE, ""),
    ),
)
def test_부분신원은_lease로공유하지_않는다(
    corp_id: str,
    namespace,
    source_digest: str,
) -> None:
    session = _session("partial-source", "bucket-a")

    assert session.coordinate(corp_id, namespace, source_digest) is None
    assert not session.owns_generation


def test_owner실패는_waiter에게_fanout되어_즉시재과금을_막는다() -> None:
    owner = _session("failed-owner", "bucket-a")
    assert owner.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    owner.fail("provider_timeout")
    waiter = _session("failed-waiter", "bucket-a")

    with pytest.raises(generation_coordination.GenerationOwnerFailed):
        waiter.coordinate("00126380", _NAMESPACE, _source_digest())


def test_waiter취소는_owner_lease를_건드리지_않는다() -> None:
    owner = _session("cancel-owner", "bucket-a")
    assert owner.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    waiter = _session("cancel-waiter", "bucket-a")
    errors: list[BaseException] = []

    def wait() -> None:
        try:
            waiter.coordinate("00126380", _NAMESPACE, _source_digest())
        except BaseException as exc:  # noqa: BLE001 - 시험이 형식을 확인한다
            errors.append(exc)

    thread = threading.Thread(target=wait)
    thread.start()
    waiter.cancel_waiter()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], generation_coordination.GenerationWaitCancelled)
    assert owner.owns_generation
    owner.abandon()


def test_waiter는_총대기한을_넘기면_owner를_건드리지않고_끝난다(
    monkeypatch,
) -> None:
    owner = _session("timeout-owner", "bucket-a")
    assert owner.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    waiter = _session("timeout-waiter", "bucket-a")
    monkeypatch.setattr(generation_singleflight, "WAITER_MAX_AGE_SEC", 0.01)

    started = dt.datetime.now(dt.timezone.utc)
    with pytest.raises(generation_coordination.GenerationWaitTimedOut):
        waiter.coordinate("00126380", _NAMESPACE, _source_digest())
    elapsed = dt.datetime.now(dt.timezone.utc) - started

    assert elapsed < dt.timedelta(seconds=1)
    assert owner.owns_generation
    owner.abandon()


def test_owner_heartbeat는_요청전체시작기준_절대마감을_넘기지않는다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_MAX_AGE",
        dt.timedelta(seconds=10),
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_PROVIDER_ADMISSION_AGE",
        dt.timedelta(seconds=6),
    )
    session = _session("bounded-heartbeat-owner", "bucket-a")
    assert session.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert session._handle is not None
    handle = session._handle

    assert session._execution_started_at is not None
    midpoint = session._execution_started_at + dt.timedelta(seconds=7)
    assert session._bounded_heartbeat_ttl(handle, midpoint) == dt.timedelta(seconds=3)
    with pytest.raises(
        generation_coordination.GenerationExecutionDeadlineExceeded
    ):
        session._bounded_heartbeat_ttl(
            handle,
            session._execution_started_at + dt.timedelta(seconds=10),
        )
    with pytest.raises(
        generation_coordination.GenerationExecutionDeadlineExceeded
    ):
        session._require_provider_admission_time(
            handle,
            session._execution_started_at + dt.timedelta(seconds=6),
        )
    session.abandon()


def test_owner마감은_기존_호출수_timeout_lease계약에_결속된다() -> None:
    assert generation_singleflight.OWNER_MAX_AGE == dt.timedelta(
        seconds=PAID_PHASE_LEASE_SEC
    )
    assert (
        generation_singleflight.OWNER_PROVIDER_ADMISSION_AGE
        + generation_singleflight.PROVIDER_IN_FLIGHT_GRACE
        == generation_singleflight.OWNER_MAX_AGE
    )
    assert (
        MAX_AI_CALLS_PER_REQUEST * ANTHROPIC_TIMEOUT_SEC
        <= generation_singleflight.OWNER_PROVIDER_ADMISSION_AGE.total_seconds()
    )
    assert generation_singleflight.WAITER_MAX_AGE_SEC == PAID_PHASE_LEASE_SEC


def test_preflight뒤_늦게_owner가돼도_전체마감은_다시시작하지않는다(
    monkeypatch,
) -> None:
    monotonic_now = [100.0]
    started_at = dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)
    wall_now = [started_at]
    monkeypatch.setattr(
        generation_singleflight.time,
        "monotonic",
        lambda: monotonic_now[0],
    )
    monkeypatch.setattr(
        generation_singleflight.clock,
        "now_kst",
        lambda: wall_now[0],
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_MAX_AGE",
        dt.timedelta(seconds=10),
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_PROVIDER_ADMISSION_AGE",
        dt.timedelta(seconds=6),
    )
    session = _session("late-owner", "bucket-a")

    # 무료 preflight가 5초 쓴 뒤 owner를 얻었다고 흉내 낸다.
    monotonic_now[0] = 105.0
    wall_now[0] = started_at + dt.timedelta(seconds=5)
    assert session.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert session._handle is not None
    provider_deadline, execution_deadline = session._owner_deadlines(
        session._handle
    )
    assert provider_deadline == started_at + dt.timedelta(seconds=6)
    assert execution_deadline == started_at + dt.timedelta(seconds=10)
    # owner를 5초 늦게 얻었어도 최초 lease가 새 10초를 받으면 안 된다.
    # DB에 저장된 만료도 요청 전체의 원래 10초 경계와 정확히 같아야 한다.
    assert session._handle.expires_at == execution_deadline

    monotonic_now[0] = 106.0
    wall_now[0] = started_at + dt.timedelta(seconds=6)
    with pytest.raises(
        generation_coordination.GenerationExecutionDeadlineExceeded
    ):
        session._require_provider_admission_time(session._handle, wall_now[0])
    session.abandon()


def test_벽시계가_뒤로가도_monotonic마감에서_provider와_heartbeat를_막는다(
    monkeypatch,
) -> None:
    monotonic_now = [100.0]
    monkeypatch.setattr(
        generation_singleflight.time,
        "monotonic",
        lambda: monotonic_now[0],
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_MAX_AGE",
        dt.timedelta(seconds=10),
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_PROVIDER_ADMISSION_AGE",
        dt.timedelta(seconds=6),
    )
    session = _session("clock-jump-owner", "bucket-a")
    assert session.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert session._handle is not None
    handle = session._handle
    backward_wall_clock = handle.acquired_at - dt.timedelta(days=1)

    monotonic_now[0] = 106.0
    with pytest.raises(
        generation_coordination.GenerationExecutionDeadlineExceeded
    ):
        session._require_provider_admission_time(handle, backward_wall_clock)

    monotonic_now[0] = 110.0
    with pytest.raises(
        generation_coordination.GenerationExecutionDeadlineExceeded
    ):
        session._bounded_heartbeat_ttl(handle, backward_wall_clock)
    session.abandon()


def test_부분지문_bypass도_전체마감에서_새provider를_시작하지않는다(
    monkeypatch,
) -> None:
    monotonic_now = [100.0]
    wall_now = [dt.datetime(2026, 8, 28, 0, 0, tzinfo=dt.timezone.utc)]
    monkeypatch.setattr(
        generation_singleflight.time,
        "monotonic",
        lambda: monotonic_now[0],
    )
    monkeypatch.setattr(
        generation_singleflight.clock,
        "now_kst",
        lambda: wall_now[0],
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_MAX_AGE",
        dt.timedelta(seconds=10),
    )
    monkeypatch.setattr(
        generation_singleflight,
        "OWNER_PROVIDER_ADMISSION_AGE",
        dt.timedelta(seconds=6),
    )
    session = _session("deadline-bypass", "bucket-a")
    assert session.coordinate("", None, "") is None
    monkeypatch.setattr(
        paid_runtime,
        "_begin_paid_phase",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("마감 뒤 비용 phase를 열면 안 됩니다")
        ),
    )

    monotonic_now[0] = 106.0
    wall_now[0] += dt.timedelta(seconds=6)
    with pytest.raises(
        generation_coordination.GenerationExecutionDeadlineExceeded
    ):
        session.ensure_paid_phase()


def test_만료된_owner는_더높은_fencing_token으로_takeover한다() -> None:
    key = singleflight.LeaseKey(
        billing_bucket_id="bucket-a",
        corp_id="00126380",
        cache_namespace_id=_NAMESPACE_ID,
        source_identity_digest=_source_digest(),
    )
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    with storage_db.connect() as conn:
        crashed = singleflight.acquire(
            conn,
            key=key,
            owner_id="crashed-owner",
            now=old,
            lease_ttl=dt.timedelta(seconds=1),
        )
    assert crashed.handle is not None

    recovery = _session("recovery-owner", "bucket-a")
    assert recovery.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    assert recovery.owns_generation
    assert recovery._handle is not None
    assert recovery._handle.fencing_token == crashed.handle.fencing_token + 1
    recovery.abandon()


def test_lease_DB를_확인할수없으면_provider를_열지_않는다(
    monkeypatch,
) -> None:
    session = _session("db-failed", "bucket-a")

    def broken_connect():
        raise OSError("DB unavailable")

    monkeypatch.setattr(generation_singleflight.storage_db, "connect", broken_connect)

    with pytest.raises(generation_singleflight.GenerationSingleflightUnavailable):
        session.coordinate("00126380", _NAMESPACE, _source_digest())
    assert session.paid_phase is None


def test_첫provider뒤_heartbeat오류가나면_다음provider는_열지않는다(
    monkeypatch,
) -> None:
    paid_phase = paid_runtime.PaidPhase(
        run_id="heartbeat-owner",
        phase="pipeline",
        day=dt.date(2026, 8, 28),
        share_key="share:bucket-a",
        bucket_id="bucket-a",
        reserved_krw=900.0,
    )
    monkeypatch.setattr(
        paid_runtime,
        "_begin_paid_phase",
        lambda **_kwargs: paid_phase,
    )
    monkeypatch.setattr(
        paid_runtime,
        "_activate_paid_provider",
        lambda _ticket: contextlib.nullcontext(),
    )
    session = _session("heartbeat-owner", "bucket-a")
    assert session.coordinate("00126380", _NAMESPACE, _source_digest()) is None
    provider_calls = 0

    def call_provider() -> None:
        nonlocal provider_calls
        session.ensure_paid_phase()
        provider_calls += 1

    call_provider()
    with session._lock:
        session._lease_error = OSError("heartbeat DB unavailable")

    with pytest.raises(generation_singleflight.GenerationSingleflightUnavailable):
        call_provider()

    assert provider_calls == 1
    session.close_provider_context()
    session.abandon()
