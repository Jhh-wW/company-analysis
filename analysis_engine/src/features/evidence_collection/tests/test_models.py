"""frozen dataclass 자료형의 생성 검증(ValueError) 시험."""

from __future__ import annotations

import hashlib

import pytest

from features.evidence_collection import constants as c
from features.evidence_collection.models import (
    CollectedDocument,
    CollectionAttempt,
    DartEvidenceHarvest,
    DocumentTextRange,
    EvidenceCollectionError,
    EvidenceFragment,
)

_COMPANY_ID = "00126380"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_document(**overrides: object) -> CollectedDocument:
    fields = {
        "company_id": _COMPANY_ID,
        "document_id": "dart_business_report:20250315000001",
        "canonical_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250315000001",
        "source_tier": c.SOURCE_TIER_OFFICIAL,
        "source_kind": c.SOURCE_KIND_BUSINESS_REPORT,
        "publisher": c.DART_PUBLISHER_NAME,
        "title": "사업보고서",
        "published_on": "20250315",
        "collected_at": "2026-08-31T00:00:00+09:00",
        "content_sha256": _sha256("본문"),
        "identity_binding": "corp_code=00126380;rcept_no=20250315000001",
        "usable_ranges": (DocumentTextRange(0, 10),),
        "collector_version": c.COLLECTOR_VERSION,
        "parser_version": c.PARSER_VERSION,
        "requirement": c.REQUIREMENT_REQUIRED,
    }
    fields.update(overrides)
    return CollectedDocument(**fields)


def _make_fragment(**overrides: object) -> EvidenceFragment:
    text = overrides.pop("text", "당사는 전자부품을 제조하는 법인이다.")
    fields = {
        "company_id": _COMPANY_ID,
        "fragment_id": "dart_business_report:20250315000001:frag0",
        "document_id": "dart_business_report:20250315000001",
        "location": "0-20",
        "text_sha256": _sha256(text),
        "text": text,
        "section_id": "identity",
        "slot_id": "identity:corporate_identity",
        "score_millis": 500,
        "reason_codes": ("keyword_hit:identity:corporate_identity",),
    }
    fields.update(overrides)
    return EvidenceFragment(**fields)


def _make_attempt(**overrides: object) -> CollectionAttempt:
    fields = {
        "company_id": _COMPANY_ID,
        "attempt_id": "list:dart_business_report",
        "source_kind": c.SOURCE_KIND_BUSINESS_REPORT,
        "requirement": c.REQUIREMENT_REQUIRED,
        "state": c.ATTEMPT_STATE_OK,
        "slot_ids": ("identity:corporate_identity",),
        "reason_code": c.REASON_LIST_QUERY_OK,
        "elapsed_ms": 0,
        "bytes_downloaded": 0,
        "documents_seen": 1,
    }
    fields.update(overrides)
    return CollectionAttempt(**fields)


def test_document_text_range는_start가_end보다_작아야_한다() -> None:
    DocumentTextRange(0, 1)
    with pytest.raises(EvidenceCollectionError):
        DocumentTextRange(5, 5)
    with pytest.raises(EvidenceCollectionError):
        DocumentTextRange(-1, 3)


def test_collected_document_usable_ranges_겹치면_거부한다() -> None:
    with pytest.raises(EvidenceCollectionError):
        _make_document(usable_ranges=(DocumentTextRange(0, 10), DocumentTextRange(5, 15)))


def test_collected_document_빈_필드는_거부한다() -> None:
    with pytest.raises(EvidenceCollectionError):
        _make_document(company_id="")


def test_collected_document_sha256_형식_오류는_거부한다() -> None:
    with pytest.raises(EvidenceCollectionError):
        _make_document(content_sha256="not-a-hash")


def test_fragment_text_sha256_불일치는_거부한다() -> None:
    text = "당사는 전자부품을 제조하는 법인이다."
    with pytest.raises(EvidenceCollectionError):
        _make_fragment(text=text, text_sha256=_sha256("다른 문장"))


