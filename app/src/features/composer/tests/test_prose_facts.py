from __future__ import annotations

from dataclasses import replace

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.prose_facts import ProseEvidence, build_verified_prose_fact
from src.features.composer.render import render_report
from src.features.storage.reports import report_from_json, report_to_json
from src.features.provenance.sources import (
    Source,
    SourceKind,
    exact_evidence_text_hash,
)
from src.shared.report_quality.assessment import assess_safety
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.models import ReleaseDecision
from src.shared.report_quality.source_identity import document_identity


def _source(number: int, text: str) -> Source:
    return Source(
        number=number,
        kind=SourceKind.OTHER,
        label=f"공식 자료 {number}",
        source_id=f"source-{number}",
        title=f"공식 문서 {number}",
        publisher="예시회사",
        url=f"https://example.com/document/{number}",
        exact_evidence_hashes=[exact_evidence_text_hash(text)],
    )


def _sentence() -> ComposedSentence:
    return ComposedSentence(
        text="예시회사는 직접 판매 채널과 공식 제휴 채널을 함께 운영한다.",
        citations=("1", "2"),
        grade=GRADE_CONFIRMED,
        planned_claim_slot="business_model:sales_channel",
        verification_state="verified",
    )


def _evidence() -> tuple[ProseEvidence, ...]:
    texts = (
        "예시회사는 고객에게 제품을 직접 판매하는 공식 온라인 채널을 운영한다.",
        "공식 제휴사는 예시회사의 제품을 고객에게 판매하는 채널이다.",
    )
    return tuple(
        ProseEvidence(str(index), _source(index, text), text)
        for index, text in enumerate(texts, start=1)
    )


def test_검증된_일반문장은_모든_인용의_신원과_원문해시에_결속된다() -> None:
    fact = build_verified_prose_fact(
        _sentence(),
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=_evidence(),
    )

    assert fact is not None
    assert fact.supporting_source_ids == ["source-1", "source-2"]
    assert fact.source_id == fact.supporting_source_ids[0]
    assert fact.supporting_source_identities == [
        document_identity(item.source) for item in _evidence()
    ]
    assert fact.supporting_evidence_hashes == [
        exact_evidence_text_hash(item.exact_text) for item in _evidence()
    ]
    assert fact.evidence_binding == fact_evidence_binding(fact)


def test_인용순서나_원문바이트가_바뀌면_같은_사실로_가장할수없다() -> None:
    sentence = _sentence()
    evidence = _evidence()
    original = build_verified_prose_fact(
        sentence,
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=evidence,
    )
    reordered = build_verified_prose_fact(
        replace(sentence, citations=("2", "1")),
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=tuple(reversed(evidence)),
    )
    changed_text = replace(evidence[1], exact_text=evidence[1].exact_text + " 변경")

    assert original is not None and reordered is not None
    assert reordered.fact_id != original.fact_id
    assert (
        build_verified_prose_fact(
            sentence,
            section_id="business_model",
            company_name="예시회사",
            as_of_date="2026-08-31",
            evidence=(evidence[0], changed_text),
        )
        is None
    )


def test_미검증_또는_계획밖_문장은_사실장부를_만들지않는다() -> None:
    for sentence in (
        replace(_sentence(), verification_state="unverified"),
        replace(_sentence(), planned_claim_slot="business_model:없는자리"),
        replace(_sentence(), citations=()),
    ):
        assert (
            build_verified_prose_fact(
                sentence,
                section_id="business_model",
                company_name="예시회사",
                as_of_date="2026-08-31",
                evidence=_evidence(),
            )
            is None
        )


def test_품질안전검사는_두번째_출처까지_따로_대조한다() -> None:
    evidence = _evidence()
    fact = build_verified_prose_fact(
        _sentence(),
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=evidence,
    )
    assert fact is not None
    claim = ClaimFact(
        fact_id=fact.fact_id,
        section_owner=fact.section_owner,
        source_id=fact.source_id,
        source_identity=fact.supporting_source_identities[0],
        verification_state=fact.verification_status,
        claim_slot=fact.claim_slot,
        evidence_binding_valid=fact.evidence_binding == fact_evidence_binding(fact),
        claim=fact.claim,
        supporting_source_ids=tuple(fact.supporting_source_ids),
        supporting_source_identities=tuple(fact.supporting_source_identities),
        supporting_evidence_hashes=tuple(fact.supporting_evidence_hashes),
    )
    sources = tuple(
        SourceDocument(
            source_id=item.source.source_id,
            document_identity=document_identity(item.source),
            exact_evidence_hashes=tuple(item.source.exact_evidence_hashes),
        )
        for item in evidence
    )
    candidate = ReportCandidate(
        sections=(
            ReportSectionCandidate(
                "business_model", (fact.fact_id,), public_sentence_count=1
            ),
        ),
        facts=(claim,),
        sources=sources,
    )

    assert (
        assess_safety(candidate, contract_for_generation()).decision
        is ReleaseDecision.RELEASE_ALLOWED
    )
    tampered = replace(
        candidate,
        sources=(
            sources[0],
            replace(sources[1], exact_evidence_hashes=("0" * 64,)),
        ),
    )
    result = assess_safety(tampered, contract_for_generation())
    assert result.decision is ReleaseDecision.BLOCKED
    assert any("원문 조각 해시" in problem for problem in result.problems)


def test_render가_검증문장과_실제_부록출처를_같은_장부로_만든다() -> None:
    sentence = ComposedSentence(
        text="예시회사는 공식 온라인 판매 채널을 운영한다.",
        citations=("1",),
        grade=GRADE_CONFIRMED,
        planned_claim_slot="business_model:sales_channel",
        verification_state="verified",
    )
    report = render_report(
        "예시회사",
        ComposedReport(
            sections=(ComposedSection("business_model", (sentence,)),),
            summary=(sentence,),
        ),
        {
            1: {
                "종류": "공식 홈페이지",
                "원문": "예시회사는 공식 온라인 판매 채널을 운영한다.",
                "출처": "https://example.com/business",
                "문서명": "사업 소개",
            }
        },
        None,
        as_of_date="2026-08-31",
    )

    assert len(report.fact_records) == 1
    assert report.sections[0].fact_ids == [report.fact_records[0].fact_id]
    assert report.fact_records[0].supporting_source_ids == ["v2-frag-1"]
    assert report.citations[0].exact_evidence_hashes
    restored = report_from_json(report_to_json(report))
    assert restored.fact_records == report.fact_records
    assert restored.citations == report.citations
