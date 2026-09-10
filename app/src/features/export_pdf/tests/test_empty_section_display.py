"""빈 장 안내는 legacy v2 표시만 보완하고 FULL/v1을 건드리지 않는다."""

import io
from dataclasses import replace

from pypdf import PdfReader

from src.features.export_pdf import logic as pdf_logic
from src.features.export_pdf.tests.test_v2_public_projection import _report, _v2_full_report
from src.features.export_pdf.tests.test_export_pdf import _report as _v1_report
from src.core.report_display import empty_section_notice


def _text(data):
    return "".join("".join(page.extract_text().split()) for page in PdfReader(io.BytesIO(data)).pages)


def test_adds_neutral_notice_only_to_empty_section_and_preserves_body_tables_citations():
    seed = _v2_full_report()
    report = replace(_report(), citations=seed.citations)
    empty = replace(report.sections[0], cell="future_strategy", title="성장 전략", display_number="6", lines=[], prose_lines=[], prose_paragraphs=[], fact_ids=[])
    tables = [table for section in seed.sections for table in section.tables]
    assert tables and seed.citations
    table_section = replace(empty, cell="operations_partners", title="표가 있는 장", tables=tables, display_number="7")
    report = replace(report, sections=[*report.sections, empty, table_section])
    notice = empty_section_notice(report, empty)
    result = _text(pdf_logic.build_pdf(report))
    assert result.count("".join(notice.split())) == 1
    assert "공식자료로확인한공개본문문장이다." in result
    assert all("".join(table.caption.split()) in result for table in tables)
    assert report.sections[-1].tables == tables
    assert report.citations == seed.citations


def test_displays_legacy_empty_section_limits_without_inventing_a_cause():
    empty = replace(_report().sections[0], cell="future_strategy", title="성장 전략", display_number="6", lines=[], prose_lines=[], prose_paragraphs=[], fact_ids=[], empty_reason="확인한 자료 범위의 한계입니다.", guidance_lines=["별도로 명시된 한계입니다."])
    report = replace(_report(), sections=[*_report().sections, empty])
    text = _text(pdf_logic.build_pdf(report))
    assert text.count("확인한자료범위의한계입니다.") == 1
    assert text.count("별도로명시된한계입니다.") == 1
    assert "어느원인인지는확인할수없습니다" not in text


def test_full_and_v1_pdf_do_not_call_empty_section_notice(monkeypatch):
    def forbidden(*args):
        raise AssertionError("봉인 또는 v1에서 새 안내를 만들었습니다")

    monkeypatch.setattr(pdf_logic, "empty_section_notice", forbidden)
    assert pdf_logic.build_pdf(_v2_full_report()).startswith(b"%PDF")
    assert pdf_logic.build_pdf(_v1_report()).startswith(b"%PDF")
