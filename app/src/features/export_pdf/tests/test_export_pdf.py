from __future__ import annotations

import hashlib
import io
import urllib.parse
from dataclasses import fields, is_dataclass, replace

import pdfplumber
import pytest
from pypdf import PdfReader
from pypdf.generic import IndirectObject
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate

from src.core.constants import section_display_heading
from src.features.export_pdf import constants
from src.features.export_pdf.logic import (
    PDFGenerationError,
    _add_report_table,
    _normalize_pdf_text,
    _single_line_pdf_text,
    _styles,
    build_ascii_filename,
    build_content_disposition,
    build_download_filename,
    build_pdf,
    source_summary,
)
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
)
from src.features.provenance.sources import render_sources
from src.features.report_standard.constants import SECTION_SPECS


def _report(*, generated_at: str = "2026-08-19") -> Report:
    """PDF 성공 경로는 실제 출고 게이트를 통과한 1~9장 보고서만 쓴다."""

    return replace(build_demo_report(), generated_at=generated_at)


def _text(pdf: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def _flowables_pdf(story: list[object]) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=62,
        rightMargin=62,
        topMargin=62,
        bottomMargin=62,
    )
    document.build(story)
    return output.getvalue()


def _table_pdf(table: ReportTable) -> bytes:
    story: list[object] = []
    _add_report_table(story, table, _styles(), A4[0] - 124)
    return _flowables_pdf(story)


def _section_pdf(section: ReportSection) -> bytes:
    story: list[object] = []
    styles = _styles()
    story.append(Paragraph(section.title, styles["heading"]))
    for prose, _cite in section.prose_lines:
        story.append(Paragraph(prose, styles["body"]))
    for table in section.tables:
        _add_report_table(story, table, styles, A4[0] - 124)
    return _flowables_pdf(story)


def _font_objects(reader: PdfReader) -> list[object]:
    found: list[object] = []
    for page in reader.pages:
        resources = page["/Resources"]
        if isinstance(resources, IndirectObject):
            resources = resources.get_object()
        fonts = resources.get("/Font", {})
        if isinstance(fonts, IndirectObject):
            fonts = fonts.get_object()
        for value in fonts.values():
            found.append(value.get_object() if isinstance(value, IndirectObject) else value)
    return found


def _outline_titles(items: list[object]) -> list[str]:
    titles: list[str] = []
    for item in items:
        if isinstance(item, list):
            titles.extend(_outline_titles(item))
        else:
            titles.append(getattr(item, "title", ""))
    return titles


def test_pdf는_검색가능한_한글_본문과_모든_canonical_요소를_보존한다() -> None:
    report = _report()
    pdf = build_pdf(report)
    text = _text(pdf)

    assert pdf.startswith(b"%PDF-")
    expected_fragments = [
        "(주)진영",
        "분석 보고서",
        "기준일 2026.08.19",
        "핵심 요약",
        "1. 기업 정체성",
        "2. 사업 구조와 수익 모델",
        "2026년 상반기 매출 구성",
        "단위: %",
        "가구용 시트·엣지",
        "70.0",
        "4. 3개년 주요 변화와 실행",
        "완료 사업연도 연결 실적 (단위: 억원)",
        "5. 당면 과제와 대응",
        "6. 성장 전략",
        "9. 경쟁사 대비 핵심 경쟁력",
        "부록. 출처와 검증 상태",
        "주식회사 진영 사업보고서 (2025.12)",
        "주식회사 LX하우시스 사업보고서 (2025.12)",
    ]
    for fragment in expected_fragments:
        assert fragment in text

    for removed in (
        "AI를 사용",
        "사용한 소스:",
        "원문 보기",
        "자기소개서",
        "면접 질문",
        "채용공고 요구 역량",
        "급여",
        "복지",
    ):
        assert removed not in text

    headings = [f"{spec.display_number}. {spec.title}" for spec in SECTION_SPECS]
    assert [text.index(heading) for heading in headings] == sorted(
        text.index(heading) for heading in headings
    )
    assert text.index(headings[-1]) < text.index("부록. 출처와 검증 상태")


