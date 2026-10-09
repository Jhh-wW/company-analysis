"""공식 사업 조사 앵커의 개수와 장 순서."""

from typing import Final

MAX_BUSINESS_ACTIVITY_ANCHORS: Final[int] = 3
BUSINESS_ACTIVITY_ITEM_ANCHOR_PREFIX: Final[str] = "business-item-"
BUSINESS_ACTIVITY_SECTION_PRIORITY: Final[dict[str, int]] = {
    "portfolio": 0, "business_model": 1, "identity": 2, "operations_partners": 3,
}
