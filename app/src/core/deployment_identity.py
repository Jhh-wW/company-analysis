"""배포된 생성기 코드의 공개 가능한 신원을 한 raw snapshot으로 읽는다.

health 화면과 보고서 캐시가 서로 다른 환경변수·검사 규칙을 쓰면, 화면에는 새
커밋이라고 보이면서 캐시는 옛 생성기의 결과를 꺼낼 수 있다. 공백 제거·소문자
변환은 오염된 입력을 정상처럼 바꾸므로 하지 않는다. 각 환경변수를 한 번 읽은
불변 snapshot에서 health와 캐시가 같은 exact commit을 파생한다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Final


COMMIT_ENV_NAMES: Final[tuple[str, ...]] = (
    "RENDER_GIT_COMMIT",
    "APP_GIT_COMMIT",
)
COMMIT_SHORT_LEN: Final[int] = 7
COMMIT_FULL_LEN: Final[int] = 40
UNKNOWN_COMMIT: Final[str] = "unknown"
_FULL_COMMIT_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class DeploymentIdentitySnapshot:
    """한 시점에 환경변수별 raw 값을 정확히 한 번 읽은 결과."""

    raw_values: tuple[tuple[str, str], ...]
    commit: str


def capture_deployment_identity() -> DeploymentIdentitySnapshot:
    """보정하지 않은 raw 환경변수를 한 번씩 읽어 exact commit을 고른다."""

    raw_values = tuple((name, os.environ.get(name, "")) for name in COMMIT_ENV_NAMES)
    commit = next(
        (raw for _name, raw in raw_values if _FULL_COMMIT_PATTERN.fullmatch(raw)),
        "",
    )
    return DeploymentIdentitySnapshot(raw_values=raw_values, commit=commit)


def deployed_commit(snapshot: DeploymentIdentitySnapshot | None = None) -> str:
    """정확한 40자리 소문자 16진수 commit. 모르면 빈 문자열이다."""

    identity = snapshot or capture_deployment_identity()
    return identity.commit


def short_deployed_commit(snapshot: DeploymentIdentitySnapshot | None = None) -> str:
    """사람이 확인할 짧은 commit. 신원을 모르면 명시적으로 ``unknown``이다."""

    identity = snapshot or capture_deployment_identity()
    commit = identity.commit
    return commit[:COMMIT_SHORT_LEN] if commit else UNKNOWN_COMMIT
