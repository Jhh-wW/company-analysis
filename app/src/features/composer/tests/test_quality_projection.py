from __future__ import annotations

import json
from dataclasses import replace

from src.features.composer.constants import GRADE_INTERPRETED
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
from src.shared.report_quality.assessment import assess_generation
from src.shared.report_quality.constants import (
    COMPETITIVE_COMPARISON_CLAIM_TYPE,
    COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
    STRICT_QUALITY_CONTRACT_VERSION,
)
from src.shared.report_quality.models import QualityGrade, ReleaseDecision


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
        document_content_sha256="c" * 64,
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
    assert candidate.sources[0].document_content_sha256 == "c" * 64


def test_비교프로그램의_구조필드는_최종품질DTO까지_손실없이_간다() -> None:
    sentence, _source_value, fact, composed, rendered = _inputs()
    conditions = {
        "customer": "기업 고객",
        "product": "검사장비",
        "market": "반도체 시장",
        "self_period": "2025-01-01~2025-12-31",
        "comparator_period": "2025-01-01~2025-12-31",
        "self_definition": "매출액 정의",
        "comparator_definition": "매출액 정의",
        "self_accounting_scope": "연결재무제표(CFS)",
        "comparator_accounting_scope": "연결재무제표(CFS)",
    }
    comparison_fact = replace(
        fact,
        claim_type=COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
        state_evidence="자사 공식 원문",
        evidence_support_terms=["공식", "제품"],
        comparison_target="비교사 주식회사",
        comparison_metric="영업이익률",
        comparison_definition="매출액 정의",
        comparison_basis="닫힌 비교 근거",
        comparison_period="2025-01-01~2025-12-31",
        comparison_scope="연결재무제표(CFS)",
        comparison_judgment="competitive_advantage",
        comparator_source_id="comparison-source",
        comparator_state_evidence="비교사 공식 원문",
        comparison_conditions=conditions,
    )
    comparison_fact = replace(
        comparison_fact,
        evidence_binding=fact_evidence_binding(comparison_fact),
    )
    bound_sentence = replace(sentence, verified_fact_id=comparison_fact.fact_id)
    composed = replace(
        composed,
        sections=(ComposedSection("business_model", (bound_sentence,)),),
        summary=(),
    )
    rendered.fact_records[:] = [comparison_fact]

    projected = build_generation_quality_candidate(rendered, composed).facts[0]

    assert projected.legal_entity == comparison_fact.legal_entity
    assert projected.state_evidence == comparison_fact.state_evidence
    assert projected.evidence_support_terms == tuple(
        comparison_fact.evidence_support_terms
    )
    assert projected.comparison_target == comparison_fact.comparison_target
    assert projected.comparison_metric == comparison_fact.comparison_metric
    assert projected.comparison_definition == comparison_fact.comparison_definition
    assert projected.comparison_basis == comparison_fact.comparison_basis
    assert projected.comparison_period == comparison_fact.comparison_period
    assert projected.comparison_scope == comparison_fact.comparison_scope
    assert projected.comparison_judgment == comparison_fact.comparison_judgment
    assert projected.comparator_source_id == comparison_fact.comparator_source_id
    assert (
        projected.comparator_state_evidence
        == comparison_fact.comparator_state_evidence
    )
    assert projected.comparison_conditions == conditions
    assert projected.comparison_conditions is not comparison_fact.comparison_conditions


def test_추출식요약은_본문prose의_같은_factID를_정확히_재사용한다() -> None:
    sentence, _source_value, fact, composed, rendered = _inputs()
    bound_summary = replace(sentence, verified_fact_id=fact.fact_id)
    composed = replace(composed, summary=(bound_summary,))

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.summary_fact_ids == (fact.fact_id,)
    assert not candidate.has_unbound_summary_content


def test_추출식요약의_factID만_다른사실로_바꾸면_결속되지않는다() -> None:
    sentence, _source_value, fact, composed, rendered = _inputs()
    other = replace(
        fact,
        fact_id="fact-other",
        claim="회사는 공식 자료에서 기업 고객을 확인했습니다.",
        claim_slot="business_model:customer_type",
    )
    other = replace(other, evidence_binding=fact_evidence_binding(other))
    rendered.fact_records.append(other)
    forged = replace(sentence, verified_fact_id=other.fact_id)
    composed = replace(composed, summary=(forged,))

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.summary_fact_ids == ()
    assert candidate.has_unbound_summary_content


def test_요약이_본문에_공개되지않은_숨은fact를_가리키면_안전차단한다() -> None:
    sentence, source, _fact_value, composed, rendered = _inputs()
    hidden_sentence = replace(
        sentence,
        text="회사는 공식 온라인 채널에서 기업 고객에게 제품을 판매합니다.",
        planned_claim_slot="business_model:customer_type",
    )
    hidden = _fact(hidden_sentence, source)
    hidden = replace(
        hidden,
        fact_id="fact-hidden-summary",
        evidence_binding="",
    )
    hidden = replace(hidden, evidence_binding=fact_evidence_binding(hidden))
    rendered.fact_records.append(hidden)
    composed = replace(
        composed,
        summary=(replace(hidden_sentence, verified_fact_id=hidden.fact_id),),
    )

    candidate = build_generation_quality_candidate(rendered, composed)
    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert candidate.summary_fact_ids == (hidden.fact_id,)
    assert not candidate.has_unbound_summary_content
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert any("부분집합" in problem for problem in result.safety.problems)


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


