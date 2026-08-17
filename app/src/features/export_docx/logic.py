"""보고서를 워드(.docx) 바이트로 바꾼다 — 「② 워드로 받기」.

★ 세 형태(화면·워드·노션)를 따로 만들지 않는다. 하나의 원본(`Report`)에서
  «형태만» 바꾼다 — 각각 그리면 한쪽만 고쳤을 때 내용이 갈린다 (P3 위반).
  정본은 화면(`src/web/templates/result.html`)이다. 이 파일은 그 화면과
  같은 순서·문구가 나오도록 `Report`를 그대로 훑는다.

★ 출처 목록은 새로 그리지 않는다. `provenance/sources.py`의
  `render_sources()` 결과를 그대로 옮긴다 — 형태마다 따로 그리면
  한쪽만 고쳤을 때 출처 표기가 갈린다.

정본:
  - 확정/07_출력/1_흐름/01_세형태.md          §워드로 받기
  - 확정/07_출력/2_규칙/01_배치와근거표기.md   §배치 순서 · 빈칸 사유

파일을 디스크에 쓰지 않는다. `build_docx()`는 바이트만 돌려준다 — 웹이
그 바이트를 그대로 응답으로 내려보낸다 (연결 지점은 이 모듈의 docstring
아래 `build_download_filename`·`build_content_disposition` 참고).
"""

from __future__ import annotations

import datetime as dt
import io
import urllib.parse
from typing import cast

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml.shared import OxmlElement
from docx.shared import Cm, Pt, RGBColor
from docx.table import _Cell
from docx.text.run import Run

from src.core.citations import citation_marker
from src.core.constants import CELL_LABELS, RAW_SOURCE_LABEL, RAW_SOURCE_NOTE
from src.features.export_docx.constants import (
    BULLET_STYLE,
    CITATIONS_NOTE,
    COLOR_MUTED_RGB,
    COLOR_TABLE_HEADER_HEX,
    CONTENT_TYPE_DOCX,
    EMPTY_DEFAULT_REASON,
    EMPTY_PREFIX,
    DOCX_SUFFIX,
    FILENAME_ASCII_ALLOWED,
    FILENAME_ASCII_FALLBACK,
    FILENAME_ASCII_MIN_STEM,
    FILENAME_FALLBACK,
    FILENAME_FORBIDDEN_CHARS,
    FILENAME_PATTERN,
    FONT_NAME,
    FONT_SIZE_BODY_PT,
    FONT_SIZE_HEADING_PT,
    FONT_SIZE_MUTED_PT,
    FONT_SIZE_TABLE_PT,
    FONT_SIZE_TITLE_PT,
    GRADE_COLOR,
    GRADE_ICON,
    HEADING_COLLECTION,
    HEADING_SOURCES,
    PAGE_MARGIN_CM,
    REQUIREMENTS_CELL,
    REQUIREMENTS_EMPTY_REASON,
    REQUIREMENTS_NOTE,
    SOURCE_STATE_LABEL,
    SOURCES_TABLE_HEADERS,
    TABLE_STYLE,
)
from src.features.grading.logic import grade_message
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
)
from src.features.provenance.constants import SOURCES_HEADER
from src.features.provenance.sources import Source, render_sources

# ══════════════════════════════════════════════════════════
# 글꼴 — 로마자·동아시아 서체를 같이 지정한다 (한글 깨짐 방지)
# ══════════════════════════════════════════════════════════


def _set_font(
    run: Run,
    *,
    size_pt: int = FONT_SIZE_BODY_PT,
    bold: bool = False,
    color_rgb: tuple[int, int, int] | None = None,
) -> None:
    """실행(run) 하나에 한글이 깨지지 않는 서체를 강제로 지정한다.

    ★ `run.font.name`만 두면 로마자(ascii)만 바뀌고 한글은 문서 기본 서체로
      남는다. 워드는 로마자 서체(`w:rFonts/@w:ascii`)와 동아시아 서체
      (`w:rFonts/@w:eastAsia`)를 따로 관리하기 때문에 둘 다 지정해야 한다.
      python-docx 공개 API에는 eastAsia 지정이 없어 여기서만 저수준으로 연다.
    """
    run.font.name = FONT_NAME
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    if color_rgb is not None:
        run.font.color.rgb = RGBColor(*color_rgb)
    r_pr = run.font.element.get_or_add_rPr()
    r_pr.get_or_add_rFonts().set(qn("w:eastAsia"), FONT_NAME)


