"""넓은 공식 웹 수집 전용 전송 계층 — 항상 safe_http를 거친다.

★ 이 파일의 모든 실제 접속은 `safe_http.safe_urlopen()`을 통과한다
  (SSRF 방어·리다이렉트 재검사·DNS 고정은 전부 safe_http 책임).
  다만 `safe_http.read_limited_text()`/`validate_text_response()`가 허용하는
  콘텐츠 형식(text/html 등)이 sitemap.xml(application/xml)을 받아들이지
  않으므로, 이 파일은 safe_http의 공개 상수(``READ_CHUNK_BYTES``·
  ``MAX_RESPONSE_BYTES``·``response_deadline``)만 재사용해 «바이트·시간 상한
  읽기» 부분만 독립적으로 다시 구현한다(콘텐츠 형식 허용 범위만 넓힌
  의도적 소규모 중복 — safe_http.py 자체는 건드리지 않는다. 최종 보고서에
  설계 결정으로 남긴다).

★ MISSING과 FAILED 분리:
  - MISSING: HTTP 404/410처럼 «이 자원이 없다»가 명확히 확인된 경우.
  - FAILED: 시간초과·5xx·연결거부처럼 «있는지 없는지 증명하지 못한» 경우.
  DNS 실패(NXDOMAIN)는 safe_http가 다른 보안 사유(사설 IP 등)와 같은
  예외 클래스로 뭉뚱그리므로, 이 판정은 safe_http의 내부 오류 메시지
  문자열 대조에 의존한다 — 알려진 한계이며 실제 네트워크로 검증하지
  못했다(LIVE_COLLECTION_UNVERIFIED, 최종 보고서 참조).
"""

from __future__ import annotations

import http.client
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Callable, Final
from urllib import robotparser

from src.features.homepage.constants import TIMEOUT_SEC, USER_AGENT
from src.features.homepage.safe_http import (
    MAX_RESPONSE_BYTES,
    READ_CHUNK_BYTES,
    HomepageResponseError,
    UnsafeHomepageUrlError,
    response_deadline,
    safe_urlopen,
)

#: 이 전송 계층이 허용하는 콘텐츠 형식. safe_http의 기본 허용 집합
#: (text/html·application/xhtml+xml·text/plain)에 sitemap.xml용 XML을 더한다.
WIDE_ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "application/xml",
        "text/xml",
    }
)

#: safe_http DNS 실패 메시지 — NXDOMAIN류를 MISSING으로 분류하는 데 쓴다.
#: ★ 알려진 한계: safe_http.py의 문자열이 바뀌면 이 판정도 깨진다.
_DNS_NOT_FOUND_MARKER: Final[str] = "호스트 이름을 찾지 못했습니다"

UrlAllowPredicate = Callable[[str], bool]


class WideTransportError(Exception):
    """상태 코드를 못 받은 전송 실패(시간초과·DNS·연결거부·응답 검증 실패 등)."""


@dataclass(frozen=True)
class WideRawResponse:
    """검증 전 원시 응답 — 분류 로직이 상태 코드를 직접 보게 그대로 보존한다."""

    status: int
    text: str
    effective_url: str
    content_type: str


RawWideTransport = Callable[[str, UrlAllowPredicate | None], WideRawResponse]


