from __future__ import annotations

import hashlib

import pytest

from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.pipeline import comparison_transport
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.provenance.sources import Source, SourceKind, seal_collected_source
from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUE
from src.shared.report_evidence.constants import (
    EvidenceReadiness,
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    build_verified_dart_filing_official_web_binding,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.profile_domain_attestation import (
    build_registered_subdomain_profile_attestation,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import (
    FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND,
    document_slots_for_formal_source_kind,
)


_CORP_CODE = "00126380"
_COMPANY_NAME = "가나다전자"
_COLLECTED_ON = "2026-09-04"
_RECEIPT_NO = "20260315000123"
_PROFILE = {
    "status": "000",
    "corp_code": _CORP_CODE,
    "corp_name": _COMPANY_NAME,
    "hm_url": "https://example.com",
}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strict_dart_web_binding(source_url: str) -> str:
    provenance = build_dart_filing_url_provenance(
        company_id=_CORP_CODE,
        url=source_url,
        source_document_id=f"dart_business_report:{_RECEIPT_NO}",
        source_receipt_no=_RECEIPT_NO,
        source_member_name="covers/company-homepage.xml",
        source_location="raw_xml_chars:10-40",
        source_document_sha256="d" * 64,
        source_payload_sha256="e" * 64,
    )
    return build_verified_dart_filing_official_web_binding(
        provenance_value=provenance,
        company_id=_CORP_CODE,
        company_name=_COMPANY_NAME,
        company_registration_numbers=("123-45-67890",),
        candidate_url=source_url,
        effective_urls=(source_url,),
        scope_sha256="f" * 64,
        scope_allows=lambda candidate: candidate == source_url,
        identity_evidence_sha256="a" * 64,
        matched_name_sha256=_sha(_COMPANY_NAME),
        registration_number_sha256=_sha("1234567890"),
    )


def _formal_result(source_kind: str) -> OfficialEvidenceCollectionResult:
    """formal DTO를 생산 어댑터에 넣어 raw를 손으로 꾸미지 않는 fixture."""

    slot_id = sorted(document_slots_for_formal_source_kind(source_kind))[0]
    section_id = slot_id.split(":", 1)[0]
    text = f"{_COMPANY_NAME}의 {source_kind} 공식 사업 근거입니다."
    digest = _sha(text)
    tier, requirement = FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[source_kind]
    attestation_id = ""
    attestation_evidence = ""
    reporting_period = ""
    attachment_url = ""
    ir_verification = ""

    if source_kind.startswith("dart_"):
        source_url = (
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + _RECEIPT_NO
        )
        document_id = f"{source_kind}:{_RECEIPT_NO}"
        publisher = "금융감독원 전자공시시스템"
        published_on = "2026-03-15"
        identity_binding = (
            f"corp_code={_CORP_CODE};rcept_no={_RECEIPT_NO};identity_check=verified"
        )
    else:
        attestation_id, base_attestation = dart_profile_attestation_material(
            profile=_PROFILE,
            corp_code=_CORP_CODE,
            company_name=_COMPANY_NAME,
        )
        assert attestation_id and base_attestation
        if source_kind == SOURCE_KIND_OFFICIAL_RECRUIT_PAGE:
            source_url = "https://recruit.example.com/jobs"
            attestation_evidence = build_registered_subdomain_profile_attestation(
                base_attestation,
                source_url=source_url,
            )
            assert attestation_evidence
        elif source_kind == SOURCE_KIND_OFFICIAL_IR_PDF:
            source_url = "https://example.com/ir/2026-q2.pdf"
            attestation_evidence = base_attestation
            reporting_period = "2026-Q2"
            attachment_url = source_url
            ir_verification = IR_METADATA_VERIFICATION_VALUE
        else:
            source_url = "https://example.com/about"
            attestation_evidence = base_attestation
        document_id = f"{source_kind}:fixture"
        publisher = "example.com"
        published_on = (
            "2026-06-30" if source_kind == SOURCE_KIND_OFFICIAL_IR_PDF else ""
        )
        identity_binding = "dart-profile-homepage"
        if source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
            identity_binding = _strict_dart_web_binding(source_url)
            attestation_id = ""
            attestation_evidence = ""

    document = CollectedEvidenceDocument(
        company_id=_CORP_CODE,
        document_id=document_id,
        canonical_url=source_url,
        source_tier=tier,
        source_kind=source_kind,
        publisher=publisher,
        title=f"{source_kind} 원문",
        published_on=published_on,
        collected_at=_COLLECTED_ON,
        content_sha256=digest,
        exact_evidence_hashes=(digest,),
        identity_binding=identity_binding,
        usable_ranges=(DocumentTextRange(0, len(text)),),
        collector_version="comparison-round-trip-fixture/1",
        parser_version="comparison-round-trip-fixture/1",
        requirement=requirement,
        domain_attestation_source_id=attestation_id,
        domain_attestation_evidence=attestation_evidence,
        reporting_period=reporting_period,
        attachment_url=attachment_url,
        ir_metadata_verification=ir_verification,
    )
    fragment = EvidenceFragment(
        company_id=_CORP_CODE,
        fragment_id=f"{document_id}:fragment",
        document_id=document_id,
        location=f"section:{section_id}",
        text_sha256=digest,
        text=text,
        section_id=section_id,
        slot_id=slot_id,
        covered_slot_ids=(slot_id,),
        score_millis=900,
        reason_codes=("fixture_exact_match",),
    )
    candidates = tuple(
        ChapterEvidenceCandidates(
            company_id=_CORP_CODE,
            section_id=current_section,
            documents=(document,) if current_section == section_id else (),
            fragments=(fragment,) if current_section == section_id else (),
            attempts=(),
            candidate_readiness=(
                EvidenceReadiness.READY
                if current_section == section_id
                else EvidenceReadiness.INSUFFICIENT
            ),
            reason_codes=(),
            estimated_tokens=30 if current_section == section_id else 0,
            max_chars=10_000,
            max_estimated_tokens=10_000,
        )
        for current_section in REQUIRED_EVIDENCE_SECTION_IDS
    )
    return OfficialEvidenceCollectionResult(
        company_id=_CORP_CODE,
        candidates=candidates,
    )


@pytest.mark.parametrize("raw", ("2024-02-29", "20240229"))
def test_typed_비교_Source는_신규_ISO와_옛_compact를_canonical로_만든다(
    raw: str,
) -> None:
    disclosed_at = comparison_transport._source_date(raw)  # noqa: SLF001
    source = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="사업보고서",
            disclosed_at=disclosed_at,
            collected_at="2026-09-04",
            source_id="typed-comparison-source-1",
            title="사업보고서",
            publisher="가나다전자",
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250315000001",
            document_id="20250315000001",
            location="사업의 내용",
            source_type="공식 공시",
            fact_status="공시 실제값",
            evidence_hashes=["a" * 64],
            exact_evidence_hashes=["b" * 64],
        )
    )

    assert disclosed_at == "2024-02-29"
    assert source.is_canonical_valid


