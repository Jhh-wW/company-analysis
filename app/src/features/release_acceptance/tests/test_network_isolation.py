"""redirect same-origin과 자식 socket/DNS 차단의 공격 회귀시험."""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from src.features.release_acceptance.constants import MAX_REDIRECT_HOPS
from src.features.release_acceptance.logic import (
    AcceptanceBlocked,
    AcceptanceFailure,
    HttpSession,
    _provider_isolation_action,
    build_child_environment,
)


class _RouteServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _RouteHandler)
        self.routes: dict[str, tuple[int, str, bytes]] = {}
        self.requests: list[str] = []


class _RouteHandler(http.server.BaseHTTPRequestHandler):
    server: _RouteServer

    def do_GET(self) -> None:  # noqa: N802 - 표준 handler 계약
        self.server.requests.append(self.path)
        status, location, body = self.server.routes.get(
            self.path,
            (404, "", b"missing"),
        )
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _serve_routes() -> Iterator[_RouteServer]:
    server = _RouteServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_redirect는_다른_loopback_port에_socket을_열지않는다() -> None:
    with _serve_routes() as first:
        with _serve_routes() as second:
            first.routes["/start"] = (
                302,
                f"http://127.0.0.1:{second.server_port}/forged",
                b"",
            )
            second.routes["/forged"] = (200, "", b"forged")

            with pytest.raises(AcceptanceFailure, match="최초 loopback origin"):
                HttpSession(first.server_port).request(
                    "GET",
                    "/start",
                    follow_redirects=True,
                )

            assert first.requests == ["/start"]
            assert second.requests == []


@pytest.mark.parametrize(
    "location_template",
    (
        "http://localhost:{port}/final",
        "http://user@127.0.0.1:{port}/final",
        "/final#fragment",
        "http://example.invalid/final",
        "https://127.0.0.1:{port}/final",
        "ftp://127.0.0.1:{port}/final",
        "//example.invalid/final",
        "//127.0.0.1:{port}/final",
    ),
)
def test_redirect는_origin위장과_fragment를_모두_거부한다(
    location_template: str,
) -> None:
    with _serve_routes() as server:
        server.routes["/start"] = (
            302,
            location_template.format(port=server.server_port),
            b"",
        )

        with pytest.raises(AcceptanceFailure):
            HttpSession(server.server_port).request(
                "GET",
                "/start",
                follow_redirects=True,
            )

        assert server.requests == ["/start"]


def test_redirect는_같은_origin의_유한_multi_hop만_따른다() -> None:
    with _serve_routes() as server:
        server.routes.update(
            {
                "/start": (302, "/middle", b""),
                "/middle": (
                    307,
                    f"http://127.0.0.1:{server.server_port}/final?ok=1",
                    b"",
                ),
                "/final?ok=1": (200, "", b"same-origin-final"),
            }
        )

        response = HttpSession(server.server_port).request(
            "GET",
            "/start",
            follow_redirects=True,
        )

        assert response.status == 200
        assert response.body == b"same-origin-final"
        assert server.requests == ["/start", "/middle", "/final?ok=1"]


def test_redirect_loop와_hop상한은_다음_socket전에_닫힌다() -> None:
    with _serve_routes() as server:
        server.routes["/loop-a"] = (302, "/loop-b", b"")
        server.routes["/loop-b"] = (302, "/loop-a", b"")
        with pytest.raises(AcceptanceFailure, match="순환"):
            HttpSession(server.server_port).request(
                "GET",
                "/loop-a",
                follow_redirects=True,
            )
        assert server.requests == ["/loop-a", "/loop-b"]

    with _serve_routes() as server:
        for index in range(MAX_REDIRECT_HOPS + 2):
            server.routes[f"/hop-{index}"] = (
                302,
                f"/hop-{index + 1}",
                b"",
            )
        with pytest.raises(AcceptanceFailure, match="hop 상한"):
            HttpSession(server.server_port).request(
                "GET",
                "/hop-0",
                follow_redirects=True,
            )
        assert server.requests == [
            f"/hop-{index}" for index in range(MAX_REDIRECT_HOPS + 1)
        ]


def test_자식_guard는_외부DNS와IP를_거부하고_loopback만_허용한다(
    tmp_path: Path,
) -> None:
    app_root = Path(__file__).resolve().parents[4]
    audit_path = tmp_path / "egress-audit.json"
    script = r'''
import socket
import sys
from pathlib import Path

from src.features.release_acceptance.egress_guard import (
    EgressDeniedError,
    install_child_egress_guard,
)

root = Path(sys.argv[1])
audit = Path(sys.argv[2])
guard = install_child_egress_guard(audit_path=audit, data_root=root)
guard.run_self_test()

try:
    socket.getaddrinfo("runtime-egress.invalid", 443)
except EgressDeniedError:
    pass
else:
    raise SystemExit("외부 DNS가 열렸습니다")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as external:
    try:
        external.connect(("198.51.100.1", 443))
    except EgressDeniedError:
        pass
    else:
        raise SystemExit("외부 IP가 열렸습니다")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
    listener.settimeout(1.0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(listener.getsockname())
        accepted, _address = listener.accept()
        accepted.close()
print("guard-ok")
'''
    environment = build_child_environment(
        os.environ,
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path), str(audit_path)],
        cwd=app_root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "guard-ok"
    payload = json.loads(audit_path.read_text(encoding="ascii"))
    assert payload["installed"] is True
    assert payload["loopback_probe_allowed"] is True
    assert payload["self_test_dns_denied"] == 1
    assert payload["self_test_ip_denied"] == 1
    assert payload["runtime_dns_denied"] == 1
    assert payload["runtime_ip_denied"] == 1
    audit_bytes = audit_path.read_bytes()
    assert b"runtime-egress.invalid" not in audit_bytes
    assert b"198.51.100.1" not in audit_bytes


def test_Python_guard는_native_subprocess우회를_OS격리PASS로_승격하지않는다(
    tmp_path: Path,
) -> None:
    audit_path = tmp_path / "egress-audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "schema_version": "release-acceptance-egress-v1",
                "installed": True,
                "loopback_probe_allowed": True,
                "self_test_dns_denied": 1,
                "self_test_ip_denied": 1,
                "self_test_socket_denied": 0,
                "runtime_dns_denied": 0,
                "runtime_ip_denied": 0,
                "runtime_socket_denied": 0,
            },
            ensure_ascii=True,
        ),
        encoding="ascii",
    )
    environment = {
        "PIPELINE": "demo",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "RELEASE_ACCEPTANCE_EGRESS_AUDIT_PATH": str(audit_path),
    }

    with pytest.raises(AcceptanceBlocked, match="Linux network namespace/firewall"):
        _provider_isolation_action(
            (environment,),
            audit_paths=(audit_path,),
        )
