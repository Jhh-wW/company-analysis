"""5장 의미칸 채점에서만 유동성 회계 상용구 절을 제외한다.

원문·좌표·지문은 절대 바꾸지 않는다. 혼합 문단은 비상용구 절을 그대로
채점하며, 다른 장의 칸은 원래 원문으로 채점한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from features.evidence_collection import liquidity_constants as c


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def is_liquidity_boilerplate_clause(clause: str) -> bool:
    """회사 고유 금액·사건이 없는 유동성 관리 상용구 절만 참이다."""

    surface = _surface(clause)
    if surface in c.HEADING_ONLY_SURFACES:
        return False
    if not (c.LIQUIDITY_SUBJECT_RE.search(surface)
            and c.LIQUIDITY_BOILERPLATE_RE.search(surface)):
        return False
    if any(marker.search(surface) for marker in c.EXEMPTION_RES):
        return False
    if (c.ACTUAL_PRESSURE_RE.search(surface) or c.BUSINESS_OFFERING_RE.search(surface)
            or c.BUSINESS_EVENT_RE.search(surface)):
        return False
    return True


@dataclass(frozen=True)
class LiquidityClauseSplit:
    score_text: str
    excluded_clauses: int
    retained_clauses: int

    @property
    def only_boilerplate(self) -> bool:
        return self.excluded_clauses > 0 and self.retained_clauses == 0


def split_liquidity_clauses(text: str) -> LiquidityClauseSplit:
    """5장 issue/response 채점용 텍스트를 원문과 별개로 만든다."""

    clauses = [item for item in c.DECIMAL_SAFE_CLAUSE_SPLIT_RE.split(text) if _surface(item)]
    excluded = [item for item in clauses if is_liquidity_boilerplate_clause(item)]
    kept = [item for item in clauses if not is_liquidity_boilerplate_clause(item)]
    if excluded:
        # 상용구 앞뒤의 절 제목은 별도 회사 사건이 아니다. 제목만 남아서
        # '위험' 신호가 5장 필수칸에 되살아나는 것을 막는다.
        kept = [item for item in kept if _surface(item) not in c.HEADING_ONLY_SURFACES]
    return LiquidityClauseSplit(
        score_text=" ".join(kept) if excluded else text,
        excluded_clauses=len(excluded),
        retained_clauses=len(kept),
    )
