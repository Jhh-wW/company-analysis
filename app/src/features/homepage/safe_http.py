"""홈페이지 수집 전용 SSRF 방어가 있는, 외부 의존성 없는 HTTP 클라이언트.

DART가 준 주소와 내려받은 HTML의 링크는 모두 신뢰하지 않는다. 루프백·클라우드
메타데이터·사설망으로 연결하지 못하게 막는다.

검사 뒤 실제 접속까지 DNS가 바뀌는 틈도 닫는다. 아래 연결은 검증한 공인 IP로 직접
접속하되, HTTP Host와 TLS SNI·인증서 검증에는 원래 호스트 이름을 유지한다.
"""

from __future__ import annotations

import functools
import http.client
import ipaddress
import math
import socket
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Callable, Final

ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
# 일반 웹 포트와 흔한 대체 웹 포트만 허용한다. 임의 포트를 받으면 이 기능이 내부
# 포트 탐색기로 바뀔 수 있다.
ALLOWED_PORTS: Final[frozenset[int]] = frozenset({80, 443, 8080, 8443})
ALLOWED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain"}
)
MAX_REDIRECTS: Final[int] = 4
MAX_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024
# ``HTTPResponse.read1``은 실제 소켓 읽기 한 번 안에서 돌아온다. 작은 조각마다 전체
# 경과시간을 다시 봐서, 상대가 소켓 제한 직전에 한 바이트씩 흘리는 공격도 끝낸다.
READ_CHUNK_BYTES: Final[int] = 64 * 1024


class UnsafeHomepageUrlError(ValueError):
    """일반적인 안전한 공개 웹 주소가 아니다."""


class HomepageResponseError(Exception):
    """외부 응답이 안전하거나 쓸 수 있는 홈페이지 글자가 아니다."""


@dataclass(frozen=True)
class SafeTarget:
    """검증한 뒤 공인 IP 하나에 고정한 접속 대상."""

    scheme: str
    hostname: str
    port: int
    ip: str


