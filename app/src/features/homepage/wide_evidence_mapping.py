"""documents/fragments/attempts를 앱 공용 계약 필드 이름의 평범한 자료형으로 바꾼다.

★ ``src.shared.report_evidence``는 여기서 **import하지 않는다** — 앱 공용
  계약 스키마 객체를 직접 쓰는 것은 ``chapter_evidence``의 몫이다(실행계획
  §4-2). 이 모듈은 필드 «이름»만 계약과 맞춘 dict·list·str·int를 만들 뿐,
  계약 스키마 자체를 검증하거나 가져오지 않는다.
★ 문서의 canonical 본문 문자열은 ``"\\n".join(document.usable_ranges)``로
  정의한다 — ``WideDocumentIdentity.content_sha256``이 이미 이 문자열의
  해시이므로(``wide_collect.py`` 참조) 이 정의가 그 값과 항상 일치한다.
★ 계약 쪽 ``usable_ranges``는 그 canonical 문자열 안에서 각 구간의
  ``{"start": int, "end": int}`` 오프셋 목록이다(구간 사이 "\\n" 1자
  반영, 정렬·비겹침). ``WideFragment.location``의 조각index는 이 목록의
  같은 인덱스를 가리킨다.
"""

from __future__ import annotations

from src.features.homepage.wide_types import (
    WideCollectionAttempt,
    WideDocumentIdentity,
    WideFragment,
)


def to_evidence_mappings(
    *,
    documents: tuple[WideDocumentIdentity, ...],
    fragments: tuple[WideFragment, ...],
    attempts: tuple[WideCollectionAttempt, ...],
) -> dict[str, list[dict[str, object]]]:
    """세 계열 전부를 계약 필드 이름의 dict·list·str·int로만 변환한다.

    Args:
        documents: ``wide_collect.collect_official_web_documents``가 만든 문서.
        fragments: ``wide_fragments.build_fragments``가 문서별로 만든 조각을
            모두 이어붙인 것(호출자가 문서마다 만들어 합친다).
        attempts: 같은 수집 실행의 시도 기록.

    Returns:
        ``{"documents": [...], "fragments": [...], "attempts": [...]}``.
        중첩 값은 전부 ``dict``·``list``·``str``·``int``만 쓴다(``tuple``도
        ``frozenset``도 없다) — 계약 쪽 (역)직렬화가 그대로 받을 수 있게.
    """
    return {
        "documents": [_document_mapping(document) for document in documents],
        "fragments": [_fragment_mapping(fragment) for fragment in fragments],
        "attempts": [_attempt_mapping(attempt) for attempt in attempts],
    }


def canonical_text_of(document: WideDocumentIdentity) -> str:
    """문서의 canonical 본문 문자열 — content_sha256이 실제로 해시한 값과 같다."""
    return "\n".join(document.usable_ranges)


def range_offsets_of(document: WideDocumentIdentity) -> list[dict[str, int]]:
    """``canonical_text_of(document)`` 안에서 usable_ranges 각 구간의 오프셋.

    ``canonical_text_of(document)[offset["start"]:offset["end"]]``는 항상
    ``document.usable_ranges[같은 인덱스]``와 정확히 같다(왕복 보장).
    """
    offsets: list[dict[str, int]] = []
    cursor = 0
    for text in document.usable_ranges:
        start = cursor
        end = start + len(text)
        offsets.append({"start": start, "end": end})
        cursor = end + 1  # 구간 사이 "\n" 구분자 한 글자
    return offsets


def _document_mapping(document: WideDocumentIdentity) -> dict[str, object]:
    return {
        "company_id": document.company_id,
        "document_id": document.document_id,
        "canonical_url": document.canonical_url,
        "source_kind": document.source_kind,
        "publisher": document.publisher,
        "title": document.title,
        "published_on": document.published_on,
        "collected_at": document.collected_at,
        "content_sha256": document.content_sha256,
        "identity_binding": document.identity_binding,
        "usable_ranges": range_offsets_of(document),
        "collector_version": document.collector_version,
        "parser_version": document.parser_version,
        "requirement": document.requirement,
        "source_tier": document.source_tier,
    }


def _fragment_mapping(fragment: WideFragment) -> dict[str, object]:
    return {
        "fragment_id": fragment.fragment_id,
        "document_id": fragment.document_id,
        "location": fragment.location,
        "text_sha256": fragment.text_sha256,
        "text": fragment.text,
        "section_id": fragment.section_id,
        "slot_id": fragment.slot_id,
        "score_millis": fragment.score_millis,
        "reason_codes": list(fragment.reason_codes),
    }


def _attempt_mapping(attempt: WideCollectionAttempt) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id,
        "source_kind": attempt.source_kind,
        "requirement": attempt.requirement,
        "state": attempt.state,
        "slot_ids": list(attempt.slot_ids),
        "reason_code": attempt.reason_code,
        "elapsed_ms": attempt.elapsed_ms,
        "bytes_downloaded": attempt.bytes_downloaded,
        "documents_seen": attempt.documents_seen,
    }
