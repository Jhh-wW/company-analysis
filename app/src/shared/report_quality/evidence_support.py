"""일반 산문 주장과 원문을 잇는 최소 공통 근거어 계약.

검수 AI의 ``참`` 한 글자만으로는 서로 무관한 문장을 공개 사실로 만들 수
없다. 일반 산문은 작성 시점에 claim과 정확한 원문 양쪽에서 찾은 서로 다른
근거어가 두 개 이상 있어야 한다. 수치 FactRecord는 NumericBinding으로 별도
검산하므로 이 어휘 계약의 대상이 아니다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Final

from src.shared.report_quality.constants import (
    INTERPRETATION_CLAIM_TYPE,
    VERIFIED_PROSE_CLAIM_TYPE,
)


MIN_PROSE_EVIDENCE_SUPPORT_TERMS: Final[int] = 2
PROSE_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {VERIFIED_PROSE_CLAIM_TYPE, INTERPRETATION_CLAIM_TYPE}
)


def normalized_evidence_support_text(value: object) -> str:
    """claim·원문·근거어가 함께 쓰는 Unicode/공백 정본."""

    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    ).casefold()


def normalized_support_terms(values: Iterable[object]) -> tuple[str, ...]:
    """공백·Unicode·대소문자 차이를 없앤 고유 근거어를 순서대로 돌려준다."""

    normalized: list[str] = []
    for value in values:
        term = normalized_evidence_support_text(value)
        if len(term.replace(" ", "")) < 2 or term in normalized:
            continue
        normalized.append(term)
    return tuple(normalized)


def evidence_support_term_mismatches(
    claim: object,
    evidence: object,
    support_terms: Iterable[object],
) -> tuple[str, ...]:
    """claim과 실제 원문 양쪽에 그대로 없는 정규화 근거어를 돌려준다.

    작성기·프로그램 근거·최종 출고가 이 교집합 규칙을 따로 구현하면 한 경로만
    ``evidence_binding``을 다시 계산해 근거 없는 문장을 자기서명할 수 있다.
    최소 개수 정책은 수치 결속 예외가 있는 호출자가 정하고, 여기서는 동일한
    문자열 의미 검산만 단일화한다.
    """

    terms = normalized_support_terms(support_terms)
    normalized_claim = normalized_evidence_support_text(claim)
    normalized_evidence = normalized_evidence_support_text(evidence)
    return tuple(
        term
        for term in terms
        if term not in normalized_claim or term not in normalized_evidence
    )


def prose_evidence_support_ready(
    claim_type: object,
    support_terms: Iterable[object],
) -> bool:
    """일반 산문이면 최소 두 근거어를, 다른 구조 사실이면 그대로 허용한다."""

    if str(claim_type or "").strip() not in PROSE_CLAIM_TYPES:
        return True
    return (
        len(normalized_support_terms(support_terms))
        >= MIN_PROSE_EVIDENCE_SUPPORT_TERMS
    )


__all__ = [
    "MIN_PROSE_EVIDENCE_SUPPORT_TERMS",
    "PROSE_CLAIM_TYPES",
    "evidence_support_term_mismatches",
    "normalized_evidence_support_text",
    "normalized_support_terms",
    "prose_evidence_support_ready",
]
