"""표의 불일치 행만 제거하며 행별 원자료·인용의 순서를 유지한다."""

from dataclasses import replace

from src.features.pipeline.supplementary_research_filter import filter_supplementary_research_report
from src.features.pipeline.tests.test_supplementary_research_release import (
    _DART, _Verifier, _report,
)


def test_표_인용을_다른_문서로_바꾼_행만_제거한다():
    report, evidence = _report(
        (("identity", _DART), ("business_model", _DART), ("portfolio", _DART)),
        table_section="portfolio",
    )
    original = next(section for section in report.sections if section.cell == "portfolio")
    table = original.tables[0]
    row = table.rows[0]
    changed = replace(
        table, rows=[list(row), list(row)], raw_rows=[list(row), list(row)],
        evidence_rows=["첫째 원문", "둘째 원문"],
        row_cites=[["1"], ["3"]], source_cites=["1", "3"],
        row_evidence_refs=["첫째 지문", "둘째 지문"],
    )
    report = replace(report, sections=[
        replace(section, tables=[changed]) if section is original else section
        for section in report.sections
    ])

    filtered = filter_supplementary_research_report(
        report, official_evidence=evidence, source_verifier=_Verifier(),
    )

    kept = next(section for section in filtered.sections if section.cell == "portfolio").tables[0]
    assert kept.rows == [row]
    assert kept.raw_rows == [row]
    assert kept.evidence_rows == ["둘째 원문"]
    assert kept.row_cites == [["3"]]
    assert kept.row_evidence_refs == ["둘째 지문"]
    assert kept.source_cites == ["3"]
    assert report.sections[2].tables[0].rows == [row, row]
