"""DartEvidenceHarvest를 평범한 dict·list·str·int로 직렬화한다.

app 쪽 adapter가 이 Mapping을 그대로 계약 자료형으로 변환한다(2026-08-31
team-lead 통보). frozen dataclass·frozenset·tuple 같은 파이썬 전용 타입은
하나도 새지 않는다 — JSON으로 그대로 dump할 수 있는 모양만 돌려준다.

★ generation=7 계약(P0-5, 2026-08-31) — 각 document Mapping에는
``exact_evidence_hashes``가 있다: 그 document_id로 실제 내보내는 fragment들의
``text_sha256`` 전체를 결정론적 순서로, 중복 없이 담는다. ``harvest.fragments``는
collect.py가 이미 scored fragment만 넣도록 보장하므로(P0-1·P0-3) 이 값은
documents에 실린 문서마다 절대 비지 않는다 — 두 보장이 같은 경계(scored
fragment 존재 여부)에서 나온다.

★ generation=8 계약(2026-08-31 team-lead 통보) — fragments·attempts 각
항목에도 ``company_id``가 있다. **이 값은 harvest.company_id에서 채워
넣거나 호출 인자로 덮어쓰지 않는다** — fragment.company_id·attempt.company_id를
있는 그대로 옮길 뿐이다(아래 ``_fragment_to_mapping``·``_attempt_to_mapping``은
harvest를 아예 받지 않는다 — 덮어쓸 방법 자체가 없다). 값 자체가 harvest의
company_id와 다르면 그보다 앞서 ``DartEvidenceHarvest.__post_init__``이
생성을 거절하므로 여기 도달하는 값은 이미 검증된 값이다.
"""

from __future__ import annotations

from features.evidence_collection.models import (
    CollectedDocument,
    CollectionAttempt,
    DartEvidenceHarvest,
    DocumentTextRange,
    EvidenceFragment,
)


def _range_to_mapping(text_range: DocumentTextRange) -> dict[str, int]:
    return {"start": text_range.start, "end": text_range.end}


def _document_to_mapping(
    document: CollectedDocument, exact_evidence_hashes: list[str],
) -> dict[str, object]:
    return {
        "company_id": document.company_id,
        "document_id": document.document_id,
        "canonical_url": document.canonical_url,
        "source_tier": document.source_tier,
        "source_kind": document.source_kind,
        "publisher": document.publisher,
        "title": document.title,
        "published_on": document.published_on,
        "collected_at": document.collected_at,
        "content_sha256": document.content_sha256,
        "identity_binding": document.identity_binding,
        "usable_ranges": [_range_to_mapping(r) for r in document.usable_ranges],
        "collector_version": document.collector_version,
        "parser_version": document.parser_version,
        "requirement": document.requirement,
        "exact_evidence_hashes": exact_evidence_hashes,
    }


def _fragment_to_mapping(fragment: EvidenceFragment) -> dict[str, object]:
    return {
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
        "period_start": fragment.period_start,
        "period_end": fragment.period_end,
        "unit": fragment.unit,
        "company_scope": fragment.company_scope,
    }


def _attempt_to_mapping(attempt: CollectionAttempt) -> dict[str, object]:
    return {
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


def _exact_evidence_hashes_by_document_id(harvest: DartEvidenceHarvest) -> dict[str, list[str]]:
    """document_id별 fragment text_sha256을 정렬·중복 제거해 모은다(P0-5)."""
    hashes_by_document_id: dict[str, set[str]] = {}
    for fragment in harvest.fragments:
        hashes_by_document_id.setdefault(fragment.document_id, set()).add(fragment.text_sha256)
    return {
        document_id: sorted(hashes) for document_id, hashes in hashes_by_document_id.items()
    }


def harvest_to_mapping(harvest: DartEvidenceHarvest) -> dict[str, object]:
    """DartEvidenceHarvest 전체를 dict·list·str·int만으로 직렬화한다.

    documents/fragments/attempts는 harvest에 저장된 순서를 그대로 보존한다
    (재정렬하지 않는다 — 호출자가 순서로 의미를 둘 수 있으므로 결정론을
    지킨다). dataclass·frozenset·tuple은 하나도 남기지 않는다.
    """
    exact_hashes_by_document_id = _exact_evidence_hashes_by_document_id(harvest)
    return {
        "company_id": harvest.company_id,
        "company_type": harvest.company_type,
        "documents": [
            _document_to_mapping(
                document, exact_hashes_by_document_id.get(document.document_id, []),
            )
            for document in harvest.documents
        ],
        "fragments": [_fragment_to_mapping(fragment) for fragment in harvest.fragments],
        "attempts": [_attempt_to_mapping(attempt) for attempt in harvest.attempts],
    }