def test_첫_페이지는_회사명과_보고서명을_줄바꿈해_표시한다() -> None:
    pdf = build_pdf(_report())
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        first_page = document.pages[0].extract_text() or ""
        first_page_words = document.pages[0].extract_words()
        second_page = document.pages[1].extract_text() or ""

    assert "(주)진영\n분석 보고서" in first_page
    assert "기준일 2026.08.19" in first_page
    assert "핵심 요약" in first_page
    assert "1. 기업 정체성" not in first_page
    assert "1. 기업 정체성" in second_page
    company_word = next(word for word in first_page_words if word["text"] == "(주)진영")
    meta_word = next(word for word in first_page_words if word["text"] == "기준일")
    assert abs(company_word["x0"] - meta_word["x0"]) < 1


def test_pdf는_제목_언어_제목표시_outline과_내장_한글폰트를_가진다() -> None:
    pdf = build_pdf(_report())
    reader = PdfReader(io.BytesIO(pdf), strict=True)

    assert reader.metadata.title == "(주)진영 분석 보고서"
    assert reader.root_object["/Lang"] == "ko-KR"
    assert bool(reader.root_object["/ViewerPreferences"]["/DisplayDocTitle"]) is True
    assert reader.root_object["/PageMode"] == "/UseOutlines"
    assert "/StructTreeRoot" not in reader.root_object

    outline_titles = _outline_titles(reader.outline)
    assert "핵심 요약" in outline_titles
    assert "부록. 출처와 검증 상태" in outline_titles
    assert all(
        any(title.startswith(f"{spec.display_number}. {spec.title}") for title in outline_titles)
        for spec in SECTION_SPECS
    )

    fonts = _font_objects(reader)
    assert fonts
    assert all("Helvetica" not in str(font.get("/BaseFont", "")) for font in fonts)
    branded_fonts = [font for font in fonts if "Freesentation" in str(font.get("/BaseFont", ""))]
    assert branded_fonts
    embedded: list[bool] = []
    for font in branded_fonts:
        descriptor = font.get("/FontDescriptor")
        if descriptor is None:
            continue
        descriptor = descriptor.get_object() if isinstance(descriptor, IndirectObject) else descriptor
        embedded.append(any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")))
    assert embedded and all(embedded)


def test_출처는_내부_수집과정_대신_원문과_검증상태를_표로_낸다() -> None:
    text = _text(build_pdf(_report()))

    assert "[출처]" not in text
    assert text.count("부록. 출처와 검증 상태") == 1
    assert "본문의 번호가 아래 원문을 가리킵니다." in text
    assert "2026-03-18 공시" in text
    assert "2026-08-13 공시" in text
    assert "Company Overview" in text
    assert "수집" not in text


def test_부록은_본문과_분리된_새_페이지에서_전체_출처_맥락을_보여준다() -> None:
    with pdfplumber.open(io.BytesIO(build_pdf(_report()))) as document:
        pages = [page.extract_text() or "" for page in document.pages]

    ninth_page = next(
        index for index, text in enumerate(pages) if "9. 경쟁사 대비 핵심 경쟁력" in text
    )
    assert ninth_page < len(pages) - 1
    assert all("부록. 출처와 검증 상태" not in text for text in pages[:-1])
    assert "부록. 출처와 검증 상태" in pages[-1]
    assert "주식회사 진영 반기보고서 (2026.06)" in pages[-1]
    assert "주식회사 LX하우시스 사업보고서 (2025.12)" in pages[-1]


def test_아주_긴_본문과_12열_표는_LayoutError_없이_모든_글자를_보존한다() -> None:
    long_prose = "기본문검증 " * 900
    long_cell = "긴표값" * 700
    headers = [f"열{index}" for index in range(12)]
    row = [f"{index}-{long_cell}" for index in range(12)]
    section = ReportSection(
        cell="identity",
        title="긴 내용",
        display_number="1",
        prose_lines=[(long_prose, "")],
        tables=[ReportTable(caption="12열 긴문 표", headers=headers, rows=[row])],
    )

    text = _text(_section_pdf(section))
    compact = text.replace("\n", "")
    assert "기본문검증 " * 30 in compact
    assert "12열 긴문 표" in text
    assert all(header in text for header in headers)
    # 좁은 12열에서는 추출기가 셀 안에도 공백을 삽입하므로 단일 글자로 보존량을 센다.
    assert compact.count("긴") >= 700 * 12


def test_numeric_표는_첫열을_제외한_머리글과_값을_오른쪽_정렬한다() -> None:
    table = ReportTable(
        caption="금액 표",
        headers=["항목", "금액"],
        rows=[["매출", "987654321"]],
        numeric=True,
    )
    with pdfplumber.open(io.BytesIO(_table_pdf(table))) as document:
        words = [word for page in document.pages for word in page.extract_words()]

    amount_header = max(
        (word for word in words if word["text"] == "금액"),
        key=lambda word: word["x1"],
    )
    amount = next(word for word in words if word["text"] == "987654321")
    label = next(word for word in words if word["text"] == "매출")
    assert amount_header["x1"] > 520
    assert amount["x1"] > 520
    assert label["x0"] < 80


def test_표지_소스_요약은_수집현황이_아니라_실제_citations만_중복없이_센다() -> None:
    report = _report()
    assert source_summary(report) == "전자공시(DART) 4건 · 기타 자료 2건"
    assert render_sources(report.citations[:-1]) in render_sources(report.citations)

    no_citations = Report(
        company="회사",
        job="직무",
        corp_type="비상장사",
        grade=Grade.COMPLETE,
        sections=[],
        sources=[SourceStatus(name="전자공시", state="ok", detail="성공")],
    )
    assert source_summary(no_citations) == "저장된 출처 없음"


def test_생성일이_깨졌다면_그_문자열이나_로컬_오늘을_공개하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.features.export_pdf.logic.clock.today_kst",
        lambda: __import__("datetime").date(2031, 2, 3),
    )
    text = _text(build_pdf(_report(generated_at="확정 날짜")))
    assert "확정 날짜" not in text
    assert "2031-02-03" not in text
    assert "기준일 2026.08.19" in text


