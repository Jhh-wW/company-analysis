"""검증된 실적 증감 문장의 표시 단위와 정밀도."""

from decimal import Decimal
from typing import Final

RATE_AMOUNT_DIVISOR: Final[Decimal] = Decimal("100000000")
RATE_AMOUNT_UNIT: Final[str] = "억원"
RATE_AMOUNT_MIN_PLACES: Final[int] = 1
RATE_AMOUNT_DECIMAL_PRECISION: Final[int] = 160
