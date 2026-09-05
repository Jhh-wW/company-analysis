"""정본 디자인 문서의 PDF 좌표·글꼴·내용 계약을 고정한다.

PNG 해시는 렌더러와 OS에 따라 달라질 수 있으므로 여기서는 고정하지 않고,
출력 구조와 실제 text glyph의 위치·크기를 검사한다.
"""

from __future__ import annotations

import io
import hashlib
import math
from dataclasses import replace
from typing import Any, Callable

import pdfplumber
import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4

from src.features.export_pdf import constants
from src.features.export_pdf.logic import (
    _FlowGraphic,
    _split_wide_table,
    build_download_filename,
    build_pdf,
)
from src.features.export_pdf.release import prepare_pdf_bytes
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import Report, ReportTable
from src.features.report_standard.constants import SECTION_SPECS
from src.features.report_standard.visualization import TableVisualization


PT_PER_MM = 72.0 / 25.4


@pytest.fixture(scope="module")
def demo_report() -> Report:
    return build_demo_report()


@pytest.fixture(scope="module")
def demo_pdf(demo_report: Report) -> bytes:
    return build_pdf(demo_report)


def _mm(points: float) -> float:
    return points / PT_PER_MM


def _normalized_page_text(page: Any) -> str:
    return " ".join((page.extract_text() or "").split())


def _words(page: Any) -> list[dict[str, Any]]:
    return page.extract_words(
        extra_attrs=["fontname", "size", "non_stroking_color"]
    )


