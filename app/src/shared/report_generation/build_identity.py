"""옛 composer cache 지문의 최소 호환 계약.

새 생성 영수증은 ``src.shared.engine_build_identity``의 동결 epoch만 사용한다.
이 모듈은 새 cache epoch 정본이 통합되기 전의 composer/storage API가 기존
문자열 의미를 바꾸지 않도록 붙잡는 짧은 호환층이다.
"""

from __future__ import annotations

from typing import Final


UNKNOWN_BUILD_ID: Final[str] = "unknown"


def build_id_is_usable(build_id: str) -> bool:
    """알 수 없는 옛 composer build를 cache identity로 승격하지 않는다."""

    return bool(build_id) and build_id != UNKNOWN_BUILD_ID


__all__ = ["UNKNOWN_BUILD_ID", "build_id_is_usable"]
