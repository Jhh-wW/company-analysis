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
    engine_build_identity.freeze_process_engine_build_identity(current)
    with pytest.raises(engine_build_identity.EngineBuildIdentityChangedError):
        engine_build_identity.assert_engine_build_identity_current(frozen)


@pytest.mark.parametrize("frozen", (_identity("a" * 40), _identity()))
def test_생성과_쓰기의_검증된신원이_같으면_통과한다(
    frozen: engine_build_identity.EngineBuildIdentity,
) -> None:
    current = engine_build_identity.freeze_process_engine_build_identity(frozen)
    assert engine_build_identity.assert_engine_build_identity_current(frozen) is current


def test_process_신원은_최초값으로_동결되고_다른값_재동결을_거절한다() -> None:
    frozen = engine_build_identity.freeze_process_engine_build_identity(_identity("a" * 40))

    assert engine_build_identity.process_engine_build_identity() is frozen
    with pytest.raises(engine_build_identity.EngineBuildIdentityChangedError):
        engine_build_identity.freeze_process_engine_build_identity(_identity("b" * 40))


def test_process_bootstrap을_인자없이_다시불러도_raw환경을_재capture하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = engine_build_identity.freeze_process_engine_build_identity(
        _identity("a" * 40)
    )
    calls: list[str] = []

    def forbidden_capture(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("동결 뒤 raw 환경을 다시 읽었습니다")

    monkeypatch.setattr(
        engine_build_identity,
        "capture_engine_build_identity",
        forbidden_capture,
    )

    assert engine_build_identity.freeze_process_engine_build_identity() is frozen
    assert calls == []


def test_wire_왕복과_epoch_digest가_exact하다() -> None:
    identity = _identity("a" * 40)

    parsed = engine_build_identity.parse_engine_build_identity_wire(identity.wire)

    assert type(parsed) is engine_build_identity.EngineBuildIdentity
    assert parsed == identity
    assert len(identity.epoch_digest) == 64
    assert identity.epoch_digest == parsed.epoch_digest


def test_EngineBuildIdentity_상속과_가짜타입을_거절한다() -> None:
    with pytest.raises(TypeError, match="상속"):

        class ForgedIdentity(engine_build_identity.EngineBuildIdentity):
            pass

    class AttributeForgery:
        deployment_revision = "a" * 40
        build_id = f"{engine_build_identity.ENGINE_BUILD_ID_CONTRACT_VERSION}:" + "a" * 40

    with pytest.raises(TypeError, match="정확한 EngineBuildIdentity"):
        engine_build_identity.require_exact_engine_build_identity(AttributeForgery())  # type: ignore[arg-type]
