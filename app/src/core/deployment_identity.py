"""배포된 생성기 코드의 공개 가능한 신원을 한 규칙으로 읽는다.

health 화면과 보고서 캐시가 서로 다른 환경변수·검사 규칙을 쓰면, 화면에는 새
커밋이라고 보이면서 캐시는 옛 생성기의 결과를 꺼낼 수 있다. 두 소비자가 같은
정규화 함수를 쓰도록 이 모듈을 core에 둔다.
"""

from __future__ import annotations

import os
from typing import Final


COMMIT_ENV_NAMES: Final[tuple[str, ...]] = (
    "RENDER_GIT_COMMIT",
    "APP_GIT_COMMIT",
)
COMMIT_SHORT_LEN: Final[int] = 7
COMMIT_FULL_LEN: Final[int] = 40
UNKNOWN_COMMIT: Final[str] = "unknown"


def deployed_commit() -> str:
    """검증된 전체 16진수 commit을 소문자로 돌려준다. 모르면 빈 문자열이다."""

    for name in COMMIT_ENV_NAMES:
        raw = os.environ.get(name, "").strip().lower()
        if not raw or len(raw) > COMMIT_FULL_LEN:
            continue
        if all(letter in "0123456789abcdef" for letter in raw):
            return raw
    return ""


def short_deployed_commit() -> str:
    """사람이 확인할 짧은 commit. 신원을 모르면 명시적으로 ``unknown``이다."""

    commit = deployed_commit()
    return commit[:COMMIT_SHORT_LEN] if commit else UNKNOWN_COMMIT
