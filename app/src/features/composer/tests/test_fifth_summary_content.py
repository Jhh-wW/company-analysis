"""5차의 승인된 최종 사실을 재사용하며 요약 역할만 바로잡는다."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.features.composer.extractive_summary import select_extractive_summary
from src.features.composer.port import (
    ComposedReport, ComposedSection, ComposedSentence, StructuredClaim,
)
from src.features.composer.tests.test_extractive_summary import _fact, _sentence
from src.features.pipeline.port import FactRecord
from src.shared.report_quality.fact_binding import fact_evidence_binding


def _actual_final_facts():
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "fifth_summary_final_facts.json").read_text(encoding="utf-8"))
    facts = tuple(FactRecord(**entry) for entry in fixture["facts"])
    sections = {}
    for fact in facts:
        sentence = ComposedSentence(
            fact.claim,
            tuple(source.removeprefix("v2-frag-") for source in fact.supporting_source_ids),
            "확인", planned_claim_slot=fact.claim_slot, verification_state="verified",
        )
        if fact.claim_slot == "past_changes:historical_performance":
            sentence = replace(sentence, structured_claim=StructuredClaim(
                fact_id=fact.fact_id, claim_slot=fact.claim_slot,
                section_owner=fact.section_owner,
                source_fragment_id=sentence.citations[0],
                source_identity=fact.supporting_source_identities[0],
                verification_state="verified", state_evidence=fact.state_evidence,
                subject_scope=fact.subject_scope,
            ))
        sections.setdefault(fact.section_owner, []).append(sentence)
    return ComposedReport(tuple(ComposedSection(key, tuple(value))
                                for key, value in sections.items())), facts


def test_실제_승인_제품과_PG_사업을_기존_결속_그대로_요약한다():
    report, facts = _actual_final_facts()
    assert all(fact_evidence_binding(fact) == fact.evidence_binding for fact in facts)
    before = tuple(facts)

    selected = select_extractive_summary(report, facts)

    assert selected.release_ready
    assert selected.section_ids == (
        "portfolio", "past_changes", "identity", "operations_partners", "portfolio",
    )
    assert selected.items[0].sentence.planned_claim_slot == "portfolio:product_role"
    assert "브랜드페이" in selected.items[0].sentence.text
    assert "결제위젯" in selected.items[-1].sentence.text
    assert "원천사로부터 수령" in selected.items[2].sentence.text
    assert all("설립" not in item.sentence.text and "비용 구조" not in item.sentence.text
               for item in selected.items)
    originals = {id(sentence) for section in report.sections for sentence in section.sentences}
    assert all(id(item.sentence) in originals for item in selected.items)
    assert facts == before
    assert set(selected.fact_ids) <= {fact.fact_id for fact in facts}


@pytest.mark.parametrize("change", ("signature", "claim", "source", "unverified"))
def test_실제_제품_fact가_손상되면_역할_우선순위로_구제하지_않는다(change):
    report, facts = _actual_final_facts()
    product = next(f for f in facts if "브랜드페이" in f.claim)
    damaged = {
        "signature": replace(product, evidence_binding="0" * 64),
        "claim": replace(product, claim="다른 기능을 제공한다."),
        "source": replace(product, source_id="다른-출처"),
        "unverified": replace(product, verification_status="unverified"),
    }[change]
    selected = select_extractive_summary(
        report, tuple(damaged if f is product else f for f in facts),
    )
    assert product.fact_id not in selected.fact_ids


@pytest.mark.parametrize("text", (
    "비용 구조를 보면, 당기 영업비용 중 지급수수료 항목이 가장 큰 비중을 차지하며, 판매수수료도 전기 대비 증가하였다.",
    "당기 매출원가는 전기보다 증가하였다.",
    "판매관리비의 대부분은 인건비와 광고선전비이다.",
    "지급수수료가 12억원으로 증가하였다.",
))
def test_비용_사실만으로_수익모델_요약을_채우지_않는다(text):
    sentence = replace(_sentence("business_model"), text=text)
    fact = replace(_fact("business_model", sentence), evidence_support_terms=text.split()[:2])
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    report = ComposedReport((ComposedSection("business_model", (sentence,)),))
    assert select_extractive_summary(report, (fact,)).items == ()
    assert report.sections[0].sentences == (sentence,)


@pytest.mark.parametrize("text", (
    "회사의 매출은 제품 판매에서 발생하고 매출원가도 증가하였다.",
    "영업수익은 12억원이며 지급수수료는 3억원이다.",
    "회사는 가맹점에서 판매수수료를 받으며, 지급수수료도 발생한다.",
    "회사는 고객에게 비용 구조 분석 서비스를 제공한다.",
))
def test_비용과_실제_판매_수취가_함께_있는_사실은_보존한다(text):
    sentence = replace(_sentence("business_model"), text=text)
    fact = replace(_fact("business_model", sentence), evidence_support_terms=text.split()[:2])
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    report = ComposedReport((ComposedSection("business_model", (sentence,)),))
    assert select_extractive_summary(report, (fact,)).sentences == (sentence,)


def test_설립목적과_현재사업이_함께_있으면_일괄_감점하지_않는다():
    mixed = replace(_sentence("identity"), text=(
        "회사는 의료기기 개발을 목적으로 설립된 회사로, 현재 3개 제품을 제조하고 판매한다."
    ))
    business = replace(mixed, text="회사는 고객에게 의료기기를 공급한다.")
    facts = []
    for index, sentence in enumerate((mixed, business)):
        fact = replace(_fact("identity", sentence), fact_id=f"identity-{index}",
                       evidence_support_terms=sentence.text.split()[:2])
        facts.append(replace(fact, evidence_binding=fact_evidence_binding(fact)))
    report = ComposedReport((ComposedSection("identity", (mixed, business)),))
    assert select_extractive_summary(report, facts).sentences == (mixed,)


def test_해석_제품역할이_확인_수익연결보다_먼저_선택되지_않는다():
    product = replace(_sentence("portfolio"), text="제품은 고객의 결제 절차를 줄이는 것으로 해석된다.",
                      grade="해석")
    revenue = replace(product, text="회사는 결제 서비스에서 수익을 인식한다.", grade="확인",
                      planned_claim_slot="portfolio:revenue_link")
    facts = []
    for index, sentence in enumerate((product, revenue)):
        fact = replace(_fact("portfolio", sentence), fact_id=f"portfolio-{index}",
                       evidence_support_terms=sentence.text.split()[:2])
        facts.append(replace(fact, evidence_binding=fact_evidence_binding(fact)))
    report = ComposedReport((ComposedSection("portfolio", (product, revenue)),))
    assert select_extractive_summary(report, facts).sentences == (revenue,)