def _word(
    words: list[dict[str, Any]],
    text: str,
    *,
    where: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    matches = [
        word
        for word in words
        if word["text"] == text and (where is None or where(word))
    ]
    assert matches, f"PDF에서 {text!r} glyph를 찾지 못했습니다."
    return min(matches, key=lambda word: (float(word["top"]), float(word["x0"])))


def _assert_font_size(word: dict[str, Any], low: float, high: float) -> None:
    # PDF 실수 인코딩의 아주 작은 차이는 허용하되 문서 범위는 유지한다.
    size = float(word["size"])
    assert low - 0.02 <= size <= high + 0.02, (word["text"], size)


def test_모든_페이지는_a4로_출력된다(demo_pdf: bytes) -> None:
    reader = PdfReader(io.BytesIO(demo_pdf))

    assert len(reader.pages) >= 2
    for page in reader.pages:
        assert float(page.mediabox.width) == pytest.approx(A4[0], abs=0.02)
        assert float(page.mediabox.height) == pytest.approx(A4[1], abs=0.02)
        assert int(page.get("/Rotate", 0)) % 360 == 0


def test_표지_제목과_핵심요약_glyph는_정본_영역에_있다(
    demo_report: Report,
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        words = _words(document.pages[0])

    title = _word(words, demo_report.company)
    title_top_mm = _mm(float(title["top"]))
    assert 72.0 <= title_top_mm <= 118.0
    _assert_font_size(title, 34.0, 34.0)

    summary_heading = [
        _word(words, "핵심"),
        _word(words, "요약"),
    ]
    summary_top_pt = min(float(word["top"]) for word in summary_heading)
    summary_top_mm = _mm(summary_top_pt)
    summary_glyphs = [
        word
        for word in words
        if summary_top_pt <= float(word["top"]) < 270 * PT_PER_MM
    ]
    summary_bottom_mm = max(_mm(float(word["bottom"])) for word in summary_glyphs)

    assert summary_top_mm >= 190.0
    assert summary_bottom_mm <= 262.0


def test_pdf_메타데이터는_보고서_회사와_일치한다(
    demo_report: Report,
    demo_pdf: bytes,
) -> None:
    metadata = PdfReader(io.BytesIO(demo_pdf)).metadata

    assert metadata is not None
    assert demo_report.company in (metadata.title or "")
    assert demo_report.company in (metadata.author or "")
    assert demo_report.company in (metadata.subject or "")


def test_2쪽_이후_상단띠와_푸터는_회사_현재장_기준일_쪽번호를_보여준다(
    demo_report: Report,
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        assert len(document.pages) >= 2
        for page_number, page in enumerate(document.pages[1:], start=2):
            text = _normalized_page_text(page)
            words = _words(page)

            assert f"{demo_report.company} 분석 보고서" in text
            assert "기준일 2026.08.19" in text
            assert demo_report.company in text
            assert str(page_number) in text

            header_date = _word(
                words,
                "기준일",
                where=lambda word: float(word["top"]) < constants.PAGE_HEADER_HEIGHT_PT,
            )
            footer_company = _word(
                words,
                demo_report.company,
                where=lambda word: _mm(float(word["top"])) > 275.0,
            )
            footer_number = _word(
                words,
                str(page_number),
                where=lambda word: _mm(float(word["top"])) > 275.0,
            )
            _assert_font_size(header_date, 8.0, 8.0)
            _assert_font_size(footer_company, 6.6, 6.8)
            _assert_font_size(footer_number, 6.6, 6.8)


def test_2쪽_이후_상단띠와_흰_glyph는_렌더_PNG에_실제_잉크가_있다(
    demo_report: Report,
    demo_pdf: bytes,
) -> None:
    candidate = prepare_pdf_bytes(demo_pdf, render_scale=1.5)
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        assert len(candidate.pages) == len(document.pages)
        for page_number, (page, rendered) in enumerate(
            zip(document.pages[1:], candidate.pages[1:], strict=True),
            start=2,
        ):
            words = _words(page)
            company = _word(
                words,
                demo_report.company,
                where=lambda word: float(word["top"]) < 45.0,
            )
            report_date = _word(
                words,
                "기준일",
                where=lambda word: float(word["top"]) < 45.0,
            )
            image = Image.open(io.BytesIO(rendered.png_bytes)).convert("RGB")
            scale_x = image.width / float(page.width)
            scale_y = image.height / float(page.height)

            def glyph_ink(word: dict[str, Any]) -> int:
                crop = image.crop(
                    (
                        max(0, math.floor(float(word["x0"]) * scale_x) - 2),
                        max(0, math.floor(float(word["top"]) * scale_y) - 2),
                        min(image.width, math.ceil(float(word["x1"]) * scale_x) + 2),
                        min(image.height, math.ceil(float(word["bottom"]) * scale_y) + 2),
                    )
                ).convert("L")
                return sum(crop.histogram()[:230])

            assert glyph_ink(company) >= 12, page_number
            assert glyph_ink(report_date) >= 12, page_number

            # 상단 22pt 전체가 검은 띠인지 실제 렌더 픽셀로 확인한다.
            band = image.crop(
                (
                    0,
                    0,
                    image.width,
                    math.ceil(constants.PAGE_HEADER_HEIGHT_PT * scale_y),
                )
            ).convert("L")
            assert sum(band.histogram()[:40]) >= image.width * 20, page_number


def test_장제목_카드_소제목_카드본문_표의_글꼴크기가_정본_범위다(
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        second_page_words = _words(document.pages[1])
        operations_page = next(
            page
            for page in document.pages
            if "자원순환 운영 구조" in _normalized_page_text(page)
        )
        operations_words = _words(operations_page)

    chapter_badge = _word(second_page_words, "1.")
    chapter_heading = _word(
        second_page_words,
        "기업",
        where=lambda word: abs(float(word["size"]) - 20.0) < 0.05,
    )
    card_heading = _word(second_page_words, "회사")
    card_body = _word(second_page_words, "진영은")
    table_header = _word(operations_words, "주체")

    _assert_font_size(chapter_badge, 9.0, 9.0)
    _assert_font_size(chapter_heading, 20.0, 20.0)
    _assert_font_size(card_heading, 11.0, 11.0)
    assert card_heading["non_stroking_color"] == pytest.approx((1.0, 1.0, 1.0))
    _assert_font_size(card_body, 8.2, 8.6)
    _assert_font_size(table_header, 7.5, 7.8)


def test_구성비와_추세는_값_단위_자료안내를_보이고_원표를_중복하지_않는다(
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        text = " ".join(_normalized_page_text(page) for page in document.pages)

    assert "2026년 상반기 매출 구성 (단위: %)" in text
    assert "완료 사업연도 연결 실적 (단위: 억원)" in text
    assert "매출 (억원)" in text
    assert "영업이익(손실) (억원)" in text
    assert "원문 비율을 소수 첫째 자리로 반올림해 표시" in text
    assert "원값을 억원 단위로 환산해 표시" in text

    # 그래프와 원표가 둘 다 나오면 같은 값이 두 번씩 추출된다.
    for value in ("70.0%", "9.1%", "6.3%", "14.6%"):
        assert text.count(value) == 1
    for value in ("324.2", "-26.6", "342.2", "-29.4", "309.0", "-23.6"):
        assert text.count(value) == 1
    for year in ("2023", "2024", "2025"):
        assert year in text


def test_장제목은_첫_내용블록과_같은_페이지에_놓인다(
    demo_report: Report,
    demo_pdf: bytes,
) -> None:
    from src.features.report_standard.section_content import section_content_blocks

    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        pages = [_normalized_page_text(page) for page in document.pages]

    for section in demo_report.sections:
        heading = f"{section.display_number}. {section.title}"
        first_block = section_content_blocks(demo_report, section)[0]
        heading_page = next(index for index, text in enumerate(pages) if heading in text)
        assert first_block.title in pages[heading_page], (heading, first_block.title)


def test_자원순환_운영구조는_그래프가_아닌_표로_남는다(demo_pdf: bytes) -> None:
    expected_rows = [
        [
            "네체로",
            "생산·운영",
            "종속회사",
            "폐산·폐알칼리 지정폐기물 처리",
            "운영 중",
        ],
    ]
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        page = next(
            candidate
            for candidate in document.pages
            if "자원순환 운영 구조" in _normalized_page_text(candidate)
        )
        text = _normalized_page_text(page)

    assert "자원순환 운영 구조" in text
    headers = ["주체", "가치사슬 단계", "관계 유형", "확인된 역할", "현재 상태"]
    assert all(header in text for header in headers)
    for row in expected_rows:
        assert all(cell in text for cell in row)
    assert "가동 준비" not in text

    # 바깥 좌우선을 없앤 토큰 v1 표는 pdfplumber의 기본 격자 추출 대상이
    # 아닐 수 있다. 공개 글자와 행 순서는 위에서 확인하고, 좌우 바깥선이
    # 실제로 생기지 않았는지는 아래 선 좌표로 고정한다.
    table_lines = [
        line
        for line in page.lines
        if float(line["top"]) > 100 and float(line["bottom"]) < 760
    ]
    assert not any(
        abs(float(line["x0"]) - float(line["x1"])) < 0.01
        and abs(float(line["x0"]) - constants.PAGE_MARGIN_PT) < 1.0
        for line in table_lines
    )


def test_출처표의_영문_원문위치는_한글자_고아줄로_갈라지지_않는다(
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        source_page = next(
            page
            for page in document.pages
            if "부록. 출처와 검증 상태" in _normalized_page_text(page)
        )
        rows = [
            row
            for table in source_page.extract_tables()
            for row in table
            if len(row) >= 5 and "Company Overview" in (row[1] or "")
        ]

    assert len(rows) == 1
    assert rows[0][4] == "Company > Overview"


def test_넓은_표는_첫열을_반복하고_5열_이하로_모든_셀을_보존한다() -> None:
    headers = ["구분", *[f"지표-{index}" for index in range(1, 11)]]
    rows = [
        [f"행-{row_index}", *[f"값-{row_index}-{column}" for column in range(1, 11)]]
        for row_index in range(1, 4)
    ]
    raw_rows = [
        [f"raw-{row_index}-0", *[f"raw-{row_index}-{column}" for column in range(1, 11)]]
        for row_index in range(1, 4)
    ]
    original = ReportTable(
        caption="넓은 계약 표",
        headers=headers,
        rows=rows,
        cite="[9]",
        numeric=True,
        raw_rows=raw_rows,
        scale_divisor="100",
        scale_places=1,
        display_unit="억원",
        presentation="table",
        evidence_rows=[f"원문-{index}" for index in range(1, 4)],
    )

    chunks = _split_wide_table(original)

    assert len(chunks) == 3
    assert all(len(chunk.headers) <= 5 for chunk in chunks)
    assert all(chunk.headers[0] == headers[0] for chunk in chunks)
    assert all(chunk.presentation == "table" for chunk in chunks)
    assert chunks[0].caption == original.caption
    assert chunks[-1].caption.endswith("(계속 3/3)")

    reconstructed_headers = [headers[0], *[cell for chunk in chunks for cell in chunk.headers[1:]]]
    assert reconstructed_headers == headers
    for row_index, row in enumerate(rows):
        assert all(chunk.rows[row_index][0] == row[0] for chunk in chunks)
        reconstructed = [row[0], *[cell for chunk in chunks for cell in chunk.rows[row_index][1:]]]
        assert reconstructed == row
    for row_index, raw_row in enumerate(raw_rows):
        reconstructed = [
            raw_row[0],
            *[cell for chunk in chunks for cell in chunk.raw_rows[row_index][1:]],
        ]
        assert reconstructed == raw_row


def test_다운로드_파일명은_회사_slug_계약을_따른다(demo_report: Report) -> None:
    assert build_download_filename(demo_report) == "주-진영-company-analysis.pdf"

    renamed = replace(demo_report, company="  테스트 회사 / R&D  ")
    filename = build_download_filename(renamed)
    assert filename == "테스트-회사-r-d-company-analysis.pdf"
    assert filename == constants.FILENAME_PATTERN.format(company_slug="테스트-회사-r-d")
    assert not any(character.isspace() for character in filename)
    assert not any(character in filename for character in '\\/:*?"<>|%')


def test_디자인_토큰_v1_색값은_한벌로_고정된다() -> None:
    assert constants.COLOR_INK == "#111111"
    assert constants.COLOR_MUTED == "#666666"
    assert constants.COLOR_WEAK == "#999999"
    assert constants.COLOR_LINE == "#CCCCCC"
    assert constants.COLOR_SURFACE == "#F5F5F5"
    assert constants.COLOR_HEADER == "#111111"
    assert constants.CHART_PALETTE == (
        "#B3B3B3",
        "#8C8C8C",
        "#666666",
        "#444444",
        "#222222",
    )


def test_표지는_전면_검정이고_제목은_34pt_흰글자다(
    demo_report: Report,
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        page = document.pages[0]
        background = next(
            rect
            for rect in page.rects
            if float(rect["width"]) == pytest.approx(float(page.width), abs=0.05)
            and float(rect["height"]) == pytest.approx(float(page.height), abs=0.05)
        )
        title = _word(_words(page), demo_report.company)

    assert float(background["x0"]) == pytest.approx(0.0, abs=0.05)
    assert float(background["top"]) == pytest.approx(0.0, abs=0.05)
    assert background["non_stroking_color"] == pytest.approx(
        (17 / 255,) * 3, abs=1e-5
    )
    assert title["non_stroking_color"] == pytest.approx((1.0, 1.0, 1.0))
    _assert_font_size(title, 34.0, 34.0)


def test_장제목_검정배지는_9장과_부록에_모두_있다(demo_pdf: bytes) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        badges = [
            rect
            for page in document.pages
            for rect in page.rects
            if float(rect["height"])
            == pytest.approx(constants.SECTION_BADGE_SIZE_PT, abs=0.05)
            and any(
                float(rect["width"]) == pytest.approx(width, abs=0.05)
                for width in (
                    constants.SECTION_BADGE_SIZE_PT,
                    constants.SECTION_APPENDIX_BADGE_WIDTH_PT,
                )
            )
            and rect["non_stroking_color"]
            == pytest.approx((17 / 255,) * 3, abs=1e-5)
        ]

    assert len(badges) == len(SECTION_SPECS) + 1
    assert sum(
        float(rect["width"])
        == pytest.approx(constants.SECTION_APPENDIX_BADGE_WIDTH_PT, abs=0.05)
        for rect in badges
    ) == 1


def test_흐름은_짧은_3에서_5단계만_쉐브론을_쓴다() -> None:
    def graphic(values: list[str]) -> _FlowGraphic:
        headers = [f"단계 {index}" for index in range(1, len(values) + 1)]
        visual = TableVisualization(
            kind="flow",
            caption="흐름",
            flows=(tuple(values),),
        )
        return _FlowGraphic(visual, headers, A4[0] - 124)

    assert graphic(["하나", "둘", "셋"])._chevron_rows == [True]
    assert graphic(["하나", "둘", "셋", "넷", "다섯"])._chevron_rows == [True]
    assert graphic(["하나", "둘"])._chevron_rows == [False]
    assert graphic(["아주 긴 설명 " * 30, "둘", "셋"])._chevron_rows == [False]


def test_표_머리행은_검정_바탕과_흰_semibold_글자를_쓴다(
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        page = next(
            candidate
            for candidate in document.pages
            if "자원순환 운영 구조" in _normalized_page_text(candidate)
        )
        header = _word(_words(page), "주체")
        ink_fills = [
            rect
            for rect in page.rects
            if rect["non_stroking_color"]
            == pytest.approx((17 / 255,) * 3, abs=1e-5)
        ]

    assert header["non_stroking_color"] == pytest.approx((1.0, 1.0, 1.0))
    assert "SemiBold" in str(header["fontname"])
    assert ink_fills


def test_상단띠는_페이지의_첫_장제목_하나를_보여준다(demo_pdf: bytes) -> None:
    current = ""
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        for page in document.pages[1:]:
            top = page.crop((0, 0, float(page.width), constants.PAGE_HEADER_HEIGHT_PT))
            body = page.crop(
                (
                    0,
                    constants.PAGE_HEADER_HEIGHT_PT,
                    float(page.width),
                    float(page.height) - 33,
                )
            )
            top_text = _normalized_page_text(top)
            body_text = _normalized_page_text(body)
            candidates = [
                (body_text.index(heading), spec.title)
                for spec in SECTION_SPECS
                if (heading := f"{spec.display_number}. {spec.title}") in body_text
            ]
            if "부록. 출처와 검증 상태" in body_text:
                candidates.append(
                    (body_text.index("부록. 출처와 검증 상태"), "출처와 검증 상태")
                )
            if candidates:
                current = min(candidates)[1]
            assert current
            assert current in top_text


def test_본문_추출글자는_디자인변경전_골든과_글자단위로_같다(
    demo_pdf: bytes,
) -> None:
    with pdfplumber.open(io.BytesIO(demo_pdf)) as document:
        pages = [page.extract_text() or "" for page in document.pages[1:]]

    # 머리말·바닥글은 디자인 부품이라 제외한다. 나머지 장 제목·문장·숫자·
    # 출처·번호의 줄바꿈까지 변경 전 출력과 같아야 한다.
    body = "\n".join(
        "\n".join(page.splitlines()[1:-1])
        for page in pages
    )
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        "1ff8894b18e931c1326ae862644935185ba70d6d8c8a823a1e60732bc9d5bcdf"
    )
