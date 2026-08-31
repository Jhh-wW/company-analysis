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
        "requirement", "exact_evidence_hashes",
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


def test_P0_5_exact_evidence_hashes는_같은_문서_fragment의_text_sha256_전체다() -> None:
    """왕복 시험 — 문서 1개, 서로 다른 원문의 fragment 2개."""
    doc = _document("dart_business_report:20250315000001")
    frag_a = _fragment("frag-a", doc.document_id)
    frag_b = EvidenceFragment(
        fragment_id="frag-b",
        document_id=doc.document_id,
        location="20-40",
        text_sha256=hashlib.sha256("또 다른 조각 원문".encode("utf-8")).hexdigest(),
        text="또 다른 조각 원문",
        section_id="business_model",
        slot_id="business_model:revenue_model",
        score_millis=500,
        reason_codes=("keyword_hit:business_model:revenue_model",),
    )
    harvest = DartEvidenceHarvest(
        company_id="00126380",
        company_type=c.COMPANY_TYPE_LISTED,
        documents=(doc,),
        fragments=(frag_a, frag_b),
        attempts=(),
    )

    mapping = harvest_to_mapping(harvest)
    exact_hashes = mapping["documents"][0]["exact_evidence_hashes"]

    assert exact_hashes == sorted([frag_a.text_sha256, frag_b.text_sha256])
    assert all(len(h) == 64 for h in exact_hashes)


def test_P0_5_exact_evidence_hashes는_다른_문서의_fragment를_섞지_않는다() -> None:
    """문서 2개 — 각 document의 exact_evidence_hashes에는 자기 fragment의 해시만 있다."""
    harvest = _harvest()  # doc_a에는 frag-1, doc_b에는 frag-2

    mapping = harvest_to_mapping(harvest)

    assert mapping["documents"][0]["exact_evidence_hashes"] == [harvest.fragments[0].text_sha256]
    assert mapping["documents"][1]["exact_evidence_hashes"] == [harvest.fragments[1].text_sha256]


def test_P0_5_exact_evidence_hashes는_중복_없이_담는다() -> None:
    """같은 문서에 같은 text_sha256을 가진 fragment가 두 개면 한 번만 담는다."""
    doc = _document("dart_business_report:20250315000001")
    same_text = "당사는 서로 다른 위치에 같은 문장이 반복돼 실린 경우다."
    same_hash = hashlib.sha256(same_text.encode("utf-8")).hexdigest()
    frag_a = EvidenceFragment(
        fragment_id="frag-a", document_id=doc.document_id, location="0-30",
        text_sha256=same_hash, text=same_text, section_id="identity",
        slot_id="identity:corporate_identity", score_millis=500,
        reason_codes=("keyword_hit:identity:corporate_identity",),
    )
    frag_b = EvidenceFragment(
        fragment_id="frag-b", document_id=doc.document_id, location="500-530",
        text_sha256=same_hash, text=same_text, section_id="identity",
        slot_id="identity:corporate_identity", score_millis=500,
        reason_codes=("keyword_hit:identity:corporate_identity",),
    )
    harvest = DartEvidenceHarvest(
        company_id="00126380",
        company_type=c.COMPANY_TYPE_LISTED,
        documents=(doc,),
        fragments=(frag_a, frag_b),
        attempts=(),
    )

    mapping = harvest_to_mapping(harvest)

    assert mapping["documents"][0]["exact_evidence_hashes"] == [frag_a.text_sha256]


def test_P0_5_문서가_있으면_exact_evidence_hashes는_절대_비지_않는다() -> None:
    harvest = _harvest()
    mapping = harvest_to_mapping(harvest)

    assert all(document["exact_evidence_hashes"] for document in mapping["documents"])


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
