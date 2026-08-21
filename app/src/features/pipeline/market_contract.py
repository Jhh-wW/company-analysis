"""2장 고객·시장 단계와 원문 근거의 공통 닫힌 계약."""

from __future__ import annotations

import re
from typing import Final


MARKET_STAGES: Final[frozenset[str]] = frozenset({"핵심", "성장", "진입"})

# 단계 단어가 단순 수치 변화(예: ``매출 성장``)에 쓰인 경우를 시장 단계로
# 승격하지 않는다. 단계와 시장 분류 대상이 한 문구에 직접 붙어 있어야 한다.
MARKET_STAGE_EVIDENCE_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "핵심": re.compile(r"핵심\s*(?:고객\s*)?(?:시장|지역|산업|용도|채널)"),
    "성장": re.compile(r"성장\s*(?:시장|지역|산업|용도|채널)"),
    "진입": re.compile(r"진입\s*(?:시장|지역|산업|용도|채널)"),
}