def default_wide_transport(
    url: str,
    url_allowed: UrlAllowPredicate | None = None,
) -> WideRawResponse:
    """실제로 접속하는 기본 구현. 상태 코드를 보존해 MISSING/FAILED 분류를 돕는다."""

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with safe_urlopen(request, timeout=TIMEOUT_SEC, url_allowed=url_allowed) as response:
            effective_url = str(getattr(response, "geturl", lambda: url)() or "").strip()
            if not effective_url:
                raise WideTransportError("최종 URL을 확인하지 못했습니다")
            status = int(getattr(response, "status", 200) or 200)
            text, content_type = _read_bounded_text(response, timeout=TIMEOUT_SEC)
            return WideRawResponse(
                status=status,
                text=text,
                effective_url=effective_url,
                content_type=content_type,
            )
    except urllib.error.HTTPError as exc:
        return WideRawResponse(
            status=int(exc.code),
            text="",
            effective_url=str(getattr(exc, "url", "") or url),
            content_type="",
        )
    except (
        UnsafeHomepageUrlError,
        HomepageResponseError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        raise WideTransportError(f"{type(exc).__name__}: {exc}") from exc


def _read_bounded_text(response: object, *, timeout: float) -> tuple[str, str]:
    """safe_http와 같은 바이트·시간 상한으로 읽되 콘텐츠 형식 허용 범위만 넓힌다."""

    deadline, clock = response_deadline(response, timeout=timeout)
    content_type = _content_type_of(response)
    if content_type not in WIDE_ALLOWED_CONTENT_TYPES:
        raise WideTransportError(f"지원하지 않는 응답 형식: {content_type or '없음'}")

    reader = getattr(response, "read1", None)
    if not callable(reader):
        reader = getattr(response, "read", None)
    if not callable(reader):
        raise WideTransportError("응답을 읽을 수 없습니다")

    body = bytearray()
    while True:
        remaining_seconds = deadline - clock()
        if remaining_seconds <= 0:
            raise WideTransportError("응답 시간이 초과됐습니다")
        requested = min(READ_CHUNK_BYTES, MAX_RESPONSE_BYTES + 1 - len(body))
        try:
            chunk = reader(requested)
        except TimeoutError as exc:
            raise WideTransportError("응답 시간이 초과됐습니다") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise WideTransportError("응답을 읽지 못했습니다") from exc
        if clock() >= deadline:
            raise WideTransportError("응답 시간이 초과됐습니다")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise WideTransportError("응답 데이터가 올바르지 않습니다")
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise WideTransportError("응답이 너무 큽니다")

    charset = _content_charset_of(response)
    raw = bytes(body)
    if charset:
        try:
            return raw.decode(charset, errors="replace"), content_type
        except LookupError:
            pass
    try:
        return raw.decode("utf-8"), content_type
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace"), content_type


def _header_value(headers: object, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    return str(value) if value is not None else None


def _content_type_of(response: object) -> str:
    headers = getattr(response, "headers", None)
    raw = (_header_value(headers, "Content-Type") or "").split(";", 1)[0]
    return raw.strip().lower()


def _content_charset_of(response: object) -> str | None:
    headers = getattr(response, "headers", None)
    if isinstance(headers, Message):
        return headers.get_content_charset()
    raw = _header_value(headers, "Content-Type") or ""
    for item in raw.split(";")[1:]:
        name, separator, value = item.partition("=")
        if separator and name.strip().lower() == "charset":
            return value.strip().strip('"') or None
    return None


def classify_general_outcome(
    response: WideRawResponse | None,
    error: WideTransportError | None,
) -> tuple[str, str]:
    """일반 페이지 조회 결과의 (state, reason_code)를 정한다.

    state는 ``"OK"``·``"MISSING"``·``"FAILED"`` 중 하나다(``"TRUNCATED"``는
    상위 오케스트레이션이 상한 도달 시 별도로 매긴다).
    """
    if error is not None:
        if _DNS_NOT_FOUND_MARKER in str(error):
            return "MISSING", "dns_missing"
        return "FAILED", "network_failed"
    if response is None:
        return "FAILED", "network_failed"
    if response.status == 200:
        return "OK", "page_ok"
    if response.status in (404, 410):
        return "MISSING", f"page_missing_{response.status}"
    return "FAILED", f"page_failed_{response.status}"


def robots_decision(
    response: WideRawResponse | None,
    error: WideTransportError | None,
) -> tuple[str, str]:
    """robots.txt 조회 결과의 (outcome, reason_code)를 RFC 9309 의미론으로 정한다.

    outcome:
      - ``"proceed_empty_rules"``: HTTP 4xx로 명시적으로 없음 — 빈 규칙(전부 허용)으로 진행.
      - ``"proceed_parsed"``: 200 — 받은 글자를 규칙으로 해석해 진행.
      - ``"blocked"``: 그 외 전부(5xx·시간초과·DNS 등) — fail-closed, 이 호스트는 긁지 않는다.
    """
    if error is not None:
        return "blocked", "robots_unreachable"
    if response is None:
        return "blocked", "robots_unreachable"
    if response.status == 200:
        return "proceed_parsed", "robots_ok"
    if 400 <= response.status <= 499:
        return "proceed_empty_rules", "robots_missing"
    return "blocked", "robots_unreachable"


@dataclass(frozen=True)
class WideRobotsPolicy:
    """호스트 하나의 robots.txt 확인 결과."""

    host: str
    parser: robotparser.RobotFileParser
    outcome: str
    reason_code: str

    def can_fetch(self, url: str) -> bool:
        return self.parser.can_fetch(USER_AGENT, url)

    @property
    def blocked(self) -> bool:
        return self.outcome == "blocked"


def load_robots_policy(
    *,
    scheme: str,
    host: str,
    fetch: RawWideTransport,
) -> WideRobotsPolicy:
    """robots.txt를 fail-closed로 한 번 확인한다. 본문 조회보다 항상 먼저 부른다."""

    robots_url = f"{scheme}://{host}/robots.txt"
    response: WideRawResponse | None = None
    error: WideTransportError | None = None
    try:
        response = fetch(robots_url, None)
    except WideTransportError as exc:
        error = exc
    outcome, reason_code = robots_decision(response, error)
    parser = robotparser.RobotFileParser()
    text = response.text if (outcome == "proceed_parsed" and response is not None) else ""
    parser.parse(text.splitlines())
    return WideRobotsPolicy(
        host=host.casefold(),
        parser=parser,
        outcome=outcome,
        reason_code=reason_code,
    )


def fetch_sitemap(
    *,
    scheme: str,
    host: str,
    fetch: RawWideTransport,
    robots: WideRobotsPolicy,
    max_bytes: int,
) -> tuple[str, str]:
    """sitemap.xml 원문을 읽는다. robots가 막았거나 못 받으면 빈 문자열.

    Returns:
        (원문, reason_code). 원문이 빈 문자열이면 sitemap이 없거나 실패한 것.
    """
    sitemap_url = f"{scheme}://{host}/sitemap.xml"
    if not robots.can_fetch(sitemap_url):
        return "", "robots_disallowed"
    try:
        response = fetch(sitemap_url, robots.can_fetch)
    except WideTransportError:
        return "", "sitemap_failed"
    if response.status != 200:
        return "", f"sitemap_missing_{response.status}"
    text = response.text[: max_bytes if max_bytes > 0 else 0]
    return text, "sitemap_ok"
