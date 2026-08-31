from __future__ import annotations

import json
from dataclasses import replace

from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.quality_projection import build_generation_quality_candidate
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
)
from src.features.provenance.sources import Source, SourceKind, exact_evidence_text_hash
from src.shared.report_quality.fact_binding import fact_evidence_binding


def _sentence() -> ComposedSentence:
    return ComposedSentence(
        text="회사는 공식 온라인 채널에서 제품을 직접 판매합니다.",
        citations=("1",),
        grade="확인",
        planned_claim_slot="business_model:sales_channel",
        verification_state="verified",
    )


def _source(text: str = "공식 온라인 채널에서 제품을 직접 판매합니다.") -> Source:
    return Source(
        number=1,
        kind=SourceKind.OTHER,
        label="공식 판매 안내",
        collected_at="2026-08-31",
        source_id="v2-frag-1",
        title="공식 판매 안내",
        publisher="가나다 주식회사",
        host="example.com",
        url="https://example.com/sales",
        document_id="sales-page",
        location="판매 안내",
        source_type="공식 웹",
        fact_status="현재",
        used_in=["business_model"],
        exact_evidence_hashes=[exact_evidence_text_hash(text)],
    )


def _fact(sentence: ComposedSentence, source: Source) -> FactRecord:
    fact = FactRecord(
        fact_id="fact-sales-channel",
        legal_entity="가나다 주식회사",
        subject_scope="가나다 주식회사",
        relationship_or_action="sales_channel",
        claim=sentence.text,
        claim_type="verified_prose",
        section_owner="business_model",
        time_state="present",
        as_of="2026-08-31",
        source_id=source.source_id,
        source_type=source.source_type,
        source_title=source.title,
        source_publisher=source.publisher,
        source_host=source.host,
        source_url=source.url,
        source_document_id=source.document_id,
        location=source.location,
        state_evidence=json.dumps(
            [
                {
                    "fragment_id": "1",
                    "source_id": source.source_id,
                    "document_identity": "document:example.com:sales-page",
                    "exact_sha256": source.exact_evidence_hashes[0],
                }
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        status="verified",
        fact_status="actual",
        verification_status="verified",
        evidence_support_terms=["공식", "제품"],
        claim_slot=sentence.planned_claim_slot,
        supporting_source_ids=[source.source_id],
        supporting_source_identities=["document:example.com:sales-page"],
        supporting_evidence_hashes=list(source.exact_evidence_hashes),
    )
    return replace(fact, evidence_binding=fact_evidence_binding(fact))


def _inputs():
    sentence = _sentence()
    source = _source()
    fact = _fact(sentence, source)
    composed = ComposedReport(
        (ComposedSection("business_model", (sentence,)),),
        summary=(sentence,),
    )
    rendered = Report(
        company="가나다 주식회사",
        job="",
        corp_type="비상장 외감",
        grade=Grade.PARTIAL,
        sections=[
            ReportSection(
                cell="business_model",
                title="사업 모델",
                prose_lines=[(sentence.text, "[1]")],
                fact_ids=[fact.fact_id],
            )
        ],
        citations=[source],
        fact_records=[fact],
    )
    return sentence, source, fact, composed, rendered


def test_일반산문과_추출식요약을_같은_사실ID에_결속한다() -> None:
    _sentence_value, _source_value, fact, composed, rendered = _inputs()

    candidate = build_generation_quality_candidate(rendered, composed)

    assert not candidate.sections[0].has_unbound_public_content
    assert candidate.summary_fact_ids == (fact.fact_id,)
    assert not candidate.has_unbound_summary_content
    assert candidate.facts[0].supporting_source_ids == ("v2-frag-1",)
    assert candidate.sources[0].exact_evidence_hashes


def test_사실지문이_손상되면_본문과_요약을_결속됐다고_세지_않는다() -> None:
    _sentence_value, _source_value, fact, composed, rendered = _inputs()
    rendered.fact_records[:] = [replace(fact, evidence_binding="0" * 64)]

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].has_unbound_public_content
    assert candidate.summary_fact_ids == ()
    assert candidate.has_unbound_summary_content


def test_일반산문_근거어가_두개_미만이면_결속됐다고_세지_않는다() -> None:
    _sentence_value, _source_value, fact, composed, rendered = _inputs()
    unsupported = replace(fact, evidence_support_terms=["공식"])
    unsupported = replace(
        unsupported,
        evidence_binding=fact_evidence_binding(unsupported),
    )
    rendered.fact_records[:] = [unsupported]

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].has_unbound_public_content
    assert candidate.summary_fact_ids == ()
    assert candidate.has_unbound_summary_content


def test_같은_문장키의_사실이_둘이면_임의로_하나를_고르지_않는다() -> None:
    _sentence_value, _source_value, fact, composed, rendered = _inputs()
    second = replace(fact, fact_id="fact-sales-channel-2")
    second = replace(second, evidence_binding=fact_evidence_binding(second))
    rendered.fact_records.append(second)
    rendered.sections[0].fact_ids.append(second.fact_id)

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].has_unbound_public_content
    assert candidate.summary_fact_ids == ()


def test_렌더뒤_문장의_검증상태나_인용을_바꾸면_결속이_끊긴다() -> None:
    sentence, _source_value, _fact_value, composed, rendered = _inputs()
    changed_sentence = replace(sentence, citations=("9",))
    changed = replace(
        composed,
        sections=(ComposedSection("business_model", (changed_sentence,)),),
        summary=(replace(sentence, verification_state="unverified"),),
    )

    candidate = build_generation_quality_candidate(rendered, changed)

    assert candidate.sections[0].has_unbound_public_content
    assert candidate.summary_fact_ids == ()
    assert candidate.has_unbound_summary_content


def test_표나_도식은_행단위_사실계약_전까지_결속으로_위장하지_않는다() -> None:
    _sentence_value, _source_value, _fact_value, composed, rendered = _inputs()
    composed = replace(
        composed,
        sections=(replace(composed.sections[0], flow_rows=()),),
    )
    rendered.sections[0].tables.append(
        # ReportTable의 상세 내용은 이 시험의 핵심이 아니므로 최소 유효 표다.
        ReportTable("표", ["항목"], [["값"]])
    )

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].has_unbound_public_content