@pytest.mark.parametrize("raw", ("2025-02-30", "20250230", "임의 문자열"))
def test_typed_비교_adapter는_잘못된_DART_날짜를_즉시_거부한다(raw: str) -> None:
    with pytest.raises(ValueError, match="공식 자료 날짜"):
        comparison_transport._source_date(raw)  # noqa: SLF001


def test_발행일이_없는_공식웹은_수집일을_확인일로_사용한다() -> None:
    assert comparison_transport._source_date(  # noqa: SLF001
        "",
        source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        source_url="https://example.com/news/completed",
        collected_on=_COLLECTED_ON,
    ) == _COLLECTED_ON


@pytest.mark.parametrize(
    "source_kind",
    (
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
        SOURCE_KIND_OFFICIAL_IR_PDF,
    ),
)
def test_DART와_IR은_빈_발행일을_수집일로_위장하지_않는다(
    source_kind: str,
) -> None:
    with pytest.raises(ValueError, match="공식 자료 날짜"):
        comparison_transport._source_date(  # noqa: SLF001
            "",
            source_kind=source_kind,
            collected_on=_COLLECTED_ON,
        )


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_8종이_생산_merge에서_비교_Source까지_provenance를_보존한다(
    source_kind: str,
) -> None:
    result = _formal_result(source_kind)
    # raw evidence_rows를 시험이 손으로 보충하지 않는다. 실제 typed→숫자
    # 생산 어댑터가 만든 값만 비교 transport에 전달한다.
    raw_fragments, added = merge_official_evidence_fragments({}, result)

    sources, sentences = comparison_transport.build_typed_comparison_candidate_inputs(
        raw_fragments,
        result=result,
        profile=_PROFILE,
        corp_code=_CORP_CODE,
        company_name=_COMPANY_NAME,
        collected_on=_COLLECTED_ON,
    )

    assert added == 1
    source = next(item for item in sources if item.formal_source_kind == source_kind)
    document = next(
        document
        for candidate in result.candidates
        for document in candidate.documents
    )
    assert source.url == document.canonical_url
    assert source.document_content_sha256 == document.content_sha256
    assert source.identity_binding == document.identity_binding
    assert source.domain_attestation_source_id == document.domain_attestation_source_id
    assert source.domain_attestation_evidence == document.domain_attestation_evidence
    assert source.reporting_period == document.reporting_period
    assert source.attachment_url == document.attachment_url
    assert source.ir_metadata_verification == document.ir_metadata_verification
    assert source.domain_redirect_verification == document.domain_redirect_verification
    assert source.domain_redirect_from_host == document.domain_redirect_from_host
    assert source.domain_redirect_to_host == document.domain_redirect_to_host
    if source_kind.startswith("dart_"):
        assert source.disclosed_at == document.published_on
        assert source.document_id == _RECEIPT_NO
    else:
        assert source.published_at == (document.published_on or _COLLECTED_ON)
        assert source.document_id == document.document_id
    assert sentences
