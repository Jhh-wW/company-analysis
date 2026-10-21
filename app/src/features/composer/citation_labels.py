"""장에 실제 제공한 조각의 표시 접두어만 검수 전에 정리한다."""
from collections.abc import Collection
from dataclasses import replace

from src.features.composer.citation_label_constants import DRAFT_CITATION_LABEL_PATTERN
from src.features.composer.port import ComposedSentence, FlowRow


def _canonical_draft_citations(
    citations: tuple[str, ...], allowed_ids: Collection[str],
) -> tuple[str, ...]:
    result = []
    for citation in citations:
        matched = DRAFT_CITATION_LABEL_PATTERN.fullmatch(citation)
        if citation not in allowed_ids and matched is not None:
            candidate = matched.group("fragment_id")
            if candidate in allowed_ids:
                citation = candidate
        result.append(citation)
    return tuple(result)


def normalize_draft_sentence_citations(
    sentence: ComposedSentence, allowed_ids: Collection[str],
) -> ComposedSentence:
    """문장·의미칸·검수 상태를 유지하며 기존 승인과 구조화 사실은 건드리지 않는다."""
    if (sentence.verification_state != "unverified" or sentence.structured_claim is not None
            or sentence.verified_fact_id):
        return sentence
    citations = _canonical_draft_citations(sentence.citations, allowed_ids)
    return replace(sentence, citations=citations) if citations != sentence.citations else sentence


def normalize_draft_flow_citations(row: FlowRow, allowed_ids: Collection[str]) -> FlowRow:
    """검수 영수증이 없는 작성 직후 도식만 동일 조각 번호로 정리한다."""
    if row.review_binding is not None:
        return row
    citations = _canonical_draft_citations(row.citations, allowed_ids)
    return replace(row, citations=citations) if citations != row.citations else row
