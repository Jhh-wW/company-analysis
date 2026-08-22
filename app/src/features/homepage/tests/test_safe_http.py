from __future__ import annotations

import socket
import urllib.error
import urllib.request
from email.message import Message

import pytest

from src.features.homepage import logic as homepage_logic
from src.features.homepage import safe_http
from src.features.homepage.logic import HomepageFetchError, default_fetch
from src.features.homepage.safe_http import (
    HomepageResponseError,
    UnsafeHomepageUrlError,
    read_limited_text,
    resolve_safe_target,
    validate_text_response,
)


def _dns_answer(ip: str) -> list[tuple]:
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::",
    ],
)
def test_private_and_special_dns_answers_are_rejected(monkeypatch, ip: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _dns_answer(ip))

    with pytest.raises(UnsafeHomepageUrlError):
        resolve_safe_target("https://company.example/about")


def test_private_ip_literal_is_rejected_without_opening_a_socket() -> None:
    with pytest.raises(HomepageFetchError, match="UnsafeHomepageUrlError"):
        default_fetch("http://127.0.0.1/admin")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:password@example.com/",
        "https://example.com:22/",
        "https://example.com\\@127.0.0.1/",
    ],
)
def test_non_web_or_ambiguous_urls_are_rejected(monkeypatch, url: str) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_answer("93.184.216.34"),
    )

    with pytest.raises(UnsafeHomepageUrlError):
        resolve_safe_target(url)


def test_valid_public_url_is_resolved_and_pinned(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_answer("93.184.216.34"),
    )

    target = resolve_safe_target("https://company.example/about")

    assert target.scheme == "https"
    assert target.hostname == "company.example"
    assert target.port == 443
    assert target.ip == "93.184.216.34"


def test_mixed_public_and_private_dns_answers_fail_closed(monkeypatch) -> None:
    mixed = _dns_answer("93.184.216.34") + _dns_answer("10.0.0.7")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: mixed)

    with pytest.raises(UnsafeHomepageUrlError):
        resolve_safe_target("https://company.example/")


@pytest.mark.parametrize(
    "mapped_ip",
    ["::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1"],
)
def test_ipv4_mapped_private_ipv6_is_rejected(monkeypatch, mapped_ip: str) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: _dns_answer(mapped_ip)
    )

    with pytest.raises(UnsafeHomepageUrlError):
        resolve_safe_target("https://company.example/")


def test_idna_hostname_is_used_for_host_header_and_sni(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_answer("93.184.216.34"),
    )
    target = resolve_safe_target("https://회사.example/")
    request = safe_http._ascii_hostname_request(
        urllib.request.Request("https://회사.example/소개"), target
    )

    expected_host = f"{'회사'.encode('idna').decode('ascii')}.example"
    assert request.host == expected_host
    assert request.full_url.startswith(f"https://{expected_host}/")
    assert "%EC%86%8C%EA%B0%9C" in request.full_url


def test_control_characters_in_host_are_rejected() -> None:
    with pytest.raises(UnsafeHomepageUrlError):
        resolve_safe_target("https://company.example\r\nHost:127.0.0.1/")


def test_custom_host_header_is_rejected_before_dns_or_connection() -> None:
    request = urllib.request.Request(
        "https://company.example/", headers={"Host": "127.0.0.1"}
    )

    with pytest.raises(UnsafeHomepageUrlError, match="Host 헤더"):
        safe_http.safe_urlopen(request, timeout=1)


