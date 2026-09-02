from __future__ import annotations

import pytest

from src.web import deployment_mode


CONTRACT = deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT
REAL_CONTRACT = deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT: CONTRACT,
        deployment_mode.ENV_PUBLIC_ORIGIN: "https://demo.example",
    }
    values.update(overrides)
    return values


def test_좁은계약은_PUBLIC_ORIGIN만_고정출처로_쓴다() -> None:
    environment = _environment(RENDER_EXTERNAL_URL="https://fallback.example")

    assert deployment_mode.render_admin_demo_no_forwarded(environment)
    assert deployment_mode.render_admin_no_forwarded(environment)
    assert deployment_mode.configured_public_origin(environment) == (
        "https",
        "demo.example",
        443,
    )

    environment.pop(deployment_mode.ENV_PUBLIC_ORIGIN)
    assert deployment_mode.configured_public_origin(environment) is None


def test_실제분석관리자계약도_같은_고정출처경계를_쓴다() -> None:
    environment = _environment(
        DEPLOYMENT_RUNTIME_CONTRACT=REAL_CONTRACT,
        RENDER_EXTERNAL_URL="https://fallback.example",
    )

    assert deployment_mode.render_admin_real_no_forwarded(environment)
    assert not deployment_mode.render_admin_demo_no_forwarded(environment)
    assert deployment_mode.render_admin_no_forwarded(environment)
    assert deployment_mode.configured_public_origin(environment) == (
        "https",
        "demo.example",
        443,
    )
    assert deployment_mode.configured_public_host_matches(
        "demo.example", environment
    )
    assert not deployment_mode.configured_public_host_matches(
        "attacker.example", environment
    )
    assert deployment_mode.fixed_public_https_origin(environment)


def test_runtime계약_대소문자가_달라도_시작검증과_같은_관리자잠금을_쓴다() -> None:
    environment = _environment(
        DEPLOYMENT_RUNTIME_CONTRACT=" RENDER-ADMIN-REAL-NO-FORWARDED-V1 "
    )

    assert deployment_mode.render_admin_real_no_forwarded(environment)
    assert deployment_mode.render_admin_no_forwarded(environment)
    assert deployment_mode.configured_public_origin(environment) == (
        "https",
        "demo.example",
        443,
    )


@pytest.mark.parametrize(
    "public_origin",
    (
        "",
        "http://demo.example",
        "https://user@demo.example",
        "https://demo.example/path",
        "https://demo.example?query=1",
        "https://demo.example#fragment",
        "https://demo.example:99999",
    ),
)
def test_좁은계약은_경로없는_HTTPS_PUBLIC_ORIGIN만_받는다(
    public_origin: str,
) -> None:
    assert (
        deployment_mode.configured_public_origin(
            _environment(PUBLIC_ORIGIN=public_origin)
        )
        is None
    )


def test_PUBLIC_ORIGIN의_끝슬래시와_기본HTTPS포트를_정규화한다() -> None:
    assert deployment_mode.configured_public_origin(
        _environment(PUBLIC_ORIGIN="https://DEMO.example/")
    ) == ("https", "demo.example", 443)


@pytest.mark.parametrize("host", ("demo.example", "DEMO.EXAMPLE", "demo.example:443"))
def test_Host는_PUBLIC_ORIGIN의_host와_유효포트가_같아야한다(host: str) -> None:
    assert deployment_mode.configured_public_host_matches(host, _environment())


@pytest.mark.parametrize(
    "host",
    (
        "",
        "attacker.example",
        "demo.example:444",
        "demo.example/path",
        "demo.example,attacker.example",
        "user@demo.example",
    ),
)
def test_다른_Host는_고정공개출처로_인정하지않는다(host: str) -> None:
    assert not deployment_mode.configured_public_host_matches(host, _environment())


