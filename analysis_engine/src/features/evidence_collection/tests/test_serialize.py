"""harvest_to_mapping — dataclass 없는 평범한 Mapping 직렬화 왕복 시험."""

from __future__ import annotations

import hashlib

from features.evidence_collection import constants as c
from features.evidence_collection.models import (
    CollectedDocument,
    CollectionAttempt,
    DartEvidenceHarvest,
    DocumentTextRange,
    EvidenceFragment,
)
from features.evidence_collection.serialize import harvest_to_mapping

_JSON_SAFE_SCALAR_TYPES = (str, int, float, bool, type(None))


def _assert_json_safe(value: object) -> None:
    """dict·list·str·int만 남았는지 재귀로 확인한다(dataclass·tuple·frozenset 금지)."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str)
            _assert_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            _assert_json_safe(item)
    else:
        assert isinstance(value, _JSON_SAFE_SCALAR_TYPES), f"JSON-safe하지 않은 값: {value!r}"


def _document(document_id: str) -> CollectedDocument:
    return CollectedDocument(
        company_id="00126380",
        document_id=document_id,
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250315000001",
        source_tier=c.SOURCE_TIER_OFFICIAL,
        source_kind=c.SOURCE_KIND_BUSINESS_REPORT,
        publisher=c.DART_PUBLISHER_NAME,
        title="사업보고서",
        published_on="20250315",
        collected_at="2026-08-31T00:00:00+09:00",
        content_sha256="a" * 64,
        identity_binding="corp_code=00126380;rcept_no=20250315000001",
        usable_ranges=(DocumentTextRange(0, 10), DocumentTextRange(20, 35)),
        collector_version=c.COLLECTOR_VERSION,
        parser_version=c.PARSER_VERSION,
        requirement=c.REQUIREMENT_REQUIRED,
    )


def _fragment(fragment_id: str, document_id: str) -> EvidenceFragment:
    text = f"조각 원문 {fragment_id}"
    return EvidenceFragment(
        fragment_id=fragment_id,
        document_id=document_id,
        location="0-20",
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        section_id="identity",
        slot_id="identity:corporate_identity",
        score_millis=750,
        reason_codes=("keyword_hit:identity:corporate_identity", "heading_match:identity"),
    )


def _attempt(attempt_id: str) -> CollectionAttempt:
    return CollectionAttempt(
        attempt_id=attempt_id,
        source_kind=c.SOURCE_KIND_BUSINESS_REPORT,
        requirement=c.REQUIREMENT_REQUIRED,
        state=c.ATTEMPT_STATE_OK,
        slot_ids=("identity:corporate_identity", "identity:business_definition"),
        reason_code=c.REASON_DOCUMENT_FETCH_OK,
        elapsed_ms=120,
        bytes_downloaded=4096,
        documents_seen=1,
    )


def _harvest() -> DartEvidenceHarvest:
    doc_a = _document("dart_business_report:20250315000001")
    doc_b = _document("dart_business_report:20250815000002")
    return DartEvidenceHarvest(
        company_id="00126380",
        company_type=c.COMPANY_TYPE_LISTED,
        documents=(doc_a, doc_b),
        fragments=(
            _fragment("frag-1", doc_a.document_id),
            _fragment("frag-2", doc_b.document_id),
        ),
        attempts=(_attempt("attempt-1"), _attempt("attempt-2")),
    )


def test_직렬화_결과는_JSON_safe한_dict_list_str_int만_쓴다() -> None:
    mapping = harvest_to_mapping(_harvest())
    _assert_json_safe(mapping)


def test_최상위_필드_이름은_계약_그대로다() -> None:
    mapping = harvest_to_mapping(_harvest())
    assert set(mapping.keys()) == {
        "company_id", "company_type", "documents", "fragments", "attempts",
    }


def test_company_id_company_type은_원본과_일치한다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)
    assert mapping["company_id"] == harvest.company_id
    assert mapping["company_type"] == harvest.company_type


def test_documents_필드_이름과_값이_원본과_일치한다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)
    original = harvest.documents[0]
    serialized = mapping["documents"][0]

    assert set(serialized.keys()) == {
        "company_id", "document_id", "canonical_url", "source_tier", "source_kind",
        "publisher", "title", "published_on", "collected_at", "content_sha256",
        "identity_binding", "usable_ranges", "collector_version", "parser_version",
        "requirement",
    }
    assert serialized["document_id"] == original.document_id
    assert serialized["content_sha256"] == original.content_sha256
    assert serialized["requirement"] == original.requirement


def test_usable_ranges는_start_end_dict_리스트로_바뀐다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)
    original_ranges = harvest.documents[0].usable_ranges
    serialized_ranges = mapping["documents"][0]["usable_ranges"]

    assert serialized_ranges == [
        {"start": r.start, "end": r.end} for r in original_ranges
    ]
    assert isinstance(serialized_ranges, list)
    assert all(isinstance(r, dict) for r in serialized_ranges)


def test_fragments_필드_이름과_값이_원본과_일치하고_reason_codes는_list다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)
    original = harvest.fragments[0]
    serialized = mapping["fragments"][0]

    assert set(serialized.keys()) == {
        "fragment_id", "document_id", "location", "text_sha256", "text",
        "section_id", "slot_id", "score_millis", "reason_codes",
        "period_start", "period_end", "unit", "company_scope",
    }
    assert serialized["fragment_id"] == original.fragment_id
    assert serialized["text"] == original.text
    assert serialized["score_millis"] == original.score_millis
    assert serialized["reason_codes"] == list(original.reason_codes)
    assert isinstance(serialized["reason_codes"], list)


def test_attempts_필드_이름과_값이_원본과_일치하고_slot_ids는_list다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)
    original = harvest.attempts[0]
    serialized = mapping["attempts"][0]

    assert set(serialized.keys()) == {
        "attempt_id", "source_kind", "requirement", "state", "slot_ids",
        "reason_code", "elapsed_ms", "bytes_downloaded", "documents_seen",
    }
    assert serialized["attempt_id"] == original.attempt_id
    assert serialized["slot_ids"] == list(original.slot_ids)
    assert isinstance(serialized["slot_ids"], list)
    assert serialized["elapsed_ms"] == original.elapsed_ms


def test_documents_fragments_attempts_순서는_원본_순서를_보존한다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)

    assert [d["document_id"] for d in mapping["documents"]] == [
        d.document_id for d in harvest.documents
    ]
    assert [f["fragment_id"] for f in mapping["fragments"]] == [
        f.fragment_id for f in harvest.fragments
    ]
    assert [a["attempt_id"] for a in mapping["attempts"]] == [
        a.attempt_id for a in harvest.attempts
    ]


def test_빈_harvest도_빈_리스트로_직렬화된다() -> None:
    harvest = DartEvidenceHarvest(
        company_id="00126380",
        company_type=c.COMPANY_TYPE_AUDIT_ONLY,
        documents=(),
        fragments=(),
        attempts=(),
    )
    mapping = harvest_to_mapping(harvest)
    assert mapping["documents"] == []
    assert mapping["fragments"] == []
    assert mapping["attempts"] == []
