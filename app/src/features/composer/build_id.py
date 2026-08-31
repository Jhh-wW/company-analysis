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

★ 어떤 파일을 보나 — «출력물의 모양을 바꾸는» 것만 본다.
  프롬프트(constants) · 작성(logic) · 검증(verify) · 중복제거(dedupe) ·
  도식검증(diagram_check) · 조립(render) · 출고검증(validate) · 배선(pipeline).
  시험 파일과 이 파일 자신은 뺀다 — 바뀌어도 보고서 모양이 안 바뀐다.
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

#: 보고서 «모양»을 정하는 composer 파일들. 사람이 목록에 새 모듈을 넣는
#: 것을 기억하게 하지 않고 디렉터리에서 계산한다. 시험·패키지 표식·지문기
#: 자신만 제외하며 이름 정렬로 같은 코드의 순서를 고정한다.
_SHAPING_MODULES: Final[tuple[str, ...]] = tuple(
    path.name
    for path in sorted(Path(__file__).resolve().parent.glob("*.py"))
    if path.name not in {"__init__.py", "build_id.py"}
)

#: 패키지 밖에 있어 자동 발견할 수 없는 필수 생산 파일. 이 파일은 하나라도
#: 없거나 읽을 수 없으면 지문을 ``unknown``으로 만들어 캐시를 닫는다.
_REQUIRED_CONTENT_MODULES: Final[tuple[str, ...]] = (
    "analysis_engine/tools/run_pilot.py",
    # 운영 v2가 생성 전 source digest와 생성기 namespace를 만들고, delivery가
    # 생성 후 실제 공식 문서 snapshot을 봉인할 때 쓰는 단일 정본들이다.
    "app/src/features/report_delivery/source_identity.py",
    "app/src/features/pipeline/real.py",
    "app/src/shared/generation_cache_identity.py",
    "app/src/shared/official_ir.py",
    "app/src/shared/report_source_identity.py",
)

#: 보고서의 원문·표·근거·공개 모양을 생산하는 feature/package 뿌리.
#:
#: ★ 왜 파일 목록이 아니라 패키지 뿌리인가 (2026-08-31)
#:   홈페이지와 매출 구성 모듈이 실제 수집 경로에 들어왔는데도 사람이 이 파일의
#:   목록을 고치지 않아 지문에서 빠졌다. 같은 방식이면 새 evidence_collection,
#:   report_evidence, chapter_evidence도 또 빠진다. 여기에는 책임 경계인 패키지
#:   뿌리만 고정하고, 그 아래 production ``.py``는 실행 때 전부 자동 발견한다.
#:
#: 아직 생기지 않은 신규 패키지 뿌리는 건너뛴다. 그 패키지가 추가되는 순간부터
#: 별도 지문 목록 수정 없이 안의 production 모듈이 자동으로 들어간다.
_CONTENT_PACKAGE_ROOTS: Final[tuple[str, ...]] = (
    "analysis_engine/src/features/evidence_collection",
    "app/src/features/chapter_evidence",
    # 비교 후보·자사 고유성은 현재 v1 경로의 공개 문장을 만들며, v2 장별 근거
    # 통합 대상으로 고정돼 있다. 통합 파일만 지문에 넣고 생산자를 빼면 같은
    # 코드 namespace가 서로 다른 경쟁력 내용을 가리키게 된다.
    "app/src/features/company_comparison",
    "app/src/features/company_performance",
    "app/src/features/company_specificity",
    "app/src/features/filingclean",
    "app/src/features/homepage",
    "app/src/features/newspick",
    # provenance는 인용 장부와 공식 출처 사용 가능 여부, spanselect는 실제
    # 원문에서 공개 주장 후보를 고르는 규칙을 소유한다.
    "app/src/features/provenance",
    "app/src/features/report_standard",
    "app/src/features/revenuemix",
    "app/src/features/spanselect",
    "app/src/shared/report_evidence",
    "app/src/shared/report_quality",
)

_IGNORED_CONTENT_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {"tests", "__pycache__"}
)
_IGNORED_CONTENT_FILENAMES: Final[frozenset[str]] = frozenset(
    {"__init__.py", "conftest.py"}
)


def _raise_walk_error(error: OSError) -> None:
    """``os.walk``가 접근 실패를 조용히 삼키지 않게 한다."""

    raise error


def _production_python_modules(
    project_root: Path, package_root_name: str
) -> tuple[str, ...]:
    """한 content package 아래 production Python 모듈을 결정론적으로 찾는다.

    존재하지 않는 뿌리는 아직 합쳐지지 않은 신규 feature일 수 있으므로 빈 결과다.
    반면 존재하지만 디렉터리가 아니거나 읽을 수 없으면 ``OSError``를 올린다.
    호출자는 이를 «지문을 모름»으로 바꿔 캐시를 fail-closed 한다.
    """

    package_root = project_root / package_root_name
    try:
        mode = package_root.stat().st_mode
    except FileNotFoundError:
        return ()
    if not stat.S_ISDIR(mode):
        raise OSError(f"content package 뿌리가 디렉터리가 아닙니다: {package_root_name}")

    found: list[str] = []
    for current, directories, filenames in os.walk(
        package_root,
        topdown=True,
        onerror=_raise_walk_error,
        followlinks=False,
    ):
        directories[:] = sorted(
            name
            for name in directories
            if name not in _IGNORED_CONTENT_DIRECTORIES
        )
        current_path = Path(current)
        for filename in sorted(filenames):
            if (
                not filename.endswith(".py")
                or filename in _IGNORED_CONTENT_FILENAMES
                or filename.startswith("test_")
                or filename.endswith("_test.py")
            ):
                continue
            relative = (current_path / filename).relative_to(project_root)
            found.append(relative.as_posix())
    return tuple(found)


def _content_modules(project_root: Path) -> tuple[str, ...]:
    """필수 단일 파일과 package 생산 모듈을 중복 없이 이름순으로 돌려준다."""

    found = set(_REQUIRED_CONTENT_MODULES)
    for package_root_name in _CONTENT_PACKAGE_ROOTS:
        found.update(_production_python_modules(project_root, package_root_name))
    return tuple(sorted(found))


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

    ★ 한 번 계산하고 기억한다. 한 요청 안에서 파일이 바뀌는 일은 없고,
      매번 읽으면 조사마다 디스크를 여러 번 두드리게 된다.
    """
    global _cached_build_id
    if _cached_build_id is not None:
        return _cached_build_id

    here = Path(__file__).resolve().parent
    뿌리 = paths.PROJECT_ROOT
    읽을것: list[tuple[str, Path]] = [(이름, here / 이름) for 이름 in _SHAPING_MODULES]
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
        _cached_build_id = UNKNOWN_BUILD_ID
        return _cached_build_id
    읽을것 += [(이름, 뿌리 / 이름) for 이름 in content_modules]
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
            _cached_build_id = UNKNOWN_BUILD_ID
            return _cached_build_id

    _cached_build_id = digest.hexdigest()[:_DIGEST_CHARS]
    return _cached_build_id


__all__ = ["UNKNOWN_BUILD_ID", "build_id_is_usable", "engine_build_id"]
