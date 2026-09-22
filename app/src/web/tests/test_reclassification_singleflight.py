"""실제 세션·계량·비용 원장에서 재판정 준비와 최종 생성을 검증한다."""

from __future__ import annotations

import threading
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.features.budget import provider_budget, spend_store, state_machine
from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.pipeline import evidence_reclassify_step as step, real
from src.features.pipeline.tests.test_evidence_reclassify_step import (
    COMPANY_ID, MODEL, FakeMessages, _reclassifiable_result, _response,
)
from src.features.report_delivery import singleflight
from src.features.report_delivery import store as delivery_store
from src.features.report_delivery import artifact as delivery_artifact
from src.features.report_delivery.cache_identity import CacheLookupKey
from src.features.report_delivery.models import Delivery, DeliveryPolicy
from src.features.export_pdf import release_store
from src.features.storage import db as storage_db
from src.shared import generation_coordination
from src.web import generation_singleflight, paid_runtime
from src.web.tests.test_generation_singleflight_integration import (
    _BUILD_IDENTITY, _NAMESPACE, _persist_shared_content, _report, _source_digest,
)


WAIT_SECONDS = 10
TEST_CAP_KRW = 100_000.0
SHARE_KEY = "test:reclassification"


@pytest.fixture(autouse=True)
def _prepare(monkeypatch):
    monkeypatch.setattr(step, "evidence_reclassify_enabled", lambda: True)
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()


def _session(run_id, *, cap=TEST_CAP_KRW):
    return generation_singleflight.GenerationSession(
        run_id=run_id, share_key=SHARE_KEY,
        billing_bucket_id=spend_store.bucket_id(SHARE_KEY), cap_krw=cap,
        on_paid_phase=lambda _ticket: None, build_identity=_BUILD_IDENTITY,
    )


class _Provider(FakeMessages):
    def __init__(self, *, entered=None, release=None, error=None):
        super().__init__(_response(), error=error)
        self.entered = entered
        self.release = release

    def create(self, **kwargs):
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            assert self.release.wait(WAIT_SECONDS), "가짜 응답을 해제하지 않았습니다"
        response = super().create(**kwargs)
        response.usage = SimpleNamespace(input_tokens=100, output_tokens=100)
        response.model = MODEL
        return response


def _invoke(session, provider, *, official=None, metered=None):
    metered = metered or real._MeteredEngine(SimpleNamespace(MODEL=MODEL))
    client = metered.meter_client(SimpleNamespace(messages=provider))
    steps = []
    with generation_coordination.activate(session.callbacks):
        result = step.reclassify_official_evidence(
            official or _reclassifiable_result(), client=client,
            connect_db=storage_db.connect_explicit_commit, model=MODEL,
            steps=steps, generated_at="2026-09-22",
            preparation_namespace=lambda: _NAMESPACE,
        )
    return result, steps, metered


def _finish(session, metered=None):
    session.close_provider_context()
    if session.paid_phase is not None:
        paid_runtime._settle_paid_phase(
            session.paid_phase,
            amount_krw=sum(item["cost_krw"] for item in metered.usages) if metered else 0,
            billing_uncertain=bool(metered and metered.billing_uncertain),
        )
    session.abandon()


