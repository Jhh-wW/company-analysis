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
RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT: Final[str] = (
    "render-admin-real-no-forwarded-v1"
)
RENDER_ADMIN_NO_FORWARDED_CONTRACTS: Final[frozenset[str]] = frozenset(
    {
        RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    }
)
#: 초대 명단 친구에게 링크·초대·QR 입구를 여는 Render 계약. ★ 일부러
#: ``RENDER_ADMIN_NO_FORWARDED_CONTRACTS``에 넣지 않는다 — 그 집합에 있으면
#: `/admin` 라우터의 LINK 발급·초대(`admin.py`)와 이 모듈의 `/k/`·LINK 쿠키
#: 차단이 자동으로 걸린다. 이 계약은 그 차단을 걸지 «않는» 것이 목적이므로,
#: admin.py를 고치지 않고 이 상수 하나로 손님 입구를 연다.
RENDER_PORTFOLIO_LINK_CONTRACT: Final[str] = "render-portfolio-link-v1"
#: forwarded 헤더를 안 믿고 **하나의 고정 HTTPS 출처**만 진실로 보는 모든 계약.
#: ``RENDER_ADMIN_NO_FORWARDED_CONTRACTS``(친구 입구 차단)보다 «넓다» — 포트폴리오
#: 링크 계약도 render.yaml의 같은 ``--no-proxy-headers`` 실행 모델을 그대로 쓰므로
#: Host 고정·CSRF Origin 고정·HSTS 판정은 옛 관리자 계약과 똑같이 켜져 있어야 한다.
#: ★ 두 집합을 하나로 합치면 admin.py의 «친구 입구 차단»까지 포트폴리오 계약에
#: 다시 걸린다 — 그래서 일부러 나눴다.
RENDER_PINNED_ORIGIN_CONTRACTS: Final[frozenset[str]] = (
    RENDER_ADMIN_NO_FORWARDED_CONTRACTS | {RENDER_PORTFOLIO_LINK_CONTRACT}
)

HttpOrigin = tuple[str, str, int]


def _environment(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def _runtime_contract(environment: Mapping[str, str]) -> str:
    """시작 검증기와 같은 방식으로 runtime contract를 정규화한다."""

    return environment.get(ENV_DEPLOYMENT_RUNTIME_CONTRACT, "").strip().lower()


def render_admin_demo_no_forwarded(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """고정 공개 출처만 믿는 Render 관리자 데모 계약인지 반환한다."""

    values = _environment(environment)
    return _runtime_contract(values) == RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT


def render_admin_real_no_forwarded(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """실제 조사를 쓰되 고정 공개 출처만 믿는 Render 관리자 계약인지 반환한다."""

    values = _environment(environment)
    return _runtime_contract(values) == RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT


def render_admin_no_forwarded(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """forwarded 헤더를 믿지 않는 Render 관리자 전용 계약인지 반환한다."""

    values = _environment(environment)
    return _runtime_contract(values) in RENDER_ADMIN_NO_FORWARDED_CONTRACTS


def render_portfolio_link(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """초대 명단 친구에게 링크·초대·QR 입구를 여는 Render 계약인지 반환한다."""

    values = _environment(environment)
    return _runtime_contract(values) == RENDER_PORTFOLIO_LINK_CONTRACT


def render_pinned_origin_no_forwarded(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """forwarded 헤더를 안 믿고 하나의 고정 HTTPS 출처만 믿는 계약인지 반환한다.

    ``render_admin_no_forwarded()``보다 넓다 — 옛 관리자 두 계약에 포트폴리오
    링크 계약까지 포함한다. Host 고정·CSRF Origin 고정처럼 «친구 입구를 여는가»와
    무관하게 계속 켜져 있어야 하는 보안 판정에서만 쓴다.
    """

    values = _environment(environment)
    return _runtime_contract(values) in RENDER_PINNED_ORIGIN_CONTRACTS


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
    if not render_pinned_origin_no_forwarded(values):
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
