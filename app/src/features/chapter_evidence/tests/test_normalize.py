from __future__ import annotations

import pytest

from src.features.chapter_evidence.constants import CompanyType
from src.features.chapter_evidence.normalize import (
    normalize_attempts,
    normalize_company_type,
    normalize_documents,
    normalize_fragments,
    to_attempt,
    to_document,
    to_fragment,
)
from src.features.chapter_evidence.tests.fixtures import (
    make_attempt,
    make_document,
    make_fragment,
    sha256_of,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    CollectedEvidenceDocument,
    CollectionAttempt,
    EvidenceFragment,
)


def test_매핑_문서를_계약형으로_바꾼다() -> None:
    mapping = make_document(
        company_id="corp-1", document_id="doc-1", source_kind="dart_business_report"
    )

    document = to_document(mapping)

    assert isinstance(document, CollectedEvidenceDocument)
    assert document.company_id == "corp-1"
    assert document.source_tier is SourceTier.TIER_1_OFFICIAL
    assert document.requirement is SourceRequirement.REQUIRED


def test_이미_계약_인스턴스인_문서는_그대로_돌려준다() -> None:
    mapping = make_document(
        company_id="corp-1", document_id="doc-1", source_kind="dart_business_report"
    )
    document = to_document(mapping)

    assert to_document(document) is document


def test_문서에_필수_항목이_빠지면_한국어_예외를_낸다() -> None:
    mapping = make_document(
        company_id="corp-1", document_id="doc-1", source_kind="dart_business_report"
    )
    del mapping["canonical_url"]

    with pytest.raises(ValueError, match="필수 항목이 빠졌습니다"):
        to_document(mapping)


def test_문서가_매핑도_계약형도_아니면_거부한다() -> None:
    with pytest.raises(ValueError, match="매핑이어야 합니다"):
        to_document(object())


def test_알수없는_출처등급_문자열은_거부한다() -> None:
    mapping = make_document(
        company_id="corp-1", document_id="doc-1", source_kind="dart_business_report"
    )
    mapping["source_tier"] = "TIER_0_UNKNOWN"

    with pytest.raises(ValueError, match="문서 출처 등급"):
        to_document(mapping)


def test_문서의_정확한_근거_해시_목록이_계약형으로_전달된다() -> None:
    expected_hashes = (sha256_of("첫 번째 근거 원문"), sha256_of("두 번째 근거 원문"))
    mapping = make_document(
        company_id="corp-1",
        document_id="doc-1",
        source_kind="dart_business_report",
        exact_evidence_hashes=expected_hashes,
    )

    document = to_document(mapping)

    assert document.exact_evidence_hashes == expected_hashes


def test_문서에_정확한_근거_해시가_빠지면_한국어_예외를_낸다() -> None:
    mapping = make_document(
        company_id="corp-1", document_id="doc-1", source_kind="dart_business_report"
    )
    del mapping["exact_evidence_hashes"]

    with pytest.raises(ValueError, match="필수 항목이 빠졌습니다"):
        to_document(mapping)


def test_문서의_정확한_근거_해시_형식이_잘못되면_거부한다() -> None:
    mapping = make_document(
        company_id="corp-1", document_id="doc-1", source_kind="dart_business_report"
    )
    mapping["exact_evidence_hashes"] = "not-a-list"

    with pytest.raises(ValueError, match="목록 형식이 올바르지 않습니다"):
        to_document(mapping)


def test_매핑_조각을_계약형으로_바꾼다() -> None:
    mapping = make_fragment(
        fragment_id="frag-1",
        document_id="doc-1",
        section_id="identity",
        slot_id="identity:corporate_identity",
        text="회사는 공식 신원을 밝힌 사업자입니다.",
    )

    fragment = to_fragment(mapping)

    assert isinstance(fragment, EvidenceFragment)
    assert fragment.text_sha256 == sha256_of(mapping["text"])
    assert fragment.period_start == ""


def test_조각에_필수_항목이_빠지면_한국어_예외를_낸다() -> None:
    mapping = make_fragment(
        fragment_id="frag-1",
        document_id="doc-1",
        section_id="identity",
        slot_id="identity:corporate_identity",
        text="본문",
    )
    del mapping["slot_id"]

    with pytest.raises(ValueError, match="필수 항목이 빠졌습니다"):
        to_fragment(mapping)


def test_조각의_해시가_원문과_다르면_계약검증에서_거부된다() -> None:
    mapping = make_fragment(
        fragment_id="frag-1",
        document_id="doc-1",
        section_id="identity",
        slot_id="identity:corporate_identity",
        text="본문",
    )
    mapping["text_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="일치하지 않습니다"):
        to_fragment(mapping)


def test_매핑_시도를_계약형으로_바꾼다() -> None:
    mapping = make_attempt(
        attempt_id="attempt-1",
        source_kind="dart_business_report",
        slot_ids=("identity:corporate_identity",),
        state=CollectionState.OK.value,
        reason_code="dart_ok",
    )

    attempt = to_attempt(mapping)

    assert isinstance(attempt, CollectionAttempt)
    assert attempt.state is CollectionState.OK
    assert attempt.requirement is SourceRequirement.REQUIRED
    assert attempt.elapsed_ms == 0


def test_시도에_필수_항목이_빠지면_한국어_예외를_낸다() -> None:
    mapping = make_attempt(
        attempt_id="attempt-1",
        source_kind="dart_business_report",
        slot_ids=("identity:corporate_identity",),
        state=CollectionState.OK.value,
        reason_code="dart_ok",
    )
    del mapping["state"]

    with pytest.raises(ValueError, match="필수 항목이 빠졌습니다"):
        to_attempt(mapping)


def test_배치_정규화는_순서를_보존한다() -> None:
    docs = [
        make_document(company_id="corp-1", document_id=f"doc-{i}", source_kind="dart_x")
        for i in range(3)
    ]

    normalized = normalize_documents(docs)

    assert [document.document_id for document in normalized] == [
        "doc-0",
        "doc-1",
        "doc-2",
    ]


def test_빈_배치는_빈_튜플이다() -> None:
    assert normalize_fragments([]) == ()
    assert normalize_attempts([]) == ()


def test_회사유형_문자열을_열거형으로_바꾼다() -> None:
    assert normalize_company_type("listed") is CompanyType.LISTED
    assert normalize_company_type(CompanyType.AUDIT_ONLY) is CompanyType.AUDIT_ONLY


def test_알수없는_회사유형은_거부한다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 회사 유형"):
        normalize_company_type("unicorn")