def test_only_owner_calls_provider_and_waiter_and_cache_hit_do_not(monkeypatch):
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    acquire = singleflight.acquire

    def observe_acquire(conn, **kwargs):
        result = acquire(conn, **kwargs)
        if result.disposition is singleflight.AcquireDisposition.WAIT:
            waiting.set()
        return result

    monkeypatch.setattr(singleflight, "acquire", observe_acquire)
    owner, waiter = _session("prepare-owner"), _session("prepare-waiter")
    owner_provider = _Provider(entered=entered, release=release)
    waiter_provider = _Provider()

    def run(session, provider):
        metered = real._MeteredEngine(SimpleNamespace(MODEL=MODEL))
        try:
            return _invoke(session, provider, metered=metered)
        finally:
            _finish(session, metered)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, owner, owner_provider)
        assert entered.wait(WAIT_SECONDS)
        second = pool.submit(run, waiter, waiter_provider)
        try:
            assert waiting.wait(WAIT_SECONDS)
            assert waiter.paid_phase is None
        finally:
            release.set()
        first_result, first_steps, _ = first.result(timeout=WAIT_SECONDS)
        second_result, second_steps, _ = second.result(timeout=WAIT_SECONDS)
    assert len(owner_provider.requests) == 1
    assert waiter_provider.requests == []
    assert first_steps[-1]["AI호출"] == 1
    assert second_steps[-1]["캐시"] == "hit"
    assert first_result.source_snapshot_sha256 == second_result.source_snapshot_sha256
    assert owner.cache_namespace is None
    assert owner.preflight_identity_digest == ""
    cached_session, cached_provider = _session("prepare-cache"), _Provider()
    try:
        cached, cached_steps, _ = _invoke(cached_session, cached_provider)
        assert cached.source_snapshot_sha256 == first_result.source_snapshot_sha256
        assert cached_steps[-1]["캐시"] == "hit"
        assert cached_provider.requests == []
        assert cached_session.paid_phase is None
    finally:
        _finish(cached_session)
    with storage_db.connect() as conn:
        attempts = state_machine.list_attempts(conn, run_id=owner.run_id, phase=SPEND_PHASE_PIPELINE)
        assert len(attempts) == 1
        assert attempts[0].billing_state is state_machine.BillingState.KNOWN_COST
        assert not state_machine.list_attempts(conn, run_id=waiter.run_id, phase=SPEND_PHASE_PIPELINE)
        active = conn.execute(
            f"SELECT COUNT(*) FROM {singleflight.TABLE_SINGLEFLIGHT_LEASES} WHERE state='active'"
        ).fetchone()[0]
        assert active == 0


@pytest.mark.parametrize("denial", ["cancel", "budget", "lease", "deadline"])
def test_cancel_budget_lease_and_deadline_reject_before_provider(monkeypatch, denial):
    session = _session("denied-" + denial, cap=0 if denial == "budget" else TEST_CAP_KRW)
    provider = _Provider()
    if denial == "cancel":
        session.cancel_waiter()
    elif denial == "deadline":
        session._execution_started_monotonic -= generation_singleflight.OWNER_MAX_AGE.total_seconds()
    elif denial == "lease":
        monkeypatch.setattr(singleflight, "heartbeat", lambda *_args, **_kwargs: None)
    try:
        with pytest.raises(generation_coordination.GenerationCoordinationError):
            _invoke(session, provider)
        assert provider.requests == []
        assert session._preparation_handle is None
        assert session._handle is None
        assert session.paid_phase is None
    finally:
        _finish(session)


def test_unsettled_provider_blocks_further_calls_even_for_final_owner():
    session, provider = _session("unknown-provider"), _Provider(error=TimeoutError("가짜 타임아웃"))
    metered = real._MeteredEngine(SimpleNamespace(MODEL=MODEL))
    try:
        original = _reclassifiable_result()
        result, _, _ = _invoke(session, provider, official=original, metered=metered)
        assert result.source_snapshot_sha256 == original.source_snapshot_sha256
        assert metered.billing_uncertain
        assert session.coordinate(COMPANY_ID, _NAMESPACE, result.source_snapshot_sha256) is None
        client = metered.meter_client(SimpleNamespace(messages=provider))
        with generation_coordination.activate(session.callbacks):
            with pytest.raises(provider_budget.ProviderBudgetUnavailable):
                client.messages.create(model=MODEL, max_tokens=100, messages=[])
        assert len(provider.requests) == 1
        session.fail("unknown_provider")
    finally:
        _finish(session, metered)
    with storage_db.connect() as conn:
        attempts = state_machine.list_attempts(conn, run_id=session.run_id, phase=SPEND_PHASE_PIPELINE)
        assert len(attempts) == 1
        assert attempts[0].billing_state is state_machine.BillingState.CONSERVATIVE_LIABILITY


