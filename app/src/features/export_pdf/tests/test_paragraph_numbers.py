"""PDF 문단 번호(pno)가 웹(result.html의 ``.pno``, v2-32/c881cb5)과
«같은 계산식»으로 나오는지 확인한다.

★ 왜 이 시험이 있나 (팀장 실측·2026-08-25) — 웹 화면엔 문단 번호가 25개
  찍히는데 같은 보고서의 PDF엔 0개였다. PDF가 다운로드 정본이라 「3-2 문단
  보세요」가 PDF에서도 성립해야 한다. 이 시험은 번호가 «찍히는지»뿐 아니라
  ``display_number``가 비어 있을 때의 웹 quirk(문단 자신의 0-기준 순번을
  장번호 자리에 대신 쓰는 것)까지 «그대로» 재현하는지 본다 — 다르게
  계산하면 오히려 웹·PDF 번호가 갈린다.
"""

from __future__ import annotations

import io

import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Flowable, SimpleDocTemplate

from src.features.export_pdf.logic import (
    _OutlineAnchor,
    _add_section,
    _register_fonts,
    _styles,
)
from src.features.pipeline.port import Grade, Report, ReportSection

_register_fonts()
_WIDTH = A4[0] - 124


def _render_sections(sections: list[ReportSection]) -> str:
    report = Report(
        company="테스트",
        job="",
        corp_type="",
        sections=sections,
        citations=[],
        grade=Grade.COMPLETE,
    )
    # ★ _OutlineAnchor는 outline level이 0부터 순서대로 열려야 한다
    #   (ReportLab 제약) — _document_header가 실제 PDF에서 만드는 표지
    #   anchor를 흉내 낸 자리표시자다. 이 시험은 표지를 안 그리므로 직접
    #   하나 넣는다.
    story: list[Flowable] = [_OutlineAnchor("root", "root", level=0)]
    for index, section in enumerate(sections):
        _add_section(story, report, section, _styles(), _WIDTH, f"anchor-{index}")

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=62, rightMargin=62, topMargin=62, bottomMargin=62
    )
    document.build(story)
    with pdfplumber.open(io.BytesIO(buffer.getvalue())) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_prose_paragraphs_get_section_number_dash_paragraph_number() -> None:
    section = ReportSection(
        cell="business_model",
        title="사업 구조와 수익 모델",
        lines=[("근거", "[1]")],
        prose_paragraphs=["첫 번째 문단입니다.", "두 번째 문단입니다.", "세 번째 문단입니다."],
        display_number="2",
    )

    text = _render_sections([section])

    assert "2-1" in text and "2-2" in text and "2-3" in text
    assert "첫 번째 문단입니다." in text
    assert "두 번째 문단입니다." in text


def test_paragraph_text_itself_is_unchanged_by_numbering() -> None:
    """번호가 «본문 글자»에 섞여 들어가지 않는지 — v2-32의 핵심 약속."""
    section = ReportSection(
        cell="portfolio",
        title="핵심 제품",
        lines=[("근거", "[1]")],
        prose_paragraphs=["3장 진짜 문장 원문."],
        display_number="3",
    )

    text = _render_sections([section])

    # 번호가 문장 «안»에 끼어들지 않고 문장 앞에 따로 붙는지 — 원문 그대로
    # 남아 있어야 인용 추적·중복 검사가 번호를 사실로 오인하지 않는다.
    assert "3장 진짜 문장 원문." in text


def test_missing_display_number_falls_back_like_the_web_loop_quirk() -> None:
    """★ display_number가 비어 있으면 웹은 «이 문단 루프의 0-기준 순번»을
    장번호 자리에 대신 쓴다(Jinja의 loop.index0가 안쪽 루프를 가리키는
    quirk). 이상해 보이지만 실제 웹 동작이므로 PDF도 그대로 따라간다 —
    다르게 고치면 웹·PDF 번호가 갈린다.
    """
    section = ReportSection(
        cell="culture",
        title="인재상",
        lines=[("근거", "[1]")],
        prose_paragraphs=["첫 문단.", "둘째 문단."],
        display_number="",
    )

    text = _render_sections([section])

    assert "0-1" in text
    assert "1-2" in text


def test_prose_lines_only_sections_stay_unnumbered_like_the_web() -> None:
    """v2-32는 ``prose_paragraphs``만 번호를 붙였다 — ``prose_lines``만
    있는(문단 분리가 안 된) 장은 웹도 번호가 없다. PDF도 같아야 한다."""
    section = ReportSection(
        cell="identity",
        title="기업 정체성",
        lines=[("근거", "[1]")],
        prose_lines=[("문장 한 줄.", "[1]")],
        display_number="1",
    )

    text = _render_sections([section])

    assert "문장 한 줄." in text
    assert "1-1" not in text
