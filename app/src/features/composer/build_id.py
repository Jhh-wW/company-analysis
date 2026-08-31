"""엔진 v2의 «지금 코드» 지문 — 캐시가 옛 결과를 물고 오지 못하게 막는다.

★ 왜 필요한가 (오늘 실측으로 당한 사고)
  1층 캐시가 v2 분기보다 앞에 있어서, ENGINE_V2=1을 켜도 그 회사의 저장본이
  살아 있으면 «옛 보고서»가 그대로 반환됐다. 화면에는 「이전에 조사한
  결과입니다」만 뜨므로, 엔진을 고쳐도 「하나도 안 고쳐졌다」로 보였다.
  v2-26에서 «v2는 캐시를 아예 안 읽는다»로 막았다.

  그런데 그 대가로 같은 회사를 두 번 조사하면 두 번 다 900원이 나간다.
  캐시를 되살리고 싶은데, 그냥 되살리면 «똑같은 사고»가 v2 안에서 재현된다:
  오늘 도식을 고쳐도 어제 캐시에 든 도식 없는 보고서가 나온다.

★ 그래서 «코드가 바뀌면 캐시가 저절로 무효»가 되게 한다.
  보고서 «모양»을 정하는 파일들의 내용을 해시로 요약해 캐시 열쇠에 넣는다.
  - 코드가 그대로면 → 적중(돈을 아낀다)
  - 한 글자라도 바뀌면 → 자동으로 불일치(옛 결과가 절대 안 나온다)

  사람이 「이번엔 캐시를 비워야지」를 기억할 필요가 없다. 기억에 의존하는
  안전장치는 반드시 잊힌다 — 이 프로젝트가 이미 네 번 증명했다.

★ 어떤 파일을 보나 — 보고서 생산 런타임 세 뿌리의 production Python 전부와
  실제 출력에 쓰는 자료·글꼴·템플릿·정적 자원이다. `app/src` ·
  `analysis_engine/src` · `analysis_engine/tools`를 자동으로 훑고,
  `app/requirements.txt`도 함께 묶는다. 시험·fixture·임시 캐시·로컬 산출물만
  뺀다. 배포 commit은 코드·requirements·Dockerfile 변경을 가르는 이 서비스의
  배포 신원 권위이고, 로컬 fallback은 이 파일들을 넓게 묶어 손목록 누락을 줄인다.

  ⚠️ 로컬 fallback은 requirements에 적히지 않은 전이 의존성 버전·기본 이미지·
  OS 패키지까지 증명하지 못한다. 운영에서는 immutable(실행 중 바뀌지 않는)
  배포 이미지와 검증된 배포 commit을 신뢰 경계로 삼는다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import threading
from pathlib import Path
from typing import Final

from src.core import deployment_identity, paths
from src.shared.report_generation.build_identity import (
    UNKNOWN_BUILD_ID,
    build_id_is_usable,
)

logger = logging.getLogger(__name__)

#: 보고서 생성·검증·출고·캐시 신원을 실행하는 활성 production 뿌리다.
#: 선택 feature 목록으로 돌아가면 새 생산자가 또 빠지므로 이 세 경계만 고정한다.
_PRODUCTION_ROOTS: Final[tuple[str, ...]] = (
    "analysis_engine/src",
    "analysis_engine/tools",
    "app/src",
)

_RUNTIME_RESOURCE_ROOTS: Final[tuple[str, ...]] = (
    "analysis_engine/src/features/public_org/data",
    "app/src/features/export_pdf/fonts",
    "app/src/web/templates",
    "app/src/web/static",
)

# 이 파일들은 현재 제품이 실제로 여는 자원이다. 디렉터리 자동 발견만으로는
# 실수로 지워진 파일을 «새 정상 목록»으로 오인하므로 최소 필수 파일은 별도로
# 존재를 강제한다. 같은 자원 뿌리에 새 파일이 생기면 아래 목록 수정 없이도
# 확장자/뿌리 정책으로 자동 포함된다.
_REQUIRED_RUNTIME_FILES: Final[tuple[str, ...]] = (
    "analysis_engine/src/features/public_org/data/public_org_registry_2026.json",
    "app/src/features/export_pdf/fonts/Freesentation-Regular.ttf",
    "app/src/features/export_pdf/fonts/Freesentation-SemiBold.ttf",
    "app/requirements.txt",
)

_IGNORED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        "tests",
        "test",
        "fixtures",
        "fixture",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        ".local-artifacts",
        "local-artifacts",
        "local_artifacts",
    }
)
_IGNORED_FILENAMES: Final[frozenset[str]] = frozenset({"conftest.py"})
_IGNORED_RESOURCE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".pyc", ".pyo", ".tmp", ".temp", ".bak", ".swp", ".log"}
)
_TEMPLATE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".html", ".jinja", ".jinja2", ".j2"}
)
_FONT_SUFFIXES: Final[frozenset[str]] = frozenset({".ttf", ".otf"})
_WINDOWS_REPARSE_POINT: Final[int] = int(
    getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
)


def _is_link_or_reparse(file_status: os.stat_result) -> bool:
    """심볼릭 링크와 Windows junction/reparse point를 같은 위험으로 본다."""

    attributes = int(getattr(file_status, "st_file_attributes", 0))
    return stat.S_ISLNK(file_status.st_mode) or bool(
        attributes & _WINDOWS_REPARSE_POINT
    )


def _is_production_python(filename: str) -> bool:
    """시험 전용 이름만 제외한다. ``__init__.py``는 실행 코드라 포함한다."""

    lowered = filename.lower()
    return bool(
        lowered.endswith(".py")
        and lowered not in _IGNORED_FILENAMES
        and not lowered.startswith("test_")
        and not lowered.endswith("_test.py")
    )


def _is_within(relative: str, root: str) -> bool:
    """POSIX 상대경로가 지정 production 자원 뿌리 안에 있는가."""

    return relative == root or relative.startswith(root + "/")


def _is_production_content(relative: str) -> bool:
    """코드 또는 실제 런타임 자원만 선택한다.

    Python은 세 production 뿌리 전체를 포함한다. 비-Python 자원은 실행 시
    실제로 읽는 네 뿌리별 정책으로 제한해 README·시험 snapshot·로컬 산출물이
    지문을 흔들지 않게 한다.
    """

    filename = relative.rsplit("/", 1)[-1]
    lowered = filename.lower()
    suffix = Path(lowered).suffix
    if (
        lowered.startswith(".")
        or lowered in _IGNORED_FILENAMES
        or lowered.startswith("test_")
        or lowered.endswith("_test.py")
        or suffix in _IGNORED_RESOURCE_SUFFIXES
    ):
        return False
    if _is_production_python(filename):
        return True
    if _is_within(relative, "analysis_engine/src/features/public_org/data"):
        return suffix == ".json"
    if _is_within(relative, "app/src/features/export_pdf/fonts"):
        return suffix in _FONT_SUFFIXES
    if _is_within(relative, "app/src/web/templates"):
        return suffix in _TEMPLATE_SUFFIXES
    if _is_within(relative, "app/src/web/static"):
        # static은 URL로 그대로 배포되는 뿌리다. 새 이미지·글꼴·CSS 형식을
        # 손목록에 추가하지 않아도 되도록 임시 산출물 확장자만 빼고 포함한다.
        return True
    return relative in _REQUIRED_RUNTIME_FILES


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    """두 stat이 같은 파일 객체를 가리키는가."""

    try:
        return os.path.samestat(left, right)
    except (AttributeError, OSError, ValueError):
        return False


def _checked_directory_status(directory: Path) -> os.stat_result:
    status = directory.lstat()
    if _is_link_or_reparse(status) or not stat.S_ISDIR(status.st_mode):
        raise OSError("production 경로가 안전한 일반 디렉터리가 아닙니다")
    return status


def _checked_regular_status(file_path: Path) -> os.stat_result:
    status = file_path.lstat()
    if _is_link_or_reparse(status) or not stat.S_ISREG(status.st_mode):
        raise OSError("production 경로가 안전한 일반 파일이 아닙니다")
    return status


def _scan_production_directory(
    project_root: Path,
    directory: Path,
    found: list[str],
) -> None:
    """한 디렉터리를 링크 없이 전부 순회한다. 어느 오류도 부분 목록으로 삼키지 않는다."""

    before = _checked_directory_status(directory)
    with os.scandir(directory) as iterator:
        entries = sorted(iterator, key=lambda item: item.name.lower())
    for entry in entries:
        if entry.name.lower() in _IGNORED_DIRECTORIES:
            continue
        status = entry.stat(follow_symlinks=False)
        if _is_link_or_reparse(status):
            raise OSError("production 뿌리 안의 링크·reparse point는 허용하지 않습니다")
        if stat.S_ISDIR(status.st_mode):
            _scan_production_directory(project_root, Path(entry.path), found)
            continue
        if stat.S_ISREG(status.st_mode):
            relative = Path(entry.path).relative_to(project_root).as_posix()
            if _is_production_content(relative):
                found.append(relative)
            continue
        relative = Path(entry.path).relative_to(project_root).as_posix()
        if _is_production_content(relative):
            raise OSError("production content 경로가 일반 파일이 아닙니다")
    after = _checked_directory_status(directory)
    if not _same_file(before, after):
        raise OSError("production 디렉터리가 순회 중 교체되었습니다")


def _content_modules(project_root: Path) -> tuple[str, ...]:
    """필수 production 코드·자원을 중복 없이 이름순으로 찾는다.

    뿌리/필수 자원 누락·형식 오류·접근 실패·링크/reparse point 중 하나라도
    있으면 완전한 목록이 아니므로 ``OSError``를 올린다. 호출자가 캐시를 닫는다.
    """

    found: list[str] = []
    for root_name in _PRODUCTION_ROOTS:
        production_root = project_root / root_name
        _checked_directory_status(production_root)
        _scan_production_directory(project_root, production_root, found)
    for root_name in _RUNTIME_RESOURCE_ROOTS:
        _checked_directory_status(project_root / root_name)
    for relative in _REQUIRED_RUNTIME_FILES:
        _checked_regular_status(project_root / relative)
        found.append(relative)
    return tuple(sorted(set(found)))


#: 지문 길이. 충돌 확률보다 로그 가독성을 우선한 값이다 — 16자리 16진수는
#: 우연히 겹칠 일이 사실상 없으면서 로그 한 줄에 들어간다.
_DIGEST_CHARS: Final[int] = 16

#: 파일을 못 읽었을 때 쓰는 값. «알 수 없음»을 캐시 적중으로 바꾸지 않으려고
#: 매번 달라지지 않는 «고정» 값을 쓴다 — 대신 아래에서 캐시를 끈다.
_cached_build_id: str | None = None
_build_id_condition = threading.Condition()
_scan_in_progress = False
_scan_owner_thread_id: int | None = None
_scan_generation = 0
_last_failed_generation: int | None = None
_scan_reentry_detected = False
_scan_waiter_count = 0
_generation_results: dict[int, str] = {}
_generation_waiters: dict[int, int] = {}


def _stable_file_metadata(status: os.stat_result) -> tuple[int, int, int]:
    """읽는 동안 바뀌면 안 되는 최소 파일 metadata."""

    modified_ns = int(
        getattr(status, "st_mtime_ns", int(float(status.st_mtime) * 1_000_000_000))
    )
    return (stat.S_IFMT(status.st_mode), int(status.st_size), modified_ns)


def _check_parent_directories(file_path: Path) -> None:
    """중간 디렉터리 reparse 교체로 마지막 파일 lstat를 우회하지 못하게 한다."""

    project_root = paths.PROJECT_ROOT
    try:
        relative = file_path.relative_to(project_root)
    except ValueError as error:
        raise OSError("production 파일이 프로젝트 경계 밖에 있습니다") from error
    current = project_root
    _checked_directory_status(current)
    for part in relative.parts[:-1]:
        current = current / part
        _checked_directory_status(current)


def _read_stable_file(file_path: Path) -> bytes:
    """링크 교체와 읽는 중 변경을 감지하며 일반 파일 bytes를 읽는다.

    ``scandir`` 뒤 실제 ``open`` 사이에는 시간이 있으므로 open 직전 lstat,
    열린 descriptor의 fstat, 읽은 뒤 fstat/lstat를 모두 맞춘다. Unix에서는
    ``O_NOFOLLOW``도 함께 쓴다. Windows reparse point는 경로 lstat의 파일 속성으로
    전후 확인한다.

    이 검사는 흔한 실수·동시 교체를 fail-closed로 잡는 경계다. 공격자가 아주
    짧은 순간 같은 inode로 경로를 바꿨다 되돌리는 모든 경우까지 증명하지는
    않는다. 운영의 최종 전제는 실행 중 파일이 바뀌지 않는 immutable 배포 이미지다.
    """

    _check_parent_directories(file_path)
    before_path = _checked_regular_status(file_path)
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(file_path, flags)
    try:
        opened_before = os.fstat(descriptor)
        if (
            _is_link_or_reparse(opened_before)
            or not stat.S_ISREG(opened_before.st_mode)
            or not _same_file(before_path, opened_before)
            or _stable_file_metadata(before_path)
            != _stable_file_metadata(opened_before)
        ):
            raise OSError("production 파일이 open 직전에 교체되었습니다")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)

        opened_after = os.fstat(descriptor)
        _check_parent_directories(file_path)
        after_path = _checked_regular_status(file_path)
        if (
            not _same_file(opened_before, opened_after)
            or not _same_file(opened_after, after_path)
            or _stable_file_metadata(opened_before)
            != _stable_file_metadata(opened_after)
            or _stable_file_metadata(opened_after)
            != _stable_file_metadata(after_path)
        ):
            raise OSError("production 파일이 읽는 동안 교체·변경되었습니다")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _digest_entry(digest: "hashlib._Hash", name: str, content: bytes) -> None:
    """파일 경계가 bytes 내용과 섞이지 않도록 길이를 함께 해시한다."""

    encoded_name = name.encode("utf-8")
    digest.update(len(encoded_name).to_bytes(8, "big"))
    digest.update(encoded_name)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


def _compute_engine_build_id() -> str:
    """한 번의 완전한 scan/read로 지문을 계산한다. 실패는 호출자에게 올린다."""

    project_root = paths.PROJECT_ROOT
    content_files = _content_modules(project_root)
    digest = hashlib.sha256()

    # 운영 계약에서는 검증된 commit을 Dockerfile·requirements를 포함한 배포
    # 입력 신원의 권위로 삼는다. 다만 현재 런타임은 실제로 resolve된 전이
    # 의존성 목록이나 base image digest를 주지 않으므로 그것까지 암호학적으로
    # 증명하지는 못한다. commit을 모르는 로컬 fallback은 그보다 더 좁다.
    release_commit = deployment_identity.deployed_commit()
    if release_commit:
        _digest_entry(digest, "deployment-commit", release_commit.encode("ascii"))
    for name in content_files:
        _digest_entry(digest, name, _read_stable_file(project_root / name))
    return digest.hexdigest()[:_DIGEST_CHARS]


def engine_build_id() -> str:
    """지금 v2 엔진 코드의 지문. 코드가 바뀌면 값이 바뀐다.

    Returns:
        16자리 16진수. 파일을 하나라도 못 읽으면 ``UNKNOWN_BUILD_ID``.

    ★ 성공한 지문만 기억한다. ``unknown``은 일시적인 파일 접근 실패일 수 있어
      기억하지 않는다. 다음 요청에서 파일이 정상화되면 실제 지문으로 회복한다.
    """
    global _cached_build_id
    global _last_failed_generation
    global _scan_generation
    global _scan_in_progress
    global _scan_owner_thread_id
    global _scan_reentry_detected
    global _scan_waiter_count

    current_thread = threading.get_ident()
    while True:
        with _build_id_condition:
            if _cached_build_id is not None:
                return _cached_build_id
            if not _scan_in_progress:
                _scan_generation += 1
                generation = _scan_generation
                _scan_in_progress = True
                _scan_owner_thread_id = current_thread
                _scan_reentry_detected = False
                break
            if _scan_owner_thread_id == current_thread:
                # 같은 계산이 deployment identity 같은 의존 경로를 통해 자신을
                # 다시 부르면 Condition을 기다리는 순간 영구 교착한다.
                _scan_reentry_detected = True
                logger.warning(
                    "엔진 지문 계산이 재진입했습니다. 이번 실행에서는 캐시를 쓰지 않습니다."
                )
                return UNKNOWN_BUILD_ID

            observed_generation = _scan_generation
            _scan_waiter_count += 1
            _generation_waiters[observed_generation] = (
                _generation_waiters.get(observed_generation, 0) + 1
            )
            try:
                while (
                    observed_generation not in _generation_results
                    and _scan_in_progress
                    and _scan_generation == observed_generation
                ):
                    _build_id_condition.wait()
                observed_result = _generation_results.get(observed_generation)
                if observed_result is not None:
                    # 같은 cold scan을 기다리던 호출은 모두 같은 UNKNOWN을 본다.
                    # 뒤 scan이 먼저 끝나도 자신이 기다린 세대의 결과가 바뀌지 않는다.
                    return observed_result
            finally:
                _scan_waiter_count -= 1
                remaining = _generation_waiters[observed_generation] - 1
                if remaining:
                    _generation_waiters[observed_generation] = remaining
                else:
                    _generation_waiters.pop(observed_generation, None)
                    _generation_results.pop(observed_generation, None)

    result = UNKNOWN_BUILD_ID
    interrupted: BaseException | None = None
    try:
        result = _compute_engine_build_id()
    except Exception as error:  # noqa: BLE001 - 어떤 불완전한 scan도 cache key가 아니다
        logger.warning(
            "엔진 production content 지문을 만들 수 없습니다(kind=%s). "
            "이번 실행에서는 캐시를 쓰지 않습니다.",
            type(error).__name__,
        )
        result = UNKNOWN_BUILD_ID
    except BaseException as error:  # KeyboardInterrupt 등에도 대기자를 반드시 깨운다
        interrupted = error
        result = UNKNOWN_BUILD_ID
    finally:
        with _build_id_condition:
            if _scan_reentry_detected:
                result = UNKNOWN_BUILD_ID
            if result and result != UNKNOWN_BUILD_ID:
                _cached_build_id = result
                _last_failed_generation = None
            else:
                result = UNKNOWN_BUILD_ID
                _last_failed_generation = generation
            if _generation_waiters.get(generation, 0):
                _generation_results[generation] = result
            _scan_in_progress = False
            _scan_owner_thread_id = None
            _scan_reentry_detected = False
            _build_id_condition.notify_all()
    if interrupted is not None:
        raise interrupted
    return result


__all__ = ["UNKNOWN_BUILD_ID", "build_id_is_usable", "engine_build_id"]
