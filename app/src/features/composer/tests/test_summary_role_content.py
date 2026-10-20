"""실제 PDF의 회계 인식조건·합병이 가치교환·제품역할 요약으로 바뀌지 않는다."""

from dataclasses import replace

import pytest

from src.features.composer.extractive_summary import distinct_summary_candidates, select_extractive_summary
from src.features.composer.logic import SummaryCandidate
from src.features.composer.port import ComposedReport, ComposedSection
from src.features.composer.tests.test_extractive_summary import _fact, _sentence


RECOGNITION_ONLY = "회사는 PG 서비스의 경우, 거래승인이 이루어지는 시점에 수행의무가 완료되는 것으로 판단하고 있다."
MERGER_ONLY = "회사는 2023년 10월 31일을 간주취득일로 하여 블리츠패스트 주식회사와의 합병을 완료하였으며, 이 합병은 지배회사 비바리퍼블리카의 또 다른 종속기업이었던 블리츠패스트를 흡수하는 방식으로 이루어졌다."


@pytest.mark.parametrize("section,slot,text", [
    ("business_model", "business_model:value_exchange", RECOGNITION_ONLY),
    ("business_model", "business_model:revenue_model", RECOGNITION_ONLY),
    ("portfolio", "portfolio:product_role", MERGER_ONLY),
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