def test_fragment_slot_id는_section_id_소속과_일치해야_한다() -> None:
    with pytest.raises(EvidenceCollectionError):
        _make_fragment(section_id="business_model", slot_id="identity:corporate_identity")


def test_fragment_reason_codes는_1개_이상이어야_한다() -> None:
    with pytest.raises(EvidenceCollectionError):
        _make_fragment(reason_codes=())


def test_gen8_fragment_company_id는_빈_문자열을_거부한다() -> None:
    """generation=8(2026-08-31) — company_id는 EvidenceFragment의 필수 필드다."""
    with pytest.raises(EvidenceCollectionError):
        _make_fragment(company_id="")


def test_collection_attempt_slot_ids는_알려진_값이어야_한다() -> None:
    with pytest.raises(EvidenceCollectionError):
        _make_attempt(slot_ids=("모르는:슬롯",))


def test_gen8_attempt_company_id는_빈_문자열을_거부한다() -> None:
    """generation=8 — company_id는 CollectionAttempt의 필수 필드다."""
    with pytest.raises(EvidenceCollectionError):
        _make_attempt(company_id="")


def test_harvest_document_id_중복은_거부한다() -> None:
    document = _make_document()
    with pytest.raises(EvidenceCollectionError):
        DartEvidenceHarvest(
            company_id=_COMPANY_ID,
            company_type=c.COMPANY_TYPE_LISTED,
            documents=(document, document),
            fragments=(),
            attempts=(),
        )


def test_harvest_fragment의_document_id는_documents에_있어야_한다() -> None:
    fragment = _make_fragment()
    with pytest.raises(EvidenceCollectionError):
        DartEvidenceHarvest(
            company_id=_COMPANY_ID,
            company_type=c.COMPANY_TYPE_LISTED,
            documents=(),
            fragments=(fragment,),
            attempts=(),
        )


def test_gen8_harvest_document_company_id가_다르면_거부한다() -> None:
    """다른 회사 문서가 섞여 들어오면 harvest 생성 자체가 거절된다."""
    document = _make_document(company_id="99999999")
    with pytest.raises(EvidenceCollectionError):
        DartEvidenceHarvest(
            company_id=_COMPANY_ID,
            company_type=c.COMPANY_TYPE_LISTED,
            documents=(document,),
            fragments=(),
            attempts=(),
        )


def test_gen8_harvest_fragment_company_id가_다르면_거부한다() -> None:
    """document_id는 맞아도 조각 자체의 company_id가 다르면 거절한다."""
    document = _make_document()
    fragment = _make_fragment(company_id="99999999")
    with pytest.raises(EvidenceCollectionError):
        DartEvidenceHarvest(
            company_id=_COMPANY_ID,
            company_type=c.COMPANY_TYPE_LISTED,
            documents=(document,),
            fragments=(fragment,),
            attempts=(),
        )


def test_gen8_harvest_attempt_company_id가_다르면_거부한다() -> None:
    attempt = _make_attempt(company_id="99999999")
    with pytest.raises(EvidenceCollectionError):
        DartEvidenceHarvest(
            company_id=_COMPANY_ID,
            company_type=c.COMPANY_TYPE_LISTED,
            documents=(),
            fragments=(),
            attempts=(attempt,),
        )


def test_harvest_정상_생성() -> None:
    document = _make_document()
    fragment = _make_fragment()
    attempt = _make_attempt()
    harvest = DartEvidenceHarvest(
        company_id=_COMPANY_ID,
        company_type=c.COMPANY_TYPE_LISTED,
        documents=(document,),
        fragments=(fragment,),
        attempts=(attempt,),
    )
    assert harvest.documents[0].document_id == fragment.document_id
    assert harvest.fragments[0].company_id == _COMPANY_ID
    assert harvest.attempts[0].company_id == _COMPANY_ID
