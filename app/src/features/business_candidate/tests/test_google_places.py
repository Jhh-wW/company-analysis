from __future__ import annotations

import json
import urllib.error

import pytest

from src.features.business_candidate.google_places import (
    ENDPOINT,
    FIELD_MASK,
    GooglePlacesRateLimited,
    GooglePlacesTextSearchProvider,
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


def test_공식_text_search요청은_최소필드_ko_kr_상한_1회만_쓴다():
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


def test_429와_5xx는_재시도하지_않고_키나_응답본문을_예외에_싣지_않는다():
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


def test_깨진json과_큰응답은_fail_closed한다():
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
