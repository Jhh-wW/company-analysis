"""엔진 v2 캐시의 배포 신원 계약.

캐시는 «지금 실행 중인 생산물을 정확히 안다»고 증명할 때만 쓴다. 실행 중인
파일을 훑어 만든 지문은 마지막 검사 직후에도 파일이 바뀔 수 있어 그 증명이
될 수 없다. 그래서 가변 로컬 트리나 짧은 revision에서는 캐시를 완전히 끄고,
검증된 40자리 배포 commit만 권위로 삼는다.

배포 commit에는 보고서 코드뿐 아니라 ``app/Dockerfile``과
``app/requirements.txt``도 들어 있다. Dockerfile은 base image digest를,
requirements는 직접·전이 의존성을 고정한다. 따라서 같은 contract version과
같은 full commit은 같은 immutable 배포 입력을 뜻한다. 이 전제가 바뀌면
``ENGINE_BUILD_ID_CONTRACT_VERSION``을 올려 기존 namespace와 분리해야 한다.
"""

from __future__ import annotations

from typing import Final

from src.core import deployment_identity


# 캐시 열쇠의 의미가 바뀔 때만 올린다. 코드 변경은 full commit이 자동으로 가른다.
ENGINE_BUILD_ID_CONTRACT_VERSION: Final[str] = "deployment-commit-v1"
UNKNOWN_BUILD_ID: Final[str] = "unknown"

_SEPARATOR: Final[str] = ":"
_LOWER_HEX: Final[frozenset[str]] = frozenset("0123456789abcdef")


def _validated_full_commit() -> str:
    """정확한 canonical 40자리 commit만 돌려준다."""

    commit = deployment_identity.deployed_commit()
    if len(commit) != deployment_identity.COMMIT_FULL_LEN:
        return ""
    if commit != commit.lower() or any(letter not in _LOWER_HEX for letter in commit):
        return ""
    return commit


def _namespace(full_commit: str) -> str:
    """contract version과 full commit을 손실 없이 결합한다."""

    return f"{ENGINE_BUILD_ID_CONTRACT_VERSION}{_SEPARATOR}{full_commit}"


def engine_build_id() -> str:
    """검증된 배포 namespace 또는 ``unknown``을 돌려준다.

    이 함수는 파일을 읽거나 결과를 장기 기억하지 않는다. full commit이 없는
    로컬/가변 환경은 호출할 때마다 ``unknown``이고, 그 값은 저장 계층에서
    캐시 읽기·쓰기를 모두 차단한다.
    """

    full_commit = _validated_full_commit()
    if not full_commit:
        return UNKNOWN_BUILD_ID
    return _namespace(full_commit)


def build_id_is_usable(build_id: str) -> bool:
    """값 자체도 현재 계약의 canonical namespace인지 엄격히 확인한다."""

    if not isinstance(build_id, str):
        return False
    prefix = f"{ENGINE_BUILD_ID_CONTRACT_VERSION}{_SEPARATOR}"
    if not build_id.startswith(prefix):
        return False
    commit = build_id[len(prefix) :]
    return bool(
        len(commit) == deployment_identity.COMMIT_FULL_LEN
        and commit == commit.lower()
        and all(letter in _LOWER_HEX for letter in commit)
    )


__all__ = ["UNKNOWN_BUILD_ID", "build_id_is_usable", "engine_build_id"]
