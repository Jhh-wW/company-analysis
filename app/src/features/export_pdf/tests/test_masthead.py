"""표지 다음 첫 본문 페이지 맨 위 회사명 마스트헤드를 검증한다.

2쪽(표지 다음 장) 맨 위에는 이미 ``_page_furniture``가 그리는 작은(6.8pt)
머리말 한 줄("회사명 분석 보고서 · 기준일 ...")이 있다. 이건 캔버스에
페이지마다 따로 그리는 러닝 헤더일 뿐, 여기서 말하는 "마스트헤드"는
그 아래, 본문 story 흐름의 맨 앞에 오는 더 크고 눈에 띄는 두 줄이다.
"""

from __future__ import annotations

import io
from dataclasses import replace

import pdfplumber
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

from src.features.export_pdf import constants
from src.features.export_pdf.logic import (
    _BrandedCanvas,
    _display_generated_at,
    _register_fonts,
    _SectionHeading,
    build_pdf,
)
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import Report
from src.features.report_standard.section_content import masthead_lines


def _report(**overrides: object) -> Report:
    return replace(build_demo_report(), **overrides)


def _page2_lines(pdf: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        text = document.pages[1].extract_text() or ""
    return [line for line in text.split("\n") if line.strip()]


def test_표지_다음_장_첫_줄은_회사명이다() -> None:
    report = _report()
    company_line, _meta_line = masthead_lines(report)

    lines = _page2_lines(build_pdf(report))

    # lines[0]은 매 쪽에 따로 그리는 작은 러닝 헤더(_page_furniture)다.
    # 본문 story 흐름의 첫 줄(마스트헤드)은 그 바로 다음이며, 회사명
    # 하나로만 이루어져 「회사명 분석 보고서 기준일 ...」처럼 다른 말이
    # 섞이지 않는다.
    assert lines[0] != company_line
    assert lines[1] == company_line


def test_마스트헤드_둘째줄은_표지_메타와_같은_생성일을_쓴다() -> None:
    report = _report(generated_at="2026-08-19T09:30:00+09:00")
    _company_line, meta_line = masthead_lines(report)

    lines = _page2_lines(build_pdf(report))

    assert lines[2] == meta_line

    # 표지(_cover_metadata)의 「내용 생성」 라벨과 같은 generated_at 필드·
    # 같은 KST 변환을 쓴다 — 구분자(마침표 vs 대시)만 다르고 날짜는 같다.
    cover_generated = _display_generated_at(report)
    assert cover_generated
    assert cover_generated.replace(".", "-") in meta_line


def test_마스트헤드_회사명은_표지_제목보다_작고_장제목보다_크다() -> None:
    report = _report()

    with pdfplumber.open(io.BytesIO(build_pdf(report))) as document:
        page2_words = document.pages[1].extract_words(extra_attrs=["size"])

    # 회사명은 러닝 헤더(작은 6.8pt)에도 한 번 더 나오므로, 마스트헤드
    # 크기(20pt)로 찍힌 것만 골라야 한다.
    company_word = next(
        word
        for word in page2_words
        if word["text"] == report.company
        and abs(float(word["size"]) - constants.MASTHEAD_TITLE_FONT_SIZE_PT) < 0.05
    )
    assert float(company_word["size"]) == constants.MASTHEAD_TITLE_FONT_SIZE_PT
    assert constants.HEADING_FONT_SIZE_PT < constants.MASTHEAD_TITLE_FONT_SIZE_PT
    assert constants.MASTHEAD_TITLE_FONT_SIZE_PT < constants.TITLE_FONT_SIZE_PT


def _heading_style() -> ParagraphStyle:
    _register_fonts()
    base = getSampleStyleSheet()
    return ParagraphStyle(
        "TestSectionHeading",
        parent=base["Heading2"],
        fontName=constants.FONT_REGULAR,
        fontSize=constants.HEADING_FONT_SIZE_PT,
        leading=24,
    )


def _draw_heading(canvas: _BrandedCanvas, badge: str, name: str) -> None:
    heading = _SectionHeading(f"<b>{badge}. {name}</b>", name, badge, _heading_style(), 300)
    heading.wrap(300, 100)
    heading.canv = canvas
    heading.draw()


def test_mixed_page_still_shows_its_own_first_heading() -> None:
    """실측 사례(8장 인재상과 일하는 방식 · 9장 회사가 밝힌 차별점이 한 쪽에
    같이 실림)를 재현한다. 그 쪽 «자체»의 머리말은 기존 표시를 그대로
    지킨다 — 장이 여럿인 쪽은 각 장 제목이 본문에 그대로 보이므로, 머리말이
    그중 하나(첫 장)와 달라도 읽는 사람이 본문에서 확인할 수 있다."""
    canvas = _BrandedCanvas(io.BytesIO(), pagesize=(420, 800))

    _draw_heading(canvas, "8", "인재상과 일하는 방식")
    assert canvas._current_section_name == "인재상과 일하는 방식"

    _draw_heading(canvas, "9", "회사가 밝힌 차별점")
    # 이 쪽 자체의 머리말은 여전히 먼저 그려진 8장이다(변경 없음).
    assert canvas._current_section_name == "인재상과 일하는 방식"
    # 다음 쪽에 물려줄 값만 마지막 장(9장)으로 갱신돼 있다.
    assert canvas._carry_section_name == "회사가 밝힌 차별점"


def test_continuation_page_after_mixed_page_inherits_last_heading() -> None:
    """8장·9장이 같이 실린 쪽 다음에 장 제목 없이 9장 본문만 이어지는 쪽이
    오면, 그 다음 쪽 머리말은 9장을 정확히 물려받아야 한다 — 예전 규칙(첫
    장 고정)은 이 다음 쪽 머리말을 8장으로 잘못 멈춰 뒀다(SM PDF 7쪽 실측)."""
    canvas = _BrandedCanvas(io.BytesIO(), pagesize=(420, 800))

    _draw_heading(canvas, "8", "인재상과 일하는 방식")
    _draw_heading(canvas, "9", "회사가 밝힌 차별점")
    # 혼합 쪽을 닫는다 — showPage()가 다음 쪽의 시작 머리말을 carry 값으로 미리 채운다.
    canvas.showPage()

    # 다음 쪽(순수 이어짐, 새 장 제목 없음)의 머리말은 이제 9장이다.
    assert canvas._current_section_name == "회사가 밝힌 차별점"


def test_single_heading_page_keeps_its_own_name() -> None:
    """장이 하나뿐인 보통의 쪽(대부분의 실제 쪽)은 예전과 같은 결과다 —
    이번 수정이 단일 장 쪽까지 건드리지 않았는지 확인하는 회귀 시험."""
    canvas = _BrandedCanvas(io.BytesIO(), pagesize=(420, 800))

    _draw_heading(canvas, "3", "핵심 제품·서비스와 포트폴리오 역할")

    assert canvas._current_section_name == "핵심 제품·서비스와 포트폴리오 역할"