@pytest.mark.parametrize(
    "company",
    [
        '네이버\r\nX-Evil: yes/%*?"<>|',
        "../../회사",
        "   ",
        "evil\u202efdp.exe",
        "zero\u200bwidth",
        "isolate\u2066name\u2069",
    ],
)
def test_파일명과_Content_Disposition은_헤더주입과_경로문자를_막는다(company: str) -> None:
    report = Report(
        company=company,
        job="매니지먼트",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
    )
    filename = build_download_filename(report)
    header = build_content_disposition(filename)

    assert filename.endswith("-company-analysis.pdf")
    assert "매니지먼트" not in filename
    assert "\r" not in filename and "\n" not in filename
    assert "\u202e" not in filename and "\u200b" not in filename
    assert "\u2066" not in filename and "\u2069" not in filename
    assert not any(char in filename for char in '\\/:*?"<>|%')
    assert "\r" not in header and "\n" not in header
    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in header
    encoded = header.split("filename*=UTF-8''", 1)[1]
    assert urllib.parse.unquote(encoded) == filename
    assert build_ascii_filename(filename).isascii()


@pytest.mark.parametrize("reserved", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9", "con.txt"])
def test_ASCII_fallback은_Windows_예약_장치명을_쓰지_않는다(reserved: str) -> None:
    assert build_ascii_filename(f"{reserved}_분석_보고서.pdf") == "analysis-report.pdf"


def test_생성기_내부_오류는_보고서_내용이_없는_단일_공개오류로_정규화된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_report: Report) -> bytes:
        raise ValueError("C:/secret/사용자안내")

    monkeypatch.setattr("src.features.export_pdf.logic._build_pdf", fail)
    with pytest.raises(PDFGenerationError, match="PDF 보고서를 만들지 못했습니다") as caught:
        build_pdf(_report())
    assert "secret" not in str(caught.value)


def test_partial_legacy_보고서는_PDF를_만들지_않고_fail_closed한다() -> None:
    invalid = Report(
        company="부분 보고서 회사",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
    )
    with pytest.raises(PDFGenerationError):
        build_pdf(invalid)


def test_폰트_라이선스_원문을_배포물과_함께_둔다() -> None:
    license_text = (constants.FONT_DIR / "OFL.txt").read_text(encoding="utf-8")
    readme = (constants.FONT_DIR / "README.md").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "Copyright (c) 2024 PT& / 피티앤" in license_text
    assert "https://openfontlicense.org" in readme
    assert constants.FONT_REGULAR_PATH.stat().st_size > 100_000
    assert constants.FONT_SEMIBOLD_PATH.stat().st_size > 100_000


