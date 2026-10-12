"""직접 산업 조사 변환도 이미 결속된 원문 예시 문맥을 그대로 운반한다."""

import hashlib

import pytest

from src.features.composer.industry_context import discovery_fragments_for_supplement
from src.shared.business_challenge_context import IndustryProblemEvidence
from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence, OfficialIndustrySupplement
from src.shared.report_evidence.models import CollectedEvidenceDocument, DocumentTextRange
from src.shared.report_evidence.practice_context import build_practice_context


@pytest.mark.parametrize("practice", [True, False])
def test_산업_조사_변환은_원문_문맥과_위치_지문을_보존한다(practice):
    marker = "예를 들어 가상의 회사 업무를 수행했다고 가정합니다."
    quote = "2025년 국내 산업용 센서 수요가 위축되었다. 당사는 감지모듈을 생산한다."
    ranges = (marker, quote) if practice else (quote,)
    full = "\n".join(ranges)
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    start = len(marker)+1 if practice else 0
    location = f"{start}-{len(full)}"
    doc = CollectedEvidenceDocument(
        company_id="00000017", document_id="dart:20260331000017",
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260331000017",
        source_tier=SourceTier.TIER_1_OFFICIAL, source_kind="dart_business_report",
        publisher="금융감독원", title="2025년 사업보고서", published_on="2026-03-31",
        collected_at="2026-10-10", content_sha256=sha(full), exact_evidence_hashes=(sha(quote),),
        identity_binding="corp_code=00000017;rcept_no=20260331000017;source_kind=dart_business_report;identity_check=verified_match",
        usable_ranges=(DocumentTextRange(start, len(full)),),
        collector_version="독립검사", parser_version="독립검사", requirement=SourceRequirement.REQUIRED,
    )
    context = build_practice_context(
        ranges=ranges, document_id=doc.document_id, document_sha256=doc.content_sha256,
        fragment_index=len(ranges)-1, fragment_location=location,
    ) if practice else ""
    if practice:
        assert context
    candidate = OfficialIndustryCandidateEvidence(
        company_id=doc.company_id, fragment_id="independent-practice", document=doc,
        location=location, text_sha256=sha(quote), text=quote, practice_context_json=context,
    )
    problem = IndustryProblemEvidence(
        evidence_id="independent-problem", business_anchor_id="independent-anchor",
        document_id=doc.document_id, source_url=doc.canonical_url, publisher=doc.publisher,
        title=doc.title, published_on=doc.published_on, location=location,
        exact_text=quote, text_sha256=sha(quote), industry="산업용 센서", problem="수요가 위축",
        geography="unspecified", geography_detail="", geography_evidence="",
        document_content_sha256=doc.content_sha256, analysis_response_sha256="c"*64,
        applicability_quote="감지모듈", source_kind=doc.source_kind,
        identity_binding=doc.identity_binding, assessment_quote=quote, observation_period="2025년",
    )
    supplement = OfficialIndustrySupplement(problems=(problem,), candidates=(candidate,))
    extra = discovery_fragments_for_supplement(supplement, fragments=(), company_id=doc.company_id)
    assert len(extra) == 1
    actual = extra[0]
    assert actual.practice_context_json == context
    assert actual.text == quote
    assert sha(actual.text) == candidate.text_sha256
    assert actual.document_content_sha256 == doc.content_sha256
    assert actual.source_document_id == doc.document_id
    assert actual.location == location
    assert actual.supported_claim_slots == ()
