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


def _appendix_pdf(report, *, remaining=None, projection=None, page_height=A4[1]) -> bytes:
    logic._register_fonts()
    styles = logic._styles()
    story = [Paragraph("9장 마지막 문단", styles["body"])]
    if remaining is not None:
        story.append(_LeaveHeight(remaining))
    logic._add_citations(story, report, styles, projection=projection)
    output = io.BytesIO()
    SimpleDocTemplate(
        output, pagesize=(A4[0], page_height),
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


def test_40행_부록의_마지막_한_행을_앞_행과_함께_넘긴다(monkeypatch, tmp_path):
    import re
    from reportlab.platypus import Table, TableStyle

    class UnprotectedTable(Table):
        def setStyle(self, style):
            super().setStyle(TableStyle([command for command in style.getCommands() if command[0] != "NOSPLIT"]))

    report = _many_sources_report()
    def counts(data):
        return [len(re.findall(r"출처행\d+", page.extract_text())) for page in PdfReader(io.BytesIO(data)).pages]

    boundary = None
    with monkeypatch.context() as patch:
        patch.setattr(logic, "Table", UnprotectedTable)
        for height in range(500, 1150, 5):
            baseline = _appendix_pdf(report, page_height=height)
            if counts(baseline)[-1] == 1:
                boundary = height
                break
    assert boundary is not None, "마지막 한 행이 고립되는 쪽 높이를 재현해야 합니다"
    fixed = _appendix_pdf(report, page_height=boundary)
    assert counts(fixed)[-1] >= 2
    assert sum(counts(fixed)) == 40
    for page in PdfReader(io.BytesIO(fixed)).pages:
        assert page.extract_text().count("원문 위치") == 1
    (tmp_path / "appendix-before.pdf").write_bytes(baseline)
    (tmp_path / "appendix-after.pdf").write_bytes(fixed)


@pytest.mark.parametrize("has_news", [False, True])
def test_외부_언론_0건_안내문이_PDF와_봉인에_같이_나온다(has_news):
    import json
    from src.features.export_notion.logic import _source_list_blocks, _v2_source_list_blocks
    from src.features.provenance.sources import SourceKind
    from src.features.report_standard.public_projection import build_public_projection
    from src.shared.report_generation.public_projection import public_report_projection_to_dict, public_report_projection_from_dict
    from src.shared.report_generation.public_projection import build_report_digest

    report = _v2_full_report()
    source = replace(report.citations[0], kind=SourceKind.NEWS if has_news else SourceKind.FILING)
    report = replace(report, citations=[source])
    projection = build_public_projection(report)
    expected = "" if has_news else constants.CITATIONS_NO_EXTERNAL_NEWS_NOTE
    assert projection.citations_note == expected
    payload = public_report_projection_to_dict(projection)
    assert public_report_projection_from_dict(payload) == projection
    changed = replace(projection, citations_note="다른 안내")
    assert build_report_digest(changed).display_sha256 != build_report_digest(projection).display_sha256
    for blocks in (_source_list_blocks(report), _v2_source_list_blocks(projection)):
        assert (constants.CITATIONS_NO_EXTERNAL_NEWS_NOTE in json.dumps(blocks, ensure_ascii=False)) is (not has_news)
    for sealed in (None, projection):
        text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(_appendix_pdf(report, projection=sealed))).pages)
        assert (constants.CITATIONS_NO_EXTERNAL_NEWS_NOTE in text) is (not has_news)


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
    "https://wrtn.io/company/ · 목록 11번째 항목",
    "https://wrtn.io/news/ 게시글 1",
    "https:///news/",
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
    if location in {
        "https://wrtn.io/news/#1", "http://wrtn.io/news/#2",
        "https://wrtn.io/news/?a=1&b=2#1",
    }:
        expected_urls.add(location)
    assert urls == expected_urls


