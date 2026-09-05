from __future__ import annotations

import hashlib

from src.features.company_comparison.stated_differentiator import (
    STATED_DIFFERENTIATOR_SLOT,
    add_stated_differentiator_fragments,
    stated_differentiator_sentence_is_eligible,
)
from src.features.company_comparison.logic import (
    OfficialCompanyBundle,
    build_competitive_position,
)
from src.features.company_comparison.official_sources import OfficialCandidateSentence
from src.features.pipeline.port import Grade, Report
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    seal_collected_source,
)
from src.features.report_standard.publish import _fact_problems
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.logic import build_section_bundle
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


COMPANY_ID = "00000001"
COMPANY_NAME = "가나다전자"
SELF_SLOT = "competitive_position:self_context"
TEXT = "당사는 세계 최초로 초정밀 센서를 독자 개발했습니다."


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _result(*, text: str = TEXT) -> OfficialEvidenceCollectionResult:
    document = CollectedEvidenceDocument(
        company_id=COMPANY_ID,
        document_id="dart:202603310001",
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603310001",
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind="dart_business_report",
        publisher="금융감독원 전자공시시스템(DART)",
        title="사업보고서",
        published_on="2026-03-31",
        collected_at="2026-09-06",
        content_sha256=_sha("document:" + text),
        exact_evidence_hashes=(_sha(text),),
        identity_binding="corp_code_and_receipt_verified",
        usable_ranges=(DocumentTextRange(0, len(text)),),
        collector_version="typed-dart-v1",
        parser_version="typed-dart-parser-v1",
        requirement=SourceRequirement.REQUIRED,
    )
    fragment = EvidenceFragment(
        company_id=COMPANY_ID,
        fragment_id="competitive-self-context",
        document_id=document.document_id,
        location="사업의 내용 1문단",
        text_sha256=_sha(text),
        text=text,
        section_id="competitive_position",
        slot_id=SELF_SLOT,
        covered_slot_ids=(SELF_SLOT,),
        score_millis=900,
        reason_codes=("official_direct_statement",),
    )
    attempt = CollectionAttempt(
        company_id=COMPANY_ID,
        attempt_id="dart-competitive-position",
        source_kind="dart_business_report",
        requirement=SourceRequirement.REQUIRED,
        state=CollectionState.OK,
        slot_ids=(SELF_SLOT, STATED_DIFFERENTIATOR_SLOT),
        reason_code="dart_document_ok",
        documents_seen=1,
    )
    candidates = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        target = section_id == "competitive_position"
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=COMPANY_ID,
                section_id=section_id,
                documents=(document,) if target else (),
                fragments=(fragment,) if target else (),
                attempts=(attempt,) if target else (),
                candidate_readiness=(
                    EvidenceReadiness.INSUFFICIENT
                    if target
                    else EvidenceReadiness.UNKNOWN
                ),
                reason_codes=("missing_required_slot",) if target else (),
                estimated_tokens=30 if target else 0,
                max_chars=10_000,
                max_estimated_tokens=2_500,
            )
        )
    return OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))


def test_company_subject_and_closed_marker_are_required() -> None:
    assert stated_differentiator_sentence_is_eligible(
        TEXT,
        company_name=COMPANY_NAME,
        publisher="금융감독원 전자공시시스템(DART)",
    )
    assert not stated_differentiator_sentence_is_eligible(
        "업계는 세계 최초 기술로 평가합니다.",
        company_name=COMPANY_NAME,
        publisher="금융감독원 전자공시시스템(DART)",
    )
    assert not stated_differentiator_sentence_is_eligible(
        "당사는 센서를 개발했습니다.",
        company_name=COMPANY_NAME,
        publisher="금융감독원 전자공시시스템(DART)",
    )


def test_deterministic_projection_adds_typed_fragment_and_makes_chapter_ready() -> None:
    projected = add_stated_differentiator_fragments(
        _result(),
        company_name=COMPANY_NAME,
    )
    candidate = projected.candidates[-1]
    stated = [
        fragment
        for fragment in candidate.fragments
        if fragment.slot_id == STATED_DIFFERENTIATOR_SLOT
    ]
    assert len(stated) == 1
    assert stated[0].text == TEXT
    assert stated[0].text_sha256 == _sha(TEXT)
    assert stated[0].document_id == "dart:202603310001"
    bundle = build_section_bundle(
        candidate,
        required_slot_ids=(SELF_SLOT, STATED_DIFFERENTIATOR_SLOT),
    )
    assert bundle.readiness is EvidenceReadiness.READY


def test_absent_declaration_is_insufficient_without_optional_comparison() -> None:
    projected = add_stated_differentiator_fragments(
        _result(text="당사는 초정밀 센서를 개발했습니다."),
        company_name=COMPANY_NAME,
    )
    candidate = projected.candidates[-1]
    bundle = build_section_bundle(
        candidate,
        required_slot_ids=(SELF_SLOT, STATED_DIFFERENTIATOR_SLOT),
    )
    assert bundle.readiness is EvidenceReadiness.INSUFFICIENT
    assert STATED_DIFFERENTIATOR_SLOT in bundle.missing_slot_ids


def test_stated_differentiator_survives_when_same_condition_comparison_is_blocked() -> None:
    source = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="가나다전자 사업보고서",
            disclosed_at="2026-03-31",
            collected_at="2026-09-06",
            source_id="source-stated-differentiator",
            title="가나다전자 사업보고서",
            publisher=COMPANY_NAME,
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603310001",
            document_id="202603310001",
            location="사업의 내용 1문단",
            source_type="공식 공시",
            fact_status="공시 실제값",
            used_in=["competitive_position"],
            evidence_hashes=[evidence_text_hash(TEXT)],
            exact_evidence_hashes=[exact_evidence_text_hash(TEXT)],
        )
    )
    report = Report(
        company=COMPANY_NAME,
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
        citations=[source],
    )
    result = build_competitive_position(
        report,
        self_bundle=OfficialCompanyBundle(
            corp_code=COMPANY_ID,
            company_name=COMPANY_NAME,
            financials=None,
            filing=None,
            official_text="",
        ),
        catalog=(),
        fetch_comparator=lambda _record: None,
        collected_on="2026-09-06",
        official_candidate_sentences=(OfficialCandidateSentence(source, TEXT),),
        candidate_source_registry=(source,),
    )
    assert {fact.claim_slot for fact in result.facts} == {
        STATED_DIFFERENTIATOR_SLOT
    }
    fact = result.facts[0]
    registry = {item.source_id: item for item in result.sources}
    assert not any("[comparison]" in item for item in _fact_problems(fact, registry))
