"""9장 본문이 기댄 원문 절에 실제 차별점 소재가 있는지 확인한다.

빈 결과는 의미 일치나 우열의 증명이 아니다. 자기 인용·주체·수치·조건 검수는
계속 필요하다. 다른 절의 선언 단어로 회계 주석을 이 장에 옮기지 않는다.
"""

from collections.abc import Mapping

from src.features.composer.competitive_scope_constants import (
    COMPETITIVE_ASSERTION_RE, COMPETITIVE_CLAUSE_RE, COMPETITIVE_LIMITATION_RE,
    COMPETITIVE_MIN_SUPPORT_TERMS, COMPETITIVE_NEGATED_DECLARATION_RE,
    COMPETITIVE_SECTION_EVIDENCE_OFFCONTRACT,
)
from src.features.composer.prose_own_source import own_source_support_terms


def competitive_section_evidence_problem(text: str, sources: Mapping[str, str]) -> str:
    if not text.strip():
        return ""
    clauses = [clause.strip() for source in sources.values()
               for clause in COMPETITIVE_CLAUSE_RE.split(source) if clause.strip()]
    support = [(len(own_source_support_terms(text, [clause])), clause) for clause in clauses]
    best = max((score for score, _clause in support), default=0)
    if best < COMPETITIVE_MIN_SUPPORT_TERMS:
        return COMPETITIVE_SECTION_EVIDENCE_OFFCONTRACT
    for score, clause in support:
        if score != best or COMPETITIVE_NEGATED_DECLARATION_RE.search(clause):
            continue
        if COMPETITIVE_ASSERTION_RE.search(clause) or COMPETITIVE_LIMITATION_RE.search(clause):
            return ""
    return COMPETITIVE_SECTION_EVIDENCE_OFFCONTRACT