def _tail_overflow_report(chars: int):
    """마지막 두 행의 「원문 위치」 칸을 한 쪽에 못 들어갈 만큼 길게 만든다."""

    report = _many_sources_report()
    long_location = "긴 원문 위치 " * (chars // 7)
    citations = list(report.citations)
    for index in (-2, -1):
        citations[index] = replace(citations[index], location=long_location)
    return replace(report, citations=citations)


@pytest.mark.parametrize("chars", [500, 800])
def test_마지막_두_행이_한_쪽에_못_들어가도_PDF가_나온다(chars):
    """2026-09-23 독립 검토 N1 — 꼬리 두 행 NOSPLIT이 LayoutError를 만들던 경로."""

    import re

    data = _appendix_pdf(_tail_overflow_report(chars))
    pages = PdfReader(io.BytesIO(data)).pages
    assert sum(len(re.findall(r"출처행\d+", page.extract_text())) for page in pages) == 40


def test_꼬리_두_행_높이_판정은_짧으면_참_길면_거짓이다():
    from reportlab.platypus import Paragraph, TableStyle

    logic._register_fonts()
    styles = logic._styles()
    width = A4[0] - constants.PAGE_MARGIN_PT * 2
    widths = [width * share for share in (0.06, 0.27, 0.20, 0.15, 0.18, 0.14)]

    def rows(location: str):
        header = [Paragraph(text, styles["table_head_on_ink"]) for text in ("#", "자료", "상태", "검증", "원문 위치", "장")]
        body = [Paragraph(cell, styles["table"]) for cell in ("1", "자료", "상태", "검증", location, "1장")]
        return [header, body, body]

    assert logic._appendix_tail_fits_one_page(rows("사업내용"), widths, TableStyle([]), width)
    assert not logic._appendix_tail_fits_one_page(rows("긴 원문 위치 " * 120), widths, TableStyle([]), width)


def test_가드를_강제로_켜면_같은_입력이_LayoutError로_죽는다(monkeypatch):
    """음성 대조 — 위 시험이 지키는 것은 정확히 이 가드다."""

    from reportlab.platypus.doctemplate import LayoutError

    monkeypatch.setattr(logic, "_appendix_tail_fits_one_page", lambda *args: True)
    with pytest.raises(LayoutError):
        _appendix_pdf(_tail_overflow_report(800))


def test_틀_padding만큼_넘치는_경계_입력도_PDF가_나온다():
    """2026-09-23 총괄 실측 — 꼬리 높이 736pt는 여백 기준(745.5)엔 들어가지만
    Frame 위·아래 padding 12pt를 뺀 실제 본문(733.5)엔 안 들어간다."""

    import re

    report = _tail_overflow_report(524)
    citations = list(report.citations)
    citations[-1] = replace(citations[-1], location=citations[-1].location + "x " * 36)
    data = _appendix_pdf(replace(report, citations=citations))
    pages = PdfReader(io.BytesIO(data)).pages
    assert sum(len(re.findall(r"출처행\d+", page.extract_text())) for page in pages) == 40


def test_꼬리_최대_높이는_틀_padding을_뺀_값이다():
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.platypus import SimpleDocTemplate as _Doc

    document = _Doc(io.BytesIO(), pagesize=_A4, topMargin=constants.PAGE_TOP_MARGIN_PT,
                    bottomMargin=constants.PAGE_BOTTOM_MARGIN_PT)
    frame = document.pageTemplates[0].frames[0] if document.pageTemplates else None
    if frame is None:
        document.build([])
        frame = document.pageTemplates[0].frames[0]
    usable = frame._height - frame._topPadding - frame._bottomPadding
    assert abs(constants.APPENDIX_TAIL_MAX_HEIGHT_PT - usable) < 0.01


def test_padding을_빼지_않은_상한이면_경계_입력이_LayoutError로_죽는다(monkeypatch):
    """음성 대조 — 위 경계 시험이 지키는 것은 정확히 padding 12pt다."""

    from reportlab.platypus.doctemplate import LayoutError

    monkeypatch.setattr(
        constants, "APPENDIX_TAIL_MAX_HEIGHT_PT",
        constants.APPENDIX_TAIL_MAX_HEIGHT_PT + constants.FRAME_VERTICAL_PADDING_PT,
    )
    report = _tail_overflow_report(524)
    citations = list(report.citations)
    citations[-1] = replace(citations[-1], location=citations[-1].location + "x " * 36)
    with pytest.raises(LayoutError):
        _appendix_pdf(replace(report, citations=citations))


@pytest.mark.parametrize("url", [
    "https://example.com/\n본문", "https://example.com/\x00본문",
    "https://[잘못된주소/", "javascript:alert(1)",
])
def test_잘못된_위치_주소도_표시_문구는_보존한다(url):
    assert logic._link_markup("원문 위치", url) == "원문 위치"
