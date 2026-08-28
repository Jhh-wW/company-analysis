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

from src.core.provider_gateway import attempt_context, gateway
from src.core.provider_gateway.google_places_adapter import GooglePlacesAdapter
from src.features.business_candidate.constants import (
    GOOGLE_PLACES_ACCOUNTING_COST_KRW,
    MAX_CANDIDATES,
)
from src.features.business_candidate.logic import (
    ProviderRateLimited,
    ProviderTimedOut,
    RawBusinessCandidate,
)
from src.shared import credentialed_http


ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.websiteUri,"
    "places.businessStatus,places.attributions"
)
MAX_RESPONSE_BYTES = 256 * 1024
MAX_QUERY_CHARS = 256


class GooglePlacesError(RuntimeError):
    """키·응답 본문을 싣지 않는 공급자 경계 오류."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GooglePlacesRateLimited(ProviderRateLimited, GooglePlacesError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        GooglePlacesError.__init__(self, message, status_code=status_code)


class GooglePlacesTimedOut(ProviderTimedOut, GooglePlacesError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        GooglePlacesError.__init__(self, message, status_code=status_code)


class GooglePlacesUnavailable(GooglePlacesError):
    pass


Transport = Callable[[urllib.request.Request, float], Any]


_OPENER = credentialed_http.build_no_redirect_opener()


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
        try:
            callbacks = attempt_context.current()
            attempt_token = callbacks.begin_attempt(
                "google_places",
                "text_search",
                float(self.accounting_cost_krw),
            )
        except Exception as error:
            raise GooglePlacesUnavailable(
                "Google Places 비용 원장을 시작할 수 없어 호출하지 않았습니다"
            ) from error

        def send_and_decode() -> dict[str, Any]:
            response = None
            status: int | None = None
            try:
                response = self._transport(request, timeout_sec)
                status = int(getattr(response, "status", 200))
                credentialed_http.require_exact_response_url(
                    response,
                    expected_url=ENDPOINT,
                )
                if status == 429:
                    raise GooglePlacesRateLimited(
                        "Google Places 요청 한도에 도달했습니다",
                        status_code=status,
                    )
                if status < 200 or status >= 300:
                    raise GooglePlacesUnavailable(
                        "Google Places가 정상 응답하지 않았습니다",
                        status_code=status,
                    )
                raw = credentialed_http.read_limited_bytes(
                    response,
                    max_bytes=MAX_RESPONSE_BYTES,
                )
            except urllib.error.HTTPError as exc:
                try:
                    if exc.code == 429:
                        raise GooglePlacesRateLimited(
                            "Google Places 요청 한도에 도달했습니다",
                            status_code=int(exc.code),
                        ) from None
                    raise GooglePlacesUnavailable(
                        "Google Places가 정상 응답하지 않았습니다",
                        status_code=int(exc.code),
                    ) from None
                finally:
                    try:
                        exc.close()
                    except Exception:
                        pass
            except GooglePlacesError:
                raise
            except (TimeoutError, socket.timeout):
                raise GooglePlacesTimedOut(
                    "Google Places 연결 시간이 초과되었습니다",
                    status_code=status,
                ) from None
            except Exception:
                # URL, key, provider 응답/예외 본문을 상위 로그에 노출하지 않는다.
                raise GooglePlacesUnavailable(
                    "Google Places 연결에 실패했습니다",
                    status_code=status,
                ) from None
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise GooglePlacesUnavailable(
                    "Google Places 응답 형식이 올바르지 않습니다",
                    status_code=status,
                ) from None
            if not isinstance(document, dict):
                raise GooglePlacesUnavailable(
                    "Google Places 응답 형식이 올바르지 않습니다",
                    status_code=status,
                )
            return document

        def before_dispatch() -> None:
            callbacks.heartbeat(attempt_token)
            callbacks.mark_dispatch_intent(attempt_token)

        try:
            document = gateway.call_once(
                adapter=GooglePlacesAdapter(
                    accounting_cost_krw=float(self.accounting_cost_krw)
                ),
                reserved_krw=float(self.accounting_cost_krw),
                before_dispatch=before_dispatch,
                send=send_and_decode,
                record_observation=lambda observation: callbacks.record_observation(
                    attempt_token, observation
                ),
            )
        except gateway.ProviderDispatchNotStarted as error:
            raise GooglePlacesUnavailable(
                "Google Places 전송 의도를 기록하지 못해 호출하지 않았습니다"
            ) from error
        except gateway.ProviderObservationRecordFailed as error:
            raise GooglePlacesUnavailable(
                "Google Places 호출 결과를 비용 원장에 기록하지 못했습니다"
            ) from error
        except gateway.ProviderCallFailed as wrapped:
            error = wrapped.__cause__
            if isinstance(error, GooglePlacesError):
                raise error
            raise GooglePlacesUnavailable("Google Places 연결에 실패했습니다") from wrapped

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
