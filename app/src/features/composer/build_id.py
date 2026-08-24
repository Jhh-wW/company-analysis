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

logger = logging.getLogger(__name__)

#: 보고서 «모양»을 정하는 파일들. 순서를 고정한다 — 순서가 흔들리면 같은
#: 코드에서도 다른 지문이 나와 캐시가 영영 안 맞는다.
_SHAPING_MODULES: Final[tuple[str, ...]] = (
    "constants.py",
    "logic.py",
    "verify.py",
    "dedupe.py",
    "diagram_check.py",
    "render.py",
    "validate.py",
    "pipeline.py",
    "port.py",
)

#: 지문 길이. 충돌 확률보다 로그 가독성을 우선한 값이다 — 16자리 16진수는
#: 우연히 겹칠 일이 사실상 없으면서 로그 한 줄에 들어간다.
_DIGEST_CHARS: Final[int] = 16

#: 파일을 못 읽었을 때 쓰는 값. «알 수 없음»을 캐시 적중으로 바꾸지 않으려고
#: 매번 달라지지 않는 «고정» 값을 쓴다 — 대신 아래에서 캐시를 끈다.
UNKNOWN_BUILD_ID: Final[str] = "unknown"

_cached_build_id: str | None = None


def engine_build_id() -> str:
    """지금 v2 엔진 코드의 지문. 코드가 바뀌면 값이 바뀐다.

    Returns:
        16자리 16진수. 파일을 하나라도 못 읽으면 ``UNKNOWN_BUILD_ID``.

    ★ 한 번 계산하고 기억한다. 한 요청 안에서 파일이 바뀌는 일은 없고,
      매번 읽으면 조사마다 디스크를 9번씩 두드리게 된다.
    """
    global _cached_build_id
    if _cached_build_id is not None:
        return _cached_build_id

    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in _SHAPING_MODULES:
        try:
            digest.update(name.encode("utf-8"))
            digest.update(b"\x00")
            digest.update((here / name).read_bytes())
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


def build_id_is_usable(build_id: str) -> bool:
    """이 지문으로 캐시를 써도 되는가.

    ★ 「모르겠다」를 「같다」로 바꾸지 않는다. 지문을 못 만들었으면 캐시를
      읽지도 쓰지도 않는다 — 옛 결과가 새 결과인 척 나가는 것이 이 캐시에서
      가장 나쁜 결말이기 때문이다. 비용을 아끼는 것보다 중요하다.
    """
    return bool(build_id) and build_id != UNKNOWN_BUILD_ID
