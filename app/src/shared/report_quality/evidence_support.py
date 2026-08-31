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


MIN_PROSE_EVIDENCE_SUPPORT_TERMS: Final[int] = 2
PROSE_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {"verified_prose", "evidence_based_interpretation"}
)


def normalized_support_terms(values: Iterable[object]) -> tuple[str, ...]:
    """공백·Unicode·대소문자 차이를 없앤 고유 근거어를 순서대로 돌려준다."""

    normalized: list[str] = []
    for value in values:
        term = " ".join(
            unicodedata.normalize("NFKC", str(value or "")).split()
        ).casefold()
        if len(term.replace(" ", "")) < 2 or term in normalized:
            continue
        normalized.append(term)
    return tuple(normalized)


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
    "normalized_support_terms",
    "prose_evidence_support_ready",
]
