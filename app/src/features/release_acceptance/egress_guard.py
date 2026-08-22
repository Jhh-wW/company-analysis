"""수락시험 자식 프로세스의 비-loopback socket과 DNS를 차단한다.

운영용 방화벽 대체물이 아니라, 무료 로컬 demo 수락시험이 실수로 외부 provider에
접속하는 것을 자식 프로세스 시작 전에 fail-closed하는 시험 경계다. 차단 감사에는
주소·hostname 원문을 남기지 않고 종류별 횟수만 기록한다.
"""

from __future__ import annotations

import _socket
import ipaddress
import json
import os
import socket
import threading
from pathlib import Path

from src.features.release_acceptance.constants import (
    EGRESS_AUDIT_COUNTER_KEYS,
    EGRESS_AUDIT_SCHEMA_VERSION,
    LOOPBACK_HOST,
)


class EgressGuardError(RuntimeError):
    """자식 egress guard 설치·자체검증·감사 기록이 완결되지 않았다."""


class EgressDeniedError(PermissionError):
    """외부 socket 또는 DNS 시도가 OS에 도달하기 전에 거부됐다."""


class ChildEgressGuard:
    def __init__(self, *, audit_path: Path, data_root: Path) -> None:
        root = data_root.expanduser().resolve(strict=True)
        path = audit_path.expanduser()
        parent = path.parent.resolve(strict=True)
        if not root.is_dir() or not parent.is_relative_to(root) or path.is_symlink():
            raise EgressGuardError("egress 감사 경로가 격리 데이터 경계 밖입니다")
        self.audit_path = path
        self._lock = threading.RLock()
        self._phase = "self_test"
        self._counts = {key: 0 for key in EGRESS_AUDIT_COUNTER_KEYS}
        self._loopback_probe_allowed = False
        self._installed = False

    def _payload(self) -> dict[str, object]:
        return {
            "schema_version": EGRESS_AUDIT_SCHEMA_VERSION,
            "installed": self._installed,
            "loopback_probe_allowed": self._loopback_probe_allowed,
            **self._counts,
        }

    def _persist(self) -> None:
        with self._lock:
            if self.audit_path.is_symlink():
                raise EgressGuardError("egress 감사 파일은 심볼릭 링크일 수 없습니다")
            temporary = self.audit_path.with_name(f".{self.audit_path.name}.tmp")
            if temporary.is_symlink():
                raise EgressGuardError("egress 임시 감사 파일은 심볼릭 링크일 수 없습니다")
            try:
                temporary.write_text(
                    json.dumps(
                        self._payload(),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    encoding="ascii",
                )
                os.replace(temporary, self.audit_path)
            except OSError as error:
                raise EgressGuardError("egress 감사 증거를 기록하지 못했습니다") from error

    def mark_installed(self) -> None:
        self._installed = True
        self._persist()

    def deny(self, category: str) -> None:
        normalized = category if category in {"dns", "ip", "socket"} else "socket"
        key = f"{self._phase}_{normalized}_denied"
        with self._lock:
            self._counts[key] += 1
            self._persist()
        label = {"dns": "DNS", "ip": "외부 IP", "socket": "외부 socket"}[normalized]
        raise EgressDeniedError(f"릴리스 수락 자식의 {label} 접근을 차단했습니다")

    def assert_address(self, address: object) -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            self.deny("socket")
        host = address[0]
        normalized = host.decode("ascii", errors="ignore") if isinstance(host, bytes) else str(host)
        if normalized == LOOPBACK_HOST:
            return
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            self.deny("dns")
        self.deny("ip")

    def assert_dns_host(self, host: object) -> None:
        normalized = host.decode("ascii", errors="ignore") if isinstance(host, bytes) else str(host)
        if normalized == LOOPBACK_HOST:
            return
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            self.deny("dns")
        self.deny("ip")

    def run_self_test(self) -> None:
        if not self._installed:
            raise EgressGuardError("egress guard 설치 전에 자체검증할 수 없습니다")
        try:
            socket.getaddrinfo("release-acceptance-egress.invalid", 443)
        except EgressDeniedError:
            pass
        else:
            raise EgressGuardError("외부 DNS 자체검증이 차단되지 않았습니다")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as external:
            try:
                external.connect(("192.0.2.1", 443))
            except EgressDeniedError:
                pass
            else:
                raise EgressGuardError("외부 IP 자체검증이 차단되지 않았습니다")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.settimeout(1.0)
            listener.bind((LOOPBACK_HOST, 0))
            listener.listen(1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                client.settimeout(1.0)
                client.connect(listener.getsockname())
                accepted, _address = listener.accept()
                accepted.close()
        self._loopback_probe_allowed = True
        if self._counts["self_test_dns_denied"] != 1 or self._counts["self_test_ip_denied"] != 1:
            raise EgressGuardError("egress 자체검증 차단 횟수가 정확하지 않습니다")
        self._phase = "runtime"
        self._persist()


_INSTALLED_GUARD: ChildEgressGuard | None = None


def install_child_egress_guard(
    *,
    audit_path: Path,
    data_root: Path,
) -> ChildEgressGuard:
    """표준 socket 생성·DNS 경계를 한 번만 교체한다."""

    global _INSTALLED_GUARD
    if _INSTALLED_GUARD is not None:
        raise EgressGuardError("egress guard는 자식 프로세스에서 한 번만 설치할 수 있습니다")
    guard = ChildEgressGuard(audit_path=audit_path, data_root=data_root)
    original_socket = socket.socket
    original_getaddrinfo = socket.getaddrinfo

    class GuardedSocket(original_socket):
        def connect(self, address: object) -> None:
            guard.assert_address(address)
            return super().connect(address)

        def connect_ex(self, address: object) -> int:
            guard.assert_address(address)
            return super().connect_ex(address)

        def bind(self, address: object) -> None:
            guard.assert_address(address)
            return super().bind(address)

        def sendto(self, data: bytes, *args: object) -> int:
            if args:
                guard.assert_address(args[-1])
            return super().sendto(data, *args)

        if hasattr(original_socket, "sendmsg"):
            def sendmsg(self, buffers, *args):  # noqa: ANN001
                if len(args) >= 3:
                    guard.assert_address(args[-1])
                return super().sendmsg(buffers, *args)

    def guarded_getaddrinfo(
        host: object,
        port: object,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ):
        guard.assert_dns_host(host)
        return original_getaddrinfo(host, port, family, type, proto, flags)

    def denied_dns(*_args: object, **_kwargs: object):
        guard.deny("dns")

    socket.socket = GuardedSocket
    socket.getaddrinfo = guarded_getaddrinfo
    _socket.getaddrinfo = guarded_getaddrinfo
    for name in ("gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
        if hasattr(socket, name):
            setattr(socket, name, denied_dns)
        if hasattr(_socket, name):
            setattr(_socket, name, denied_dns)
    _INSTALLED_GUARD = guard
    guard.mark_installed()
    return guard


__all__ = [
    "ChildEgressGuard",
    "EgressDeniedError",
    "EgressGuardError",
    "install_child_egress_guard",
]
