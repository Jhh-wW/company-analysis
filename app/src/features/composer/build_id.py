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

★ 어떤 파일을 보나 — 보고서 생산 런타임 세 뿌리의 production Python 전부다.
  `app/src` · `analysis_engine/src` · `analysis_engine/tools` 아래를 자동으로 훑는다.
  시험·Python 임시 캐시만 뺀다. 배포 commit은 원래 모든 코드 변경에서 캐시를
  가르므로, 로컬 지문도 좁은 손목록보다 넓고 안전한 쪽을 택한다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
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

_IGNORED_DIRECTORIES: Final[frozenset[str]] = frozenset({"tests", "__pycache__"})
_IGNORED_FILENAMES: Final[frozenset[str]] = frozenset({"conftest.py"})
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


def _scan_production_directory(
    project_root: Path,
    directory: Path,
    found: list[str],
) -> None:
    """한 디렉터리를 링크 없이 전부 순회한다. 어느 오류도 부분 목록으로 삼키지 않는다."""

    with os.scandir(directory) as iterator:
        entries = sorted(iterator, key=lambda item: item.name.lower())
    for entry in entries:
        status = entry.stat(follow_symlinks=False)
        if _is_link_or_reparse(status):
            raise OSError("production 뿌리 안의 링크·reparse point는 허용하지 않습니다")
        if stat.S_ISDIR(status.st_mode):
            if entry.name.lower() in _IGNORED_DIRECTORIES:
                continue
            _scan_production_directory(project_root, Path(entry.path), found)
            continue
        if stat.S_ISREG(status.st_mode):
            if _is_production_python(entry.name):
                relative = Path(entry.path).relative_to(project_root)
                found.append(relative.as_posix())
            continue
        if _is_production_python(entry.name):
            raise OSError("production Python 경로가 일반 파일이 아닙니다")


def _content_modules(project_root: Path) -> tuple[str, ...]:
    """세 필수 production 뿌리의 Python 모듈을 중복 없이 이름순으로 찾는다.

    뿌리 누락·비디렉터리·접근 실패·링크/reparse point 중 하나라도 있으면
    완전한 목록이 아니므로 ``OSError``를 올린다. 호출자가 캐시를 닫는다.
    """

    found: list[str] = []
    for root_name in _PRODUCTION_ROOTS:
        production_root = project_root / root_name
        status = production_root.lstat()
        if _is_link_or_reparse(status) or not stat.S_ISDIR(status.st_mode):
            raise OSError(f"필수 production 뿌리가 안전한 디렉터리가 아닙니다: {root_name}")
        _scan_production_directory(project_root, production_root, found)
    return tuple(sorted(set(found)))


#: 지문 길이. 충돌 확률보다 로그 가독성을 우선한 값이다 — 16자리 16진수는
#: 우연히 겹칠 일이 사실상 없으면서 로그 한 줄에 들어간다.
_DIGEST_CHARS: Final[int] = 16

#: 파일을 못 읽었을 때 쓰는 값. «알 수 없음»을 캐시 적중으로 바꾸지 않으려고
#: 매번 달라지지 않는 «고정» 값을 쓴다 — 대신 아래에서 캐시를 끈다.
_cached_build_id: str | None = None


def engine_build_id() -> str:
    """지금 v2 엔진 코드의 지문. 코드가 바뀌면 값이 바뀐다.

    Returns:
        16자리 16진수. 파일을 하나라도 못 읽으면 ``UNKNOWN_BUILD_ID``.

    ★ 성공한 지문만 기억한다. ``unknown``은 일시적인 파일 접근 실패일 수 있어
      기억하지 않는다. 다음 요청에서 파일이 정상화되면 실제 지문으로 회복한다.
    """
    global _cached_build_id
    if _cached_build_id is not None:
        return _cached_build_id

    뿌리 = paths.PROJECT_ROOT
    try:
        content_modules = _content_modules(뿌리)
    except OSError as error:
        # production 패키지를 일부만 훑은 지문은 완전한 지문이 아니다.
        # 접근 실패 경로는 노출하지 않고 예외 종류만 남긴다.
        logger.warning(
            "엔진 content 모듈을 전부 찾을 수 없습니다(kind=%s). "
            "이번 실행에서는 캐시를 쓰지 않습니다.",
            type(error).__name__,
        )
        return UNKNOWN_BUILD_ID
    읽을것 = [(이름, 뿌리 / 이름) for 이름 in content_modules]
    digest = hashlib.sha256()
    # 배포 commit은 자동 발견 뿌리 바깥의 변경까지 덮는 마지막 안전망이다.
    # 로컬처럼 commit을 모르는 환경에서는 기존 파일 지문만 사용한다.
    release_commit = deployment_identity.deployed_commit()
    if release_commit:
        digest.update(b"deployment-commit\x00")
        digest.update(release_commit.encode("ascii"))
        digest.update(b"\x00")
    for name, 파일 in 읽을것:
        try:
            digest.update(name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(파일.read_bytes())
        except OSError:
            # 못 읽으면 «모르는 상태»다. 캐시를 쓰면 안 된다 (아래 참고).
            logger.warning(
                "엔진 지문을 만들 수 없습니다 — %s를 못 읽었습니다. "
                "이번 실행에서는 캐시를 쓰지 않습니다.",
                name,
            )
            return UNKNOWN_BUILD_ID

    _cached_build_id = digest.hexdigest()[:_DIGEST_CHARS]
    return _cached_build_id


__all__ = ["UNKNOWN_BUILD_ID", "build_id_is_usable", "engine_build_id"]
