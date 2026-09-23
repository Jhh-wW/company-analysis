"""수집 운반자와 소비자가 함께 쓰는 등록된 typed 종류 식별자 형식.

이 판정은 수집 승인이 아니다. 소비자는 선언된 자료 종류·승인 상태·원문
결속도 확인해야 하며, 접두가 같은 임의 문자열을 typed 근거로 승격하지 않는다.
"""

import re
from typing import Final


TYPED_TRANSPORT_KIND_PREFIX: Final[str] = "typed-evidence-v3:"
_TYPED_TRANSPORT_KIND_RE = re.compile(re.escape(TYPED_TRANSPORT_KIND_PREFIX) + r"[0-9a-f]{64}")


def is_typed_transport_kind(value: object) -> bool:
    """현재 등록된 버전과 완전한 운반 지문 형식에만 참을 반환한다."""
    return type(value) is str and _TYPED_TRANSPORT_KIND_RE.fullmatch(value) is not None
