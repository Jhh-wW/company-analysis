"""여러 보고서 기능이 공유하는 품질·수치 계약과 승인된 하한."""

from __future__ import annotations

from decimal import Decimal
from typing import Final

QUALITY_CONTRACT_VERSION: Final[str] = "report-quality-v1"
LEGACY_UNVERSIONED_CONTRACT: Final[str] = "legacy-unversioned"
NUMERIC_BINDING_VERSION: Final[str] = "numeric-binding-v1"
NUMERIC_CHECK_PREFIX: Final[str] = f"{NUMERIC_BINDING_VERSION}:"

# 승인된 v2 완성 하한. 시험이 숫자를 복사하지 않고 이 값을 직접 읽는다.
MIN_SUBSTANTIVE_CLAIMS: Final[int] = 40
MIN_VERIFIED_RATIO: Final[Decimal] = Decimal("0.50")
MIN_DOCUMENT_SOURCES: Final[int] = 8
MAX_NOTICE_ONLY_SECTIONS: Final[int] = 1

# 1~8장 중 근거가 있는 장도 claim 하나로 끝나면 COMPLETE가 될 수 없다.
# 부족한 보고서를 삭제하는 값이 아니라 COMPLETE/PARTIAL을 가르는 하한이다.
MIN_CLAIMS_PER_COVERED_SECTION: Final[int] = 2
REQUIRED_QUALITY_SECTION_IDS: Final[tuple[str, ...]] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
)

ROUNDING_MODE: Final[str] = "ROUND_HALF_UP"
# 공식 재계산 과정의 Decimal 정밀도 차이만 허용한다. 이보다 큰 값은 틀린
# 공식을 tolerance로 숨길 수 있으므로 새 생성 계약에서 거부한다.
MAX_FORMULA_TOLERANCE: Final[Decimal] = Decimal("0.000001")
