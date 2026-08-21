from __future__ import annotations

from src.features.pipeline.canonical_demo import build_demo_report
from src.features.provenance.sources import (
    SourceKind,
    is_canonical_official_with_registry,
)
from src.features.spanselect.canonical import PRIORITY_SIGNAL_PATTERNS


def test_demo_portfolio_signals_and_visible_claims_stay_bound() -> None:
    report = build_demo_report()
    facts = {
        fact.fact_id: fact
        for fact in report.fact_records
        if fact.section_owner == "portfolio"
    }
    section = next(item for item in report.sections if item.cell == "portfolio")

    assert 1 <= len(facts) <= 3
    assert [text for text, _cite in section.prose_lines] == [
        facts[fact_id].claim for fact_id in section.fact_ids
    ]
    for fact in facts.values():
        assert fact.claim_type == "priority_product"
        assert len(set(fact.priority_signals)) >= 2
        assert all(
            PRIORITY_SIGNAL_PATTERNS[signal].search(fact.state_evidence)
            for signal in fact.priority_signals
        )


def test_demo_completed_execution_keeps_source_year_and_numeric_ledger() -> None:
    report = build_demo_report()
    fact = next(
        item for item in report.fact_records if item.fact_id == "past-execution-01"
    )

    assert fact.claim_type == "completed_execution"
    assert fact.event_date == "2025"
    assert fact.event_date in fact.state_evidence
    assert fact.raw_value == "2025"
    assert fact.display_value == "2025"
    assert fact.numeric_checks == ["2025|1|0|2025"]


def test_demo_claim_types_match_upstream_canonical_vocabulary() -> None:
    report = build_demo_report()
    by_id = {fact.fact_id: fact.claim_type for fact in report.fact_records}

    assert by_id["identity-01"] == "identity_summary"
    assert "operations-01" not in by_id
    assert "operations-02" not in by_id
    assert by_id["operations-core-01"] == "operating_core"
    assert by_id["ops-branch-02"] == "operating_core"
    assert "ops-branch-03" not in by_id
    assert by_id["culture-01"] == "official_value"
    assert by_id["culture-02"] == "official_value"
    assert by_id["competition-01"] == "competitive_comparison"
    assert "competition-02" not in by_id

    comparison = next(
        fact for fact in report.fact_records if fact.fact_id == "competition-01"
    )
    assert comparison.comparison_judgment == "operating_characteristic"


def test_demo_sales_regions_stay_an_observation_without_a_market_stage() -> None:
    report = build_demo_report()
    market = next(
        fact for fact in report.fact_records if fact.fact_id == "biz-customer-01"
    )

    assert market.market_priority == ""
    assert market.market_stage == ""
    assert market.market_observation == "국내·중국·인도 매출"
    assert market.market_observation in market.state_evidence


def test_demo_sections_five_to_seven_keep_distinct_structured_roles() -> None:
    report = build_demo_report()
    by_id = {fact.fact_id: fact for fact in report.fact_records}

    issue = by_id["current-01"]
    responses = [by_id["current-02"], by_id["current-03"]]
    assert issue.next_check_metric == "본계약"
    assert all(response.response_to_fact_id == issue.fact_id for response in responses)
    assert all(response.response_action in response.state_evidence for response in responses)
    assert all(response.initial_signal == "" for response in responses)

    plans = [
        fact
        for fact in report.fact_records
        if fact.section_owner == "future_strategy"
    ]
    assert {fact.plan_status for fact in plans} == {"announced"}
    assert all(fact.plan_timing in fact.state_evidence for fact in plans)
    assert all(fact.plan_execution_signal in fact.state_evidence for fact in plans)

    branches = [by_id["operations-core-01"], by_id["ops-branch-02"]]
    assert all(
        (fact.value_chain_stage, fact.relationship_type)
        == ("production", "subsidiary")
        for fact in branches
    )
    operations = next(
        section for section in report.sections if section.cell == "operations_partners"
    )
    assert operations.tables[0].headers == [
        "주체",
        "가치사슬 단계",
        "관계 유형",
        "확인된 역할",
        "현재 상태",
    ]
    assert operations.prose_lines == [
        (by_id["operations-core-01"].claim, "[1]")
    ]


def test_demo_official_web_domains_are_bound_to_independent_filing_evidence() -> None:
    report = build_demo_report()
    sources = list(report.citations)
    websites = [source for source in sources if source.kind is SourceKind.OTHER]

    assert websites
    assert all(
        is_canonical_official_with_registry(source, sources) for source in websites
    )
