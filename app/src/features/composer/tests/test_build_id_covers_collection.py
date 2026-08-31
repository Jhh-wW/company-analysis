# -*- coding: utf-8 -*-
"""캐시는 immutable 배포 commit만 믿는다는 제품 계약을 못 박는다.

이전 시험은 가변 로컬 파일을 훑어 usable 지문을 만들도록 강제했다. 그러나
마지막 파일 검사 직후의 변경은 끝내 막을 수 없으므로 그 약속 자체를 폐기했다.
이제 로컬 파일 변화는 언제나 UNKNOWN이고, 정확한 full deployment commit만
캐시 namespace가 된다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
import os
from pathlib import Path, PurePosixPath
import re

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from src.core import deployment_identity, paths
from src.features.composer import build_id


_FULL_COMMIT_A = "1" * deployment_identity.COMMIT_FULL_LEN
_FULL_COMMIT_B = "2" * deployment_identity.COMMIT_FULL_LEN


@pytest.fixture(autouse=True)
def _배포_커밋을_시험마다_비운다(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _set_full_commit(monkeypatch: pytest.MonkeyPatch, commit: str = _FULL_COMMIT_A) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", commit)


def test_full_commit과_contract_version을_손실없이_namespace로_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_full_commit(monkeypatch)

    actual = build_id.engine_build_id()

    assert actual == (
        f"{build_id.ENGINE_BUILD_ID_CONTRACT_VERSION}:{_FULL_COMMIT_A}"
    )
    assert build_id.build_id_is_usable(actual)


def test_한_deployment_snapshot에서_revision과_build_id를_함께_만든다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_full_commit(monkeypatch, _FULL_COMMIT_A)
    snapshot = deployment_identity.capture_deployment_identity()

    monkeypatch.setenv("RENDER_GIT_COMMIT", _FULL_COMMIT_B)
    identity = build_id.capture_engine_build_identity(snapshot)

    assert identity.deployment_revision == _FULL_COMMIT_A
    assert identity.build_id.endswith(_FULL_COMMIT_A)
    assert identity.cache_usable


def test_EngineBuildIdentity는_revision과_다른_build를_허용하지_않는다() -> None:
    with pytest.raises(ValueError, match="결속"):
        build_id.EngineBuildIdentity(
            deployment_revision=_FULL_COMMIT_A,
            build_id=f"{build_id.ENGINE_BUILD_ID_CONTRACT_VERSION}:{_FULL_COMMIT_B}",
        )

    with pytest.raises(ValueError, match="unknown"):
        build_id.EngineBuildIdentity(
            deployment_revision="",
            build_id=f"{build_id.ENGINE_BUILD_ID_CONTRACT_VERSION}:{_FULL_COMMIT_A}",
        )


def test_대문자_full_commit은_보정하지_않고_UNKNOWN이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
    _set_full_commit(monkeypatch, commit)

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID


@pytest.mark.parametrize(
    "untrusted",
    (
        "a" * 7,
        "a" * 39,
        "a" * 41,
        "g" * 40,
        "a" * 40 + "-dirty",
        "abcdef0;polluted",
        " " + "a" * 40,
        "a" * 40 + " ",
    ),
)
def test_짧거나_오염된_revision은_UNKNOWN이다(
    untrusted: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", untrusted)

    actual = build_id.engine_build_id()

    assert actual == build_id.UNKNOWN_BUILD_ID
    assert not build_id.build_id_is_usable(actual)


def test_커밋이_없으면_매호출_UNKNOWN이고_나중_full_commit을_즉시_본다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNKNOWN도 usable 값도 프로세스 장기 memoization하지 않는다."""

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID
    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID

    _set_full_commit(monkeypatch)
    assert build_id.build_id_is_usable(build_id.engine_build_id())

    monkeypatch.setenv("RENDER_GIT_COMMIT", _FULL_COMMIT_B)
    assert build_id.engine_build_id().endswith(_FULL_COMMIT_B)


def test_로컬파일을_바꿔도_UNKNOWN이라_캐시할수없다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """로컬 변화→usable 지문이라는 옛 약속을 안전한 fail-closed 계약으로 교체한다."""

    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    local_source = tmp_path / "app/src/features/new_feature/logic.py"
    local_source.parent.mkdir(parents=True)
    local_source.write_text("VALUE = 1\n", encoding="utf-8")

    before = build_id.engine_build_id()
    local_source.write_text("VALUE = 2\n", encoding="utf-8")
    after = build_id.engine_build_id()

    assert before == after == build_id.UNKNOWN_BUILD_ID
    assert not build_id.build_id_is_usable(after)


def test_full_commit이_바뀌면_namespace도_바뀐다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_full_commit(monkeypatch, _FULL_COMMIT_A)
    before = build_id.engine_build_id()

    _set_full_commit(monkeypatch, _FULL_COMMIT_B)

    assert build_id.engine_build_id() != before


def test_contract_version이_바뀌면_같은_commit도_옛_namespace와_갈린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_full_commit(monkeypatch)
    before = build_id.engine_build_id()

    monkeypatch.setattr(
        build_id,
        "ENGINE_BUILD_ID_CONTRACT_VERSION",
        "deployment-commit-v2",
    )

    assert build_id.engine_build_id() != before


