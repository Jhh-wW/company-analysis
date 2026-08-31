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
★ P0-3(계약 generation=7) ``exact_evidence_hashes``: document Mapping마다
  그 document_id로 실제 내보내는 fragment들의 ``text_sha256`` 전체를
  결정론(정렬) 순서로, 중복 없이 담는다. 앱 계약은 이 값이 비어 있거나
  fragment의 text_sha256이 자기 문서 목록에 없으면 거절하므로, **scored
  fragment가 하나도 없는 문서는 애초에 documents 출력에서 뺀다**(조회
  사실 자체는 attempt로 남아 있으니 손실이 아니다).
★ 계약 generation=8 ``company_id``(fragments·attempts): fragment·attempt
  각각이 **생성 시점에** 스스로 실은 값을 그대로 pass-through만 한다 —
  이 변환 함수가 document.company_id로 채워 넣거나 호출 인자로 덮어쓰지
  않는다. 그렇게 하면 소유권 검증이 무의미해지므로(같은 document_id·슬롯의
  다른 회사 조회 결과가 섞여도 조용히 통과할 수 있다), 값의 원천은 항상
  ``wide_collect._CollectionState.add_attempt``·``wide_fragments.build_fragments``
  호출자다.
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
        ``fragments``가 하나도 가리키지 않는(scored fragment가 0개인) 문서는
        ``documents``에서 빠진다 — ``exact_evidence_hashes``가 빈 값이 되어
        앱 계약에 거절당하는 대신, 애초에 내보내지 않는다(P0-3).
    """
    hashes_by_document = _exact_hashes_by_document(fragments)
    return {
        "documents": [
            _document_mapping(document, hashes_by_document[document.document_id])
            for document in documents
            if hashes_by_document.get(document.document_id)
        ],
        "fragments": [_fragment_mapping(fragment) for fragment in fragments],
        "attempts": [_attempt_mapping(attempt) for attempt in attempts],
    }


def _exact_hashes_by_document(fragments: tuple[WideFragment, ...]) -> dict[str, list[str]]:
    """document_id별로 실제 내보내는 fragment의 text_sha256을 결정론·중복없이 모은다.

    정렬(사전식) 순서를 쓴다 — 호출자가 넘긴 fragments의 나열 순서에
    의존하지 않아도 항상 같은 출력을 보장한다(왕복·재현성).
    """
    grouped: dict[str, set[str]] = {}
    for fragment in fragments:
        grouped.setdefault(fragment.document_id, set()).add(fragment.text_sha256)
    return {document_id: sorted(hashes) for document_id, hashes in grouped.items()}


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


def _document_mapping(
    document: WideDocumentIdentity, exact_evidence_hashes: list[str]
) -> dict[str, object]:
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
        #: P0-3(계약 generation=7) — 이 문서로 실제 내보내는 fragment의
        #: text_sha256 전체(정렬·중복없음). 호출 시점에 이미 1개 이상임이
        #: 보장된다(``to_evidence_mappings``가 0개인 문서는 애초에 뺀다).
        "exact_evidence_hashes": exact_evidence_hashes,
    }


def _fragment_mapping(fragment: WideFragment) -> dict[str, object]:
    return {
        # 계약 generation=8 — fragment 자신이 생성 시점에 실은 값을 그대로
        # 내보낸다(document.company_id로 채워 넣거나 덮어쓰지 않는다).
        "company_id": fragment.company_id,
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
        # 계약 generation=8 — attempt 자신이 생성 시점에 실은 값을 그대로
        # 내보낸다(document.company_id로 채워 넣거나 덮어쓰지 않는다).
        "company_id": attempt.company_id,
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