def test_명시포트_PUBLIC_ORIGIN은_Host에도_같은포트를_요구한다() -> None:
    environment = _environment(PUBLIC_ORIGIN="https://demo.example:8443")

    assert deployment_mode.configured_public_host_matches(
        "demo.example:8443", environment
    )
    assert not deployment_mode.configured_public_host_matches(
        "demo.example", environment
    )


def test_기존계약에서는_고정공개출처모드를_켜지않는다() -> None:
    environment = _environment(
        DEPLOYMENT_RUNTIME_CONTRACT="render-public-web-v1"
    )

    assert not deployment_mode.render_admin_demo_no_forwarded(environment)
    assert not deployment_mode.render_admin_real_no_forwarded(environment)
    assert not deployment_mode.render_admin_no_forwarded(environment)
    assert deployment_mode.configured_public_origin(environment) is None
    assert not deployment_mode.fixed_public_https_origin(environment)


# ══════════════════════════════════════════════════════════
# 포트폴리오 링크 계약(render-portfolio-link-v1, G-S1)
# ══════════════════════════════════════════════════════════

PORTFOLIO_CONTRACT = deployment_mode.RENDER_PORTFOLIO_LINK_CONTRACT


def test_포트폴리오_계약은_no_forwarded가_아니다() -> None:
    """★ 이 계약이 render_admin_no_forwarded()에 없어야 admin.py의 LINK 발급·초대
    차단과 main.py의 /k/·LINK 쿠키 차단이 자동으로 걸리지 않는다 — admin.py를
    고치지 않고 손님 입구를 여는 전체 설계의 전제다."""
    environment = _environment(DEPLOYMENT_RUNTIME_CONTRACT=PORTFOLIO_CONTRACT)

    assert deployment_mode.render_portfolio_link(environment)
    assert not deployment_mode.render_admin_demo_no_forwarded(environment)
    assert not deployment_mode.render_admin_real_no_forwarded(environment)
    assert not deployment_mode.render_admin_no_forwarded(environment)
    assert PORTFOLIO_CONTRACT not in deployment_mode.RENDER_ADMIN_NO_FORWARDED_CONTRACTS


def test_포트폴리오_계약도_고정출처_보안판정은_옛_관리자계약과_같다() -> None:
    """★ Host 고정·CSRF Origin 고정·HSTS 판정은 render_admin_no_forwarded()가 아니라
    이 더 넓은 판정을 쓴다 — 안 그러면 포트폴리오 계약은 Host 위조를 막지 못한다
    (response_security.py·request_helpers.py가 이 판정으로 옮겨간 이유)."""
    environment = _environment(
        DEPLOYMENT_RUNTIME_CONTRACT=PORTFOLIO_CONTRACT,
        RENDER_EXTERNAL_URL="https://fallback.example",
    )

    assert deployment_mode.render_pinned_origin_no_forwarded(environment)
    assert deployment_mode.configured_public_origin(environment) == (
        "https",
        "demo.example",
        443,
    )
    assert deployment_mode.fixed_public_https_origin(environment)
    assert deployment_mode.configured_public_host_matches("demo.example", environment)
    assert not deployment_mode.configured_public_host_matches(
        "attacker.example", environment
    )


@pytest.mark.parametrize(
    "contract",
    (
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_PORTFOLIO_LINK_CONTRACT,
    ),
)
def test_고정출처_계약_세_개_모두_render_pinned_origin_no_forwarded다(
    contract: str,
) -> None:
    """옛 관리자 두 계약의 동작이 바뀌지 않았다는 것도 같은 자리에서 못 박는다."""
    environment = _environment(DEPLOYMENT_RUNTIME_CONTRACT=contract)

    assert deployment_mode.render_pinned_origin_no_forwarded(environment)


def test_일반계약은_render_pinned_origin_no_forwarded도_아니다() -> None:
    environment = _environment(DEPLOYMENT_RUNTIME_CONTRACT="render-public-web-v1")

    assert not deployment_mode.render_pinned_origin_no_forwarded(environment)
    assert not deployment_mode.render_portfolio_link(environment)