@pytest.mark.parametrize(
    ("redirect_url", "resolved_ip"),
    [
        ("http://127.0.0.1/admin", "127.0.0.1"),
        ("http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("http://[::1]/admin", "::1"),
    ],
)
def test_redirect_to_private_network_is_rejected(
    monkeypatch, redirect_url: str, resolved_ip: str
) -> None:
    def fake_dns(host: str, *args, **kwargs):
        if host in {"127.0.0.1", "169.254.169.254", "::1"}:
            return _dns_answer(resolved_ip)
        return _dns_answer("93.184.216.34")

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    handler = safe_http._SafeRedirectHandler()
    original = urllib.request.Request("https://company.example/")

    with pytest.raises(UnsafeHomepageUrlError):
        handler.redirect_request(original, None, 302, "Found", {}, redirect_url)


@pytest.mark.parametrize(
    "redirect_url",
    ["https://user:password@example.com/", "https://example.com:22/admin"],
)
def test_redirect_with_credentials_or_non_web_port_is_rejected(
    monkeypatch, redirect_url: str
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_answer("93.184.216.34"),
    )
    handler = safe_http._SafeRedirectHandler()

    with pytest.raises(UnsafeHomepageUrlError):
        handler.redirect_request(
            urllib.request.Request("https://company.example/"),
            None,
            302,
            "Found",
            {},
            redirect_url,
        )


def test_relative_redirect_is_joined_then_validated(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns_answer("93.184.216.34"),
    )
    opened: list[tuple[str, object]] = []

    class _Parent:
        def open(self, request, timeout=None):
            opened.append((request.full_url, timeout))
            return "followed"

    class _RedirectBody:
        def __init__(self) -> None:
            self.close_count = 0

        def read(self):
            raise AssertionError("redirect body must not be buffered")

        def close(self):
            self.close_count += 1

    handler = safe_http._SafeRedirectHandler()
    handler.parent = _Parent()
    original = urllib.request.Request("https://company.example/old")
    original.timeout = 3

    redirect_body = _RedirectBody()
    result = handler.http_error_302(
        original,
        redirect_body,
        302,
        "Found",
        {"location": "/about"},
    )

    assert result == "followed"
    assert opened == [("https://company.example/about", 3)]
    assert redirect_body.close_count >= 1


def test_redirect_handler_has_a_small_hard_limit() -> None:
    assert safe_http._SafeRedirectHandler.max_redirections == safe_http.MAX_REDIRECTS
    assert safe_http.MAX_REDIRECTS == 4


def test_redirect는_연결전에_호출자_robots_정책을_적용한다(monkeypatch) -> None:
    def should_not_resolve(_url: str):
        raise AssertionError("차단 경로는 DNS 확인이나 연결 단계로 넘어가면 안 됩니다")

    monkeypatch.setattr(safe_http, "resolve_safe_target", should_not_resolve)
    handler = safe_http._SafeRedirectHandler(url_allowed=lambda _url: False)
    original = urllib.request.Request("https://company.example/about")

    with pytest.raises(safe_http.UnsafeHomepageUrlError):
        handler.redirect_request(
            original,
            None,
            302,
            "Found",
            {},
            "https://company.example/private",
        )


def test_https_socket_is_pinned_but_sni_keeps_original_hostname(monkeypatch) -> None:
    connections: list[tuple[str, int]] = []
    server_names: list[str] = []

    class _Socket:
        def setsockopt(self, *args) -> None:
            return None

    class _Context:
        def wrap_socket(self, sock, *, server_hostname):
            server_names.append(server_hostname)
            return sock

    def fake_create_connection(address, timeout, source_address=None):
        connections.append(address)
        return _Socket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    connection = safe_http._PinnedHTTPSConnection(
        "company.example:443",
        pinned_ip="93.184.216.34",
        context=_Context(),
    )

    connection.connect()

    assert connections == [("93.184.216.34", 443)]
    assert server_names == ["company.example"]


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | Message) -> None:
        self.body = body
        self.headers = headers
        self.offset = 0
        self.read_sizes: list[int] = []

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_chunked_response_over_byte_limit_is_rejected() -> None:
    response = _FakeResponse(
        b"x" * (safe_http.MAX_RESPONSE_BYTES + 1),
        {"Content-Type": "text/html; charset=utf-8"},
    )

    with pytest.raises(HomepageResponseError, match="너무 큽니다"):
        read_limited_text(response, timeout=1)

    assert sum(response.read_sizes) == safe_http.MAX_RESPONSE_BYTES + 1
    assert max(response.read_sizes) <= safe_http.READ_CHUNK_BYTES


def test_response_at_exact_byte_limit_is_accepted() -> None:
    response = _FakeResponse(
        b"x" * safe_http.MAX_RESPONSE_BYTES,
        {"Content-Type": "text/plain; charset=utf-8"},
    )

    result = read_limited_text(response, timeout=1)

    assert len(result) == safe_http.MAX_RESPONSE_BYTES
    assert response.offset == safe_http.MAX_RESPONSE_BYTES


def test_declared_oversized_response_is_rejected_before_reading() -> None:
    response = _FakeResponse(
        b"not read",
        {
            "Content-Type": "text/html",
            "Content-Length": str(safe_http.MAX_RESPONSE_BYTES + 1),
        },
    )

    with pytest.raises(HomepageResponseError, match="너무 큽니다"):
        read_limited_text(response, timeout=1)

    assert response.read_sizes == []


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "application/json", ""])
def test_non_text_response_is_rejected(content_type: str) -> None:
    response = _FakeResponse(b"data", {"Content-Type": content_type})

    with pytest.raises(HomepageResponseError, match="응답 형식"):
        validate_text_response(response)


def test_missing_content_type_on_real_http_message_is_rejected() -> None:
    response = _FakeResponse(b"data", Message())

    with pytest.raises(HomepageResponseError, match="응답 형식"):
        validate_text_response(response)


def test_ordinary_html_response_is_accepted_and_decoded() -> None:
    response = _FakeResponse(
        "<p>회사 소개</p>".encode(),
        {"Content-Type": "text/html; charset=utf-8"},
    )

    assert read_limited_text(response, timeout=1) == "<p>회사 소개</p>"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_slow_drip_body_is_stopped_by_total_wall_clock_deadline() -> None:
    clock = _FakeClock()
    socket_timeouts: list[float] = []

    class _Socket:
        def settimeout(self, value: float) -> None:
            socket_timeouts.append(value)

    class _Raw:
        _sock = _Socket()

    class _FP:
        raw = _Raw()

    class _SlowDripResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}
        fp = _FP()

        def __init__(self) -> None:
            self.read_count = 0

        def read1(self, size: int) -> bytes:
            assert size <= safe_http.READ_CHUNK_BYTES
            self.read_count += 1
            # Every read succeeds before a hypothetical one-second inactivity
            # timeout, but the total body time still crosses one second.
            clock.advance(0.4)
            return b"x"

    response = _SlowDripResponse()
    with pytest.raises(HomepageResponseError, match="시간이 초과"):
        read_limited_text(response, timeout=1.0, clock=clock)

    assert response.read_count == 3
    assert socket_timeouts == pytest.approx([1.0, 0.6, 0.2])


