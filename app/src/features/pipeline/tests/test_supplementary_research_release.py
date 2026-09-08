"""보완조사 전용 최종 출고 guard의 독립 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SummaryItem,
)
from src.features.pipeline.supplementary_research_release import (
    assess_supplementary_research_release,
)
from src.features.pipeline.supplementary_research_release_constants import (
    SUPPLEMENTARY_RELEASE_ALLOWED,
    SUPPLEMENTARY_RELEASE_BUSINESS_MODEL_WITHOUT_OFFICIAL_BODY,
    SUPPLEMENTARY_RELEASE_IDENTITY_WITHOUT_OFFICIAL_BODY,
    SUPPLEMENTARY_RELEASE_INSUFFICIENT_BODY_SECTIONS,
    SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY,
    SUPPLEMENTARY_RELEASE_WITHOUT_DART_BODY,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_verification import SourceVerification
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import collected_document_identity


_COMPANY = "검증회사"
_COMPANY_ID = "00126380"
_DART = "dart"
_WEB = "web"
_NEWS = "news"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Citation:
    verification: SourceVerification
    exact_evidence_hashes: tuple[str, ...]
    source_id: str
    source_type: str
    title: str
    label: str
    publisher: str
    host: str
    url: str
    document_id: str
    location: str
    published_at: str
    disclosed_at: str = ""
    collected_at: str = ""


class _Verifier:
    def __call__(self, source, _registry, *, reference_date, evidence_text):
        assert reference_date == "2026-09-08"
        if type(source) is not _Citation:
            return None
        return replace(
            source.verification,
            evidence_bound=bool(
                evidence_text and _sha(evidence_text) in source.exact_evidence_hashes
            ),
        )


def _document_fields(number: int, kind: str) -> tuple[str, str, str, str]:
    if kind == _DART:
        receipt = f"20260330{number:06d}"
        return (
            SOURCE_KIND_DART_BUSINESS_REPORT,
            f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}",
            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            "corp_code=00126380",
        )
    if kind == _WEB:
        return (
            SOURCE_KIND_OFFICIAL_WEB_PAGE,
            f"official-web-{number}",
            f"https://example.com/company/{number}",
            "dart_profile_homepage",
        )
    raise ValueError("공식 문서 종류가 아닙니다")


def _official_evidence(
    specs: tuple[tuple[str, int, str, str], ...]
) -> OfficialEvidenceCollectionResult:
    attestation_id = f"dart-company-profile-{_COMPANY_ID}"
    attestation_evidence = json.dumps(
        {
            "corp_code": _COMPANY_ID,
            "corp_name": _COMPANY,
            "hm_url": "https://example.com",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    by_section = {
        section_id: (number, kind, claim)
        for section_id, number, kind, claim in specs
        if kind != _NEWS
    }
    candidates: list[ChapterEvidenceCandidates] = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        slots = collector_slots_for(section_id)
        spec = by_section.get(section_id)
        if spec is None:
            candidates.append(
                ChapterEvidenceCandidates(
                    company_id=_COMPANY_ID,
                    section_id=section_id,
                    documents=(),
                    fragments=(),
                    attempts=(
                        CollectionAttempt(
                            company_id=_COMPANY_ID,
                            attempt_id=f"missing-{section_id}",
                            source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                            requirement=SourceRequirement.REQUIRED,
                            state=CollectionState.MISSING,
                            slot_ids=slots,
                            reason_code="fixture_missing",
                        ),
                    ),
                    candidate_readiness=EvidenceReadiness.INSUFFICIENT,
                    reason_codes=(),
                    estimated_tokens=0,
                    max_chars=10_000,
                    max_estimated_tokens=10_000,
                )
            )
            continue
        number, kind, claim = spec
        source_kind, document_id, url, identity_binding = _document_fields(number, kind)
        document = CollectedEvidenceDocument(
            company_id=_COMPANY_ID,
            document_id=document_id,
            canonical_url=url,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            source_kind=source_kind,
            publisher=_COMPANY,
            title=f"{section_id} 공식 원문",
            published_on="2026-09-01",
            collected_at="2026-09-08",
            content_sha256=_sha(f"document-{number}"),
            exact_evidence_hashes=(_sha(claim),),
            identity_binding=identity_binding,
            usable_ranges=(DocumentTextRange(0, len(claim)),),
            collector_version="fixture-v1",
            parser_version="fixture-v1",
            requirement=SourceRequirement.REQUIRED,
            domain_attestation_source_id=attestation_id if kind == _WEB else "",
            domain_attestation_evidence=attestation_evidence if kind == _WEB else "",
        )
        fragment = EvidenceFragment(
            company_id=_COMPANY_ID,
            fragment_id=f"fragment-{number}",
            document_id=document_id,
            location="본문 1문단",
            text_sha256=_sha(claim),
            text=claim,
            section_id=section_id,
            slot_id=slots[0],
            covered_slot_ids=slots,
            score_millis=900,
            reason_codes=("fixture_match",),
        )
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=_COMPANY_ID,
                section_id=section_id,
                documents=(document,),
                fragments=(fragment,),
                attempts=(
                    CollectionAttempt(
                        company_id=_COMPANY_ID,
                        attempt_id=f"ok-{section_id}",
                        source_kind=source_kind,
                        requirement=SourceRequirement.REQUIRED,
                        state=CollectionState.OK,
                        slot_ids=slots,
                        reason_code="fixture_ok",
                    ),
                ),
                candidate_readiness=EvidenceReadiness.READY,
                reason_codes=(),
                estimated_tokens=10,
                max_chars=10_000,
                max_estimated_tokens=10_000,
            )
        )
    return OfficialEvidenceCollectionResult(
        company_id=_COMPANY_ID,
        candidates=tuple(candidates),
    )


def _citation(number: int, kind: str, claim: str) -> _Citation:
    if kind == _NEWS:
        url = f"https://news.example.com/articles/{number}"
        verification = SourceVerification(
            source_id=f"source-{number}",
            number=number,
            document_identity=f"url:{url}",
            content_sha256=_sha(f"news-document-{number}"),
            formal_kind="",
            exact_evidence_hashes=(_sha(claim),),
            official=False,
            news=True,
            evidence_bound=False,
        )
        source_type = "news"
        document_id = f"news-{number}"
        host = "news.example.com"
    else:
        source_kind, document_id, url, _binding = _document_fields(number, kind)
        verification = SourceVerification(
            source_id=f"source-{number}",
            number=number,
            document_identity=collected_document_identity(
                source_kind=source_kind,
                document_id=document_id,
                url=url,
            ),
            content_sha256=_sha(f"document-{number}"),
            formal_kind=source_kind,
            exact_evidence_hashes=(_sha(claim),),
            official=True,
            news=False,
            evidence_bound=False,
        )
        source_type = "official"
        host = "dart.fss.or.kr" if kind == _DART else "example.com"
    return _Citation(
        verification=verification,
        exact_evidence_hashes=(_sha(claim),),
        source_id=verification.source_id,
        source_type=source_type,
        title=f"검증 문서 {number}",
        label=f"검증 자료 {number}",
        publisher=_COMPANY,
        host=host,
        url=url,
        document_id=document_id,
        location="본문 1문단",
        published_at="2026-09-01",
    )


def _fact(number: int, section_id: str, claim: str, citation: _Citation) -> FactRecord:
    source = citation.verification
    fact = FactRecord(
        fact_id=f"fact-{number}",
        legal_entity=_COMPANY,
        claim=claim,
        claim_slot=CLAIM_SLOTS_BY_SECTION[section_id][0],
        section_owner=section_id,
        source_id=source.source_id,
        source_type=citation.source_type,
        source_title=citation.title,
        source_publisher=citation.publisher,
        source_host=citation.host,
        source_url=citation.url,
        source_document_id=citation.document_id,
        location=citation.location,
        source_date=citation.published_at,
        status="verified",
        verification_status="verified",
        state_evidence=claim,
        supporting_source_ids=[source.source_id],
        supporting_source_identities=[source.document_identity],
        supporting_evidence_hashes=[_sha(claim)],
    )
    return replace(fact, evidence_binding=fact_evidence_binding(fact))


def _report(
    section_sources: tuple[tuple[str, str], ...] = (
        ("identity", _DART),
        ("business_model", _DART),
        ("portfolio", _NEWS),
    ),
    *,
    table_section: str = "",
) -> tuple[Report, OfficialEvidenceCollectionResult]:
    citations: list[_Citation] = []
    facts: list[FactRecord] = []
    sections: list[ReportSection] = []
    official_specs: list[tuple[str, int, str, str]] = []
    for number, (section_id, kind) in enumerate(section_sources, start=1):
        claim = f"{section_id}에서 확인한 서로 다른 본문 사실 {number}입니다."
        citation = _citation(number, kind, claim)
        fact = _fact(number, section_id, claim, citation)
        if kind != _NEWS:
            official_specs.append((section_id, number, kind, claim))
        if section_id == table_section:
            section = ReportSection(
                cell=section_id,
                title=section_id,
                tables=[
                    ReportTable(
                        caption="확인된 본문 표",
                        headers=["항목", "내용"],
                        rows=[[section_id, claim]],
                        cite=f"[{number}]",
                        source_cites=[f"[{number}]"],
                        row_cites=[[f"[{number}]"]],
                    )
                ],
                fact_ids=[fact.fact_id],
            )
        else:
            section = ReportSection(
                cell=section_id,
                title=section_id,
                prose_lines=[(claim, f"[{number}]")],
                fact_ids=[fact.fact_id],
            )
        citations.append(citation)
        facts.append(fact)
        sections.append(section)
    report = Report(
        company=_COMPANY,
        company_id=_COMPANY_ID,
        job="개발",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=sections,
        citations=list(citations),
        fact_records=facts,
        summary_items=[SummaryItem(text="본문을 가리키는 요약")],
        as_of_date="2026-09-08",
        safety_decision="release_blocked_shadow_observation",
    )
    return report, _official_evidence(tuple(official_specs))


def _assess(report: Report, evidence: OfficialEvidenceCollectionResult):
    return assess_supplementary_research_release(
        report,
        official_evidence=evidence,
        source_verifier=_Verifier(),
    )


def test_shadow_observation_does_not_override_grounded_partial_release():
    report, evidence = _report()

    decision = _assess(report, evidence)

    assert decision.allowed is True
    assert decision.code == SUPPLEMENTARY_RELEASE_ALLOWED
    assert decision.qualified_section_ids == (
        "identity",
        "business_model",
        "portfolio",
    )
    assert decision.official_grounded_core_section_ids == (
        "identity",
        "business_model",
    )
    assert decision.dart_grounded_section_ids == ("identity", "business_model")


def test_inline_markers_with_matching_multi_source_facts_count_as_body():
    report, evidence = _report()
    identity_fact = next(
        fact for fact in report.fact_records if fact.section_owner == "identity"
    )
    extra = _citation(9, _NEWS, identity_fact.claim)
    changed_fact = replace(
        identity_fact,
        supporting_source_ids=[*identity_fact.supporting_source_ids, extra.source_id],
        supporting_source_identities=[
            *identity_fact.supporting_source_identities,
            extra.verification.document_identity,
        ],
        supporting_evidence_hashes=[
            *identity_fact.supporting_evidence_hashes,
            _sha(identity_fact.claim),
        ],
        evidence_binding="",
    )
    changed_fact = replace(
        changed_fact,
        evidence_binding=fact_evidence_binding(changed_fact),
    )
    changed_sections = [
        replace(
            section,
            prose_lines=[(f"{identity_fact.claim} [1][9]", "")],
        )
        if section.cell == "identity"
        else section
        for section in report.sections
    ]
    changed_facts = [
        changed_fact if fact.fact_id == identity_fact.fact_id else fact
        for fact in report.fact_records
    ]

    decision = _assess(
        replace(
            report,
            sections=changed_sections,
            citations=[*report.citations, extra],
            fact_records=changed_facts,
        ),
        evidence,
    )

    assert decision.allowed is True
    assert decision.qualified_section_ids == (
        "identity",
        "business_model",
        "portfolio",
    )


def test_official_web_can_ground_core_but_dart_body_is_still_required():
    report, evidence = _report(
        (("identity", _WEB), ("business_model", _WEB), ("portfolio", _DART))
    )
    no_dart_report, no_dart_evidence = _report(
        (("identity", _WEB), ("business_model", _WEB), ("portfolio", _NEWS))
    )

    allowed = _assess(report, evidence)
    blocked = _assess(no_dart_report, no_dart_evidence)

    assert allowed.allowed is True
    assert allowed.dart_grounded_section_ids == ("portfolio",)
    assert blocked.code == SUPPLEMENTARY_RELEASE_WITHOUT_DART_BODY


def test_news_can_ground_third_section_but_not_official_core():
    identity_report, identity_evidence = _report(
        (("identity", _NEWS), ("business_model", _DART), ("portfolio", _NEWS))
    )
    business_report, business_evidence = _report(
        (("identity", _DART), ("business_model", _NEWS), ("portfolio", _NEWS))
    )

    assert _assess(identity_report, identity_evidence).code == (
        SUPPLEMENTARY_RELEASE_IDENTITY_WITHOUT_OFFICIAL_BODY
    )
    assert _assess(business_report, business_evidence).code == (
        SUPPLEMENTARY_RELEASE_BUSINESS_MODEL_WITHOUT_OFFICIAL_BODY
    )


def test_summary_notices_citations_and_empty_tables_do_not_inflate_body_count():
    report, evidence = _report(
        (("identity", _DART), ("business_model", _DART))
    )
    noise_sections = [
        ReportSection(
            cell="portfolio",
            title="안내만 있는 장",
            guidance_lines=["추가 자료를 확인하세요."],
        ),
        ReportSection(
            cell="past_changes",
            title="빈 표만 있는 장",
            tables=[ReportTable(caption="빈 표", headers=["항목"], rows=[[""]])],
        ),
    ]

    decision = _assess(
        replace(report, sections=[*report.sections, *noise_sections]), evidence
    )

    assert decision.code == SUPPLEMENTARY_RELEASE_INSUFFICIENT_BODY_SECTIONS
    assert decision.qualified_section_ids == ("identity", "business_model")


def test_valid_cited_nonempty_official_table_counts_as_body():
    report, evidence = _report(
        (("identity", _DART), ("business_model", _DART), ("portfolio", _DART)),
        table_section="portfolio",
    )

    decision = _assess(report, evidence)

    assert decision.allowed is True
    assert "portfolio" in decision.qualified_section_ids


def test_news_listing_with_hidden_fact_does_not_count_as_third_body_section():
    report, evidence = _report(table_section="portfolio")
    portfolio = next(
        section for section in report.sections if section.cell == "portfolio"
    )
    assert any(
        citation.verification.news
        for citation in report.citations
        if citation.source_id
        == next(
            fact.source_id
            for fact in report.fact_records
            if fact.section_owner == "portfolio"
        )
    )

    decision = _assess(
        replace(
            report,
            sections=[
                replace(section, prose_lines=[])
                if section.cell == "portfolio"
                else section
                for section in report.sections
            ],
        ),
        evidence,
    )

    assert portfolio.tables
    assert decision.code == SUPPLEMENTARY_RELEASE_INSUFFICIENT_BODY_SECTIONS
    assert decision.qualified_section_ids == ("identity", "business_model")


def test_hidden_dart_fact_does_not_promote_news_body_to_official_identity():
    report, _old_evidence = _report(
        (("identity", _NEWS), ("business_model", _DART), ("portfolio", _NEWS))
    )
    hidden_claim = "공개 본문에는 나오지 않는 DART 정체성 사실입니다."
    hidden_citation = _citation(9, _DART, hidden_claim)
    hidden_fact = _fact(9, "identity", hidden_claim, hidden_citation)
    business_claim = next(
        fact.claim for fact in report.fact_records if fact.section_owner == "business_model"
    )
    evidence = _official_evidence(
        (
            ("identity", 9, _DART, hidden_claim),
            ("business_model", 2, _DART, business_claim),
        )
    )
    sections = [
        replace(section, fact_ids=[*section.fact_ids, hidden_fact.fact_id])
        if section.cell == "identity"
        else section
        for section in report.sections
    ]

    decision = _assess(
        replace(
            report,
            sections=sections,
            citations=[*report.citations, hidden_citation],
            fact_records=[*report.fact_records, hidden_fact],
        ),
        evidence,
    )

    assert decision.code == SUPPLEMENTARY_RELEASE_IDENTITY_WITHOUT_OFFICIAL_BODY


def test_duplicate_citation_numbers_fail_closed():
    report, evidence = _report()
    original = report.citations[-1]
    duplicate_verification = replace(
        original.verification,
        source_id="source-duplicate",
        number=1,
    )
    duplicate = replace(original, verification=duplicate_verification)

    decision = _assess(
        replace(report, citations=[*report.citations, duplicate]), evidence
    )

    assert decision.code == SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY


def test_source_content_hash_mismatch_with_collection_does_not_ground_body():
    report, evidence = _report()
    original = report.citations[0]
    changed = replace(
        original,
        verification=replace(
            original.verification,
            content_sha256="f" * 64,
        ),
    )

    decision = _assess(
        replace(report, citations=[changed, *report.citations[1:]]), evidence
    )

    assert decision.code == SUPPLEMENTARY_RELEASE_INSUFFICIENT_BODY_SECTIONS
    assert decision.qualified_section_ids == ("business_model", "portfolio")


def test_report_company_mismatch_with_collection_fails_closed():
    report, evidence = _report()

    decision = _assess(replace(report, company_id="00999999"), evidence)

    assert decision.allowed is False
    assert decision.code == "supplementary_release_invalid_report_dto"
