from __future__ import annotations

from dataclasses import replace

from src.features.report_quality.assessment import (
    assess_generation,
    has_public_numeric_token,
)
from src.features.report_quality.constants import REQUIRED_QUALITY_SECTION_IDS
from src.features.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.features.report_quality.models import (
    QualityGrade,
    ReleaseDecision,
    VerificationState,
)


def _candidate(
    counts: dict[str, int] | None = None,
    *,
    one_document: bool = False,
) -> ReportCandidate:
    requested = counts or {section_id: 5 for section_id in REQUIRED_QUALITY_SECTION_IDS}
    sources: list[SourceDocument] = []
    facts: list[ClaimFact] = []
    sections: list[ReportSectionCandidate] = []
    for source_number, section_id in enumerate(REQUIRED_QUALITY_SECTION_IDS, start=1):
        source_id = f"source-{source_number}"
        identity = (
            "document:dart.fss.or.kr:same-document"
            if one_document
            else f"document:dart.fss.or.kr:document-{source_number}"
        )
        sources.append(SourceDocument(source_id, identity))
        section_fact_ids: list[str] = []
        for item_number in range(requested[section_id]):
            fact_id = f"{section_id}-fact-{item_number}"
            section_fact_ids.append(fact_id)
            facts.append(
                ClaimFact(
                    fact_id=fact_id,
                    section_owner=section_id,
                    source_id=source_id,
                    source_identity=identity,
                    verification_state=VerificationState.VERIFIED.value,
                    claim_slot=f"{section_id}:{item_number}",
                    evidence_binding_valid=True,
                    claim="공식 원문과 결속된 회사 고유 사실이다.",
                    subject_scope="연결",
                )
            )
        sections.append(
            ReportSectionCandidate(section_id, tuple(section_fact_ids))
        )
    return ReportCandidate(tuple(sections), tuple(facts), tuple(sources))


def test_40개_claim_8개_독립문서와_장별coverage면_complete다() -> None:
    result = assess_generation(_candidate())

    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.quality.substantive_claims == 40
    assert result.quality.document_sources == 8
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.publication_grade is QualityGrade.COMPLETE


def test_전체40개여도_한문장인_장이_있으면_complete가_아니다() -> None:
    counts = {section_id: 5 for section_id in REQUIRED_QUALITY_SECTION_IDS}
    counts["identity"] = 1
    counts["business_model"] = 9

    result = assess_generation(_candidate(counts))

    assert result.quality.substantive_claims == 40
    assert result.quality.one_claim_sections == ("identity",)
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED
    assert result.publication_grade is QualityGrade.PARTIAL


def test_조각8개가_같은문서면_출처는_한건으로_센다() -> None:
    result = assess_generation(_candidate(one_document=True))

    assert result.quality.document_sources == 1
    assert result.quality.grade is QualityGrade.PARTIAL
    assert result.safety.decision is ReleaseDecision.RELEASE_ALLOWED


def test_품질이_충분해도_미검증_claim은_공개안전을_통과하지_못한다() -> None:
    candidate = _candidate()
    first = candidate.facts[0]
    broken = replace(
        candidate,
        facts=(
            replace(first, verification_state=VerificationState.UNVERIFIED.value),
            *candidate.facts[1:],
        ),
    )

    result = assess_generation(broken)

    assert result.quality.grade is QualityGrade.COMPLETE
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert result.release_allowed is False


def test_평가전_보고서를_complete로_기본간주하지_않는다() -> None:
    result = assess_generation(ReportCandidate((), (), ()))

    assert result.quality.grade is QualityGrade.INCOMPLETE
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE


def test_숫자날짜퍼센트와_영문에_붙은_숫자도_결속대상이다() -> None:
    assert has_public_numeric_token("누적 증감률은 24.28%입니다.")
    assert has_public_numeric_token("2025Q1 실적입니다.")
    assert has_public_numeric_token("H100 제품입니다.")
    assert has_public_numeric_token("매출이 두 배로 늘었습니다.")
    assert has_public_numeric_token("연평균 이십오 퍼센트 이상입니다.")
    assert has_public_numeric_token("이익이 절반으로 줄었습니다.")
    assert not has_public_numeric_token("공식 자료에서 성장 흐름이 확인됩니다.")
    assert not has_public_numeric_token("이 회사의 사업 구조를 설명합니다.")


def test_같은_claim_slot을_문장수로_부풀리면_안전검사를_통과하지_못한다() -> None:
    candidate = _candidate()
    first, second = candidate.facts[:2]
    padded = replace(
        candidate,
        facts=(
            first,
            replace(second, claim_slot=first.claim_slot),
            *candidate.facts[2:],
        ),
    )

    result = assess_generation(padded)

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("claim slot" in problem for problem in result.safety.problems)


def test_원문결속지문이_유효하지_않으면_공개하지_않는다() -> None:
    candidate = _candidate()
    first = candidate.facts[0]
    broken = replace(
        candidate,
        facts=(replace(first, evidence_binding_valid=False), *candidate.facts[1:]),
    )

    result = assess_generation(broken)

    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("결속 지문" in problem for problem in result.safety.problems)
