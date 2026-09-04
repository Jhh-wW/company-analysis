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

from src.features.export_pdf import constants
from src.features.export_pdf.logic import _display_generated_at, build_pdf
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
