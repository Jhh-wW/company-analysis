"""공개 가능한 canonical 데모의 근거·본문·수치 계약 회귀 시험."""

from __future__ import annotations

import re

import pytest

from src.core.citations import citation_number
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.features.report_standard.constants import CANONICAL_SECTION_IDS
from src.features.report_standard.publish import validate_publishable


_DISPLAY_NUMBER = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")


@pytest.fixture(scope="module")
def canonical_report():
    items = available_companies()
    assert len(items) == 1
    item = items[0]
    pipeline = DemoPipeline()
    user_input = UserInput(
        company=item["company"], job="", region="", posting_text=""
    )
    card = pipeline.find_company(user_input)
    assert card is not None

    result = pipeline.run(user_input, card)

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    return result.report


def test_공개_데모는_1부터_9장까지_완성된_canonical_보고서다(
    canonical_report,
) -> None:
    report = canonical_report
    validation = validate_publishable(report)

    assert validation.publishable, validation.reasons
    assert tuple(section.cell for section in report.sections) == CANONICAL_SECTION_IDS
    assert all(section.is_filled for section in report.sections)
    assert report.job == ""
    assert report.requirements == []


def test_본문과_표의_출처번호는_공식_출처_등록부에_존재한다(
    canonical_report,
) -> None:
    report = canonical_report
    citations = {source.number: source for source in report.citations}
    cited: set[int] = set()

    for section in report.sections:
        for _sentence, cite in section.prose_lines:
            number = citation_number(cite)
            assert number is not None, f"{section.cell}: 출처 번호 없음"
            cited.add(int(number))
        for table in section.tables:
            number = citation_number(table.cite)
            assert number is not None, f"{section.cell}: 표 출처 번호 없음"
            cited.add(int(number))

    assert cited
    assert cited <= citations.keys()
    assert all(source.is_canonical_official for source in citations.values())


def test_섹션의_fact_id는_사실원장과_정확히_결속된다(canonical_report) -> None:
    report = canonical_report
    ledger_ids = {fact.fact_id for fact in report.fact_records}
    section_ids = {
        fact_id for section in report.sections for fact_id in section.fact_ids
    }

    assert section_ids == ledger_ids
    assert len(section_ids) == len(report.fact_records)


def test_요약은_완성된_본문_fact_id의_부분집합만_참조한다(
    canonical_report,
) -> None:
    report = canonical_report
    ledger_ids = {fact.fact_id for fact in report.fact_records}
    summary_ids = {
        fact_id for item in report.summary_items for fact_id in item.fact_ids
    }

    assert 3 <= len(report.summary_items) <= 5
    assert summary_ids
    assert summary_ids < ledger_ids


def test_억원_공개표는_원단위와_표시값을_분리한다(canonical_report) -> None:
    tables = [
        table
        for section in canonical_report.sections
        for table in section.tables
        if table.display_unit == "억원"
    ]

    assert tables
    for table in tables:
        assert "단위: 억원" in table.caption
        assert table.raw_rows
        assert table.scale_divisor == "100000000"
        for row in table.rows:
            assert all(_DISPLAY_NUMBER.fullmatch(value) for value in row[1:])
            assert all("원" not in value and "억" not in value for value in row[1:])
        assert any(
            raw != shown
            for raw_row, shown_row in zip(table.raw_rows, table.rows)
            for raw, shown in zip(raw_row[1:], shown_row[1:])
        )


@pytest.mark.parametrize("legacy_company", ["파마리서치", "우리엔", "플래티어"])
def test_구형_부분_보고서는_직접_입력해도_출고되지_않는다(
    legacy_company: str,
) -> None:
    pipeline = DemoPipeline()
    user_input = UserInput(
        company=legacy_company, job="", region="", posting_text=""
    )
    card = pipeline.find_company(user_input)
    if card is None:
        pytest.skip("로컬 파일럿 기록 없음")

    result = pipeline.run(user_input, card)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.charged is False
