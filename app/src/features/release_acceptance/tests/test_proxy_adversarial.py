"""릴리스 수락 HTTP가 부모 프록시로 우회되지 않는지 독립 공격 검수한다."""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterator

import pytest

from src.features.release_acceptance import logic


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler: type[http.server.BaseHTTPRequestHandler]):
        super().__init__(("127.0.0.1", 0), handler)
        self.requests: list[tuple[str, str, bytes]] = []


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    server: _Server

    def _forged(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.requests.append((self.command, self.path, body))
        payload = b"forged-parent-proxy"
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _forged  # type: ignore[assignment]  # noqa: N815
    do_POST = _forged  # type: ignore[assignment]  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class _OriginHandler(http.server.BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append(("GET", self.path, b""))
        if self.path == "/redirect":
            self.send_response(303)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/set-cookie":
            self.send_response(200)
            self.send_header("Set-Cookie", "acceptance=real; Path=/")
            payload = b"cookie-set"
        elif self.path == "/cookie":
            self.send_response(200)
            payload = self.headers.get("Cookie", "").encode("ascii")
        else:
            self.send_response(200)
            payload = f"origin:{self.path}".encode("ascii")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(("POST", self.path, body))
        payload = b"origin-post:" + body
        self.send_response(201)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _serve(
    handler: type[http.server.BaseHTTPRequestHandler],
) -> Iterator[_Server]:
    server = _Server(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _poison_parent_proxy(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    proxy_url = f"http://127.0.0.1:{port}"
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, proxy_url)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)


def test_closed_origin_cannot_be_forged_as_success_by_parent_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _serve(_ProxyHandler) as proxy:
        _poison_parent_proxy(monkeypatch, proxy.server_port)

        with pytest.raises((OSError, urllib.error.URLError)):
            logic.HttpSession(_closed_port()).request(
                "GET",
                "/readyz",
                timeout=0.5,
            )

        assert proxy.requests == []


def test_every_http_variant_uses_proxy_free_opener_and_real_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_build_opener = logic.urllib.request.build_opener
    opener_handlers: list[tuple[object, ...]] = []

    def observed_build_opener(*handlers: object):
        opener_handlers.append(handlers)
        return real_build_opener(*handlers)

    def forbidden_urlopen(*_args: object, **_kwargs: object):
        raise AssertionError("공통 HttpSession opener 밖의 urlopen을 사용했습니다")

    monkeypatch.setattr(logic.urllib.request, "build_opener", observed_build_opener)
    monkeypatch.setattr(logic.urllib.request, "urlopen", forbidden_urlopen)
    with _serve(_OriginHandler) as origin:
        with _serve(_ProxyHandler) as proxy:
            _poison_parent_proxy(monkeypatch, proxy.server_port)
            session = logic.HttpSession(origin.server_port)

            direct = session.request("GET", "/healthz")
            posted = session.request("POST", "/submit", data={"value": "real"})
            stopped = session.request("GET", "/redirect")
            followed = session.request(
                "GET",
                "/redirect",
                follow_redirects=True,
            )
            session.request("GET", "/set-cookie")
            cookie = session.request("GET", "/cookie")

    assert direct.status == 200 and direct.body == b"origin:/healthz"
    assert posted.status == 201 and posted.body == b"origin-post:value=real"
    assert stopped.status == 303 and stopped.headers["location"] == "/final"
    assert followed.status == 200 and followed.body == b"origin:/final"
    assert cookie.body == b"acceptance=real"
    assert proxy.requests == []
    assert {method for method, _path, _body in origin.requests} == {"GET", "POST"}
    assert opener_handlers
    for handlers in opener_handlers:
        assert isinstance(handlers[0], urllib.request.ProxyHandler)
        assert handlers[0].proxies == {}
