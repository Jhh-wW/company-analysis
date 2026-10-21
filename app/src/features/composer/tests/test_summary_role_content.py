"""실제 PDF의 회계 인식조건·합병이 가치교환·제품역할 요약으로 바뀌지 않는다."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.features.composer.extractive_summary import distinct_summary_candidates, select_extractive_summary
from src.features.composer.logic import SummaryCandidate
from src.features.composer.port import ComposedReport, ComposedSection, StructuredClaim
from src.features.composer.tests.test_extractive_summary import _fact, _sentence


RECOGNITION_ONLY = "회사는 PG 서비스의 경우, 거래승인이 이루어지는 시점에 수행의무가 완료되는 것으로 판단하고 있다."
MERGER_ONLY = "회사는 2023년 10월 31일을 간주취득일로 하여 블리츠패스트 주식회사와의 합병을 완료하였으며, 이 합병은 지배회사 비바리퍼블리카의 또 다른 종속기업이었던 블리츠패스트를 흡수하는 방식으로 이루어졌다."
FOUNDING_PURPOSE = (
    "토스페이먼츠 주식회사는 전자지급결제대행업을 영위할 목적으로 설립된 회사로, "
    "설립 당시의 사업 목적이 전자결제 분야에 있음이 공시 문서에 명시되어 있다."
)


@pytest.mark.parametrize("section,slot,text", [
    ("business_model", "business_model:value_exchange", RECOGNITION_ONLY),
    ("business_model", "business_model:revenue_model", RECOGNITION_ONLY),
    ("portfolio", "portfolio:product_role", MERGER_ONLY),
    ("portfolio", "portfolio:product_role", FOUNDING_PURPOSE),
])
def test_실제출력의_인식조건과_합병만으로_핵심역할요약을_채우지_않는다(section, slot, text):
    sentence = replace(_sentence("identity"), text=text, planned_claim_slot=slot)
    candidate = SummaryCandidate(section, sentence)
    assert distinct_summary_candidates((candidate,), company_name="토스페이먼츠 주식회사") == ()
    report = ComposedReport((ComposedSection(section, (sentence,)),))
    selected = select_extractive_summary(report, (_fact(section, sentence),))
    assert not selected.items
    assert report.sections[0].sentences == (sentence,)
    assert sentence.text == text


@pytest.mark.parametrize("section,slot,text", [
    ("business_model", "business_model:value_exchange", "회사는 가맹점에 결제 승인과 정산 서비스를 제공하고 거래액에 따른 수수료를 받는다. " + RECOGNITION_ONLY),
    ("business_model", "business_model:revenue_model", "회사는 소프트웨어 사용권을 판매해 수익을 얻으며 인도 시점에 수익을 인식한다."),
    ("business_model", "business_model:value_exchange", "회사는 고객에게 신용평가 보고서를 제공하고 평가 수수료를 청구한다."),
    ("portfolio", "portfolio:product_role", "회사는 소프트웨어 회사를 인수해 가맹점에 결제와 정산을 자동화하는 서비스를 제공한다."),
    ("portfolio", "portfolio:product_role", "회사는 A기업 인수로 데이터 솔루션을 핵심 제품군에 편입했다."),
    ("portfolio", "portfolio:product_role", "회사는 A기업 인수를 완료하고 데이터 솔루션을 제품군에 편입했다."),
    ("portfolio", "portfolio:product_role", "데이터 통합 제품은 여러 파일을 병합하고 고객의 자료 관리를 지원한다."),
    ("past_changes", "past_changes:completed_execution", MERGER_ONLY),
])
def test_실제사업설명과_혼합문장과_알맞은장의_합병은_그대로_보존한다(section, slot, text):
    sentence = replace(_sentence("identity"), text=text, planned_claim_slot=slot)
    candidate = SummaryCandidate(section, sentence)
    assert distinct_summary_candidates((candidate,), company_name="다른회사") == (candidate,)
    report = ComposedReport((ComposedSection(section, (sentence,)),))
    selected = select_extractive_summary(report, (_fact(section, sentence),))
    assert selected.sentences == (sentence,)
    assert selected.items[0].fact_id == _fact(section, sentence).fact_id


def test_actual_followup_policies_and_identity_merger_are_not_key_summary():
    fixture = json.loads((Path(__file__).parent / "fixtures/payment_portfolio_followup.json").read_text(encoding="utf-8"))
    from src.features.composer.logic import extract_json_payload
    rows = extract_json_payload(fixture["portfolio_writer_response"])["문장들"]
    for section, slot, text in (
        ("portfolio", "portfolio:revenue_link", rows[2]["글"]),
        ("portfolio", "portfolio:revenue_link", rows[3]["글"]),
        ("identity", "identity:origin_context", MERGER_ONLY),
    ):
        sentence = replace(_sentence("identity"), text=text, planned_claim_slot=slot)
        candidate = SummaryCandidate(section, sentence)
        assert distinct_summary_candidates((candidate,), company_name="토스페이먼츠 주식회사") == ()


def test_bound_performance_precedes_dated_news_in_the_same_section():
    numeric = replace(_sentence("identity"), text="2025년 매출액은 9,251.1억원으로 전년 대비 12.86% 증가했다.",
                      planned_claim_slot="past_changes:historical_performance")
    numeric_fact = _fact("past_changes", numeric)
    numeric = replace(numeric, structured_claim=StructuredClaim(
        fact_id=numeric_fact.fact_id, claim_slot=numeric.planned_claim_slot,
        section_owner="past_changes", source_fragment_id=numeric.citations[0],
        source_identity="검증된 공시", verification_state="verified", state_evidence=numeric.text,
        metric="매출액", raw_value="925113921197", unit="원",
    ))
    news = replace(_sentence("identity"), text="회사는 2026년 10월 PQC 적용을 완료했다.",
                   planned_claim_slot="past_changes:completed_execution")
    report = ComposedReport((ComposedSection("past_changes", (news, numeric)),))
    news_fact = _fact("past_changes", news)
    selected = select_extractive_summary(report, (numeric_fact, news_fact))
    assert selected.sentences == (numeric,)
    assert selected.fact_ids == (numeric_fact.fact_id,)
    # 가점은 기존 정본 결속 검사를 건너뛰지 못한다.
    damaged = replace(numeric_fact, evidence_binding="0" * 64)
    assert select_extractive_summary(report, (damaged, news_fact)).sentences == (news,)
