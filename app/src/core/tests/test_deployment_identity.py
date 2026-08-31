"""배포 신원을 health와 생성기 캐시가 같은 exact raw 규칙으로 읽는지 지킨다."""

from __future__ import annotations

import pytest

from src.core import deployment_identity


@pytest.fixture(autouse=True)
def _배포환경을_비운다(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_첫번째_exact_full_commit을_그대로_돌려준다(monkeypatch) -> None:
    commit = "abcdef0123456789abcdef0123456789abcdef01"
    monkeypatch.setenv("RENDER_GIT_COMMIT", commit)
    monkeypatch.setenv("APP_GIT_COMMIT", "1" * 40)

    snapshot = deployment_identity.capture_deployment_identity()

    assert snapshot.raw_values == (
        ("RENDER_GIT_COMMIT", commit),
        ("APP_GIT_COMMIT", "1" * 40),
    )
    assert deployment_identity.deployed_commit(snapshot) == commit
    assert deployment_identity.short_deployed_commit(snapshot) == "abcdef0"


@pytest.mark.parametrize(
    "untrusted",
    (
        "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
        " abcdef0123456789abcdef0123456789abcdef01",
        "abcdef0123456789abcdef0123456789abcdef01 ",
        "abc1234",
        "a" * 39,
        "a" * 41,
        "abcdef0;polluted",
        "g" * 40,
    ),
)
def test_보정해야만_정상처럼_보이는_raw값은_거절한다(
    untrusted: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", untrusted)

    assert deployment_identity.deployed_commit() == ""
    assert deployment_identity.short_deployed_commit() == "unknown"


def test_첫환경값이_오염됐으면_두번째_exact값을_쓴다(monkeypatch) -> None:
    fallback = "f" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", " f" + "f" * 39)
    monkeypatch.setenv("APP_GIT_COMMIT", fallback)

    assert deployment_identity.deployed_commit() == fallback


def test_snapshot은_환경이_뒤에_바뀌어도_처음_raw값을_유지한다(monkeypatch) -> None:
    first = "1" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", first)
    snapshot = deployment_identity.capture_deployment_identity()

    monkeypatch.setenv("RENDER_GIT_COMMIT", "2" * 40)

    assert deployment_identity.deployed_commit(snapshot) == first
    assert deployment_identity.short_deployed_commit(snapshot) == first[:7]


def test_snapshot_commit은_raw값과_독립적으로_위조할_수_없다() -> None:
    with pytest.raises(TypeError):
        deployment_identity.DeploymentIdentitySnapshot(
            raw_values=(("RENDER_GIT_COMMIT", ""), ("APP_GIT_COMMIT", "")),
            commit="a" * 40,
        )


@pytest.mark.parametrize(
    "raw_values",
    (
        (("APP_GIT_COMMIT", "a" * 40), ("RENDER_GIT_COMMIT", "")),
        (("RENDER_GIT_COMMIT", ""), ("RENDER_GIT_COMMIT", "a" * 40)),
        (("RENDER_GIT_COMMIT", "a" * 40),),
        (("RENDER_GIT_COMMIT", "a" * 40), ("EXTRA_COMMIT", "")),
    ),
)
def test_snapshot은_환경이름_순서_중복_계약을_강제한다(raw_values) -> None:
    with pytest.raises(ValueError, match="이름·순서·중복"):
        deployment_identity.DeploymentIdentitySnapshot(raw_values=raw_values)
