"""Google Places Text Search (New) 회사 후보 어댑터.

공식 계약:
- POST https://places.googleapis.com/v1/places:searchText
- 명시적 field mask만 사용하고 SDK/HTTP 자동 재시도는 하지 않는다.
- 응답은 현재 후보 화면에만 전달하며 DB·로그·캐시에 저장하지 않는다.
- API key 값과 오류 응답 본문은 예외·로그 문자열에 넣지 않는다.

이번 모듈은 Gemini를 사용하지 않는다. Places는 별도 Google Maps Platform 유료 API다.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from src.features.business_candidate.constants import (
    GOOGLE_PLACES_ACCOUNTING_COST_KRW,
    MAX_CANDIDATES,
)
from src.features.business_candidate.logic import (
    ProviderRateLimited,
    ProviderTimedOut,
    RawBusinessCandidate,
)


ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.websiteUri,"
    "places.businessStatus,places.attributions"
)
MAX_RESPONSE_BYTES = 256 * 1024
MAX_QUERY_CHARS = 256


class GooglePlacesError(RuntimeError):
    """키·응답 본문을 싣지 않는 공급자 경계 오류."""


class GooglePlacesRateLimited(ProviderRateLimited, GooglePlacesError):
    pass


class GooglePlacesTimedOut(ProviderTimedOut, GooglePlacesError):
    pass


class GooglePlacesUnavailable(GooglePlacesError):
    pass


Transport = Callable[[urllib.request.Request, float], Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """API key가 다른 호스트로 전달되는 redirect를 따르지 않는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def _urlopen(request: urllib.request.Request, timeout_sec: float):
    return _OPENER.open(request, timeout=timeout_sec)  # noqa: S310


class GooglePlacesTextSearchProvider:
    """Text Search (New)를 정확히 한 번 부르는 과금형 공급자."""

    costs_money = True
    provider_name = "Google Maps"
    accounting_cost_krw = GOOGLE_PLACES_ACCOUNTING_COST_KRW
    max_results = MAX_CANDIDATES

    def __init__(self, api_key: str, *, transport: Transport | None = None) -> None:
        clean = (api_key or "").strip()
        if not clean:
            raise ValueError("Google Places API key가 설정되지 않았습니다")
        self._api_key = clean
        self._transport = transport or _urlopen

    def search(
        self, *, company: str, address_hint: str, limit: int, timeout_sec: float
    ) -> Sequence[RawBusinessCandidate]:
        query = " ".join(piece for piece in (company.strip(), address_hint.strip()) if piece)
        query = query[:MAX_QUERY_CHARS]
        if not query:
            return ()
        count = max(1, min(int(limit), MAX_CANDIDATES))
        payload = json.dumps(
            {
                "textQuery": query,
                "languageCode": "ko",
                "regionCode": "KR",
                "pageSize": count,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            ENDPOINT,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
        )
        response = None
        try:
            response = self._transport(request, timeout_sec)
            final_url = str(getattr(response, "geturl", lambda: ENDPOINT)())
            if final_url != ENDPOINT:
                raise GooglePlacesUnavailable("Google Places 응답 위치가 올바르지 않습니다")
            status = int(getattr(response, "status", 200))
            if status == 429:
                raise GooglePlacesRateLimited("Google Places 요청 한도에 도달했습니다")
            if status < 200 or status >= 300:
                raise GooglePlacesUnavailable("Google Places가 정상 응답하지 않았습니다")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            try:
                if exc.code == 429:
                    raise GooglePlacesRateLimited(
                        "Google Places 요청 한도에 도달했습니다"
                    ) from None
                raise GooglePlacesUnavailable(
                    "Google Places가 정상 응답하지 않았습니다"
                ) from None
            finally:
                exc.close()
        except GooglePlacesError:
            raise
        except (TimeoutError, socket.timeout):
            raise GooglePlacesTimedOut("Google Places 연결 시간이 초과되었습니다") from None
        except Exception:
            # URL, key, provider 응답/예외 본문을 상위 로그에 노출하지 않는다.
            raise GooglePlacesUnavailable("Google Places 연결에 실패했습니다") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GooglePlacesUnavailable("Google Places 응답이 허용 크기를 넘었습니다")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GooglePlacesUnavailable("Google Places 응답 형식이 올바르지 않습니다") from None
        if not isinstance(document, dict):
            raise GooglePlacesUnavailable("Google Places 응답 형식이 올바르지 않습니다")

        out: list[RawBusinessCandidate] = []
        places = document.get("places")
        if not isinstance(places, list):
            return ()
        for place in places[:count]:
            if not isinstance(place, dict):
                continue
            if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            display = place.get("displayName")
            candidate_name = display.get("text", "") if isinstance(display, dict) else ""
            attrs: list[tuple[str, str]] = []
            raw_attrs = place.get("attributions")
            if isinstance(raw_attrs, list):
                for attribution in raw_attrs[:MAX_CANDIDATES]:
                    if isinstance(attribution, dict):
                        attrs.append(
                            (
                                str(attribution.get("provider", "")),
                                str(attribution.get("providerUri", "")),
                            )
                        )
            out.append(
                RawBusinessCandidate(
                    candidate_name=str(candidate_name),
                    address=str(place.get("formattedAddress", "")),
                    homepage=str(place.get("websiteUri", "")),
                    source_label="Google Maps 장소 검색",
                    source_url="",
                    provider_name=self.provider_name,
                    attributions=tuple(attrs),
                )
            )
        return out
