from __future__ import annotations

from dataclasses import replace

from src.features.composer.extractive_summary import select_extractive_summary
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    StructuredClaim,
)
from src.features.pipeline.port import FactRecord
from src.shared.report_quality.fact_binding import fact_evidence_binding


_SLOTS = {
    "identity": "identity:business_definition",
    "business_model": "business_model:revenue_model",
    "portfolio": "portfolio:product_role",
    "current_challenges": "current_challenges:issue",
    "future_strategy": "future_strategy:stated_plan",
    "competitive_position": "competitive_position:self_context",
}


def _sentence(section_id: str, suffix: str = "첫째") -> ComposedSentence:
    return ComposedSentence(
        text=f"{section_id}의 검증된 {suffix} 사실입니다.",
        citations=(str(len(section_id)),),
        grade="확인",
        planned_claim_slot=_SLOTS[section_id],
        verification_state="verified",
    )


def _fact(section_id: str, sentence: ComposedSentence) -> FactRecord:
    fact = FactRecord(
        fact_id=f"fact-{section_id}-{sentence.text[-7:]}",
        legal_entity="가나다 주식회사",
        subject_scope="가나다 주식회사",
        relationship_or_action=sentence.planned_claim_slot.split(":", 1)[1],
        claim=sentence.text,
        claim_type="verified_prose",
        section_owner=section_id,
        time_state="present",
        as_of="2026-08-31",
        source_id=f"source-{section_id}",
        source_type="공식 자료",
        source_title="공식 원문",
        source_publisher="가나다 주식회사",
        source_host="example.com",
        source_url=f"https://example.com/{section_id}",
        source_document_id=f"document-{section_id}",
        state_evidence=f"{sentence.text} 정확한 원문 지문",
        status="verified",
        fact_status="actual",
        verification_status="verified",
        evidence_support_terms=["검증된", "사실"],
        claim_slot=sentence.planned_claim_slot,
    )
    return replace(fact, evidence_binding=fact_evidence_binding(fact))


def _report(section_ids: tuple[str, ...]) -> tuple[ComposedReport, list[FactRecord]]:
    sections: list[ComposedSection] = []
    facts: list[FactRecord] = []
    for section_id in section_ids:
        sentence = _sentence(section_id)
        sections.append(ComposedSection(section_id, (sentence,)))
        facts.append(_fact(section_id, sentence))
    return ComposedReport(tuple(sections)), facts


def test_지원동기에_필요한_서로_다른_다섯_장에서_글자그대로_고른다() -> None:
    report, facts = _report(tuple(reversed(tuple(_SLOTS))))

    selected = select_extractive_summary(report, tuple(reversed(facts)))

    assert selected.release_ready
    assert selected.section_ids == (
        "business_model",
        "portfolio",
        "current_challenges",
        "future_strategy",
        "competitive_position",
    )
    body_by_section = {
        section.section_id: section.sentences[0] for section in report.sections
    }
    assert all(
        item.sentence is body_by_section[item.section_id] for item in selected.items
    )
    assert tuple(sentence.text for sentence in selected.bound_sentences) == tuple(
        sentence.text for sentence in selected.sentences
    )
    assert tuple(
        sentence.verified_fact_id for sentence in selected.bound_sentences
    ) == selected.fact_ids
    assert all(not sentence.verified_fact_id for sentence in selected.sentences)


def test_근거지문이_손상되거나_미검증인_문장은_요약재료가_아니다() -> None:
    report, facts = _report(tuple(_SLOTS))
    damaged = replace(facts[0], evidence_binding="0" * 64)
    unverified = replace(facts[1], verification_status="unverified")

    selected = select_extractive_summary(
        report,
        (damaged, unverified, *facts[2:]),
    )

    assert facts[0].fact_id not in selected.fact_ids
    assert facts[1].fact_id not in selected.fact_ids


def test_공통근거어가_두개_미만인_일반산문은_요약재료가_아니다() -> None:
    report, facts = _report(tuple(_SLOTS))
    unsupported = replace(facts[0], evidence_support_terms=["검증된"])
    unsupported = replace(
        unsupported,
        evidence_binding=fact_evidence_binding(unsupported),
    )

    selected = select_extractive_summary(report, (unsupported, *facts[1:]))

    assert unsupported.fact_id not in selected.fact_ids


def test_세_장보다_적으면_한_장_문장으로_길이만_채우지_않는다() -> None:
    first = _sentence("business_model", "첫째")
    second = _sentence("business_model", "둘째")
    third = _sentence("portfolio", "첫째")
    report = ComposedReport(
        (
            ComposedSection("business_model", (first, second)),
            ComposedSection("portfolio", (third,)),
        )
    )
    facts = (
        _fact("business_model", first),
        _fact("business_model", second),
        _fact("portfolio", third),
    )

    selected = select_extractive_summary(report, facts)

    assert not selected.release_ready
    assert selected.sentences == (first, third)


def test_구조화_수치사실도_ID와_문장내용이_둘다_맞아야_재사용한다() -> None:
    plain = _sentence("current_challenges")
    fact = _fact("current_challenges", plain)
    structured = replace(
        plain,
        structured_claim=StructuredClaim(
            fact_id=fact.fact_id,
            claim_slot=plain.planned_claim_slot,
            section_owner="current_challenges",
            source_fragment_id=plain.citations[0],
            source_identity="source-identity",
            verification_state="verified",
            state_evidence="evidence",
            subject_scope="가나다 주식회사",
        ),
    )
    report = ComposedReport((ComposedSection("current_challenges", (structured,)),))

    accepted = select_extractive_summary(report, (fact,))
    changed = select_extractive_summary(
        replace(
            report,
            sections=(
                ComposedSection(
                    "current_challenges",
                    (replace(structured, text="내용을 바꾼 문장입니다."),),
                ),
            ),
        ),
        (fact,),
    )

    assert accepted.fact_ids == (fact.fact_id,)
    assert changed.fact_ids == ()


def test_같은_사실키가_두개면_어느것도_임의로_고르지_않는다() -> None:
    sentence = _sentence("business_model")
    first = _fact("business_model", sentence)
    second = replace(first, fact_id="another-fact")
    second = replace(second, evidence_binding=fact_evidence_binding(second))
    report = ComposedReport((ComposedSection("business_model", (sentence,)),))

    selected = select_extractive_summary(report, (first, second))

    assert selected.items == ()
