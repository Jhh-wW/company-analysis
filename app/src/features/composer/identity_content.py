"""검증된 행정정보와 기업의 사업 설명을 구별하는 출력 적합성 검사.

주소·설립일·자본금 자체가 거짓이라는 판정이 아니다. 완결된 행정정보 문장만
정체성 본문과 요약에서 제외하며, 혼합 문장이나 모르는 문형은 기존 근거 검수에 맡긴다.
"""

import unicodedata

from src.features.composer.identity_content_constants import (
    IDENTITY_ADMINISTRATIVE_ONLY,
    IDENTITY_ADMINISTRATIVE_PATTERNS,
    IDENTITY_BUSINESS_ACTIVITY_RE,
    IDENTITY_CONNECTED_CLAUSE_RE,
    IDENTITY_INTERNAL_CONTROL_ACTIVITY_RE,
    IDENTITY_INTERNAL_CONTROL_METADATA_RE,
    IDENTITY_METADATA_AUXILIARY_RE,
    IDENTITY_METADATA_CLAUSE_RE,
    IDENTITY_METADATA_CLAUSE_SPLIT_RE,
    IDENTITY_REPORT_METADATA_PATTERNS,
    IDENTITY_SECTION_ID,
    IDENTITY_SENTENCE_SPLIT_RE,
)


def administrative_identity_only(text: str) -> bool:
    """모든 문장이 좁은 행정정보 문법일 때만 참이며 사업 사실은 삭제하지 않는다."""

    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized:
        return False
    clauses = tuple(part.strip() for part in IDENTITY_SENTENCE_SPLIT_RE.split(normalized) if part.strip())
    return bool(clauses) and all(_administrative_clause_only(clause) for clause in clauses)


def _administrative_clause_only(clause: str) -> bool:
    control_metadata = bool(IDENTITY_INTERNAL_CONTROL_METADATA_RE.search(clause))
    business_text = (
        IDENTITY_INTERNAL_CONTROL_ACTIVITY_RE.sub("", clause)
        if control_metadata else clause
    )
    if IDENTITY_BUSINESS_ACTIVITY_RE.search(business_text):
        return False
    if control_metadata or any(pattern.search(clause) for pattern in IDENTITY_REPORT_METADATA_PATTERNS):
        parts = tuple(
            part.strip() for part in IDENTITY_METADATA_CLAUSE_SPLIT_RE.split(clause)
            if part.strip()
        )
        return all(
            IDENTITY_METADATA_CLAUSE_RE.search(part)
            or IDENTITY_METADATA_AUXILIARY_RE.fullmatch(part)
            for part in parts
        )
    return (
        not IDENTITY_CONNECTED_CLAUSE_RE.search(clause)
        and any(pattern.fullmatch(clause) for pattern in IDENTITY_ADMINISTRATIVE_PATTERNS)
    )


def identity_content_problem(text: str, *, section_id: str) -> str:
    """다른 장의 자본 확충·지점 운영 등은 정체성 배치 검사로 막지 않는다."""

    if section_id == IDENTITY_SECTION_ID and administrative_identity_only(text):
        return IDENTITY_ADMINISTRATIVE_ONLY
    return ""