@pytest.mark.parametrize(
    "malformed",
    (
        "",
        "unknown",
        "buildA",
        "deployment-commit-v1:" + "a" * 39,
        "deployment-commit-v1:" + "g" * 40,
        "deployment-commit-v1:" + "A" * 40,
        "other-contract:" + "a" * 40,
    ),
)
def test_usable검사도_현재계약의_full_commit만_받는다(malformed: str) -> None:
    assert not build_id.build_id_is_usable(malformed)


def test_동시_열여섯호출은_같은_commit_namespace이고_scan은_0회다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_full_commit(monkeypatch)
    scan_calls = 0

    def forbidden_scan(*_args, **_kwargs):
        nonlocal scan_calls
        scan_calls += 1
        raise AssertionError("deployment commit 경로에서 파일 scan을 호출했습니다")

    monkeypatch.setattr(os, "scandir", forbidden_scan)

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(lambda _index: build_id.engine_build_id(), range(16)))

    assert results == [results[0]] * 16
    assert build_id.build_id_is_usable(results[0])
    assert scan_calls == 0


def test_옛_scan과_condition_구현은_죽은코드로_남지않는다() -> None:
    for removed_name in (
        "_content_modules",
        "_compute_engine_build_id",
        "_read_stable_content",
        "_build_id_condition",
        "_cached_build_id",
    ):
        assert not hasattr(build_id, removed_name)


def test_Docker_배포모양에서_full_commit만_usable이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dockerfile = (paths.PROJECT_ROOT / "app/Dockerfile").read_text(encoding="utf-8")
    container_app_root = PurePosixPath("/srv/app")

    assert "WORKDIR /srv/app" in dockerfile
    assert "COPY app/requirements.txt /srv/app/requirements.txt" in dockerfile
    assert container_app_root.parent / "app/requirements.txt" == PurePosixPath(
        "/srv/app/requirements.txt"
    )

    _set_full_commit(monkeypatch)
    assert build_id.build_id_is_usable(build_id.engine_build_id())

    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234")
    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID

    monkeypatch.delenv("RENDER_GIT_COMMIT")
    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID


def test_Docker_base_image는_tag와_sha256_digest를_함께_고정한다() -> None:
    dockerfile = (paths.PROJECT_ROOT / "app/Dockerfile").read_text(encoding="utf-8")
    from_lines = [line.strip() for line in dockerfile.splitlines() if line.startswith("FROM ")]

    assert len(from_lines) == 1
    assert re.fullmatch(
        r"FROM python:3\.13\.15-slim-trixie@sha256:[0-9a-f]{64}",
        from_lines[0],
    )


def test_Docker_production_tree는_root가_만들고_appuser는_읽기만한다() -> None:
    dockerfile = (paths.PROJECT_ROOT / "app/Dockerfile").read_text(encoding="utf-8")
    lines = [line.strip() for line in dockerfile.splitlines() if line.strip()]
    user_index = lines.index("USER appuser")
    copy_indices = [index for index, line in enumerate(lines) if line.startswith("COPY ")]

    assert copy_indices and max(copy_indices) < user_index
    assert all("--chown" not in lines[index] for index in copy_indices)
    assert not re.search(r"(?:chown|chmod)[^\n]*/srv", dockerfile)
    assert 'VOLUME ["/var/data"]' in dockerfile
    assert not any(
        "/srv" in line for line in lines if line.startswith("VOLUME ")
    )


def _locked_requirements() -> dict[str, Requirement]:
    raw = (paths.PROJECT_ROOT / "app/requirements.txt").read_text(encoding="utf-8")
    locked: dict[str, Requirement] = {}
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        requirement = Requirement(entry)
        name = canonicalize_name(requirement.name)
        assert name not in locked, f"requirement가 중복됐습니다: {name}"
        locked[name] = requirement
    return locked


def test_requirements의_모든_직접전이항목은_exact_pin이다() -> None:
    locked = _locked_requirements()

    for name, requirement in locked.items():
        if requirement.url:
            assert re.search(r"#sha256=[0-9a-f]{64}$", requirement.url), name
            continue
        specifiers = tuple(requirement.specifier)
        assert len(specifiers) == 1, f"exact pin이 아닌 requirement: {requirement}"
        specifier = specifiers[0]
        assert specifier.operator == "==" and "*" not in specifier.version, (
            f"exact pin이 아닌 requirement: {requirement}"
        )

    assert "et-xmlfile" in locked, "openpyxl의 전이 의존성이 lock에서 빠졌습니다"


def test_requirements는_설치된_metadata의_현재환경_의존성에_닫혀있다() -> None:
    """실제 wheel metadata의 적용되는 Requires-Dist가 lock에서 빠지지 않았는지 본다."""

    locked = _locked_requirements()
    environment = default_environment()

    for parent_name, parent in locked.items():
        if parent.marker is not None and not parent.marker.evaluate(environment):
            continue
        distribution = metadata.distribution(parent.name)
        if parent.url is None:
            specifier = next(iter(parent.specifier))
            assert distribution.version == specifier.version, (
                f"설치 metadata와 lock 버전이 다릅니다: {parent_name} "
                f"{distribution.version} != {specifier.version}"
            )

        enabled_extras = {"", *parent.extras}
        for dependency_text in distribution.requires or ():
            dependency = Requirement(dependency_text)
            applies = dependency.marker is None or any(
                dependency.marker.evaluate({**environment, "extra": extra})
                for extra in enabled_extras
            )
            if not applies:
                continue
            dependency_name = canonicalize_name(dependency.name)
            assert dependency_name in locked, (
                f"{parent_name} metadata 의존성 {dependency_name}이 lock에 없습니다"
            )