def resolve_safe_target(url: str) -> SafeTarget:
    """URL과 모든 DNS 응답을 검사하고 공인 IP 하나에 고정한다."""

    if not isinstance(url, str) or not url or _has_control_character(url):
        raise UnsafeHomepageUrlError("URL 글자가 올바르지 않습니다")
    if "\\" in url:
        # 역슬래시는 URL 파서와 HTTP 클라이언트가 다르게 해석한 사례가 있다.
        raise UnsafeHomepageUrlError("URL에는 역슬래시를 쓸 수 없습니다")

    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise UnsafeHomepageUrlError("URL 형식이 올바르지 않습니다") from exc

    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeHomepageUrlError("http와 https 주소만 허용합니다")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeHomepageUrlError("URL에 계정 정보를 넣을 수 없습니다")
    if not hostname:
        raise UnsafeHomepageUrlError("URL에 호스트 이름이 없습니다")

    hostname = hostname.rstrip(".")
    if not hostname or len(hostname) > 253:
        raise UnsafeHomepageUrlError("호스트 이름이 올바르지 않습니다")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeHomepageUrlError("국제 도메인 이름이 올바르지 않습니다") from exc

    if ascii_hostname.lower() == "localhost" or ascii_hostname.lower().endswith(
        ".localhost"
    ):
        raise UnsafeHomepageUrlError("로컬 호스트 이름은 허용하지 않습니다")

    destination_port = port or (443 if scheme == "https" else 80)
    if destination_port not in ALLOWED_PORTS:
        raise UnsafeHomepageUrlError("웹이 아닌 대상 포트는 허용하지 않습니다")

    try:
        answers = socket.getaddrinfo(
            ascii_hostname,
            destination_port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (OSError, UnicodeError) as exc:
        raise UnsafeHomepageUrlError("호스트 이름을 찾지 못했습니다") from exc

    public_ips: list[str] = []
    saw_forbidden_address = False
    for _family, _socktype, _proto, _canonname, sockaddr in answers:
        candidate = str(sockaddr[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            # ::ffff:127.0.0.1 같은 모양에도 IPv4 규칙을 적용한다.
            address = address.ipv4_mapped
        if _is_forbidden_address(address):
            saw_forbidden_address = True
            continue
        normalized = str(address)
        if normalized not in public_ips:
            public_ips.append(normalized)

    # 공인·사설 주소가 섞인 DNS 응답은 재바인딩 공격 모양일 수 있어 전부 거부한다.
    if saw_forbidden_address or not public_ips:
        raise UnsafeHomepageUrlError("호스트가 공인 IP로 확인되지 않았습니다")
    return SafeTarget(
        scheme=scheme,
        hostname=ascii_hostname,
        port=destination_port,
        ip=public_ips[0],
    )


def safe_urlopen(
    request: urllib.request.Request,
    *,
    timeout: float,
):
    """리다이렉트 재검사와 DNS 고정을 적용해 HTTP(S) 요청을 연다."""

    if any(name.lower() == "host" for name, _value in request.header_items()):
        # Host는 반드시 검증한 URL에서 만들어야 한다.
        raise UnsafeHomepageUrlError("사용자 지정 Host 헤더는 허용하지 않습니다")
    target = resolve_safe_target(request.full_url)
    request = _ascii_hostname_request(request, target)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _SafeHTTPHandler(),
        _SafeHTTPSHandler(context=ssl.create_default_context()),
        _SafeRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


def _ascii_hostname_request(
    request: urllib.request.Request, target: SafeTarget
) -> urllib.request.Request:
    """HTTP Host와 TLS SNI에 쓸 수 있는 IDNA 호스트로 요청을 복사한다."""

    parsed = urllib.parse.urlsplit(request.full_url)
    display_host = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
    explicit_port = parsed.port
    netloc = f"{display_host}:{explicit_port}" if explicit_port else display_host
    # urllib/http.client는 ASCII 요청 대상을 요구한다. 기존 % 이스케이프는 유지하고
    # 한글 등 유니코드 경로만 안전하게 인코딩한다.
    quoted_path = urllib.parse.quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    quoted_query = urllib.parse.quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    ascii_url = urllib.parse.urlunsplit(
        parsed._replace(netloc=netloc, path=quoted_path, query=quoted_query, fragment="")
    )
    return urllib.request.Request(
        ascii_url,
        data=request.data,
        headers=dict(request.header_items()),
        unverifiable=request.unverifiable,
        method=request.get_method(),
    )


def validate_text_response(response: object) -> None:
    """글자가 아닌 응답과 선언된 크기가 지나친 응답을 거부한다."""

    headers = getattr(response, "headers", None)
    content_type = _content_type(headers)
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HomepageResponseError(
            f"지원하지 않는 응답 형식: {content_type or '없음'}"
        )

    content_length = _header_value(headers, "Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError) as exc:
            raise HomepageResponseError("Content-Length가 올바르지 않습니다") from exc
        if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
            raise HomepageResponseError("홈페이지 응답이 너무 큽니다")


def read_limited_text(
    response: object,
    *,
    timeout: float,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """바이트·전체시간 상한 안에서 홈페이지 글자를 읽고 디코딩한다.

    ``urllib`` 제한은 소켓 작업 한 번의 무응답 시간이다. 별도 전체 마감이 없으면
    상대가 제한 직전마다 한 바이트를 보내 요청을 계속 붙잡을 수 있다. 그래서
    ``read1``로 작게 읽고 실제 읽기마다 하나의 절대 마감을 다시 확인한다.
    """

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout은 유한한 양수여야 합니다")

    deadline = clock() + timeout
    validate_text_response(response)
    raw = _read_limited_body(response, deadline=deadline, clock=clock)

    charset = _content_charset(getattr(response, "headers", None))
    if charset:
        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def _read_limited_body(
    response: object,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    """바이트·전체시간 예산을 넘지 않고 응답 본문 하나를 읽는다."""

    reader = getattr(response, "read1", None)
    if not callable(reader):
        # 실제 ``safe_urlopen`` 응답에는 ``read1``이 있다. 단순 파일 모양 시험 대역도
        # 쓸 수 있게 ``read``를 보조 경로로 둔다.
        reader = getattr(response, "read", None)
    if not callable(reader):
        raise HomepageResponseError("홈페이지 응답을 읽을 수 없습니다")

    body = bytearray()
    while True:
        remaining_seconds = deadline - clock()
        if remaining_seconds <= 0:
            raise HomepageResponseError("홈페이지 응답 시간이 초과됐습니다")
        _set_response_socket_timeout(response, remaining_seconds)

        requested = min(
            READ_CHUNK_BYTES,
            MAX_RESPONSE_BYTES + 1 - len(body),
        )
        try:
            chunk = reader(requested)
        except TimeoutError as exc:
            raise HomepageResponseError("홈페이지 응답 시간이 초과됐습니다") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise HomepageResponseError(
                "홈페이지 응답을 읽지 못했습니다"
            ) from exc

        # 매 실제 읽기 직후 확인해야 한 바이트씩 흘리는 우회가 닫힌다.
        if clock() >= deadline:
            raise HomepageResponseError("홈페이지 응답 시간이 초과됐습니다")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise HomepageResponseError("홈페이지 응답 데이터가 올바르지 않습니다")
        if not chunk:
            return bytes(body)

        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise HomepageResponseError("홈페이지 응답이 너무 큽니다")


def _set_response_socket_timeout(response: object, remaining_seconds: float) -> None:
    """HTTPResponse 소켓 제한을 남은 전체시간까지로 줄인다.

    마지막 블로킹 읽기가 절대 마감 뒤로 다시 한 번 긴 무응답 시간만큼 넘어가지
    않도록 하는 보조 방어다. 주 방어는 monotonic 마감 확인이다.
    """

    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if not callable(settimeout):
        return
    try:
        settimeout(remaining_seconds)
    except (OSError, TypeError, ValueError):
        # 닫힌 응답·시험 대역은 바꿀 소켓이 없을 수 있다. monotonic 검사는 남는다.
        return


def _is_forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """전 세계 공개망에서 라우팅되지 않는 모든 주소 종류면 참을 돌려준다."""

    return (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _header_value(headers: object, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    return str(value) if value is not None else None


def _content_type(headers: object) -> str:
    raw = (_header_value(headers, "Content-Type") or "").split(";", 1)[0]
    return raw.strip().lower()


def _content_charset(headers: object) -> str | None:
    if isinstance(headers, Message):
        return headers.get_content_charset()
    raw = _header_value(headers, "Content-Type") or ""
    for item in raw.split(";")[1:]:
        name, separator, value = item.partition("=")
        if separator and name.strip().lower() == "charset":
            return value.strip().strip('"') or None
    return None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """소켓 목적지를 이미 검사한 IP로 고정한 HTTP 연결."""

    def __init__(self, host: str, *, pinned_ip: str, **kwargs: object) -> None:
        super().__init__(host, **kwargs)
        original_create_connection = self._create_connection

        def create_pinned_connection(
            address: tuple[str, int],
            timeout: object,
            source_address: tuple[str, int] | None = None,
        ) -> socket.socket:
            return original_create_connection(
                (pinned_ip, address[1]), timeout, source_address
            )

        self._create_connection = create_pinned_connection


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS 연결. TLS는 계속 원래 호스트 이름을 검증한다."""

    def __init__(self, host: str, *, pinned_ip: str, **kwargs: object) -> None:
        super().__init__(host, **kwargs)
        original_create_connection = self._create_connection

        def create_pinned_connection(
            address: tuple[str, int],
            timeout: object,
            source_address: tuple[str, int] | None = None,
        ) -> socket.socket:
            return original_create_connection(
                (pinned_ip, address[1]), timeout, source_address
            )

        self._create_connection = create_pinned_connection


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request: urllib.request.Request):
        target = resolve_safe_target(request.full_url)
        connection = functools.partial(_PinnedHTTPConnection, pinned_ip=target.ip)
        return self.do_open(connection, request)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request: urllib.request.Request):
        target = resolve_safe_target(request.full_url)
        connection = functools.partial(_PinnedHTTPSConnection, pinned_ip=target.ip)
        return self.do_open(connection, request, context=self._context)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS
    max_repeats = MAX_REDIRECTS

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # 다음 요청을 만들기 전에 모든 Location을 검사한다. 실제 연결 순간 프로토콜
        # 처리기가 한 번 더 검사·고정해 DNS 재바인딩의 시간차 틈을 닫는다.
        resolve_safe_target(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)

    def http_error_302(self, request, fp, code, msg, headers):
        # 표준 처리기는 Location을 따라가기 전 제한 없는 ``fp.read()``로 리다이렉트
        # 본문을 비운다. 악성 서버가 거대한 본문을 붙이지 못하게 읽지 않고 닫는다.
        body = _UndrainedRedirectBody(fp)
        try:
            return super().http_error_302(request, body, code, msg, headers)
        finally:
            fp.close()

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class _UndrainedRedirectBody:
    """urllib가 리다이렉트 본문을 버퍼링하지 못하게 하는 최소 대리 객체."""

    def __init__(self, response: object) -> None:
        self._response = response

    def read(self, *args: object, **kwargs: object) -> bytes:
        return b""

    def close(self) -> None:
        self._response.close()  # type: ignore[attr-defined]

    def __getattr__(self, name: str):
        return getattr(self._response, name)
