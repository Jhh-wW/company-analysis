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

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION, render_report
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
from src.features.report_standard.public_projection import build_public_projection
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
    report = _report(generated_at="2026-09-22T09:30:00+09:00")
    _company_line, meta_line = masthead_lines(report)

    lines = _page2_lines(build_pdf(report))

    assert lines[2] == meta_line

    # 표지(_cover_metadata)의 「내용 생성」 라벨과 같은 generated_at 필드·
    # 같은 KST 변환과 점 구분자를 쓴다.
    cover_generated = _display_generated_at(report)
    assert cover_generated
    assert cover_generated == "2026.09.22"
    assert f"생성일 {cover_generated}" in meta_line


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


def test_heading_after_continuation_keeps_the_continued_section_as_page_header() -> None:
    """앞 장의 본문이 먼저 이어진 뒤 새 장이 시작돼도 머리말은 이어진 장이다."""
    canvas = _BrandedCanvas(io.BytesIO(), pagesize=(420, 800))

    _draw_heading(canvas, "7", "사업 운영과 파트너 구조")
    canvas.showPage()

    # 실제 도식·표 Flowable처럼 먼저 페이지에 그려진 내용이 있는 상태를 만든다.
    canvas.setFont(constants.FONT_REGULAR, 8)
    canvas.drawString(20, 700, "앞 장에서 이어진 도식")
    _draw_heading(canvas, "8", "인재상과 일하는 방식")

    assert canvas._current_section_name == "사업 운영과 파트너 구조"
    assert canvas._carry_section_name == "인재상과 일하는 방식"


def test_new_page_heading_replaces_carried_name_when_no_content_precedes_it() -> None:
    """새 쪽 첫 Flowable이 장 제목이면 새 장 머리말을 사용한다."""
    canvas = _BrandedCanvas(io.BytesIO(), pagesize=(420, 800))

    _draw_heading(canvas, "7", "사업 운영과 파트너 구조")
    canvas.showPage()
    _draw_heading(canvas, "8", "인재상과 일하는 방식")

    assert canvas._current_section_name == "인재상과 일하는 방식"


def test_single_heading_page_keeps_its_own_name() -> None:
    """장이 하나뿐인 보통의 쪽(대부분의 실제 쪽)은 예전과 같은 결과다 —
    이번 수정이 단일 장 쪽까지 건드리지 않았는지 확인하는 회귀 시험."""
    canvas = _BrandedCanvas(io.BytesIO(), pagesize=(420, 800))

    _draw_heading(canvas, "3", "핵심 제품·서비스와 포트폴리오 역할")

    assert canvas._current_section_name == "핵심 제품·서비스와 포트폴리오 역할"


# ══════════════════════════════════════════════════════════
# v2 실제 PDF로 이어지는 쪽 머리말 배선을 지키는 회귀 시험
#
# ★ 왜 위 단위 시험(_BrandedCanvas를 직접 만드는 것)만으로 부족한가 —
#   독립 검토(tmp/lead/review-deae4fff-opus.md, E-1/G-1)가 뮤테이션으로
#   확인했다. ``_build_pdf``의
#   ``masthead_continuation_fix_enabled = (report.schema_version ==
#   ENGINE_V2_SCHEMA_VERSION)`` 배선을 통째로 꺼도(``False`` 고정) 위 단위
#   시험들은 ``_BrandedCanvas``를 직접 만들어 canvas 쪽 기본값(``True``,
#   logic.py의 ``getattr(canvas, "_masthead_continuation_fix_enabled", True)``)에
#   기대고 있어서 배선이 끊겨도 그대로 통과한다. 아래 시험은 공개 진입점
#   ``build_pdf``로 실제 v2 PDF를 만들어 문서(``masthead_continuation_fix_enabled``)
#   → ``_page_furniture`` → canvas → ``_SectionHeading.draw()``로 이어지는
#   배선 전체가 살아 있는지 확인한다.
# ══════════════════════════════════════════════════════════


