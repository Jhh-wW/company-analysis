"""provider fatal이 원인·관측·비용 장부를 함께 보존하는 오프라인 회귀."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src.core.provider_gateway import gateway
from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)
from src.features.budget import provider_budget
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsCompanyContext
from src.features.pipeline import real


def _bad_request() -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        400,
        request=request,
        headers={"request-id": "req-wave3"},
    )
    return anthropic.BadRequestError(
        "provider body is not persisted", response=response, body=None
    )


def test_actual_sdk_400_gateway_adapter_with_temp_sqlite_preserves_liability_and_cause(
    tmp_path,
) -> None:
    db_path = tmp_path / "provider-attempts.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE observations (status INTEGER, error_type TEXT, transport TEXT, billing TEXT, liability REAL)"
        )

    recorded: list[ProviderObservation] = []

    def record(observation: ProviderObservation) -> None:
        recorded.append(observation)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?)",
                (
                    observation.status_code,
                    observation.error_type,
                    observation.transport_state.value,
                    observation.billing_disposition.value,
                    observation.liability_krw,
                ),
            )

    def send() -> object:
        raise _bad_request()

    with pytest.raises(gateway.ProviderCallFailed) as captured:
        gateway.call_once(
            adapter=AnthropicAdapter(lambda _response: None),
            reserved_krw=94.41,
            before_dispatch=lambda: None,
            send=send,
            record_observation=record,
        )

    assert isinstance(captured.value.__cause__, anthropic.BadRequestError)
    assert captured.value.observation.status_code == 400
    assert captured.value.observation.error_type == "BadRequestError"
    assert (
        captured.value.observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert captured.value.observation.liability_krw == pytest.approx(94.41)
    assert captured.value.observation.transport_state.value == "RESPONSE_RECEIVED"
    assert "provider body" not in str(captured.value.observation)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, error_type, transport, billing, liability FROM observations"
        ).fetchone()
    assert row == (
        400,
        "BadRequestError",
        "RESPONSE_RECEIVED",
        "CONSERVATIVE_LIABILITY",
        pytest.approx(94.41),
    )


class _FailingMessages:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs: object) -> object:
        self.calls += 1
        raise _bad_request()


def test_metered_messages_uses_observation_authority_and_blocks_followup() -> None:
    messages = _FailingMessages()
    observations: list[ProviderObservation] = []
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: "attempt-1",
        lambda _token: None,
        lambda _token: None,
        lambda _token, observation: observations.append(observation),
    )
    raw = SimpleNamespace(
        MODEL="claude-haiku-4-5", client=SimpleNamespace(messages=messages)
    )
    metered = real._MeteredEngine(raw)
    client = real._metered_client(metered, raw.client)

    with provider_budget.activate(10_000.0):
        from src.core.provider_gateway import attempt_context

        with attempt_context.activate(callbacks):
            with pytest.raises(gateway.ProviderCallFailed) as captured:
                client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=32,
                    messages=[{"role": "user", "content": "offline"}],
                )
            with pytest.raises(provider_budget.ProviderBudgetUnavailable):
                client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=32,
                    messages=[{"role": "user", "content": "must stop"}],
                )

    assert isinstance(captured.value.__cause__, anthropic.BadRequestError)
    assert (
        captured.value.observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert metered.billing_uncertain is True
    assert messages.calls == 1
    assert len(observations) == 1


def test_zero_cost_requires_pre_dispatch_known_zero_observation() -> None:
    observation = ProviderObservation(
        transport_state=TransportState.LOCAL_FAILURE,
        billing_disposition=BillingDisposition.KNOWN_ZERO,
        known_cost_krw=0.0,
        liability_krw=0.0,
        status_code=None,
        error_type="",
        request_id="",
    )
    assert real._is_determinate_zero_cost(_bad_request(), observation=observation)


def test_news_collection_propagates_provider_call_failed() -> None:
    import datetime as dt

    from src.features.news_intake.models import (
        NewsCandidate,
        NewsCollectionPolicy,
        NewsSearchSnapshot,
    )
    from src.features.news_intake.search_snapshot import (
        candidate_window,
        company_digest,
        policy_digest,
        snapshot_digest,
    )

    company = NewsCompanyContext("가나다전자", identity_context="산업설비 제조")
    policy = NewsCollectionPolicy(trusted_publisher_domains=("media.example",))
    candidate = NewsCandidate(
        id="candidate-1", title="가나다전자 공급", description="가나다전자 설비 공급",
        originallink="https://media.example/a", link="", published_on="2026-09-01",
        publisher="media.example", priority=1, source_url="https://media.example/a",
        source_category="news_report",
    )
    snapshot = NewsSearchSnapshot(
        as_of="2026-09-08", company_digest=company_digest(company), policy_digest=policy_digest(policy),
        digest="", candidates=(candidate,), reason_codes=(), exclusion_counts={}, status="success",
        cache_eligible=True, query_attempts=(),
    )
    snapshot = snapshot.__class__(
        **{**snapshot.__dict__, "digest": snapshot_digest(snapshot)}
    )
    assert candidate_window(candidate, dt.date(2026, 9, 8)) == 12
    observation = ProviderObservation(
        transport_state=TransportState.RESPONSE_RECEIVED,
        billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
        known_cost_krw=0.0,
        liability_krw=1.0,
        status_code=400,
        error_type="BadRequestError",
        request_id="req",
    )
    failure = gateway.ProviderCallFailed(observation)
    failure.__cause__ = _bad_request()
    calls: list[int] = []
    with pytest.raises(gateway.ProviderCallFailed):
        collect_from_snapshot(
            snapshot,
            company=company,
            as_of=dt.date(2026, 9, 8),
            fetch_text=lambda _url: (
                "가나다전자는 산업설비 제조 사업을 운영하며 2026년 9월 1일 "
                "자동화 설비 120대를 공급했다."
                * 4
            ),
            analyze_grounded=lambda *_args: (
                calls.append(1), (_ for _ in ()).throw(failure)
            )[1],
            policy=policy,
        )
    assert calls == [1]


def test_real_news_collector_records_safe_observation_and_propagates_wrapper() -> None:
    observation = ProviderObservation(
        transport_state=TransportState.RESPONSE_RECEIVED,
        billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
        known_cost_krw=0.0,
        liability_krw=94.41,
        status_code=400,
        error_type="BadRequestError",
        request_id="req-wave3",
    )
    failure = gateway.ProviderCallFailed(observation)
    failure.__cause__ = _bad_request()

    class _Session:
        snapshot = SimpleNamespace(query_attempts=())

        def collect(self, **_kwargs: object) -> object:
            raise failure

    steps: list[dict[str, object]] = []
    with pytest.raises(gateway.ProviderCallFailed):
        real._collect_grounded_news(
            session=_Session(),
            analyze=lambda *_args: None,
            fetch_text=lambda _url: "",
            corp_id="corp",
            official_web_documents=1,
            collected_on="2026-09-08",
            steps=steps,
        )

    assert steps[0]["실패"] == "provider_call_failed"
    assert steps[0]["provider_status"] == 400
    assert steps[0]["provider_error_type"] == "BadRequestError"
    assert steps[0]["provider_transport"] == "RESPONSE_RECEIVED"
    assert steps[0]["provider_billing"] == "CONSERVATIVE_LIABILITY"
    assert steps[0]["provider_liability_krw"] == pytest.approx(94.41)
