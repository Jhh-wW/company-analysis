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
  - FAILED: 시간초과·DNS·5xx·연결거부처럼 «있는지 없는지 증명하지 못한» 경우.
  ``safe_http``는 권위 NXDOMAIN·임시 resolver 오류·DNS 자식 프로세스
  실패를 같은 예외 문장으로 합친다. 따라서 이 계층은 문자열을 보고
  «없는 도메인»이라고 추측하지 않는다. 구분 신호가 생기기 전까지 DNS는
  모두 FAILED가 정직한 fail-closed 판정이다.
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
from src.features.homepage.robots_cache import (
    RobotsDecision,
    cached_robots_decision,
    robots_cache_key,
)
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
        return "FAILED", "network_failed"
    if response is None:
        return "FAILED", "network_failed"
    if response.status == 200:
        return "OK", "page_ok"
    if response.status in (404, 410):
        return "MISSING", f"page_missing_{response.status}"
    return "FAILED", f"page_failed_{response.status}"


#: RFC 9309 §2.3.1.3이 «unavailable»(이용 불가)로 묶는 구간 — 400~499 전체.
#: 이 구간은 「robots.txt가 없다」와 같은 뜻이라 빈 규칙(전부 허용)으로 진행한다.
_ROBOTS_UNAVAILABLE_STATUS_MIN: Final[int] = 400
_ROBOTS_UNAVAILABLE_STATUS_MAX: Final[int] = 499


def robots_decision(
    response: WideRawResponse | None,
    error: WideTransportError | None,
) -> tuple[str, str]:
    """robots.txt 조회 결과의 (outcome, reason_code)를 RFC 9309 의미론으로 정한다.

    outcome:
      - ``"proceed_parsed"``: 200 — 받은 글자를 규칙으로 해석해 진행.
      - ``"proceed_empty_rules"``: 4xx 전체 — RFC 9309 §2.3.1.3의 «unavailable».
        robots.txt가 없는 것과 같으므로 빈 규칙으로 진행.
      - ``"blocked"``: 5xx·전송 실패(시간초과·DNS·TLS·연결 거부·경로 정책 거절) —
        RFC 9309 §2.3.1.4의 «unreachable». fail-closed로 이 호스트의 본문을
        한 번도 긁지 않는다.

    ★ 401·403·407을 「명시적 거부」로, 408·409·429를 「일시 장애」로 갈라 전부
      차단하던 예전 분류를 4xx 한 덩어리로 되돌린 이유:

      (1) 실측(2026-09-05) — robots.txt를 두지 않은 정적 호스팅(S3/CloudFront)이
          404가 아니라 **403**을 돌려준다. 하이브 ``hybecorp.com``·
          ``www.hybecorp.com``이 그랬고, 같은 호스트의 루트 페이지는 200으로
          멀쩡히 열렸다. 즉 403은 「크롤링하지 마라」가 아니라 「그런 파일이
          없다」는 뜻이었는데 우리는 그걸 회사 전체 차단으로 읽고 있었다.
      (2) 표준 — RFC 9309 §2.3.1.3은 400~499를 통째로 «unavailable»로 묶고,
          이때 크롤러가 자원에 접근해도 된다(MAY)고 정한다.
      (3) 일관성 — 같은 scope의 홈페이지 수집기(``logic.py`` ``_load_robots``)와
          공식 IR PDF 수집기는 이미 4xx 전체를 부재로 본다. 세 수집기가
          ``robots_cache``를 **공유**하므로 여기만 다르게 판정하면 어느 수집기가
          먼저 물었는지에 따라 결과가 달라진다.

      사유 코드 ``robots_denied``·``robots_transient``는 이 수정으로 **없어졌다**
      (저장소 전체에 생산자도 소비자도 남아 있지 않다). 진짜 거부는 이제 사유
      코드가 아니라 규칙 평가로만 나타난다 — 받아 온 규칙이 우리 UA(또는 ``*``)를
      Disallow하면 ``WideRobotsPolicy.can_fetch``가 그 URL을 건너뛴다(sitemap만
      ``robots_disallowed``로 따로 표시한다).

    ★ 사유 코드는 4xx 전체에 대해 ``robots_missing`` 하나로 통일한다. 상태코드를
      사유 코드에 섞으면(``robots_missing_403`` 식) 이 값을 그대로 읽는 상위
      집계·시험 계약이 상태코드마다 갈라진다. 어떤 4xx였는지는
      ``WideRobotsPolicy.detail``에 남긴다.
    """
    if error is not None:
        return "blocked", "robots_unreachable"
    if response is None:
        return "blocked", "robots_unreachable"
    if response.status == 200:
        return "proceed_parsed", "robots_ok"
    if (
        _ROBOTS_UNAVAILABLE_STATUS_MIN
        <= response.status
        <= _ROBOTS_UNAVAILABLE_STATUS_MAX
    ):
        return "proceed_empty_rules", "robots_missing"
    return "blocked", "robots_unreachable"


@dataclass(frozen=True)
class WideRobotsPolicy:
    """호스트 하나의 robots.txt 확인 결과."""

    host: str
    parser: robotparser.RobotFileParser
    outcome: str
    reason_code: str
    #: 사유 코드만으로는 뭉개지는 구체적 원인(예: ``"HTTP 403"``·``"TimeoutError: ..."``).
    #: 4xx는 전부 ``robots_missing``으로 통일하므로, 몇 번이었는지는 여기에만 남는다.
    detail: str = ""

    def can_fetch(self, url: str) -> bool:
        return self.parser.can_fetch(USER_AGENT, url)

    @property
    def blocked(self) -> bool:
        return self.outcome == "blocked"