def test_cache_save_failure_returns_original_evidence_for_owner_and_waiter(monkeypatch):
    def fail_save(*_args, **_kwargs):
        raise OSError("가짜 캐시 장애")

    monkeypatch.setattr(step.evidence_reclassify_cache, "save", fail_save)
    original = _reclassifiable_result()
    owner, waiter = _session("save-owner"), _session("save-waiter")
    first_provider, second_provider = _Provider(), _Provider()
    metered = None
    try:
        first, steps, metered = _invoke(owner, first_provider, official=original)
        second, waiter_steps, _ = _invoke(waiter, second_provider, official=original)
        assert first.source_snapshot_sha256 == second.source_snapshot_sha256 == original.source_snapshot_sha256
        assert steps[-1]["채택"] == 0
        assert steps[-1]["캐시저장실패"] == "OSError"
        assert waiter_steps[-1]["AI호출"] == 0
        assert second_provider.requests == []
    finally:
        _finish(owner, metered)
        _finish(waiter)


@pytest.mark.parametrize("cached", [False, True])
def test_preparation_cost_is_settled_when_final_result_is_reused(monkeypatch, tmp_path, cached):
    # 완료 픽스처 시각에 맞춰 실제 캐시의 신선도 검사도 통과시킨다.
    now = dt.datetime(2026, 8, 29, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(generation_singleflight.clock, "now_kst", lambda: now)
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(release_store, "load_automatic_release_record", lambda *_args, **_kwargs: object())
    report = _report()
    content, artifact = _persist_shared_content(report, artifact_root=tmp_path / "report-artifacts")
    prior = _session("prior-owner")
    assert prior.coordinate(COMPANY_ID, _NAMESPACE, _source_digest()) is None
    prior.complete(content.content_id, artifact.artifact_id, cache_eligible=False)
    if cached:
        with storage_db.connect() as conn:
            delivery = Delivery.issue(
                public_id="cached-origin", billing_bucket_id=prior.billing_bucket_id,
                content=content, delivered_at=now,
                policy=DeliveryPolicy(dt.timedelta(days=2), dt.timedelta(days=2)),
                reused_from_cache=False,
            )
            delivery_store.save_delivery(conn, delivery)
            delivery_artifact.bind_artifact_to_delivery(
                conn, delivery_id=delivery.delivery_id, artifact_id=artifact.artifact_id,
            )
            delivery_store.bind_cache_entry(
                conn, key=CacheLookupKey.from_preflight(
                    billing_bucket_id=prior.billing_bucket_id, corp_id=COMPANY_ID,
                    namespace=_NAMESPACE, preflight_identity_digest=_source_digest(),
                    preflight_cache_usable=True, engine_epoch_digest=_BUILD_IDENTITY.epoch_digest,
                ), content=content, artifact_id=artifact.artifact_id,
                cached_at=now,
            )
    session, provider = _session("prepare-then-reuse"), _Provider()
    metered = None
    try:
        _, _, metered = _invoke(session, provider)
        ticket = session.paid_phase
        reused = session.coordinate(COMPANY_ID, _NAMESPACE, _source_digest())
        assert reused is not None
        assert reused.content_snapshot_id == content.content_id
        assert reused.generation_cache_eligible is cached
        assert session.paid_phase is ticket
        assert ticket is not None
        with pytest.raises(generation_singleflight.GenerationSingleflightUnavailable):
            session.ensure_paid_phase()
        assert len(provider.requests) == 1
        assert sum(item["cost_krw"] for item in metered.usages) > 0
    finally:
        _finish(session, metered)
        prior.abandon()
    with storage_db.connect() as conn:
        phase = state_machine.get_phase(conn, run_id=session.run_id, phase=SPEND_PHASE_PIPELINE)
        assert phase.state is not state_machine.PhaseState.ACTIVE
        attempts = state_machine.list_attempts(conn, run_id=session.run_id, phase=SPEND_PHASE_PIPELINE)
        assert len(attempts) == 1
        assert attempts[0].billing_state is state_machine.BillingState.KNOWN_COST


def test_cancel_reason_and_lease_fence_survive_cleanup_failure(monkeypatch):
    session = _session("cancel-and-cleanup-failure")

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("가짜 종료 기록 장애")

    try:
        with pytest.raises(generation_coordination.GenerationWaitCancelled):
            with session.paid_preparation(COMPANY_ID, _NAMESPACE, "e" * 64) as owner:
                assert owner
                monkeypatch.setattr(singleflight, "fail", fail_cleanup)
                session.cancel_waiter()
                raise generation_coordination.GenerationWaitCancelled("최초 취소 사유")
        assert isinstance(session._lease_error, OSError)
        assert session._preparation_handle is None
        assert session.paid_phase is None
        with storage_db.connect() as conn:
            active = conn.execute(
                f"SELECT COUNT(*) FROM {singleflight.TABLE_SINGLEFLIGHT_LEASES} WHERE state='active'"
            ).fetchone()[0]
            assert active == 1
        with pytest.raises(generation_coordination.GenerationWaitCancelled):
            session.ensure_paid_phase()
    finally:
        _finish(session)


def test_lost_commit_response_keeps_same_saved_assignment_for_owner_and_waiter(monkeypatch):
    save = step.evidence_reclassify_cache.save

    def commit_then_disconnect(conn, *args, **kwargs):
        save(conn, *args, **kwargs)
        conn.commit()
        raise OSError("가짜 commit 응답 유실")

    monkeypatch.setattr(step.evidence_reclassify_cache, "save", commit_then_disconnect)
    owner, waiter = _session("commit-owner"), _session("commit-waiter")
    first_provider, second_provider = _Provider(), _Provider()
    metered = None
    try:
        first, steps, metered = _invoke(owner, first_provider)
        second, waiter_steps, _ = _invoke(waiter, second_provider)
        assert steps[-1]["채택"] == 1
        assert steps[-1]["캐시저장실패"] == "OSError"
        assert first.source_snapshot_sha256 == second.source_snapshot_sha256
        assert waiter_steps[-1]["캐시"] == "hit"
        assert second_provider.requests == []
    finally:
        _finish(owner, metered)
        _finish(waiter)


def test_different_inputs_for_same_filing_do_not_overwrite_each_other():
    original_a = _reclassifiable_result()
    original_b = _reclassifiable_result(
        missing_slots=("portfolio:revenue_link", "identity:business_definition"),
    )
    # B는 같은 공시·문단을 쓰지만 기존 칸 배정이 다른 요청이다.
    assert original_a.source_snapshot_sha256 != original_b.source_snapshot_sha256
    results, records, providers = [], [], []
    for run_id, original in (
        ("input-a-first", original_a),
        ("input-b", original_b),
        ("input-a-again", original_a),
    ):
        session, provider = _session(run_id), _Provider()
        metered = None
        try:
            result, steps, metered = _invoke(session, provider, official=original)
            results.append(result)
            records.append(steps[-1])
            providers.append(provider)
        finally:
            _finish(session, metered)
    assert results[0].source_snapshot_sha256 == results[2].source_snapshot_sha256
    assert results[0].source_snapshot_sha256 != original_a.source_snapshot_sha256
    assert records[2]["캐시"] == "hit"
    assert [len(provider.requests) for provider in providers] == [1, 1, 0]
    with storage_db.connect() as conn:
        table = step.evidence_reclassify_cache.TABLE_EVIDENCE_RECLASSIFICATION_CACHE
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 2
