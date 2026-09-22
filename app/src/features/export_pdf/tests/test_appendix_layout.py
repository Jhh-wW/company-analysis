"""부록의 시작 공간·연속 표·원문 위치 링크를 실제 PDF로 확인한다."""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate

from src.features.export_pdf import constants, logic
from src.features.export_pdf.tests.test_export_pdf import _report as _demo_report
from src.features.export_pdf.tests.test_v2_public_projection import (
    _report as _legacy_report,
    _v2_full_report,
)


# 뤼튼 실제 PDF에 인쇄된 문장을 그대로 두고, 반복 횟수만 바꿔 끝 위치를 조절한다.
_ACTUAL_SENTENCE = (
    "회사의 재무제표는 일반기업회계기준에 따라 중요성의 관점에서 공정하게 표시되고 있으며, "
    "2026년 3월 31일자 주주총회에서 최종 승인될 예정이다."
)


def _ninth_report(paragraph_count: int):
    report = _legacy_report()
    paragraph = f"{_ACTUAL_SENTENCE} [1]"
    section = replace(
        report.sections[0],
        cell="competitive_position",
        title="회사가 밝힌 차별점",
        display_number="9",
        lines=[(paragraph, "")] * paragraph_count,
        prose_lines=[(paragraph, "")] * paragraph_count,
        prose_paragraphs=[paragraph] * paragraph_count,
    )
    return replace(report, sections=[section], citations=_demo_report().citations[:1])


@pytest.mark.parametrize("paragraph_count, page_offset", [(1, 0), (28, 1)])
def test_9장_뒤_남은_공간에_따라_부록이_같은_쪽이나_다음_쪽에서_시작한다(
    paragraph_count: int, page_offset: int,
) -> None:
    reader = PdfReader(io.BytesIO(logic.build_pdf(_ninth_report(paragraph_count))))
    texts = [page.extract_text() for page in reader.pages]
    last_body_page = max(i for i, text in enumerate(texts) if "주주총회" in text)
    appendix_page = next(i for i, text in enumerate(texts) if "출처와 검증 상태" in text)

    assert sum(text.count("주주총회") for text in texts) == paragraph_count
    assert appendix_page == last_body_page + page_offset
    assert "원문 위치" in texts[appendix_page]


class _LeaveHeight(Flowable):
    """서체·여백 변화와 무관하게 부록 직전의 남은 높이를 맞춘다."""

    def __init__(self, remaining: float) -> None:
        super().__init__()
        self.remaining = remaining

    def wrap(self, available_width: float, available_height: float):
        return 0, max(0, available_height - self.remaining)

    def draw(self) -> None:
        pass


def _appendix_pdf(report, *, remaining=None, projection=None) -> bytes:
    logic._register_fonts()
    styles = logic._styles()
    story = [Paragraph("9장 마지막 문단", styles["body"])]
    if remaining is not None:
        story.append(_LeaveHeight(remaining))
    logic._add_citations(story, report, styles, projection=projection)
    output = io.BytesIO()
    SimpleDocTemplate(
        output, pagesize=A4,
        leftMargin=constants.PAGE_MARGIN_PT,
        rightMargin=constants.PAGE_MARGIN_PT,
        topMargin=constants.PAGE_TOP_MARGIN_PT,
        bottomMargin=constants.PAGE_BOTTOM_MARGIN_PT,
    ).build(story)
    return output.getvalue()


def _many_sources_report(*, long_label=False):
    report = _demo_report()
    sources = [
        replace(
            report.citations[0], number=index, source_id=f"source-{index}",
            title=f"출처행{index:02d}" + (" 긴 원문 제목" * 12 if long_label else ""),
            publisher="", location="사업내용", disclosed_at="2026-09-22",
            source_type="", fact_status="", used_in=["competitive_position"],
        )
        for index in range(1, 41)
    ]
    return replace(report, citations=sources)


@pytest.mark.parametrize("height_delta, appendix_page", [(-1, 1), (1, 0)])
def test_부록_최소_높이_경계와_연속_쪽의_반복_머리행을_지킨다(
    height_delta: float, appendix_page: int,
) -> None:
    data = _appendix_pdf(
        _many_sources_report(),
        remaining=constants.APPENDIX_MIN_START_HEIGHT_PT + height_delta,
    )
    texts = [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]

    assert len(texts) > appendix_page + 1
    assert "출처와 검증 상태" in texts[appendix_page]
    assert all(f"출처행{index:02d}" in texts[appendix_page] for index in (1, 2, 3))
    assert all("출처와 검증 상태" not in text for text in texts[:appendix_page])
    assert all(text.count("원문 위치") == 1 for text in texts[appendix_page:])
    assert all("".join(texts).count(f"출처행{index:02d}") == 1 for index in range(1, 41))


def test_긴_첫_행들에도_부록_제목만_앞_쪽에_남지_않는다() -> None:
    data = _appendix_pdf(
        _many_sources_report(long_label=True),
        remaining=constants.APPENDIX_MIN_START_HEIGHT_PT + 1,
    )
    texts = [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]

    assert "출처와 검증 상태" not in texts[0]
    assert "출처와 검증 상태" in texts[1]
    assert all(f"출처행{index:02d}" in texts[1] for index in (1, 2, 3))


@pytest.mark.parametrize("sealed", [False, True])
@pytest.mark.parametrize("location", [
    "https://wrtn.io/news/#1",
    "http://wrtn.io/news/#2",
    "https://wrtn.io/news/?a=1&b=2#1",
    "사업내용",
    "9453-9682",
])
def test_원문_위치는_URL만_링크로_만들고_표시_글자는_유지한다(
    sealed: bool, location: str,
) -> None:
    source_url = "https://wrtn.io/news/"
    report = _demo_report()
    report = replace(report, citations=[replace(
        report.citations[0], url=source_url, location=location,
    )])
    projection = None
    if sealed:
        projection = _v2_full_report().public_projection
        projection = replace(projection, citations=(replace(
            projection.citations[0], url=source_url, location=location,
        ),))
    data = _appendix_pdf(report, projection=projection)
    reader = PdfReader(io.BytesIO(data))
    urls = {
        str(annotation.get_object()["/A"]["/URI"])
        for page in reader.pages for annotation in page.get("/Annots", [])
        if annotation.get_object().get("/A", {}).get("/S") == "/URI"
    }
    text = "".join(page.extract_text() for page in reader.pages)

    assert "".join(location.split()) in "".join(text.split())
    expected_urls = {source_url}
    if location.startswith(("http://", "https://")):
        expected_urls.add(location)
    assert urls == expected_urls
