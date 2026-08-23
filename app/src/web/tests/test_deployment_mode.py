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
