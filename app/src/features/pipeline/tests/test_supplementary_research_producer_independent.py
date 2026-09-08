"""실제 composer 생산물과 typed 공식 근거를 연결하는 독립 출고 시험.

손으로 만든 ``Report``를 양성 입력으로 쓰지 않는다. ``_expected_source``로
봉인한 typed Source를 ``render_report``에 전달하고, render가 만든
``FactRecord.state_evidence`` manifest를 새 supplementary guard가 다시 검증하는
경로를 고정한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from src.core import news_intake_switch
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.public_manifest import _expected_source
from src.features.composer.render import render_report
from src.features.pipeline.port import Report
from src.features.pipeline.supplementary_research_release import (
    assess_supplementary_research_release,
)
from src.features.provenance.supplementary_research_adapter import (
    verify_supplementary_research_source,
)
from src.features.provenance.sources import Source, has_valid_provenance_seal
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_NEWS,
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
from src.shared.report_quality.source_identity import document_identity_from_parts


_COMPANY = "가나다전자"
_COMPANY_ID = "00123456"
_AS_OF = "2026-09-08"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fragment(
    fragment_id: str,
    section_id: str,
    text: str,
    *,
    kind: str,
) -> CollectedFragment:
    if kind == SOURCE_KIND_DART_BUSINESS_REPORT:
        receipt = f"20260315{int(fragment_id):06d}"
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
        return CollectedFragment(
            fragment_id=fragment_id,
            kind="DART 사업보고서",
            text=text,
            source_url=url,
            document_title="2025 사업보고서",
            location="II. 사업의 내용",
            document_date="2026-03-15",
            document_identity=document_identity_from_parts(
                document_id=receipt,
                host="dart.fss.or.kr",
                url=url,
            ),
            document_content_sha256=_sha(text),
            supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id],
            formal_source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            source_document_id=f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}",
            source_publisher=_COMPANY,
            identity_binding=(
                f"corp_code={_COMPANY_ID};rcept_no={receipt};"
                "identity_check=verified"
            ),
            source_collected_on=_AS_OF,
        )
    url = f"https://news.example.com/articles/{fragment_id}"
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="뉴스",
        text=text,
        source_url=url,
        document_title="가나다전자 관련 보도",
        location="본문 1문단",
        document_date="2026-09-01",
        document_identity=document_identity_from_parts(
            document_id=f"news-{fragment_id}",
            host="news.example.com",
            url=url,
        ),
        document_content_sha256=_sha(text),
        supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id],
        formal_source_kind=SOURCE_KIND_NEWS,
        source_document_id=f"news-{fragment_id}",
        source_publisher="가나다전자 뉴스룸",
        source_collected_on=_AS_OF,
        news_grounded=True,
    )


def _sealed_source(fragment: CollectedFragment, *, section_id: str) -> Source:
    """composer의 기존 typed Source producer를 직접 실행한다."""

    return _expected_source(
        fragment,
        number=int(fragment.fragment_id),
        company_name=_COMPANY,
        used_in=(section_id,),
        filing_meta=None,
    )


def _candidate(source: Source, section_id: str, text: str) -> ChapterEvidenceCandidates:
    slots = collector_slots_for(section_id)
    document = CollectedEvidenceDocument(
        company_id=_COMPANY_ID,
        document_id=source.document_id,
        canonical_url=source.url,
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind=source.formal_source_kind,
        publisher=source.publisher,
        title=source.title,
        published_on=source.disclosed_at or source.published_at,
        collected_at=source.collected_at,
        content_sha256=source.document_content_sha256,
        exact_evidence_hashes=tuple(source.exact_evidence_hashes),
        identity_binding=source.identity_binding or "typed_fixture=verified",
        usable_ranges=(DocumentTextRange(0, len(text)),),
        collector_version="independent-fixture-v1",
        parser_version="independent-fixture-v1",
        requirement=SourceRequirement.REQUIRED,
    )
    fragment = EvidenceFragment(
        company_id=_COMPANY_ID,
        fragment_id=f"typed-{source.number}",
        document_id=source.document_id,
        location=source.location or "본문 1문단",
        text_sha256=_sha(text),
        text=text,
        section_id=section_id,
        slot_id=slots[0],
        covered_slot_ids=slots,
        score_millis=900,
        reason_codes=("independent_fixture_match",),
    )
    return ChapterEvidenceCandidates(
        company_id=_COMPANY_ID,
        section_id=section_id,
        documents=(document,),
        fragments=(fragment,),
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id=f"typed-ok-{section_id}",
                source_kind=source.formal_source_kind,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.OK,
                slot_ids=slots,
                reason_code="independent_fixture_ok",
            ),
        ),
        candidate_readiness=EvidenceReadiness.READY,
        reason_codes=(),
        estimated_tokens=10,
        max_chars=10_000,
        max_estimated_tokens=10_000,
    )


def _missing_candidate(section_id: str) -> ChapterEvidenceCandidates:
    slots = collector_slots_for(section_id)
    return ChapterEvidenceCandidates(
        company_id=_COMPANY_ID,
        section_id=section_id,
        documents=(),
        fragments=(),
        attempts=(
            CollectionAttempt(
                company_id=_COMPANY_ID,
                attempt_id=f"typed-missing-{section_id}",
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                requirement=SourceRequirement.REQUIRED,
                state=CollectionState.MISSING,
                slot_ids=slots,
                reason_code="independent_fixture_missing",
            ),
        ),
        candidate_readiness=EvidenceReadiness.INSUFFICIENT,
        reason_codes=(),
        estimated_tokens=0,
        max_chars=10_000,
        max_estimated_tokens=10_000,
    )


def _official_evidence(
    sources_by_section: dict[str, tuple[Source, str]],
) -> OfficialEvidenceCollectionResult:
    candidates = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        item = sources_by_section.get(section_id)
        candidates.append(
            _candidate(item[0], section_id, item[1])
            if item is not None
            else _missing_candidate(section_id)
        )
    return OfficialEvidenceCollectionResult(
        company_id=_COMPANY_ID,
        candidates=tuple(candidates),
    )


def _rendered_report(
    *,
    core_kind: str = SOURCE_KIND_DART_BUSINESS_REPORT,
    portfolio_kind: str = SOURCE_KIND_NEWS,
) -> tuple[Report, OfficialEvidenceCollectionResult]:
    specs = (
        ("1", "identity", core_kind, "가나다전자는 산업용 센서를 제조한다."),
        ("2", "business_model", core_kind, "가나다전자는 기업 고객에게 센서를 판매한다."),
        ("3", "portfolio", portfolio_kind, "가나다전자의 핵심 제품은 산업용 센서다."),
    )
    fragments = []
    sentences = []
    source_map: dict[str, tuple[Source, str]] = {}
    sections = []
    for fragment_id, section_id, kind, claim in specs:
        fragment = _fragment(fragment_id, section_id, claim, kind=kind)
        source = _sealed_source(fragment, section_id=section_id)
        assert has_valid_provenance_seal(source)
        fragments.append(replace(fragment, bound_source=source))
        sentences.append(
            ComposedSentence(
                text=claim,
                citations=(fragment_id,),
                grade="확인",
                planned_claim_slot=CLAIM_SLOTS_BY_SECTION[section_id][0],
                verification_state="verified",
            )
        )
        if kind == SOURCE_KIND_DART_BUSINESS_REPORT:
            source_map[section_id] = (source, claim)
        sections.append(ComposedSection(section_id, (sentences[-1],)))

    report = render_report(
        _COMPANY,
        ComposedReport(tuple(sections)),
        tuple(fragments),
        None,
        corp_type="주식회사",
        as_of_date=_AS_OF,
        company_id=_COMPANY_ID,
    )
    # render_report의 현재 계약: 번호는 본문 inline, legacy cite 필드는 빈 값.
    assert report.sections
    for section in report.sections:
        for display, cite in section.prose_lines:
            assert cite == ""
            assert "[" in display and "]" in display
    evidence = _official_evidence(source_map)
    return report, evidence


def _assess(report: Report, evidence: OfficialEvidenceCollectionResult):
    return assess_supplementary_research_release(
        report,
        official_evidence=evidence,
        source_verifier=verify_supplementary_research_source,
    )


@pytest.fixture(autouse=True)
def _news_intake_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def test_actual_composer_producer_allows_dart_core_and_non_core_news() -> None:
    report, evidence = _rendered_report()

    decision = _assess(report, evidence)

    assert decision.allowed is True
    assert decision.official_grounded_core_section_ids == (
        "identity",
        "business_model",
    )
    assert decision.dart_grounded_section_ids == ("identity", "business_model")
    assert decision.qualified_section_ids == ("identity", "business_model", "portfolio")
    assert all(fact.state_evidence.startswith("[") for fact in report.fact_records)
    assert all("document_identity" in fact.state_evidence for fact in report.fact_records)


def test_actual_composer_producer_allows_three_official_dart_pages_without_news() -> None:
    report, evidence = _rendered_report(portfolio_kind=SOURCE_KIND_DART_BUSINESS_REPORT)

    decision = _assess(report, evidence)

    assert decision.allowed is True
    assert decision.dart_grounded_section_ids == (
        "identity",
        "business_model",
        "portfolio",
    )


def test_tampering_official_dart_document_hash_drops_core_evidence() -> None:
    report, evidence = _rendered_report()
    first = evidence.candidates[0]
    changed_document = replace(first.documents[0], content_sha256="f" * 64)
    changed_candidate = replace(first, documents=(changed_document,))
    changed_evidence = replace(
        evidence,
        candidates=(changed_candidate, *evidence.candidates[1:]),
    )

    decision = _assess(report, changed_evidence)

    assert decision.allowed is False
    assert decision.code == "supplementary_release_insufficient_body_sections"
    assert decision.qualified_section_ids == ("business_model", "portfolio")
    assert "identity" not in decision.official_grounded_core_section_ids


def test_tampering_inline_citation_does_not_release_body_fact() -> None:
    report, evidence = _rendered_report()
    identity = next(section for section in report.sections if section.cell == "identity")
    display, _cite = identity.prose_lines[0]
    tampered_identity = replace(
        identity,
        prose_lines=[(display.replace("[1]", "[2]"), "")],
    )
    tampered_report = replace(
        report,
        sections=[
            tampered_identity
            if section.cell == "identity"
            else section
            for section in report.sections
        ],
    )

    decision = _assess(tampered_report, evidence)

    assert decision.allowed is False
    assert decision.code == "supplementary_release_insufficient_body_sections"
    assert decision.qualified_section_ids == ("business_model", "portfolio")
