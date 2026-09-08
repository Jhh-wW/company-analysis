"""뉴스 provider fatal 경계를 실제 collection에 연결하는 독립 회귀."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src.core.provider_gateway import gateway
from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter
from src.core.provider_gateway.types import BillingDisposition, ProviderObservation, TransportState
from src.features.news_intake.collection import collect_from_snapshot, collect_search_snapshot
from src.features.news_intake.models import NewsCollectionPolicy, NewsCompanyContext


AS_OF = dt.date(2026, 9, 8)
COMPANY = NewsCompanyContext(
    "주식회사 가나다전자",
    aliases=("가나다전자", "Ganada Electronics"),
    domain="https://company.example",
    identity_context="기업용 산업설비 제조",
)
POLICY = NewsCollectionPolicy(
    trusted_publisher_domains=("media.example",),
    batch_size=2,
    max_analysis_calls=4,
)


def _search_item(number: int) -> SimpleNamespace:
    """네트워크 대신 검색 adapter가 돌려주는 실제 입력 모양을 만든다."""

    return SimpleNamespace(
        title=f"가나다전자 산업설비 공급 {number}",
        description="가나다전자는 기업용 산업설비를 공급한다.",
        originallink=f"https://media.example/article/{number}",
        link="",
        pubDate="2026-09-01",
    )


def _snapshot(count: int, *, policy: NewsCollectionPolicy = POLICY):
    """검색 결과만 fixture로 고정하고 실제 snapshot 생산기를 사용한다."""

    items = [_search_item(number) for number in range(count)]
    calls = 0

    def search(_query: str, **_options: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            state="success",
            reason_code="news_search_ok",
            items=items if calls == 1 else [],
            transport_attempts=1,
            retry_recovered=False,
            attempt_reason_codes=("news_search_ok",),
        )

    result = collect_search_snapshot(
        search_news=search,
        company=COMPANY,
        as_of=AS_OF,
        policy=policy,
    )
    return result


def _body(url: str) -> str:
    """본문 fetch 경계만 local fixture로 대체하며 충분한 길이의 원문을 준다."""

    number = url.rsplit("/", 1)[-1]
    sentence = (
        f"가나다전자는 기업용 산업설비 제조 사업을 운영하며 2026년 9월 1일 "
        f"자동화 설비 {120 + int(number)}대를 공급했다."
    )
    return sentence * 2


def _bad_request() -> anthropic.BadRequestError:
    """실제 SDK 예외 타입을 만들되 외부 전송은 하지 않는다."""

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, headers={"request-id": "req-fatal-test"})
    return anthropic.BadRequestError("provider body is not persisted", response=response, body=None)


def _fatal_analyzer(
    calls: list[dict[str, object]], observations: list[ProviderObservation]
):
    """실제 gateway wrapper가 observation을 남기고 fatal을 전파하게 한다."""

    def analyze(prompt: str, _schema: dict[str, object], _max_tokens: int) -> object:
        payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
        calls.append(payload)

        def send() -> object:
            raise _bad_request()

        return gateway.call_once(
            adapter=AnthropicAdapter(lambda _response: None),
            reserved_krw=94.41,
            before_dispatch=lambda: None,
            send=send,
            record_observation=observations.append,
        )

    return analyze


def test_provider_fatal_preserves_wrapper_observation_and_stops_following_batches() -> None:
    """첫 fatal은 원래 wrapper·observation을 보존하고 다음 배치를 호출하지 않는다."""

    policy = replace(POLICY, batch_size=2)
    snapshot = _snapshot(4, policy=policy)
    calls: list[dict[str, object]] = []
    observations: list[ProviderObservation] = []

    with pytest.raises(gateway.ProviderCallFailed) as captured:
        collect_from_snapshot(
            snapshot,
            company=COMPANY,
            as_of=AS_OF,
            fetch_text=_body,
            analyze_grounded=_fatal_analyzer(calls, observations),
            policy=policy,
        )

    assert len(snapshot.candidates) >= policy.batch_size * 2
    assert len(calls) == 1
    assert len(observations) == 1
    assert captured.value.observation is observations[0]
    assert isinstance(captured.value.__cause__, anthropic.BadRequestError)
    assert observations[0].transport_state is TransportState.RESPONSE_RECEIVED
    assert observations[0].billing_disposition is BillingDisposition.CONSERVATIVE_LIABILITY
    assert observations[0].status_code == 400
    assert observations[0].error_type == "BadRequestError"
    assert observations[0].known_cost_krw == 0.0
    assert observations[0].liability_krw == pytest.approx(94.41)


def test_large_prompt_splits_real_collection_batches_before_analysis() -> None:
    """큰 prompt는 collection의 실제 분할 분기를 거쳐 두 배치로 분석한다."""

    policy = replace(POLICY, batch_size=4, max_prompt_chars=4_000)
    snapshot = _snapshot(4, policy=policy)
    analyzed_batches: list[tuple[str, ...]] = []

    def analyze(prompt: str, _schema: dict[str, object], _max_tokens: int) -> object:
        payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
        analyzed_batches.append(tuple(item["id"] for item in payload["articles"]))
        return {"items": []}

    result = collect_from_snapshot(
        snapshot,
        company=COMPANY,
        as_of=AS_OF,
        fetch_text=_body,
        analyze_grounded=analyze,
        policy=policy,
    )

    assert len(analyzed_batches) == 2
    assert all(len(batch) == 2 for batch in analyzed_batches)
    assert set().union(*analyzed_batches) == {candidate.id for candidate in snapshot.candidates}
    assert result.diagnostics["분석AI호출"] == 2
    assert result.diagnostics["제외"]["grounded_missing_result"] == 4
    assert result.diagnostics["실패"] == "grounded_response_incomplete"
    assert result.fragments == ()


def test_invalid_content_response_uses_existing_validation_statistics() -> None:
    """일반 잘못된 콘텐츠 응답은 fatal이 아닌 기존 검증 통계로 남긴다."""

    snapshot = _snapshot(2)
    calls = 0

    def analyze(_prompt: str, _schema: dict[str, object], _max_tokens: int) -> str:
        nonlocal calls
        calls += 1
        return "not-json-content"

    result = collect_from_snapshot(
        snapshot,
        company=COMPANY,
        as_of=AS_OF,
        fetch_text=_body,
        analyze_grounded=analyze,
        policy=POLICY,
    )

    assert calls == 1
    assert result.fragments == ()
    assert result.diagnostics["제외"]["grounded_invalid_response"] == 1
    assert result.diagnostics["실패"] == "grounded_response_incomplete"
    assert result.diagnostics["실패사유"] == ("grounded_response_incomplete",)
    assert result.diagnostics["캐시재사용가능"] is False