def _robots_detail(
    response: WideRawResponse | None,
    error: WideTransportError | None,
) -> str:
    """사유 코드가 뭉개는 구체적 원인을 사람이 읽는 한 줄로 만든다.

    4xx는 전부 ``robots_missing`` 하나로 통일되므로, 「403이라서 부재로 봤다」는
    사실은 이 값에만 남는다. 진단할 때 이게 없으면 404와 403을 구분할 수 없다.
    """
    if error is not None:
        return str(error)
    if response is None:
        return ""
    return f"HTTP {response.status}"


def load_robots_policy(
    *,
    robots_url: str,
    host: str,
    fetch: RawWideTransport,
    url_allowed: UrlAllowPredicate,
) -> WideRobotsPolicy:
    """robots.txt를 fail-closed로 확인한다. 본문 조회보다 항상 먼저 부른다.

    ★ 같은 조사(scope) 안에서 이미 다른 수집기(홈페이지·공식 IR PDF)가 같은
      **origin**(scheme+host+port, ``robots_url``에서 뽑는다 — host만 쓰면
      scheme이 다른 재시도가 다른 origin의 판정을 잘못 물려받는다, 독립
      검토 P0)의 robots.txt를 확인했으면 새 네트워크 요청 없이 그 판정을
      재사용한다(``robots_cache.cached_robots_decision``). 재사용
      시에는 ``proceed_parsed``/``proceed_empty_rules`` 구분을 두지 않는다
      (``WideRobotsPolicy.blocked``만 실제로 쓰이므로 정보 손실이 없다).
    """

    cache_host = host.casefold()
    cache_key = robots_cache_key(robots_url)

    def loader() -> RobotsDecision:
        response: WideRawResponse | None = None
        error: WideTransportError | None = None
        try:
            # robots 부트스트랩도 redirect가 공식 origin을 벗어나면 안 된다. 규칙
            # 본문을 아직 못 읽었으므로 robots 경로 자체의 origin predicate만 쓴다.
            response = fetch(robots_url, url_allowed)
        except WideTransportError as exc:
            error = exc
        outcome, reason_code = robots_decision(response, error)
        parser = robotparser.RobotFileParser()
        text = (
            response.text
            if (outcome == "proceed_parsed" and response is not None)
            else ""
        )
        parser.parse(text.splitlines())
        return RobotsDecision(
            host=cache_host,
            parser=parser,
            blocked=(outcome == "blocked"),
            reason_code=reason_code,
            detail=_robots_detail(response, error),
        )

    decision = cached_robots_decision(cache_key, loader)
    return WideRobotsPolicy(
        host=cache_host,
        parser=decision.parser,
        outcome="blocked" if decision.blocked else "proceed_parsed",
        reason_code=decision.reason_code,
        detail=decision.detail,
    )


def fetch_sitemap(
    *,
    fetch: RawWideTransport,
    robots: WideRobotsPolicy,
    max_bytes: int,
    sitemap_url: str = "",
    url_allowed: UrlAllowPredicate | None = None,
    scheme: str = "",
    host: str = "",
) -> tuple[str, str]:
    """sitemap.xml 원문을 읽는다. robots가 막았거나 못 받으면 빈 문자열.

    Returns:
        (원문, reason_code). 원문이 빈 문자열이면 sitemap이 없거나 실패한 것.
    """
    if not sitemap_url:
        sitemap_url = f"{scheme}://{host}/sitemap.xml"

    def allowed(candidate: str) -> bool:
        return (url_allowed is None or url_allowed(candidate)) and robots.can_fetch(candidate)

    if not allowed(sitemap_url):
        return "", "robots_disallowed"
    try:
        response = fetch(sitemap_url, allowed)
    except WideTransportError:
        return "", "sitemap_failed"
    if response.status in (404, 410):
        return "", f"sitemap_missing_{response.status}"
    if response.status in (401, 403, 407):
        return "", f"sitemap_denied_{response.status}"
    if response.status in (408, 409, 429):
        return "", f"sitemap_transient_{response.status}"
    if response.status != 200:
        return "", f"sitemap_failed_{response.status}"
    text = _truncate_utf8_bytes(response.text, max_bytes)
    return text, "sitemap_ok"


def _truncate_utf8_bytes(text: str, max_bytes: int) -> str:
    """UTF-8 «바이트」 기준으로 text를 max_bytes 이하로 자른다.

    ★ 바이트 상한을 문자 상한으로 세던 것을 고친 이유: 예전엔 ``text[:max_bytes]``로
      «문자 수»를 잘랐다. 한글 등 멀티바이트 문자는 UTF-8에서 문자당 최대
      3바이트라, 한글 sitemap이면 선언한 바이트 상한을 최대 3배 넘을 수 있었다.
    ★ 바이트 경계에서 멀티바이트 시퀀스 중간이 잘려도 예외를 던지지 않는다 —
      잘린 꼬리 바이트는 ``errors="ignore"``로 조용히 버린다(깨진 문자 하나
      없어지는 것이 예외로 전체 sitemap 처리를 막는 것보다 안전하다).
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