def _set_default_style(doc: DocxDocument) -> None:
    """본문 기본 서체(Normal 스타일)를 문서 전체 기본값으로 맞춘다."""
    normal = doc.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(FONT_SIZE_BODY_PT)
    r_pr = normal.font.element.get_or_add_rPr()
    r_pr.get_or_add_rFonts().set(qn("w:eastAsia"), FONT_NAME)


def _set_margins(doc: DocxDocument) -> None:
    for section in doc.sections:
        section.left_margin = Cm(PAGE_MARGIN_CM)
        section.right_margin = Cm(PAGE_MARGIN_CM)
        section.top_margin = Cm(PAGE_MARGIN_CM)
        section.bottom_margin = Cm(PAGE_MARGIN_CM)


def _shade_cell(cell: _Cell, hex_color: str) -> None:
    """표 칸 배경을 칠한다. python-docx는 셀 음영 공개 API가 없어 XML을 직접 붙인다."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)  # noqa: SLF001 — 공개 API가 없다


# ══════════════════════════════════════════════════════════
# 문단 헬퍼
# ══════════════════════════════════════════════════════════


def _add_heading(doc: DocxDocument, text: str, *, level: int) -> None:
    """제목 문단을 추가한다. `level=0`은 회사명(문서 제목), 그 외는 항목 제목."""
    heading = doc.add_heading(text, level=level)
    size = FONT_SIZE_TITLE_PT if level == 0 else FONT_SIZE_HEADING_PT
    for run in heading.runs:
        _set_font(run, size_pt=size, bold=True)


def _add_muted_paragraph(doc: DocxDocument, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    _set_font(run, size_pt=FONT_SIZE_MUTED_PT, color_rgb=COLOR_MUTED_RGB)


def _add_plain_paragraph(doc: DocxDocument, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    _set_font(run)


def _add_bullet_line(doc: DocxDocument, text: str, cite: str) -> None:
    """본문 문장 한 줄 — `문장〔출처〕` (result.html의 `<li>`와 같은 모양)."""
    paragraph = doc.add_paragraph(style=BULLET_STYLE)
    run = paragraph.add_run(text)
    _set_font(run)
    marker = citation_marker(cite)
    if marker:
        # 내부 ``조각 N·종류``가 아니라 화면과 같은 실제 번호만 내보낸다(P-127).
        cite_run = paragraph.add_run(f" {marker}")
        _set_font(cite_run, size_pt=FONT_SIZE_MUTED_PT, color_rgb=COLOR_MUTED_RGB)


def _add_cited_prose(doc: DocxDocument, lines: list[tuple[str, str]]) -> None:
    """검증된 표시용 문장을 한 문단으로 잇고, 각 문장 뒤에 실제 출처를 붙인다."""
    paragraph = doc.add_paragraph()
    for index, (text, cite) in enumerate(lines):
        if index:
            spacer = paragraph.add_run(" ")
            _set_font(spacer)
        run = paragraph.add_run(text)
        _set_font(run)
        marker = citation_marker(cite)
        if marker:
            cite_run = paragraph.add_run(f" {marker}")
            _set_font(cite_run, size_pt=FONT_SIZE_MUTED_PT, color_rgb=COLOR_MUTED_RGB)


def _add_empty_block(doc: DocxDocument, reason: str) -> None:
    """빈칸 + 사유 — result.html의 `비어 있습니다 — {사유}`와 같은 문구·순서.

    ★ 사유를 지우지 않는다 — 「왜 비었는지」는 프로그램이 붙인 값이다 (S6).
    """
    paragraph = doc.add_paragraph()
    why_run = paragraph.add_run(EMPTY_PREFIX)
    _set_font(why_run, bold=True)
    reason_run = paragraph.add_run(f" — {reason or EMPTY_DEFAULT_REASON}")
    _set_font(reason_run)


# ══════════════════════════════════════════════════════════
# 표
# ══════════════════════════════════════════════════════════


def _add_report_table(doc: DocxDocument, table: ReportTable) -> None:
    """숫자·글자 표 하나 — 문장으로 뭉개지 않고 워드 표 그대로 넣는다 (D13)."""
    caption = doc.add_paragraph()
    caption_run = caption.add_run(table.caption)
    _set_font(caption_run, bold=True)
    if table.cite:
        cite_run = caption.add_run(f" 〔{table.cite}〕")
        _set_font(cite_run, size_pt=FONT_SIZE_MUTED_PT, color_rgb=COLOR_MUTED_RGB)

    n_cols = len(table.headers)
    docx_table = doc.add_table(rows=1, cols=n_cols)
    docx_table.style = TABLE_STYLE

    for idx, head in enumerate(table.headers):
        cell = docx_table.rows[0].cells[idx]
        run = cell.paragraphs[0].add_run(head)
        _set_font(run, bold=True, size_pt=FONT_SIZE_TABLE_PT)
        _shade_cell(cell, COLOR_TABLE_HEADER_HEX)

    for row in table.rows:
        cells = docx_table.add_row().cells
        for idx in range(n_cols):
            # ★ 방어적 — 정상 데이터라면 `ReportTable.is_valid`가 열 개수를
            #   보장하지만, 만에 하나 어긋나도 화면처럼 예외로 죽지 않는다.
            value = row[idx] if idx < len(row) else ""
            run = cells[idx].paragraphs[0].add_run(value)
            _set_font(run, size_pt=FONT_SIZE_TABLE_PT)
            if table.numeric and idx > 0:
                cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT


def _add_sources_table(doc: DocxDocument, sources: list[SourceStatus]) -> None:
    """소스별 수집 현황 — result.html의 「어디서 가져왔나」표와 같은 문구."""
    _add_heading(doc, HEADING_COLLECTION, level=2)
    table = doc.add_table(rows=1, cols=len(SOURCES_TABLE_HEADERS))
    table.style = TABLE_STYLE

    for idx, head in enumerate(SOURCES_TABLE_HEADERS):
        cell = table.rows[0].cells[idx]
        run = cell.paragraphs[0].add_run(head)
        _set_font(run, bold=True, size_pt=FONT_SIZE_TABLE_PT)
        _shade_cell(cell, COLOR_TABLE_HEADER_HEX)

    for source in sources:
        cells = table.add_row().cells
        name_run = cells[0].paragraphs[0].add_run(source.name)
        _set_font(name_run, size_pt=FONT_SIZE_TABLE_PT)

        state_run = cells[1].paragraphs[0].add_run(
            SOURCE_STATE_LABEL.get(source.state, source.state)
        )
        _set_font(state_run, size_pt=FONT_SIZE_TABLE_PT)
        cells[1].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        detail_run = cells[2].paragraphs[0].add_run(source.detail)
        _set_font(detail_run, size_pt=FONT_SIZE_TABLE_PT)


# ══════════════════════════════════════════════════════════
# 상단 — 회사명·부제·등급 라벨
# ══════════════════════════════════════════════════════════


def _add_subtitle(doc: DocxDocument, report: Report) -> None:
    """`{직무} · {법인형태}[ · {생성일} 생성]` — result.html의 `.lede`와 같은 문구."""
    paragraph = doc.add_paragraph()
    job_run = paragraph.add_run(report.job)
    _set_font(job_run)

    tail = f" · {report.corp_type}"
    if report.generated_at:
        tail += f" · {report.generated_at} 생성"
    tail_run = paragraph.add_run(tail)
    _set_font(tail_run, color_rgb=COLOR_MUTED_RGB)


def _add_grade_block(doc: DocxDocument, report: Report) -> None:
    """등급 라벨 — 완성이면 부르지 않는다. result.html의 `.grade` 블록과 같은 문구."""
    grade_value = report.grade.value
    icon = GRADE_ICON.get(grade_value, "")
    note = grade_message(report.grade, report.filled_count)
    color = GRADE_COLOR.get(grade_value)

    paragraph = doc.add_paragraph()
    if icon:
        icon_run = paragraph.add_run(f"{icon} ")
        _set_font(icon_run, bold=True, color_rgb=color)
    note_run = paragraph.add_run(note)
    _set_font(note_run, bold=True, color_rgb=color)

    for reason in report.shortfall_reasons:
        reason_paragraph = doc.add_paragraph(style=BULLET_STYLE)
        reason_run = reason_paragraph.add_run(reason)
        _set_font(reason_run)


# ══════════════════════════════════════════════════════════
# 본문 — 항목(칸)
# ══════════════════════════════════════════════════════════


def _add_section(doc: DocxDocument, section: ReportSection) -> None:
    _add_heading(doc, f"{section.cell}. {section.title}", level=2)
    if not section.is_filled:
        _add_empty_block(doc, section.empty_reason)
        return
    if section.prose_lines:
        _add_cited_prose(doc, section.prose_lines)
        if section.lines:
            _add_muted_paragraph(
                doc,
                f"{RAW_SOURCE_LABEL} ({len(section.lines)}문장) — {RAW_SOURCE_NOTE}",
            )
            for text, cite in section.lines:
                _add_bullet_line(doc, text, cite)
    else:
        for text, cite in section.lines:
            _add_bullet_line(doc, text, cite)
    for table in section.tables:
        _add_report_table(doc, table)


def _add_requirements_block(doc: DocxDocument, report: Report) -> None:
    """5번 「이 자리는 뭘 요구하나」 — 공고 원문 목록. result.html의
    `requirements_block()` 매크로와 같은 문구·순서.
    """
    _add_heading(doc, f"{REQUIREMENTS_CELL}. {CELL_LABELS[REQUIREMENTS_CELL]}", level=2)
    _add_muted_paragraph(doc, REQUIREMENTS_NOTE)
    if not report.requirements:
        _add_empty_block(doc, REQUIREMENTS_EMPTY_REASON)
        return
    for requirement in report.requirements:
        _add_bullet_line(doc, requirement, "")


def _add_citations(doc: DocxDocument, report: Report) -> None:
    """출처 — `render_sources()` 결과를 그대로 옮긴다. 따로 그리지 않는다 (P3)."""
    if not report.citations:
        return
    citations = cast(list[Source], report.citations)

    _add_heading(doc, HEADING_SOURCES, level=2)
    _add_muted_paragraph(doc, CITATIONS_NOTE)

    for line in render_sources(citations).splitlines():
        if line == SOURCES_HEADER:
            # ★ 워드에는 위에 이미 "출처" 제목이 있어 같은 말(「[출처]」)을
            #   또 적지 않는다. 출처 한 줄 한 줄의 표기(날짜·언론사 등)는
            #   여기서 다시 만들지 않고 render_sources()가 만든 그대로 쓴다.
            continue
        _add_plain_paragraph(doc, line)


# ══════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════


def build_docx(report: Report) -> bytes:
    """보고서 하나를 워드(.docx) 바이트로 만든다.

    화면(`result.html`)과 같은 순서로 훑는다:
      상단 라벨(부분 보고서일 때만) → 보고서 본문(칸 1~9·附) → 출처 → 수집 현황.

    Args:
        report: 06 검증을 통과한(또는 부분 통과한) 보고서 원본.

    Returns:
        `.docx` 파일 바이트. 디스크에 쓰지 않는다 — 부르는 쪽(웹)이 그대로
        내려보낸다.
    """
    doc = Document()
    _set_margins(doc)
    _set_default_style(doc)

    _add_heading(doc, report.company, level=0)
    _add_subtitle(doc, report)

    if report.grade is not Grade.COMPLETE:
        _add_grade_block(doc, report)

    # ★ result.html과 같은 순서 — 5번 칸을 만나면 요구역량 목록을 그 자리에
    #   넣는다. 기록에 5번 칸 자체가 없으면(순서가 흐트러진 저장본 등)
    #   마지막에 한 번 더 넣어 요구역량이 빠지지 않게 한다.
    shown_requirements = False
    for section in report.sections:
        if section.cell == REQUIREMENTS_CELL:
            _add_requirements_block(doc, report)
            shown_requirements = True
            continue
        _add_section(doc, section)
    if not shown_requirements:
        _add_requirements_block(doc, report)

    _add_citations(doc, report)
    if report.sources:
        _add_sources_table(doc, report.sources)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════
# 연결 지점 — 웹이 다운로드 응답을 만들 때 쓸 값
# ══════════════════════════════════════════════════════════


def build_download_filename(report: Report) -> str:
    """다운로드 파일명 — `회사명_직무_YYYY-MM-DD.docx`.

    정본: 확정/07_출력/1_흐름/01_세형태.md §워드로 받기

    ★ 생성일을 쓴다(다운로드한 «오늘»이 아니다) — 「다시 내보내기」해도 같은
      문서가 나와야 하므로(정본 §다시 내보내기) 파일명도 같아야 한다.
      기록에 생성일이 없으면(예외적) 오늘 날짜로 대신한다.

    Args:
        report: 파일명을 만들 보고서.

    Returns:
        금지 문자(`\\ / : * ? " < > |`)를 지운 파일명.
    """
    date = report.generated_at.strip() or dt.date.today().isoformat()
    company = FILENAME_FORBIDDEN_CHARS.sub("", report.company).strip()
    job = FILENAME_FORBIDDEN_CHARS.sub("", report.job).strip()
    return FILENAME_PATTERN.format(
        company=company or FILENAME_FALLBACK,
        job=job or FILENAME_FALLBACK,
        date=date,
    )


def build_ascii_filename(filename: str) -> str:
    """한글 파일명에서 «안전한» ASCII 대체 이름을 만든다 (문제로그 P-77).

    ★ 왜 그냥 한글만 지우면 안 되나 — 지우고 남은 찌꺼기가 그대로 헤더에 들어간다.
      실제 사고: `루트로닉_고전압 파워 R&D 연구원_2026-08-15.docx` →
      `_  R&D _2026-08-15.docx` (밑줄로 시작·겹공백·`&`).
      이런 값은 브라우저·보안 프로그램에 따라 **헤더 전체를 무시**하게 만들고,
      그러면 파일명이 통째로 사라져 임의 이름(GUID)으로 저장된다.

    ★ 그래서 «남기는» 규칙으로 뒤집는다 — 영문·숫자·점·밑줄·붙임표만 남긴다.
      쓸 만한 글자가 없으면 순수 ASCII 자리표시자를 쓴다.
      ⚠️ 자리표시자에 한글을 쓰면 안 된다 — HTTP 헤더에 못 들어간다.

    Args:
        filename: `build_download_filename()`이 만든 한글 파일명.

    Returns:
        `.docx`로 끝나는, 순수 ASCII 파일명.
    """
    stem = filename[: -len(DOCX_SUFFIX)] if filename.endswith(DOCX_SUFFIX) else filename
    cleaned = FILENAME_ASCII_ALLOWED.sub("_", stem).strip("_.-")
    if len(cleaned) < FILENAME_ASCII_MIN_STEM:
        cleaned = FILENAME_ASCII_FALLBACK
    elif not any(char.isalpha() for char in cleaned):
        # 회사명·직무가 전부 한글이면 날짜만 남는다 — 「2026-08-15.docx」는
        # 이름 구실을 못 하므로 앞에 자리표시자를 붙인다.
        cleaned = f"{FILENAME_ASCII_FALLBACK}_{cleaned}"
    return f"{cleaned}{DOCX_SUFFIX}"


def build_content_disposition(filename: str) -> str:
    """다운로드 파일명을 `Content-Disposition` 헤더 값으로 만든다.

    ★ 왜 필요한가 — HTTP 헤더 값은 라틴-1(ASCII 계열)만 허용해 한글 파일명을
      그대로 넣을 수 없다. RFC 6266/5987의 `filename*=UTF-8''...` 확장을
      같이 넣으면, 최신 브라우저는 원래 한글 이름으로 받고 그 확장을 모르는
      옛 브라우저는 `filename=`의 ASCII 대체 이름으로 받는다(깨지지 않고
      다운로드 자체는 된다).

    Args:
        filename: `build_download_filename()`이 만든 한글 파일명.

    Returns:
        `attachment; filename="..."; filename*=UTF-8''...` 형태의 헤더 값.
    """
    encoded = urllib.parse.quote(filename)
    return (
        f'attachment; filename="{build_ascii_filename(filename)}"; '
        f"filename*=UTF-8''{encoded}"
    )


__all__ = [
    "CONTENT_TYPE_DOCX",
    "build_ascii_filename",
    "build_content_disposition",
    "build_docx",
    "build_download_filename",
]
