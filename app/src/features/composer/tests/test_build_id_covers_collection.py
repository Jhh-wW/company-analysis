# -*- coding: utf-8 -*-
"""보고서 생산 런타임 전체가 캐시 지문에 들어가는지 못 박는다.

파일·feature 이름을 손목록으로 관리하면 새 수집기나 근거 계약이 생길 때마다
누락된다. 지문은 활성 production 뿌리 세 곳을 전부 순회하고, 불완전하게 읽은
목록은 절대 정상 지문으로 쓰지 않아야 한다.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import paths
from src.features.composer import build_id


_REVIEW_MISSING_PRODUCERS = (
    "app/src/features/company_comparison/logic.py",
    "app/src/features/company_specificity/logic.py",
    "app/src/features/spanselect/logic.py",
    "app/src/features/provenance/citations.py",
    "app/src/shared/official_ir.py",
    "app/src/shared/report_source_identity.py",
    "app/src/shared/generation_cache_identity.py",
    "app/src/features/report_delivery/source_identity.py",
)


@pytest.fixture(autouse=True)
def _지문_기억을_지운다():
    """성공 지문 memoization이 시험 사이에 섞이지 않게 한다."""

    build_id._cached_build_id = None
    yield
    build_id._cached_build_id = None


@pytest.fixture
def 가짜프로젝트(tmp_path: Path) -> Path:
    """세 필수 production 뿌리만 갖춘 최소 프로젝트."""

    project_root = tmp_path / "repo"
    for root_name in build_id._PRODUCTION_ROOTS:
        (project_root / root_name).mkdir(parents=True)
    return project_root


def _production_file(project_root: Path, relative: str, text: str = "VALUE = 1\n") -> Path:
    target = project_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _use_project(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    monkeypatch.setattr(paths, "PROJECT_ROOT", project_root)
    build_id._cached_build_id = None


def test_활성_product_root_세곳만_고정한다() -> None:
    """선택 feature 목록을 다시 만들지 않고 넓은 실행 경계만 고정한다."""

    assert build_id._PRODUCTION_ROOTS == (
        "analysis_engine/src",
        "analysis_engine/tools",
        "app/src",
    )


@pytest.mark.parametrize("producer", _REVIEW_MISSING_PRODUCERS)
def test_리뷰에서_찾은_내용과_출처신원_생산자가_실제_지문목록에_있다(
    producer: str,
) -> None:
    """경쟁력·고유성·근거 선택·인용·캐시 source identity 누락을 막는다."""

    assert producer in build_id._content_modules(paths.PROJECT_ROOT)


@pytest.mark.parametrize("producer", _REVIEW_MISSING_PRODUCERS)
def test_리뷰에서_찾은_생산자_각각의_변경이_지문을_바꾼다(
    producer: str,
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """목록 포함 주장에 그치지 않고 실제 파일 bytes가 지문에 결속됨을 증명한다."""

    target = _production_file(가짜프로젝트, producer)
    _use_project(monkeypatch, 가짜프로젝트)
    before = build_id.engine_build_id()

    target.write_bytes(target.read_bytes() + b"# changed\n")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != before


def test_run_pilot_변경도_지문을_바꾼다(
    가짜프로젝트: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _production_file(
        가짜프로젝트,
        "analysis_engine/tools/run_pilot.py",
    )
    _use_project(monkeypatch, 가짜프로젝트)
    before = build_id.engine_build_id()

    target.write_bytes(target.read_bytes() + b"# collector changed\n")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != before


def test_임의의_새_feature_py도_목록수정없이_지문에_들어간다(
    가짜프로젝트: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """아직 이름조차 모르는 다음 feature도 app/src 경계 안이면 자동 포함한다."""

    _use_project(monkeypatch, 가짜프로젝트)
    before = build_id.engine_build_id()

    _production_file(
        가짜프로젝트,
        "app/src/features/not_yet_planned/logic.py",
    )
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != before


def test_init_py는_실행코드이므로_변경하면_지문도_바뀐다(
    가짜프로젝트: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _production_file(
        가짜프로젝트,
        "app/src/features/new_feature/__init__.py",
        "ENABLED = False\n",
    )
    _use_project(monkeypatch, 가짜프로젝트)
    before = build_id.engine_build_id()

    target.write_text("ENABLED = True\n", encoding="utf-8")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != before


@pytest.mark.parametrize(
    "irrelevant",
    (
        "app/src/features/sample/tests/helper.py",
        "app/src/features/sample/__pycache__/generated.py",
        "app/src/features/sample/conftest.py",
        "app/src/features/sample/test_logic.py",
        "app/src/features/sample/logic_test.py",
        "app/src/features/sample/readme.txt",
    ),
)
def test_시험과_임시파일은_지문에_영향을_주지_않는다(
    irrelevant: str,
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_project(monkeypatch, 가짜프로젝트)
    before = build_id.engine_build_id()

    _production_file(가짜프로젝트, irrelevant, "시험 전용\n")
    build_id._cached_build_id = None

    assert build_id.engine_build_id() == before


def test_자동발견_결과는_중복없이_이름순이다() -> None:
    modules = build_id._content_modules(paths.PROJECT_ROOT)

    assert modules == tuple(sorted(set(modules)))


@pytest.mark.parametrize("root_name", build_id._PRODUCTION_ROOTS)
def test_필수_root가_하나라도_없으면_UNKNOWN이다(
    root_name: str,
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (가짜프로젝트 / root_name).rmdir()
    _use_project(monkeypatch, 가짜프로젝트)

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID
    assert build_id._cached_build_id is None


@pytest.mark.parametrize("root_name", build_id._PRODUCTION_ROOTS)
def test_필수_root가_디렉터리가_아니면_UNKNOWN이다(
    root_name: str,
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = 가짜프로젝트 / root_name
    root.rmdir()
    root.write_text("디렉터리가 아님", encoding="utf-8")
    _use_project(monkeypatch, 가짜프로젝트)

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID
    assert build_id._cached_build_id is None


def test_순회오류는_UNKNOWN이고_다음시도에_회복한다(
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """일시적인 scandir 오류를 프로세스 수명 동안 영구 캐시하지 않는다."""

    _production_file(가짜프로젝트, "app/src/runtime.py")
    _use_project(monkeypatch, 가짜프로젝트)
    original_scandir = os.scandir
    failed = False

    def flaky_scandir(path):
        nonlocal failed
        if Path(path) == 가짜프로젝트 / "app/src" and not failed:
            failed = True
            raise OSError("일시 순회 실패")
        return original_scandir(path)

    monkeypatch.setattr(build_id.os, "scandir", flaky_scandir)

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID
    assert build_id._cached_build_id is None
    assert build_id.engine_build_id() != build_id.UNKNOWN_BUILD_ID


def test_파일읽기_오류는_UNKNOWN이고_다음시도에_회복한다(
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """첫 read_bytes 실패 뒤 정상 파일을 다시 읽어 실제 지문을 만든다."""

    target = _production_file(가짜프로젝트, "app/src/runtime.py")
    _use_project(monkeypatch, 가짜프로젝트)
    original_read_bytes = Path.read_bytes
    failed = False

    def flaky_read_bytes(path: Path) -> bytes:
        nonlocal failed
        if path == target and not failed:
            failed = True
            raise OSError("일시 파일 읽기 실패")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID
    assert build_id._cached_build_id is None
    assert build_id.engine_build_id() != build_id.UNKNOWN_BUILD_ID


@pytest.mark.parametrize("kind", ("symlink", "windows-reparse"))
def test_link나_junction_root는_조용히_건너뛰지_않고_UNKNOWN이다(
    kind: str,
    가짜프로젝트: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows 권한 없이도 lstat/reparse 판정 계약을 직접 검증한다."""

    target = 가짜프로젝트 / "app/src"
    original_lstat = Path.lstat

    def fake_lstat(path: Path):
        if path != target:
            return original_lstat(path)
        if kind == "symlink":
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=build_id._WINDOWS_REPARSE_POINT,
        )

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    _use_project(monkeypatch, 가짜프로젝트)

    assert build_id.engine_build_id() == build_id.UNKNOWN_BUILD_ID
    assert build_id._cached_build_id is None


def test_지문은_같은_코드에서_늘_같다() -> None:
    first = build_id.engine_build_id()
    build_id._cached_build_id = None
    second = build_id.engine_build_id()

    assert first == second
    assert first != build_id.UNKNOWN_BUILD_ID
    assert len(first) == build_id._DIGEST_CHARS


def test_배포_커밋이_바뀌면_지문도_바뀐다(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영은 원래 모든 배포 변경에서 cache namespace를 가른다."""

    monkeypatch.setenv(
        "RENDER_GIT_COMMIT", "1111111111111111111111111111111111111111"
    )
    first = build_id.engine_build_id()

    monkeypatch.setenv(
        "RENDER_GIT_COMMIT", "2222222222222222222222222222222222222222"
    )
    build_id._cached_build_id = None

    assert build_id.engine_build_id() != first


def test_오염된_배포_커밋은_지문_재료로_신뢰하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "1234567-not-a-commit")
    contaminated = build_id.engine_build_id()
    build_id._cached_build_id = None
    monkeypatch.delenv("RENDER_GIT_COMMIT")

    assert build_id.engine_build_id() == contaminated
