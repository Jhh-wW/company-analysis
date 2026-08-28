from __future__ import annotations

import json
import urllib.error

import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.core.provider_gateway.types import BillingDisposition, ProviderObservation
from src.features.business_candidate.google_places import (
    ENDPOINT,
    FIELD_MASK,
    GooglePlacesRateLimited,
    GooglePlacesTextSearchProvider,
    GooglePlacesTimedOut,
    GooglePlacesUnavailable,
)


class Response:
    def __init__(self, document, *, status=200, url=ENDPOINT):
        self.status = status
        self.url = url
        self.closed = False
        self.body = (
            document if isinstance(document, bytes) else json.dumps(document).encode("utf-8")
        )

    def read(self, amount):
        return self.body[:amount]

    def geturl(self):
        return self.url

    def close(self):
        self.closed = True


class _AttemptRecorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.observations: list[ProviderObservation] = []

    def callbacks(self) -> ProviderAttemptCallbacks:
        def begin(provider, operation, reserved_krw):
            self.events.append(("begin", provider, operation, reserved_krw))
            return "places-attempt"

        def mark(token):
            self.events.append(("dispatch", token))

        def heartbeat(token):
            self.events.append(("heartbeat", token))

        def record(token, observation):
            self.events.append(("observation", token))
            self.observations.append(observation)

        return ProviderAttemptCallbacks(begin, heartbeat, mark, record)


@pytest.fixture(autouse=True)
def _provider_attempt_context():
    recorder = _AttemptRecorder()
    with attempt_context.activate(recorder.callbacks()):
        yield recorder


def test_공식_text_search요청은_최소필드_ko_kr_상한_1회만_쓴다(
    _provider_attempt_context,
):
    seen = []

    def transport(request, timeout):
        seen.append((request, timeout))
        return Response(
            {
                "places": [
                    {
                        "id": "place-jyp",
                        "displayName": {"text": "JYP 엔터테인먼트", "languageCode": "ko"},
                        "formattedAddress": "대한민국 서울특별시 강동구 강동대로 205",
                        "websiteUri": "https://www.jype.com/",
                        "businessStatus": "OPERATIONAL",
                        "attributions": [
                            {
                                "provider": "공공 주소 데이터",
                                "providerUri": "https://example.org/source",
                            }
                        ],
                    },
                    {
                        "displayName": {"text": "폐업한 JYP"},
                        "businessStatus": "CLOSED_PERMANENTLY",
                    },
                ]
            }
        )

    provider = GooglePlacesTextSearchProvider("secret-never-log", transport=transport)
    rows = provider.search(
        company="JYP", address_hint="서울 강동구", limit=99, timeout_sec=1.25
    )

    assert len(seen) == 1
    request, timeout = seen[0]
    assert request.full_url == ENDPOINT
    assert request.method == "POST"
    assert request.get_header("X-goog-fieldmask") == FIELD_MASK
    assert request.get_header("X-goog-api-key") == "secret-never-log"
    assert timeout == 1.25
    payload = json.loads(request.data.decode("utf-8"))
    assert payload == {
        "textQuery": "JYP 서울 강동구",
        "languageCode": "ko",
        "regionCode": "KR",
        "pageSize": 3,
    }
    assert len(rows) == 1
    assert rows[0].candidate_name == "JYP 엔터테인먼트"
    assert rows[0].address.endswith("강동대로 205")
    assert rows[0].homepage == "https://www.jype.com/"
    assert rows[0].provider_name == "Google Maps"
    assert rows[0].attributions == (
        ("공공 주소 데이터", "https://example.org/source"),
    )
    assert [event[0] for event in _provider_attempt_context.events] == [
        "begin",
        "heartbeat",
        "dispatch",
        "observation",
    ]
    observation = _provider_attempt_context.observations[-1]
    assert observation.billing_disposition is BillingDisposition.KNOWN_COST
    assert observation.known_cost_krw == provider.accounting_cost_krw


