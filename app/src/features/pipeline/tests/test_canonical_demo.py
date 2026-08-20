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

    assert by_id["identity-01"] == "official_identity"
    assert by_id["operations-01"] == "partner_role"
    assert by_id["operations-02"] == "operating_core"
    assert by_id["ops-branch-01"] == "operating_core"
    assert by_id["ops-branch-02"] == "operating_core"
    assert by_id["ops-branch-03"] == "operating_core"
    assert by_id["culture-01"] == "official_value"
    assert by_id["culture-02"] == "official_value"


def test_demo_official_web_domains_are_bound_to_independent_filing_evidence() -> None:
    report = build_demo_report()
    sources = list(report.citations)
    websites = [source for source in sources if source.kind is SourceKind.OTHER]

    assert websites
    assert all(
        is_canonical_official_with_registry(source, sources) for source in websites
    )
