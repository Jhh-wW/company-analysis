"""빈 장 안내는 legacy v2 표시만 보완하고 FULL/v1을 건드리지 않는다."""

import io
from dataclasses import replace

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4

from src.features.export_pdf import logic as pdf_logic
from src.features.export_pdf.tests.test_v2_public_projection import _report, _v2_full_report
from src.features.export_pdf.tests.test_export_pdf import _report as _v1_report
from src.features.export_pdf.tests.test_export_pdf import _flowables_pdf
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
    assert f"1.{''.join(notice.split())}" not in result
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


def test_sealed_notice_paragraph_keeps_the_sealed_number():
    """봉인에 안내문이 있으면 다른 문단과 같은 번호 열로 그리는지 확인한다."""
    notice = "확인된 자료가 부족해 이 장은 비어 있습니다."
    projection = _v2_full_report().public_projection
    display = replace(
        projection.sections[-1].display,
        paragraphs=(("1.", notice),), sentences=((notice, ""),),
        tables=(), visuals=(), period_summary=None,
    )
    story = [pdf_logic._OutlineAnchor("root", "본문", level=0)]
    pdf_logic._add_projection_section(
        story, display, pdf_logic._styles(), A4[0] - 124, "section-9",
    )

    assert f"1.{''.join(notice.split())}" in _text(_flowables_pdf(story))


def test_available_pdf_shows_missing_prose_and_table_verification_limits():
    reasons = [
        "숫자·날짜 문장의 항목·기간·계산 관계를 원문과 맞춰 확인하지 못해 제외했습니다 (본문 2개). 자료 자체가 없다는 뜻은 아닙니다.",
        "원문과 계획·조건·공식 설명의 범위가 일치하는지 확인하지 못한 문장을 제외했습니다 (도식 1개). 자료 자체가 없다는 뜻은 아닙니다.",
        "표와 도식은 아직 하나씩 확인하지 못했습니다. 숫자를 그대로 인용하기 전에 부록의 원문을 함께 확인해 주세요.",
        "SECRET 내부 임계값",
    ]
    report = replace(_report(), publication_policy="evidence-available-v1", shortfall_reasons=reasons)
    original_lines = [list(section.prose_lines) for section in report.sections]
    text = _text(pdf_logic.build_pdf(report))
    assert "부분완성보고서" in text
    assert "일부숫자·날짜설명은원문의항목·기간·계산관계" in text
    assert "일부계획·조건·공식설명은원문과설명범위" in text
    assert "표·도식확인범위:개별항목의검증은완료되지않았습니다" in text
    assert "부록의원문과대조해" in text
    assert "SECRET" not in text
    assert [section.prose_lines for section in report.sections] == original_lines
    assert report.shortfall_reasons == reasons