def test_429와_5xx는_재시도하지_않고_키나_응답본문을_예외에_싣지_않는다(
    _provider_attempt_context,
):
    for status, error_type in ((429, GooglePlacesRateLimited), (503, GooglePlacesUnavailable)):
        calls = []

        def transport(request, timeout, current=status):
            calls.append(1)
            return Response(b"secret-response-body", status=current)

        provider = GooglePlacesTextSearchProvider("secret-api-key", transport=transport)
        with pytest.raises(error_type) as caught:
            provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)
        assert calls == [1]
        message = str(caught.value)
        assert "secret-api-key" not in message
        assert "secret-response-body" not in message
    assert len(_provider_attempt_context.observations) == 2
    assert all(
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
        for observation in _provider_attempt_context.observations
    )


def test_깨진json과_큰응답은_fail_closed한다(_provider_attempt_context):
    bad = GooglePlacesTextSearchProvider(
        "key", transport=lambda _request, _timeout: Response(b"not-json")
    )
    with pytest.raises(GooglePlacesUnavailable):
        bad.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)

    huge = GooglePlacesTextSearchProvider(
        "key", transport=lambda _request, _timeout: Response(b"x" * (256 * 1024 + 1))
    )
    with pytest.raises(GooglePlacesUnavailable):
        huge.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)

    # 두 호출 모두 2xx 응답까지 받았으므로 고정 과금은 이미 확정된 비용이다.
    assert len(_provider_attempt_context.observations) == 2
    assert all(
        observation.billing_disposition is BillingDisposition.KNOWN_COST
        for observation in _provider_attempt_context.observations
    )


def test_응답은_항상_닫고_redirect는_따르지_않는다():
    response = Response({"places": []})
    provider = GooglePlacesTextSearchProvider(
        "key", transport=lambda _request, _timeout: response
    )
    assert provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1) == []
    assert response.closed is True

    redirected = Response({"places": []}, url="https://attacker.example/collect")
    provider = GooglePlacesTextSearchProvider(
        "key", transport=lambda _request, _timeout: redirected
    )
    with pytest.raises(GooglePlacesUnavailable):
        provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)
    assert redirected.closed is True


def test_timeout은_원래_예외종류를_지키고_예약액을_부채로_남긴다(
    _provider_attempt_context,
):
    calls = []

    def transport(_request, _timeout):
        calls.append(1)
        raise TimeoutError("secret provider detail")

    provider = GooglePlacesTextSearchProvider("secret-key", transport=transport)
    with pytest.raises(GooglePlacesTimedOut) as caught:
        provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)

    assert calls == [1]
    assert "secret" not in str(caught.value)
    observation = _provider_attempt_context.observations[-1]
    assert (
        observation.billing_disposition
        is BillingDisposition.CONSERVATIVE_LIABILITY
    )
    assert observation.liability_krw == provider.accounting_cost_krw


def test_전송의도_callback이_실패하면_places는_0회다():
    calls = []

    def fail_dispatch(_token):
        raise RuntimeError("시험용 DB 실패")

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: "places-attempt",
        lambda _token: None,
        fail_dispatch,
        lambda _token, _observation: None,
    )
    provider = GooglePlacesTextSearchProvider(
        "secret-key",
        transport=lambda _request, _timeout: calls.append(1),
    )
    with attempt_context.activate(callbacks):
        with pytest.raises(GooglePlacesUnavailable):
            provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)

    assert calls == []


def test_결과_callback이_실패하면_places는_정확히_1회다():
    calls = []
    response = Response({"places": []})

    def fail_record(_token, _observation):
        raise RuntimeError("시험용 결과 DB 실패")

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: "places-attempt",
        lambda _token: None,
        lambda _token: None,
        fail_record,
    )

    def transport(_request, _timeout):
        calls.append(1)
        return response

    provider = GooglePlacesTextSearchProvider("secret-key", transport=transport)
    with attempt_context.activate(callbacks):
        with pytest.raises(GooglePlacesUnavailable):
            provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)

    assert calls == [1]
    assert response.closed is True


def test_attempt_문맥이_없으면_places는_0회다():
    calls = []
    provider = GooglePlacesTextSearchProvider(
        "secret-key",
        transport=lambda _request, _timeout: calls.append(1),
    )
    token = attempt_context._CURRENT.set(None)
    try:
        with pytest.raises(GooglePlacesUnavailable):
            provider.search(company="JYP", address_hint="서울", limit=3, timeout_sec=1)
    finally:
        attempt_context._CURRENT.reset(token)

    assert calls == []
