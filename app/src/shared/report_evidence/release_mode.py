"""새 보고서 품질 계약의 운영 모드를 한 곳에서 해석한다.

코드가 배포됐다는 사실과 새 차단 정책을 켠다는 결정은 다르다. 설정이 없으면
항상 ``SHADOW``로 남고, 오타를 다른 모드로 추측하지 않는다. 실제 환경변수를
읽는 일은 실행 경계가 맡으며 이 모듈은 순수한 값 해석만 제공한다.
"""

from __future__ import annotations

from typing import Final

from src.shared.report_evidence.constants import ReleaseMode


REPORT_RELEASE_MODE_ENV_NAME: Final[str] = "REPORT_RELEASE_MODE"
DEFAULT_REPORT_RELEASE_MODE: Final[ReleaseMode] = ReleaseMode.SHADOW


def parse_release_mode(value: object = None) -> ReleaseMode:
    """명시한 운영 모드를 돌려주고, 빈 값만 안전한 기본값으로 해석한다.

    대소문자나 앞뒤 공백을 관용적으로 고치지 않는다. 배포 설정의 오타를
    정상 설정처럼 받아들이면 사람이 ``FULL``을 켰다고 믿는 동안 실제로는
    다른 정책이 돌 수 있기 때문이다.
    """

    if value is None or value == "":
        return DEFAULT_REPORT_RELEASE_MODE
    if type(value) is not str:  # bool은 str 하위형이 아니지만 명시적으로 닫는다.
        raise ValueError("보고서 운영 모드는 문자열이어야 합니다")
    try:
        return ReleaseMode(value)
    except ValueError as error:
        allowed = ", ".join(mode.value for mode in ReleaseMode)
        raise ValueError(
            f"알 수 없는 보고서 운영 모드입니다: {value!r} (허용: {allowed})"
        ) from error
