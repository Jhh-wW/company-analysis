"""배포 계약에 따른 웹 요청 신뢰 경계를 한곳에서 판정한다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit


ENV_DEPLOYMENT_RUNTIME_CONTRACT: Final[str] = "DEPLOYMENT_RUNTIME_CONTRACT"
ENV_PUBLIC_ORIGIN: Final[str] = "PUBLIC_ORIGIN"
RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT: Final[str] = (
    "render-admin-demo-no-forwarded-v1"
)

HttpOrigin = tuple[str, str, int]


def _environment(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def render_admin_demo_no_forwarded(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """고정 공개 출처만 믿는 Render 관리자 데모 계약인지 반환한다."""

    values = _environment(environment)
    return (
        values.get(ENV_DEPLOYMENT_RUNTIME_CONTRACT, "").strip()
        == RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT
    )


def http_origin(
    raw: str,
    *,
    require_https: bool = False,
    allow_trailing_slash: bool = False,
) -> HttpOrigin | None:
    """경로 없는 HTTP(S) 출처를 비교 가능한 tuple로 정규화한다."""

    try:
        parsed = urlsplit(str(raw or "").strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    allowed_paths = {"", "/"} if allow_trailing_slash else {""}
    if (
        scheme not in {"http", "https"}
        or (require_https and scheme != "https")
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in allowed_paths
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    return scheme, hostname, effective_port


def configured_public_origin(
    environment: Mapping[str, str] | None = None,
) -> HttpOrigin | None:
    """좁은 계약의 고정 HTTPS ``PUBLIC_ORIGIN``을 반환하며 fallback하지 않는다."""

    values = _environment(environment)
    if not render_admin_demo_no_forwarded(values):
        return None
    return http_origin(
        values.get(ENV_PUBLIC_ORIGIN, ""),
        require_https=True,
        allow_trailing_slash=True,
    )


def configured_public_host_matches(
    raw_host: str,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """요청 ``Host``가 고정 공개 출처의 host·port와 정확히 같은지 판정한다."""

    expected = configured_public_origin(environment)
    if expected is None:
        return False
    value = str(raw_host or "").strip()
    if not value or any(character in value for character in "/?#,"):
        return False
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return False
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return False
    expected_scheme, expected_hostname, expected_port = expected
    effective_port = port if port is not None else (
        443 if expected_scheme == "https" else 80
    )
    return (
        parsed.hostname.lower() == expected_hostname
        and effective_port == expected_port
    )


def fixed_public_https_origin(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """좁은 계약과 유효한 고정 HTTPS 출처가 함께 설정됐는지 반환한다."""

    return configured_public_origin(environment) is not None
