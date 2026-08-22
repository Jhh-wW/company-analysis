"""컨테이너 readiness probe의 네트워크·응답 경계를 공격적으로 검증한다."""

from __future__ import annotations

import contextlib
import http.server
import importlib.util
import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest


HEALTHCHECK_PATH = Path(__file__).resolve().parents[1] / "container_healthcheck.py"
SPEC = importlib.util.spec_from_file_location(
    "deploy_container_healthcheck", HEALTHCHECK_PATH
)
assert SPEC is not None and SPEC.loader is not None
healthcheck = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = healthcheck
SPEC.loader.exec_module(healthcheck)


@contextlib.contextmanager
def _serve(
    *,
    status: int = 200,
    body: bytes = b'{"status":"ready"}',
    location: str = "",
    content_type: str = "application/json",
) -> Iterator[tuple[int, type[http.server.BaseHTTPRequestHandler]]]:
    class Handler(http.server.BaseHTTPRequestHandler):
        requests = 0

        def do_GET(self) -> None:  # noqa: N802 - 표준 라이브러리 callback 이름
            type(self).requests += 1
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def test_정상_exact_readiness만_통과한다() -> None:
    with _serve() as (port, _handler):
        assert healthcheck.check(port=str(port)) is True


def test_닫힌_loopback을_악성_환경_proxy_200으로_위조할_수_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed_port = _closed_loopback_port()
    with _serve() as (proxy_port, proxy_handler):
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("http_proxy", proxy_url)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")

        assert healthcheck.check(port=str(closed_port)) is False
        assert proxy_handler.requests == 0


@pytest.mark.parametrize(
    "location",
    ("/readyz", "http://127.0.0.1:9/readyz"),
    ids=("relative", "absolute"),
)
def test_redirect는_상대_절대_모두_거부한다(location: str) -> None:
    with _serve(status=302, body=b"", location=location) as (port, _handler):
        assert healthcheck.check(port=str(port)) is False


@pytest.mark.parametrize(
    "body",
    (
        b"",
        b'{"status":"unready"}',
        b'{"status":"ready","extra":true}',
        b'{"status":"unready","status":"ready"}',
    ),
)
def test_빈값_거짓값_추가필드_중복키_200을_거부한다(body: bytes) -> None:
    with _serve(body=body) as (port, _handler):
        assert healthcheck.check(port=str(port)) is False


def test_응답_body_상한을_넘으면_거부한다() -> None:
    body = b'{"status":"ready","padding":"' + (
        b"x" * healthcheck.MAX_RESPONSE_BYTES
    ) + b'"}'
    with _serve(body=body) as (port, _handler):
        assert healthcheck.check(port=str(port)) is False


def test_JSON_media_type과_고정_readyz_path를_강제한다() -> None:
    with _serve(content_type="text/plain") as (port, _handler):
        assert healthcheck.check(port=str(port)) is False
        assert healthcheck.check(port=str(port), path="/healthz") is False