def test_default_fetch_uses_same_timeout_for_open_and_body(monkeypatch) -> None:
    captured: dict[str, float] = {}
    response = object()

    class _Opened:
        def __enter__(self):
            return response

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_open(request, *, timeout, url_allowed=None):
        del request
        assert url_allowed is None
        captured["open"] = timeout
        return _Opened()

    def fake_read(received, *, timeout):
        assert received is response
        captured["body"] = timeout
        return "<p>회사 소개</p>"

    monkeypatch.setattr(homepage_logic, "safe_urlopen", fake_open)
    monkeypatch.setattr(homepage_logic, "read_limited_text", fake_read)

    fetched = default_fetch("https://company.example/")
    assert fetched.html == "<p>회사 소개</p>"
    assert fetched.effective_url == "https://company.example/"
    assert captured == {
        "open": homepage_logic.TIMEOUT_SEC,
        "body": homepage_logic.TIMEOUT_SEC,
    }


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (404, homepage_logic.HomepageRobotsUnavailable),
        (503, homepage_logic.HomepageRobotsUnreachable),
    ],
)
def test_default_fetch는_robots_4xx와_5xx를_구분한다(
    monkeypatch,
    status: int,
    error_type: type[Exception],
) -> None:
    robots_url = "https://company.example/robots.txt"

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(robots_url, status, "가짜 오류", {}, None)

    monkeypatch.setattr(homepage_logic, "safe_urlopen", fail)

    with pytest.raises(error_type):
        default_fetch(robots_url)


def test_open에서_쓴_시간과_body시간은_같은_절대마감을_공유한다() -> None:
    clock = _FakeClock()
    budget = safe_http._DeadlineBudget.after(1.0, clock=clock)

    class _SharedDeadlineResponse(_FakeResponse):
        def read1(self, size: int) -> bytes:
            clock.advance(0.4)
            return super().read1(size)

    response = _SharedDeadlineResponse(
        b"x" * 10,
        {"Content-Type": "text/plain; charset=utf-8"},
    )
    setattr(response, safe_http._RESPONSE_DEADLINE_ATTR, budget)
    clock.advance(0.7)  # DNS·redirect·연결/헤더에서 이미 소비한 시간

    with pytest.raises(HomepageResponseError, match="시간이 초과"):
        read_limited_text(response, timeout=10.0, clock=clock)
    assert response.offset == 10


def test_DNS는_작업마감_이내_별도상한과_cache를_공유한다(monkeypatch) -> None:
    clock = _FakeClock()
    budget = safe_http._DeadlineBudget.after(5.0, clock=clock)
    calls: list[float] = []

    def fake_dns(hostname: str, port: int, *, timeout: float):
        assert (hostname, port) == ("company.example", 443)
        calls.append(timeout)
        clock.advance(2.0)
        return tuple(_dns_answer("93.184.216.34"))

    monkeypatch.setattr(safe_http, "_system_dns_answers", fake_dns)
    monkeypatch.setattr(socket, "getaddrinfo", safe_http._SYSTEM_GETADDRINFO)

    first = resolve_safe_target(
        "https://company.example/first",
        deadline=budget,
    )
    second = resolve_safe_target(
        "https://company.example/second",
        deadline=budget,
    )

    assert first == second
    assert calls == [float(safe_http.DNS_TIMEOUT_SEC)]
    assert budget.remaining() == pytest.approx(3.0)


def test_DNS_subprocess가_마감되면_강제종료하고_fail_closed한다(
    monkeypatch,
) -> None:
    class _ParentConnection:
        def poll(self, _timeout: float) -> bool:
            return False

        def close(self) -> None:
            return None

    class _ChildConnection:
        def close(self) -> None:
            return None

    class _Process:
        def __init__(self) -> None:
            self.started = False
            self.alive = False
            self.terminated = False

        def start(self) -> None:
            self.started = True
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def join(self, timeout: float) -> None:
            del timeout

    process = _Process()

    class _Context:
        def Pipe(self, *, duplex: bool):
            assert duplex is False
            return _ParentConnection(), _ChildConnection()

        def Process(self, **kwargs):
            assert kwargs["target"] is safe_http._dns_worker
            assert kwargs["daemon"] is True
            return process

    monkeypatch.setattr(
        safe_http.multiprocessing,
        "get_context",
        lambda mode: _Context() if mode == "spawn" else None,
    )

    with pytest.raises(UnsafeHomepageUrlError, match="시간이 초과"):
        safe_http._system_dns_answers("slow.example", 443, timeout=0.01)
    assert process.started
    assert process.terminated
