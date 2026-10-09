"""요약은 연결된 검증 사실의 의미만 표시하고 혼합 의미를 넓히지 않는다."""

from dataclasses import replace

import pytest

from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import FactRecord, SummaryItem
from src.features.report_standard.public_projection import _summary_rows
from src.features.report_standard.section_content import summary_topic


@pytest.mark.parametrize("slot,action,expected", [
    ("customer_type", "customer_type", "고객 대상"),
    ("sales_channel", "sales_channel", "판매 경로"),
    ("value_exchange", "value_exchange", "제공 가치와 대가"),
    ("revenue_model", "revenue_model", "수익 모델"),
    ("customer_type", "", "고객 대상"),
    ("", "sales_channel", "판매 경로"),
    ("customer_type", "revenue_model", "사업 구조"),
    ("", "customer_market", "사업 구조"),
])
def test_정본_요약은_연결사실의_닫힌_의미만_표시한다(slot, action, expected):
    fact = FactRecord(fact_id="f1", section_owner="business_model",
                      claim_slot=f"business_model:{slot}" if slot else "",
                      relationship_or_action=action,
                      status="verified", verification_status="verified")
    item = SummaryItem(section_id="business_model", fact_ids=["f1"])
    report = replace(build_demo_report(), fact_records=[fact], summary_items=[item])
    assert summary_topic(item.section_id, report, item) == expected
    assert _summary_rows(report)[0].topic == expected


def test_혼합_미검증_연결누락과_레거시는_사업구조다():
    fact = FactRecord(fact_id="f1", section_owner="business_model",
                      claim_slot="business_model:customer_type",
                      status="verified", verification_status="verified")
    other = replace(fact, fact_id="f2", claim_slot="business_model:revenue_model")
    report = replace(build_demo_report(), fact_records=[fact, other])
    for ids in ([], ["missing"], ["f1", "f2"]):
        assert summary_topic("business_model", report,
                             SummaryItem(fact_ids=ids)) == "사업 구조"
    unverified = replace(report, fact_records=[replace(fact, status="partial")])
    assert summary_topic("business_model", unverified,
                         SummaryItem(fact_ids=["f1"])) == "사업 구조"
    assert summary_topic("business_model") == "사업 구조"
    assert summary_topic("portfolio") == "제품역할"
