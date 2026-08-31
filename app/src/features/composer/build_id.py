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

#: 보고서 «내용»을 만드는 파일들. 프로젝트 뿌리 기준 경로다.
#:
#: ★ 왜 생겼나 (2026-08-28) — 캐시 열쇠가 `_SHAPING_MODULES`(composer/ 9개)만
#:   봤다. 그런데 원문을 «모으는» 코드는 그 밖에 있다. 실제로 v2-90 이
#:   `analysis_engine/tools/run_pilot.py` 를 고쳐 비상장 회사의 공시 원문을
#:   0자에서 37만자로 늘렸는데, **지문이 그대로라 옛 껍데기 보고서가 계속 나왔다.**
#:   배포하고 재시작해도 안 바뀌었다 — 이 모듈이 스스로 약속한
#:   「고쳤는데 화면이 그대로」를 막지 못한 것이다.
#:
#: ⚠️ 넓게 잡는 쪽이 안전하다. 빠뜨리면 «틀린 옛 보고서»가 나가고,
#:   더 넣으면 캐시가 한 번 더 빗나가 900원을 더 쓸 뿐이다.
#:   돈보다 «거짓말하지 않는 것»이 우선이다.
_CONTENT_MODULES: Final[tuple[str, ...]] = (
    # 1판 엔진 — 공시 원문과 조각을 실제로 모으는 곳 (real.py 가 동적 로드한다)
    "analysis_engine/tools/run_pilot.py",
    # v2 수집 흐름·회사 판정·캐시 열쇠
    "app/src/features/pipeline/real.py",
    # 구조화 3개년 표의 raw 값·회계범위 계약
    "app/src/features/company_performance/logic.py",
    # 조각 보정과 확장
    "app/src/features/filingclean/logic.py",
    "app/src/features/filingclean/extra.py",
    "app/src/features/filingclean/relationships.py",
    # 홈페이지·대표 이름
    "app/src/features/newspick/logic.py",
    "app/src/features/newspick/constants.py",
    # 출력 계약 (장 구성·검증 문구)
    "app/src/features/report_standard/constants.py",
    "app/src/features/report_standard/section_content.py",
    "app/src/features/report_standard/publish.py",
    # composer와 report_standard가 함께 쓰는 품질·수치·근거 결속 정본.
    # 새 모듈을 추가해도 지문 목록을 손으로 고치지 않도록 계산한다.
    *tuple(
        f"app/src/shared/report_quality/{path.name}"
        for path in sorted(
            (paths.PROJECT_ROOT / "app/src/shared/report_quality").glob("*.py")
        )
        if path.name != "__init__.py"
    ),
)

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
    읽을것 += [(이름, 뿌리 / 이름) for 이름 in _CONTENT_MODULES]
    digest = hashlib.sha256()
    # 배포 commit은 손으로 적은 파일 목록의 마지막 안전망이다. 새 생성 모듈을
    # `_CONTENT_MODULES`에 넣는 것을 잊어도 다른 revision이면 캐시가 갈린다.
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