def test_화면에_없는_숨은_사실ID로_품질개수를_부풀릴수없다() -> None:
    _sentence_value, _source_value, fact, composed, rendered = _inputs()
    hidden = replace(
        fact,
        fact_id="hidden-fact",
        claim="화면에는 없는 숨은 사실입니다.",
        claim_slot="business_model:customer_type",
        relationship_or_action="customer_type",
    )
    hidden = replace(hidden, evidence_binding=fact_evidence_binding(hidden))
    rendered.fact_records.append(hidden)
    rendered.sections[0].fact_ids.append(hidden.fact_id)

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].has_unbound_public_content


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


def test_FULL은_재봉인한_미지원_claim_type도_검증사실로_투영하지_않는다() -> None:
    """지문만 다시 만든 공격도 공개 타입의 닫힌 계약은 우회하지 못한다."""

    _sentence_value, _source_value, fact, composed, rendered = _inputs()
    attacked = replace(fact, claim_type="verified_prosee")
    attacked = replace(attacked, evidence_binding=fact_evidence_binding(attacked))
    rendered.fact_records[:] = [attacked]

    candidate = build_generation_quality_candidate(rendered, composed)
    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    # 이 시험은 사실 장부·공개 문장 지문을 모두 일관되게 바꾼 공격이다.
    # 단순 evidence_binding 불일치가 아니라 claim_type 계약이 직접 막아야 한다.
    assert not candidate.sections[0].has_unbound_public_content
    assert result.quality.substantive_claims == 0
    assert result.quality.verified_claims == 0
    assert result.safety.decision is ReleaseDecision.BLOCKED
    assert result.publication_grade is QualityGrade.INCOMPLETE
    assert any("claim_type" in problem for problem in result.safety.problems)


def test_해석문장의_사실종류만_재봉인해도_검증사실로_바뀌지않는다() -> None:
    sentence, source, _fact_value, _composed_value, _rendered_value = _inputs()
    interpreted = replace(sentence, grade=GRADE_INTERPRETED)
    # 공격자는 문장 등급은 그대로 두고 FactRecord를 verified_prose로 만든 뒤
    # evidence_binding까지 다시 계산했다. 단순 지문 비교만으로는 잡히지 않는다.
    relabeled = _fact(interpreted, source)
    composed = ComposedReport(
        (ComposedSection("business_model", (interpreted,)),),
        summary=(interpreted,),
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
                prose_lines=[(interpreted.text, "[1]")],
                fact_ids=[relabeled.fact_id],
            )
        ],
        citations=[source],
        fact_records=[relabeled],
    )

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].has_unbound_public_content
    assert candidate.summary_fact_ids == ()
    assert candidate.has_unbound_summary_content


def test_일반산문을_허용된_구조타입으로_재봉인해도_안전차단한다() -> None:
    for masquerade_type in (
        HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
        COMPETITIVE_COMPARISON_CLAIM_TYPE,
    ):
        _sentence_value, _source_value, fact, composed, rendered = _inputs()
        attacked = replace(fact, claim_type=masquerade_type)
        attacked = replace(attacked, evidence_binding=fact_evidence_binding(attacked))
        rendered.fact_records[:] = [attacked]

        candidate = build_generation_quality_candidate(rendered, composed)
        result = assess_generation(
            candidate,
            contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        )

        assert candidate.sections[0].has_unbound_public_content
        assert result.safety.decision is ReleaseDecision.BLOCKED
        assert result.publication_grade is QualityGrade.INCOMPLETE
        assert any(
            "NumericBinding" in problem or "공식 비교 수치" in problem
            for problem in result.safety.problems
        )


def test_같은_fact문장을_세번복제해도_장별문장수는_한건이고_차단한다() -> None:
    sentence, _source_value, _fact_value, composed, rendered = _inputs()
    duplicated = replace(
        composed,
        sections=(ComposedSection("business_model", (sentence,) * 3),),
    )

    candidate = build_generation_quality_candidate(rendered, duplicated)
    result = assess_generation(
        candidate,
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert candidate.sections[0].public_sentence_count == 1
    assert candidate.sections[0].has_unbound_public_content
    assert result.safety.decision is ReleaseDecision.BLOCKED


def test_서로다른_원자fact_세문장은_장별문장수_세건으로_센다() -> None:
    first_sentence, source, first_fact, _composed_value, _rendered_value = _inputs()
    second_sentence = replace(
        first_sentence,
        text="회사는 공식 온라인 채널에서 기업 고객에게 제품을 판매합니다.",
        planned_claim_slot="business_model:customer_type",
    )
    third_sentence = replace(
        first_sentence,
        text="회사는 공식 온라인 채널의 제품 판매에서 구독 매출을 얻습니다.",
        planned_claim_slot="business_model:revenue_model",
    )
    facts = [first_fact]
    for index, sentence in enumerate((second_sentence, third_sentence), start=2):
        fact = _fact(sentence, source)
        fact = replace(
            fact,
            fact_id=f"fact-business-model-{index}",
            relationship_or_action=sentence.planned_claim_slot.split(":", 1)[-1],
        )
        facts.append(replace(fact, evidence_binding=fact_evidence_binding(fact)))
    composed = ComposedReport(
        (
            ComposedSection(
                "business_model",
                (first_sentence, second_sentence, third_sentence),
            ),
        ),
        summary=(first_sentence,),
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
                prose_lines=[
                    (sentence.text, "[1]")
                    for sentence in (first_sentence, second_sentence, third_sentence)
                ],
                fact_ids=[fact.fact_id for fact in facts],
            )
        ],
        citations=[source],
        fact_records=facts,
    )

    candidate = build_generation_quality_candidate(rendered, composed)

    assert candidate.sections[0].public_sentence_count == 3
    assert not candidate.sections[0].has_unbound_public_content