def _v2_report_with_continued_identity_section(
    *, long_sentence_count: int = 90
) -> Report:
    """1장(기업 정체성) 본문이 다음 쪽까지 이어지도록 문장을 늘린 v2 FULL 보고서.

    ``render_report``(실제 v2 조립 경로)로 아홉 장을 만들고
    ``build_public_projection``으로 봉인까지 붙인다 — 손으로 지은 Report가
    아니라 실제 생산 함수가 만든 산출물이라야 이 수정이 타는 진짜 분기
    (``report.schema_version == ENGINE_V2_SCHEMA_VERSION`` → 봉인 블록 배치
    경로)를 검증할 수 있다(``test_v2_public_projection.py``와 같은 이유).

    1장에만 합성 확인 문장을 많이 채워 그 본문이 2쪽 끝까지 다 못 들어가고
    3쪽으로 넘어가게 만들고, 나머지 장은 문장 하나짜리 대조군으로 둬 3쪽에서
    2장 이후 제목들이 뒤이어 시작되게 한다 — 실측(별도 스크립트로 문장 수
    68~150을 훑음)으로 확인한 안정 구간(70~150문장, 3쪽 머리말이 항상
    「기업 정체성」으로 유지됨) 한가운데 값이 90이다. 68문장 이하에서는
    1장 본문이 2쪽 안에서 끝나 이어짐 자체가 재현되지 않는다.
    """

    sections: list[ComposedSection] = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            sentences = tuple(
                ComposedSentence(
                    text=(
                        f"이 장은 이어짐 확인을 위해 늘린 합성 확인 문장 {i}번이며 "
                        "공식 자료를 근거로 인용한다."
                    ),
                    citations=("1",),
                    grade=GRADE_CONFIRMED,
                )
                for i in range(1, long_sentence_count + 1)
            )
        else:
            sentences = (
                ComposedSentence(
                    text="이 장은 짧은 확인 문장 하나만 담은 대조군이다.",
                    citations=("1",),
                    grade=GRADE_CONFIRMED,
                ),
            )
        sections.append(ComposedSection(section_id=section_id, sentences=sentences))

    composed = ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(
                text="요약 문장 1이다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
            ComposedSentence(
                text="요약 문장 2이다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
            ComposedSentence(
                text="요약 문장 3이다.", citations=("1",), grade=GRADE_INTERPRETED
            ),
        ),
    )
    fragments = {
        1: {"종류": "사업내용", "원문": "회사는 사업 전반에 관한 사실을 공시했다."}
    }
    rendered = render_report(
        "가나다전자",
        composed,
        fragments,
        None,
        generated_at="2026-09-01",
        as_of_date="2026-09-01",
        analysis_period="2023~2025 완료 회계연도",
    )
    return replace(rendered, public_projection=build_public_projection(rendered))


def _cropped_top_and_body(pdf: bytes, *, page_index: int) -> tuple[str, str]:
    """``test_design_contract.py``의 쪽 검사와 같은 경계(상단 띠 vs 본문)로 자른다."""

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        page = document.pages[page_index]
        top = page.crop((0, 0, float(page.width), constants.PAGE_HEADER_HEIGHT_PT))
        body = page.crop(
            (
                0,
                constants.PAGE_HEADER_HEIGHT_PT,
                float(page.width),
                float(page.height) - 33,
            )
        )
        top_text = " ".join((top.extract_text() or "").split())
        body_text = body.extract_text() or ""
    return top_text, body_text


def test_v2_실제_pdf에서_이어진_쪽_머리말은_새_장이름으로_바뀌지_않는다() -> None:
    """1장 본문이 이어지는 3쪽에 2장 제목이 뒤늦게 시작해도, 그 쪽 머리말은
    이어진 1장을 유지해야 한다(실측 사례를 고친 deae4fff의 핵심 동작).

    이 시험이 지키는 배선: ``_build_pdf``가 ``report.schema_version``으로
    켜는 ``masthead_continuation_fix_enabled`` → ``_page_furniture``가 문서
    속성을 canvas로 옮김 → ``_SectionHeading.draw()``가 그 값을 읽어 새 장
    제목의 덮어쓰기를 막음. 이 중 하나라도 끊기면(예: 플래그를 늘 ``False``로
    고정) 이 시험이 실패해야 한다 — 뮤테이션 결과는 별도 보고서에 기록한다.
    """
    report = _v2_report_with_continued_identity_section()
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert report.public_projection is not None

    pdf = build_pdf(report)
    # 3쪽(표지 다음의 다음 쪽) = pdfplumber 0-index 2.
    top_text, body_text = _cropped_top_and_body(pdf, page_index=2)

    # 이 쪽이 «이어짐 + 새 장 시작 혼합» 쪽이 맞는지 먼저 확인한다 — 2장
    # 제목이 실제로 이 쪽 본문에 나타나야 검증 대상 쪽이 맞다.
    assert "사업 구조와 수익 모델" in body_text

    # 머리말은 이어진 1장을 유지하고, 뒤늦게 시작한 2장 이름으로 바뀌지 않는다.
    assert "기업 정체성" in top_text
    assert "사업 구조와 수익 모델" not in top_text
