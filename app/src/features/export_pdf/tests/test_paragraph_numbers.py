"""PDF 문단 번호(pno)가 웹(result.html의 ``.pno``)과 «같은 계산식»으로
나오는지 확인한다.

★ 왜 이 시험이 있나 (팀장 실측·2026-08-25) — 웹 화면엔 문단 번호가 25개
  찍히는데 같은 보고서의 PDF엔 0개였다. PDF가 다운로드 정본이라 「3번 문단
  보세요」가 PDF에서도 성립해야 한다.

★ 형식이 바뀌었다 (2026-08-25) — «장번호-문단번호»(2-1)에서 «문단번호만»(1.)로.
  이유(사용자): 이미 「2. 사업 구조와 수익 모델」 장 제목 아래에 있으므로 장
  번호를 문단마다 되풀이할 이유가 없다.
  그 결과 옛 quirk가 «사라졌다» — 전에는 ``display_number``가 비면 웹이 문단
  자신의 0-기준 순번을 장번호 자리에 대신 써서 「0-1」「1-2」가 나왔고, PDF도
  그 이상한 동작을 그대로 흉내 내야 했다. 이제 장번호를 아예 안 쓰므로 그
  자리가 없어졌고, 웹·PDF가 어긋날 여지도 같이 없어졌다.
  ★ 웹 쪽 짝은 ``app/src/web/tests/test_paragraph_number_format.py``다.
    한쪽만 고치면 「3-2 문단 보세요」가 다시 어긋나므로 둘이 같이 있어야 한다.
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


def _line_with(text: str, needle: str) -> str:
    """``needle``이 들어 있는 «줄»을 돌려준다.

    ★ 왜 줄 단위로 보나 — 장 제목도 「1. 기업 정체성」처럼 번호로 시작한다.
      추출된 글 «전체»에서 ``"1." in text``를 보면 제목의 번호에 걸려, 문단
      번호가 하나도 없어도 통과한다(2026-08-25 실측으로 확인). 문단이 있는
      «그 줄»만 봐야 문단 번호를 정확히 잰다.
    """
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"추출된 PDF 글에 {needle!r}이 없습니다:\n{text}")


def test_prose_paragraphs_get_plain_paragraph_number() -> None:
    """문단 앞에 «1.» «2.» «3.»이 붙는다 — 장번호는 붙지 않는다."""
    section = ReportSection(
        cell="business_model",
        title="사업 구조와 수익 모델",
        lines=[("근거", "[1]")],
        prose_paragraphs=["첫 번째 문단입니다.", "두 번째 문단입니다.", "세 번째 문단입니다."],
        display_number="2",
    )

    text = _render_sections([section])

    assert _line_with(text, "첫 번째 문단입니다.").startswith("1.")
    assert _line_with(text, "두 번째 문단입니다.").startswith("2.")
    assert _line_with(text, "세 번째 문단입니다.").startswith("3.")
    # ★ 옛 «장번호-문단번호» 형식이 되살아나면 바로 빨간불이 되게 못을 박는다.
    assert "2-1" not in text
    assert "2-2" not in text
    assert "2-3" not in text


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


def test_missing_display_number_no_longer_changes_the_number() -> None:
    """★ 옛 quirk가 사라졌다는 것을 못으로 박는다.

    전에는 ``display_number``가 비면 웹이 «문단 자신의 0-기준 순번»을 장번호
    자리에 대신 써서 「0-1」「1-2」가 나왔고 PDF도 그걸 흉내 냈다. 이제
    장번호를 아예 안 쓰므로 ``display_number``가 있든 없든 번호가 같아야 한다.
    """
    section = ReportSection(
        cell="culture",
        title="인재상",
        lines=[("근거", "[1]")],
        prose_paragraphs=["첫 문단.", "둘째 문단."],
        display_number="",
    )

    text = _render_sections([section])

    assert _line_with(text, "첫 문단.").startswith("1.")
    assert _line_with(text, "둘째 문단.").startswith("2.")
    # 옛 quirk가 되살아나면 빨간불
    assert "0-1" not in text
    assert "1-2" not in text


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

    # ★ 장 제목 줄도 「1. 기업 정체성」이라 번호로 시작한다. 문단이 있는
    #   «그 줄»만 봐야 문단 번호가 «안» 붙었다는 것을 정확히 잰다.
    assert not _line_with(text, "문장 한 줄.").startswith("1.")