def test_내장폰트_미지원_표시문자는_의미_같은_검색가능_텍스트로_정규화한다() -> None:
    company = "\ufeff카카오페이"
    body = "모델링・개발・튜닝 ⚠️ 확인\ufe0f"
    assert _single_line_pdf_text(company) == "카카오페이"
    assert _normalize_pdf_text(body) == "모델링·개발·튜닝 주의: 확인"

    normalized = _normalize_pdf_text(f"{company} {body} 참고")
    for index, path in enumerate((constants.FONT_REGULAR_PATH, constants.FONT_SEMIBOLD_PATH)):
        font = TTFont(f"CmapCoverage{index}", str(path))
        cmap = font.face.charWidths
        assert all(char.isspace() or ord(char) in cmap for char in normalized)


def test_NUL_bell_formfeed와_surrogate는_metadata_outline_body에_남지_않는다() -> None:
    unsafe_title = "nul\x00bell\x07company\ud800\rnext"
    unsafe_body = "inside\x00value\x07end\ud800\x0cnext"
    assert _single_line_pdf_text(unsafe_title) == "nul bell company? next"
    assert _normalize_pdf_text(unsafe_body) == "inside value end? next"

    pdf = build_pdf(_report())
    reader = PdfReader(io.BytesIO(pdf), strict=True)
    text = _text(pdf)
    document_strings = [reader.metadata.title or "", text]
    document_strings.extend(_outline_titles(reader.outline))
    assert not any(
        char in value
        for value in document_strings
        for char in ("\x00", "\x07", "\x0c", "\ud800")
    )


def test_같은_canonical_보고서는_항상_동일한_PDF_bytes와_hash를_만든다() -> None:
    report = _report()
    first = build_pdf(report)
    second = build_pdf(report)

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_현재_canonical_demo_보고서의_모든_model문자는_내장폰트로_검색가능하다() -> None:
    from src.features.pipeline.demo import DemoPipeline, available_companies
    from src.features.pipeline.port import UserInput

    def strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if is_dataclass(value):
            return [
                item
                for field in fields(value)
                for item in strings(getattr(value, field.name))
            ]
        if isinstance(value, dict):
            return [item for pair in value.items() for part in pair for item in strings(part)]
        if isinstance(value, (list, tuple, set)):
            return [item for part in value for item in strings(part)]
        return []

    regular = TTFont("DemoSweepRegular", str(constants.FONT_REGULAR_PATH))
    semibold = TTFont("DemoSweepSemibold", str(constants.FONT_SEMIBOLD_PATH))
    cmap = set(regular.face.charWidths) | set(semibold.face.charWidths)
    reports: list[Report] = []
    model_strings = 0
    available = [row for row in available_companies() if row["is_report"] == "1"]
    for row in available:
        pipeline = DemoPipeline()
        user_input = UserInput(
            company=row["company"],
            job=row["job"],
            region="",
            posting_text="",
        )
        result = pipeline.run(user_input, pipeline.find_company(user_input))
        assert result.report is not None
        report = result.report
        reports.append(report)
        values = strings(report)
        model_strings += len(values)
        missing = {
            char
            for value in values
            for char in _normalize_pdf_text(value)
            if not char.isspace() and ord(char) not in cmap
        }
        assert missing == set(), (
            report.company,
            sorted(f"U+{ord(char):04X}" for char in missing),
        )

        pdf = build_pdf(report)
        extracted = _text(pdf)
        assert "\x00" not in extracted
        assert not any(char in extracted for char in ("⚠", "・", "附", "\ufeff"))

        with pdfplumber.open(io.BytesIO(pdf)) as document:
            page_texts = [page.extract_text() or "" for page in document.pages]
        assert f"{report.company}\n분석 보고서" in page_texts[0]
        if report.sections:
            first_section = report.sections[0]
            first_heading = (
                f"{first_section.display_number}. {first_section.title}"
                if first_section.display_number
                else section_display_heading(first_section.cell, first_section.title)
            )
            assert first_heading not in page_texts[0]
            assert any(first_heading in page for page in page_texts[1:])
        assert "주의사항" not in "\n".join(page_texts)

    assert len(reports) == len(available) == 1
    assert model_strings >= 50
