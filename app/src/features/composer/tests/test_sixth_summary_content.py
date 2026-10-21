"""6차 최종 승인 사실의 회계 표시와 실제 정산 흐름을 요약에서 구분한다."""

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


ACCOUNTING_FACT = "v2-prose-d29b1c80a00e4a0c42cc95808da6c680"
EXCHANGE_FACT = "v2-prose-72209fb1596dc49ae90ac2d8653c455d"


def _actual_final_facts():
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "sixth_summary_final_facts.json").read_text(encoding="utf-8"))
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


def test_실제_6차는_회계표시가_아닌_기존_PG_정산사실을_요약한다():
    report, facts = _actual_final_facts()
    assert all(fact_evidence_binding(fact) == fact.evidence_binding for fact in facts)
    before = tuple(facts)
    selected = select_extractive_summary(report, facts)

    assert selected.release_ready
    assert selected.items[0].fact_id == EXCHANGE_FACT
    assert ACCOUNTING_FACT not in selected.fact_ids
    assert selected.items[0].sentence.citations == ("90",)
    assert "원천사로부터 대금을 받은 후" in selected.items[0].sentence.text
    assert "수수료와 부가가치세를 제외하여 지급" in selected.items[0].sentence.text
    assert any("브랜드페이" in item.sentence.text for item in selected.items)
    originals = {id(sentence) for section in report.sections for sentence in section.sentences}
    assert all(id(item.sentence) in originals for item in selected.items)
    assert facts == before
    # 본문 사실을 삭제하거나 새 승인으로 대체하지 않는다.
    assert any(fact.fact_id == ACCOUNTING_FACT for fact in facts)


@pytest.mark.parametrize("text", (
    "회사가 가맹점에 지급할 의무가 발생하는 시점에 해당 채무는 재무상태표상 예수금으로 표시된다.",
    "회사는 해당 부채를 재무상태표상 예수금으로 계상한다.",
    "가맹점에 지급할 의무가 생기면 해당 채무를 재무상태표에 미지급금으로 표시한다.",
    "회사는 해당 채무를 대차대조표상 예수금으로 표시한다.",
))
def test_채무의_회계표시만으로_제공가치_요약을_채우지_않는다(text):
    sentence = replace(_sentence("business_model"), text=text)
    fact = replace(_fact("business_model", sentence), evidence_support_terms=text.split()[:2])
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    report = ComposedReport((ComposedSection("business_model", (sentence,)),))
    assert select_extractive_summary(report, (fact,)).items == ()
    assert report.sections[0].sentences == (sentence,)


@pytest.mark.parametrize("text", (
    "회사는 원천사로부터 대금을 받은 후 가맹점에 지급하며 해당 채무를 재무상태표상 예수금으로 표시한다.",
    "회사는 결제 서비스를 제공하며 해당 채무를 재무상태표상 예수금으로 표시한다.",
    "회사는 이용료를 수취한다. 해당 부채를 재무상태표상 미지급금으로 계상한다.",
    "회사는 회계 표시 서비스를 제공한다.",
))
def test_실제_거래와_회계표시가_함께_있는_검증사실은_보존한다(text):
    sentence = replace(_sentence("business_model"), text=text)
    fact = replace(_fact("business_model", sentence), evidence_support_terms=text.split()[:2])
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    report = ComposedReport((ComposedSection("business_model", (sentence,)),))
    assert select_extractive_summary(report, (fact,)).sentences == (sentence,)


@pytest.mark.parametrize("change", ("binding", "claim", "source", "unverified"))
def test_회계표시_제외가_손상된_정산사실의_승인을_대신하지_않는다(change):
    report, facts = _actual_final_facts()
    exchange = next(fact for fact in facts if fact.fact_id == EXCHANGE_FACT)
    damaged = {
        "binding": replace(exchange, evidence_binding="0" * 64),
        "claim": replace(exchange, claim="회사는 다른 거래에서 수익을 얻는다."),
        "source": replace(exchange, source_id="다른-출처"),
        "unverified": replace(exchange, verification_status="unverified"),
    }[change]
    selected = select_extractive_summary(
        report, tuple(damaged if fact is exchange else fact for fact in facts),
    )
    assert EXCHANGE_FACT not in selected.fact_ids
    assert ACCOUNTING_FACT not in selected.fact_ids
