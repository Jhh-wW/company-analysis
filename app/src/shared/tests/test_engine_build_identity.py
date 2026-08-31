"""최종 쓰기용 엔진 빌드 신원 fence 계약."""

from __future__ import annotations

import pytest

from src.shared import engine_build_identity


def _identity(commit: str = "") -> engine_build_identity.EngineBuildIdentity:
    namespace = (
        f"{engine_build_identity.ENGINE_BUILD_ID_CONTRACT_VERSION}:{commit}"
        if commit
        else engine_build_identity.UNKNOWN_BUILD_ID
    )
    return engine_build_identity.EngineBuildIdentity(commit, namespace)


@pytest.mark.parametrize(
    ("frozen", "current"),
    (
        (_identity("a" * 40), _identity("b" * 40)),
        (_identity("a" * 40), _identity()),
        (_identity(), _identity("b" * 40)),
    ),
)
def test_생성뒤_배포신원이_달라지면_최종쓰기를_거절한다(
    frozen: engine_build_identity.EngineBuildIdentity,
    current: engine_build_identity.EngineBuildIdentity,
) -> None:
    with pytest.raises(engine_build_identity.EngineBuildIdentityChangedError):
        engine_build_identity.assert_engine_build_identity_current(
            frozen,
            capture_current=lambda: current,
        )


@pytest.mark.parametrize("frozen", (_identity("a" * 40), _identity()))
def test_생성과_쓰기의_검증된신원이_같으면_통과한다(
    frozen: engine_build_identity.EngineBuildIdentity,
) -> None:
    assert (
        engine_build_identity.assert_engine_build_identity_current(
            frozen,
            capture_current=lambda: frozen,
        )
        is frozen
    )
