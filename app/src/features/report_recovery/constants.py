"""보고서 회복 정책의 승인된 호출 상한."""

from __future__ import annotations

from typing import Final


# FULL 근거 패킷: 장별 작가 9회 + 장 경계가 묶인 검수 1회.
PRIMARY_WRITER_CALLS: Final[int] = 9
PRIMARY_REVIEW_CALLS: Final[int] = 1

# 얇은 장 하나마다 한 번만 보충하고, 보충 결과를 한 번에 다시 검수한다.
MAX_SUPPLEMENT_SECTIONS: Final[int] = 2
SUPPLEMENT_CALLS_PER_SECTION: Final[int] = 1
SUPPLEMENT_REVIEW_CALLS: Final[int] = 1

PRIMARY_AI_CALLS: Final[int] = PRIMARY_WRITER_CALLS + PRIMARY_REVIEW_CALLS
MAX_TOTAL_AI_CALLS: Final[int] = (
    PRIMARY_AI_CALLS
    + MAX_SUPPLEMENT_SECTIONS * SUPPLEMENT_CALLS_PER_SECTION
    + SUPPLEMENT_REVIEW_CALLS
)
