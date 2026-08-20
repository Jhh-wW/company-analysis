"""검증된 ``Report`` 하나를 검색 가능한 한국어 PDF로 내보낸다.

화면·DOCX·Notion과 별도의 사실을 만들지 않는다. canonical 출고 게이트가
확정한 표지, 핵심 요약, 본문, 표와 출처만 읽으며 직무·수집 과정·완성도·AI 제작
메타문구는 최종 PDF에 넣지 않는다.

ReportLab 오픈소스판은 tagged PDF를 지원하지 않는다. 따라서 존재하지 않는
구조 트리를 흉내 내지 않고, 논리적인 그리기/텍스트 순서, 문서 제목, ``ko-KR``
언어, 제목 표시 환경설정, outline/bookmark와 실제 내장 한글 글꼴을 제공한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import re
import threading
import unicodedata
import urllib.parse
from collections import OrderedDict
from typing import Iterable, Sequence, cast

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    TextStringObject,
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.core import clock
from src.core.citations import citation_marker
from src.core.constants import section_display_heading
from src.features.export_pdf import constants
from src.features.pipeline.port import Report, ReportSection, ReportTable
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard import SECTION_BY_ID, build_published_report

_FONT_LOCK = threading.Lock()
_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|T|\s)")
_PDF_TEXT_REPLACEMENTS = (
    ("附", "참고"),
    ("⚠️", "주의:"),
    ("⚠", "주의:"),
    ("・", "·"),
)


class PDFGenerationError(RuntimeError):
    """PDF를 만들지 못했을 때 웹 경계가 안전한 503으로 바꿀 공개 오류."""


class _BrandedCanvas(Canvas):
    """기본 Helvetica 리소스조차 만들지 않는 Freesentation 전용 canvas."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("initialFontName", constants.FONT_REGULAR)
        kwargs.setdefault("initialFontSize", constants.BODY_FONT_SIZE_PT)
        # 승인된 PDF hash가 재다운로드 때 바뀌지 않도록 ReportLab의 생성시각·ID를
        # 입력 내용에 대해 결정적으로 만든다. 시각 승인은 이 최종 bytes에 결박된다.
        # BaseDocTemplate가 ``invariant=None``을 명시적으로 넘기므로 setdefault로는
        # 고정되지 않는다. 강제로 1을 넣어 CreationDate/ModDate도 내용 결정적으로 만든다.
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)


class _OutlineAnchor(Flowable):
    """보이는 자리를 차지하지 않으면서 PDF outline을 논리 순서대로 만든다."""

    def __init__(self, key: str, title: str, *, level: int = 0) -> None:
        super().__init__()
        self.key = key
        self.title = _single_line_pdf_text(title)
        self.level = level

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        return (0.0, 0.0)

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        canvas.bookmarkPage(self.key)
        canvas.addOutlineEntry(self.title, self.key, level=self.level, closed=False)


def _register_fonts() -> None:
    """웹과 같은 OFL Freesentation을 ReportLab에 한 번만 등록한다."""

    with _FONT_LOCK:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if constants.FONT_REGULAR not in registered:
            pdfmetrics.registerFont(
                TTFont(constants.FONT_REGULAR, str(constants.FONT_REGULAR_PATH))
            )
        if constants.FONT_SEMIBOLD not in registered:
            pdfmetrics.registerFont(
                TTFont(constants.FONT_SEMIBOLD, str(constants.FONT_SEMIBOLD_PATH))
            )
        pdfmetrics.registerFontFamily(
            "FreesentationPDF",
            normal=constants.FONT_REGULAR,
            bold=constants.FONT_SEMIBOLD,
            italic=constants.FONT_REGULAR,
            boldItalic=constants.FONT_SEMIBOLD,
        )


def _report_date(report: Report) -> dt.date:
    """유효한 생성일을 KST 일자로 읽고, 없거나 깨졌을 때만 KST 오늘을 쓴다."""

    raw = report.generated_at.strip()
    if raw and _ISO_DATE_PREFIX.match(raw):
        try:
            if len(raw) == 10:
                return dt.date.fromisoformat(raw)
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(clock.KST).date()
            return parsed.date()
        except ValueError:
            pass
    return clock.today_kst()


def _display_report_date(report: Report) -> str:
    """저장값이 ISO 날짜일 때만 표지와 머리말에 표시한다."""

    raw = report.as_of_date.strip()
    if not raw or _ISO_DATE_PREFIX.match(raw) is None:
        return ""
    try:
        if len(raw) == 10:
            return dt.date.fromisoformat(raw).isoformat()
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(clock.KST).date().isoformat()
        return parsed.date().isoformat()
    except ValueError:
        return ""


def _citations(report: Report) -> list[Source]:
    """실제 ``Source``만 쓰고 같은 번호는 최종 표에 한 번만 낸다."""

    unique: list[Source] = []
    seen_numbers: set[int] = set()
    for item in report.citations:
        if not isinstance(item, Source) or item.number in seen_numbers:
            continue
        seen_numbers.add(item.number)
        unique.append(item)
    return unique


def _source_category(source: Source) -> str:
    if source.kind is SourceKind.FILING:
        return "전자공시(DART)"
    if source.kind is SourceKind.NEWS:
        return "뉴스"
    label = _normalize_pdf_text(source.label).casefold()
    if any(token in label for token in ("홈페이지", "웹사이트", "website", "homepage")):
        return "회사 홈페이지"
    return "기타 자료"


def source_summary(report: Report) -> str:
    """실제 인용된 출처만 중복 제거해 종류별 개수로 요약한다."""

    unique: set[tuple[object, ...]] = set()
    counts: OrderedDict[str, int] = OrderedDict()
    for source in _citations(report):
        key = (
            source.number,
            source.kind,
            source.label,
            source.disclosed_at,
            source.collected_at,
            source.published_at,
            source.domain,
        )
        if key in unique:
            continue
        unique.add(key)
        category = _source_category(source)
        counts[category] = counts.get(category, 0) + 1
    if not counts:
        return "저장된 출처 없음"
    return " · ".join(f"{name} {count}건" for name, count in counts.items())


def _normalize_pdf_text(value: object) -> str:
    """현재 내장 글꼴이 못 그리는 표시문자를 뜻이 같은 검색 가능 텍스트로 바꾼다."""

    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    for source, replacement in _PDF_TEXT_REPLACEMENTS:
        text = text.replace(source, replacement)
    # BOM·bidi override/isolate·zero-width 같은 형식문자는 내용이 아니며, FE0F는
    # 앞의 경고 기호 치환 뒤 남을 수 있는 emoji variation selector다.
    safe: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if char == "\n":
            safe.append(char)
        elif char == "\ufe0f" or category == "Cf":
            continue
        elif category == "Cc":
            safe.append(" ")
        elif category == "Cs":
            safe.append("?")
        else:
            safe.append(char)
    return "".join(safe)


def _escape(value: object) -> str:
    return html.escape(_normalize_pdf_text(value), quote=False).replace("\n", "<br/>")


def _single_line_pdf_text(value: object) -> str:
    """PDF Title·outline·표지 metadata처럼 한 줄이어야 하는 경계를 접는다."""

    return re.sub(r"\s+", " ", _normalize_pdf_text(value)).strip()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    ink = colors.HexColor(constants.COLOR_INK)
    muted = colors.HexColor(constants.COLOR_MUTED)
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TITLE_FONT_SIZE_PT,
            leading=36,
            alignment=TA_LEFT,
            textColor=ink,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=base["Normal"],
            fontName=constants.FONT_REGULAR,
            fontSize=10.5,
            leading=15,
            alignment=TA_LEFT,
            textColor=muted,
            spaceAfter=2,
            wordWrap="CJK",
        ),
        "heading": ParagraphStyle(
            "ReportHeading",
            parent=base["Heading2"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.HEADING_FONT_SIZE_PT,
            leading=21,
            textColor=ink,
            spaceBefore=18,
            spaceAfter=9,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.BODY_FONT_SIZE_PT,
            leading=constants.BODY_LEADING_PT,
            textColor=ink,
            spaceAfter=7,
            wordWrap="CJK",
        ),
        "body_center": ParagraphStyle(
            "ReportBodyCenter",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.BODY_FONT_SIZE_PT,
            leading=constants.BODY_LEADING_PT,
            textColor=ink,
            alignment=TA_CENTER,
            spaceAfter=7,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.SMALL_FONT_SIZE_PT,
            leading=13.5,
            textColor=muted,
            spaceAfter=5,
            wordWrap="CJK",
        ),
        "small_bold": ParagraphStyle(
            "ReportSmallBold",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.SMALL_FONT_SIZE_PT,
            leading=13.5,
            textColor=ink,
            spaceAfter=4,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "ReportTable",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=12.5,
            textColor=ink,
            wordWrap="CJK",
        ),
        "table_head": ParagraphStyle(
            "ReportTableHead",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=12.5,
            textColor=ink,
            wordWrap="CJK",
        ),
        "table_numeric": ParagraphStyle(
            "ReportTableNumeric",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=12.5,
            textColor=ink,
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
        "table_head_numeric": ParagraphStyle(
            "ReportTableHeadNumeric",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=12.5,
            textColor=ink,
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
        "summary_number": ParagraphStyle(
            "SummaryNumber",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=13,
            textColor=colors.white,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "summary_ref": ParagraphStyle(
            "SummaryReference",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.SMALL_FONT_SIZE_PT,
            leading=13,
            textColor=muted,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
    }


def _cited_text(text: str, cite: str) -> str:
    marker = citation_marker(cite)
    return f"{text} {marker}" if marker else text


def _add_report_table(
    story: list[Flowable], table: ReportTable, styles: dict[str, ParagraphStyle], width: float
) -> None:
    caption = _cited_text(table.caption, table.cite)
    story.append(Paragraph(_escape(caption), styles["small_bold"]))

    max_columns = max(
        [len(table.headers), *(len(row) for row in table.rows)],
        default=0,
    )
    if max_columns == 0:
        return

    # ReportLab 표는 한 행을 다음 페이지로 쪼개지 못한다. 셀 원문을 없애지 않고
    # 물리 행 여러 개로 나눠, 비정상적으로 긴 공시 표도 LayoutError 없이 흐르게 한다.
    inner_width = max(8.0, (width / max_columns) - 12.0)
    chars_per_line = max(1, int(inner_width / (constants.TABLE_FONT_SIZE_PT * 0.9)))
    chars_per_chunk = max(36, chars_per_line * 34)

    def chunk_row(
        values: Sequence[str], cell_styles: Sequence[ParagraphStyle]
    ) -> list[list[Paragraph]]:
        padded = [*values, *("" for _ in range(max_columns - len(values)))]
        chunks = [
            [value[pos : pos + chars_per_chunk] for pos in range(0, len(value), chars_per_chunk)]
            or [""]
            for value in padded
        ]
        physical_rows: list[list[Paragraph]] = []
        for part in range(max(len(parts) for parts in chunks)):
            physical_rows.append(
                [
                    Paragraph(
                        _escape(parts[part] if part < len(parts) else ""),
                        cell_styles[index],
                    )
                    for index, parts in enumerate(chunks)
                ]
            )
        return physical_rows

    data: list[list[Paragraph]] = []
    repeat_rows = 0
    if table.headers:
        header_styles = [
            styles["table_head_numeric"] if table.numeric and index > 0 else styles["table_head"]
            for index in range(max_columns)
        ]
        header_rows = chunk_row(table.headers, header_styles)
        data.extend(header_rows)
        repeat_rows = len(header_rows)
    body_styles = [
        styles["table_numeric"] if table.numeric and index > 0 else styles["table"]
        for index in range(max_columns)
    ]
    for row in table.rows:
        data.extend(chunk_row(row, body_styles))
    if not data:
        return

    col_widths = [width / max_columns] * max_columns
    report_table = Table(
        data,
        colWidths=col_widths,
        repeatRows=repeat_rows,
        hAlign="LEFT",
    )
    commands: list[tuple[object, ...]] = [
        ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor(constants.COLOR_LINE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if table.headers:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(constants.COLOR_HEADER)))
    if table.numeric and max_columns > 1:
        commands.append(("ALIGN", (1, 0), (-1, -1), "RIGHT"))
    report_table.setStyle(TableStyle(commands))
    story.extend([report_table, Spacer(1, 9)])


def _add_section(
    story: list[Flowable],
    section: ReportSection,
    styles: dict[str, ParagraphStyle],
    width: float,
    anchor: str,
) -> None:
    title = _section_heading(section)
    story.extend([_OutlineAnchor(anchor, title, level=1), Paragraph(_escape(title), styles["heading"])])
    if not section.is_filled:
        return

    if section.prose_lines:
        prose = " ".join(_cited_text(text, cite) for text, cite in section.prose_lines)
        story.append(Paragraph(_escape(prose), styles["body"]))
    for item in section.tables:
        _add_report_table(story, item, styles, width)


def _section_heading(section: ReportSection) -> str:
    if section.display_number:
        tag = f"  {section.tag}" if section.tag else ""
        return f"{section.display_number}. {section.title}{tag}"
    return section_display_heading(section.cell, section.title)


def _add_citations(story: list[Flowable], report: Report, styles: dict[str, ParagraphStyle]) -> None:
    citations = _citations(report)
    if not citations:
        return
    story.extend(
        [
            # 부록 표가 앞 장 끝에서 잘려 다음 장에 맥락 없는 행 몇 개만 남지
            # 않도록 본문 1~9장과 출처 원장을 페이지 경계로 분리한다.
            PageBreak(),
            _OutlineAnchor("sources", "부록. 출처와 검증 상태", level=0),
            Paragraph("부록. 출처와 검증 상태", styles["heading"]),
            Paragraph(_escape(constants.CITATIONS_NOTE), styles["small"]),
        ]
    )
    width = A4[0] - (constants.PAGE_MARGIN_PT * 2)
    rows: list[list[Paragraph]] = [
        [
            Paragraph("#", styles["table_head"]),
            Paragraph("자료", styles["table_head"]),
            Paragraph("기준일·상태", styles["table_head"]),
            Paragraph("원문 위치", styles["table_head"]),
            Paragraph("본문 사용 장", styles["table_head"]),
        ]
    ]
    for source in citations:
        rows.append(
            [
                Paragraph(_escape(str(source.number)), styles["table"]),
                Paragraph(_source_label_markup(source), styles["table"]),
                Paragraph(_escape(_source_status(source)), styles["table"]),
                Paragraph(_escape(source.location.strip() or "—"), styles["table"]),
                Paragraph(_escape(_source_used_sections(source)), styles["table"]),
            ]
        )
    table = Table(
        rows,
        colWidths=[
            width * 0.06,
            width * 0.35,
            width * 0.25,
            width * 0.19,
            width * 0.15,
        ],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor(constants.COLOR_LINE)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(constants.COLOR_HEADER)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([table, Spacer(1, 5)])


def _source_label(source: Source) -> str:
    """최종 보고서에 보일 문서명과 발행 주체를 간결하게 묶는다."""

    label = (source.title or source.label).strip()
    publisher = source.publisher.strip()
    if publisher and publisher.casefold() not in label.casefold():
        return f"{label} · {publisher}"
    return label


def _source_label_markup(source: Source) -> str:
    """원문 URL이 있으면 무채색 밑줄 링크로, 없으면 같은 문서명으로 낸다."""

    label = _escape(_source_label(source))
    url = source.url.strip()
    if not url.startswith(("https://", "http://")):
        return label
    escaped_url = html.escape(_normalize_pdf_text(url), quote=True)
    return (
        f'<link href="{escaped_url}" color="{constants.COLOR_INK}">'
        f"<u>{label}</u></link>"
    )


def _source_status(source: Source) -> str:
    """내부 수집 절차 대신 독자가 판단할 기준일과 사실 상태만 보여 준다."""

    parts: list[str] = []
    if source.published_at:
        parts.append(f"{source.published_at} 보도")
    elif source.disclosed_at:
        parts.append(f"{source.disclosed_at} 공시")
    elif source.collected_at:
        parts.append(f"{source.collected_at} 확인")
    else:
        parts.append("기준일 미확인")
    for value in (source.domain, source.source_type, source.fact_status):
        cleaned = value.strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return " · ".join(parts)


def _source_used_sections(source: Source) -> str:
    labels: list[str] = []
    for section_id in source.used_in:
        spec = SECTION_BY_ID.get(section_id)
        label = f"{spec.display_number}장" if spec is not None else section_id.strip()
        if label and label not in labels:
            labels.append(label)
    return " · ".join(labels) or "—"


def _cover_metadata(report: Report) -> str:
    values = [
        report.corp_type.strip(),
        f"{report.as_of_date.strip()} 기준" if report.as_of_date.strip() else "",
        report.analysis_period.strip(),
        (
            f"최신 실적 {report.latest_performance_period.strip()}"
            if report.latest_performance_period.strip()
            else ""
        ),
    ]
    return " · ".join(value for value in values if value)


def _summary_table(
    report: Report,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> Table | None:
    if not report.summary_items:
        return None
    rows: list[list[Paragraph]] = []
    for index, item in enumerate(report.summary_items, start=1):
        spec = SECTION_BY_ID.get(item.section_id)
        related = f"{spec.display_number}장" if spec is not None else ""
        rows.append(
            [
                Paragraph(f"{index:02d}", styles["summary_number"]),
                Paragraph(_escape(item.text.strip()), styles["table"]),
                Paragraph(related, styles["summary_ref"]),
            ]
        )
    table = Table(
        rows,
        colWidths=[width * 0.08, width * 0.79, width * 0.13],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor(constants.COLOR_LINE)),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(constants.COLOR_INK)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _document_header(
    report: Report,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> list[Flowable]:
    """표지의 제목과 0장 핵심 요약을 만들고 본문을 다음 페이지에서 시작한다."""

    title = f"{_single_line_pdf_text(report.company)} 분석 보고서"
    metadata = _cover_metadata(report)
    items: list[Flowable] = [
        _OutlineAnchor("cover", title, level=0),
        Spacer(1, 54 * mm),
        Paragraph(f"{_escape(report.company)}<br/>분석 보고서", styles["cover_title"]),
    ]
    if metadata:
        items.append(Paragraph(_escape(metadata), styles["cover_meta"]))
    summary = _summary_table(report, styles, width)
    if summary is not None:
        items.extend(
            [
                Spacer(1, 30 * mm),
                _OutlineAnchor("summary", "핵심 요약", level=0),
                Paragraph("핵심 요약", styles["heading"]),
                summary,
            ]
        )
    items.extend(
        [
        PageBreak(),
        _OutlineAnchor("report-body", "분석 본문", level=0),
        ]
    )
    return items


def _page_furniture(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    title = cast(str, getattr(doc, "report_title", "분석 보고서"))
    company = cast(str, getattr(doc, "report_company", ""))
    report_date = cast(str, getattr(doc, "report_date", ""))
    canvas.saveState()
    canvas.setTitle(title)
    canvas.setAuthor("")
    canvas.setSubject("회사 중심 분석 보고서")
    if doc.page > 1:
        canvas.setFont(constants.FONT_REGULAR, constants.SMALL_FONT_SIZE_PT)
        canvas.setFillColor(colors.HexColor(constants.COLOR_MUTED))
        y = A4[1] - 34
        canvas.drawString(constants.PAGE_MARGIN_PT, y, title)
        if report_date:
            canvas.drawRightString(A4[0] - constants.PAGE_MARGIN_PT, y, report_date)
        canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
        canvas.setLineWidth(0.5)
        canvas.line(constants.PAGE_MARGIN_PT, y - 8, A4[0] - constants.PAGE_MARGIN_PT, y - 8)
        canvas.setFont(constants.FONT_REGULAR, 8.5)
        canvas.drawString(constants.PAGE_MARGIN_PT, 24, company)
        canvas.drawRightString(A4[0] - constants.PAGE_MARGIN_PT, 24, str(doc.page))
    canvas.restoreState()


def _add_accessibility_metadata(raw_pdf: bytes, title: str) -> bytes:
    """문서 제목·언어·제목 표시 설정을 추가한다(가짜 tagged 구조는 만들지 않는다)."""

    reader = PdfReader(io.BytesIO(raw_pdf), strict=True)
    writer = PdfWriter(clone_from=reader)

    # ReportLab canvas가 모든 페이지 시작에 실제 글자를 그리지 않는
    # ``BT /F1 12 Tf 14.4 TL ET``와 Helvetica 리소스를 자동으로 넣는다. 한국어
    # 텍스트는 전부 Freesentation으로 그렸으므로 이 빈 초기화만 제거해, 문서가
    # 시스템 기본 글꼴에 의존한다고 오해되지 않게 한다.
    font_candidates: list[tuple[DictionaryObject, NameObject]] = []
    used_fonts: set[tuple[int, str]] = set()
    for page in writer.pages:
        contents = page.get_contents()
        if contents is None:
            continue
        data = contents.get_data()
        resources = page.get("/Resources")
        if isinstance(resources, IndirectObject):
            resources = resources.get_object()
        fonts = resources.get("/Font") if isinstance(resources, DictionaryObject) else None
        if isinstance(fonts, IndirectObject):
            fonts = fonts.get_object()
        if not isinstance(fonts, DictionaryObject):
            continue
        changed = False
        for resource_name, font_ref in list(fonts.items()):
            font = font_ref.get_object() if isinstance(font_ref, IndirectObject) else font_ref
            if not isinstance(font, DictionaryObject) or str(font.get("/BaseFont", "")) != "/Helvetica":
                continue
            font_candidates.append((fonts, resource_name))
            name_bytes = re.escape(str(resource_name).encode("ascii"))
            empty_initializer = re.compile(
                rb"BT\s+"
                + name_bytes
                + rb"\s+[0-9.]+\s+Tf\s+[0-9.]+\s+TL\s+ET"
            )
            data, removed = empty_initializer.subn(b"", data)
            remaining_use = re.search(name_bytes + rb"\s+[0-9.]+\s+Tf", data)
            if remaining_use is not None:
                used_fonts.add((id(fonts), str(resource_name)))
            changed = changed or bool(removed)
        if changed:
            stream = DecodedStreamObject()
            stream.set_data(data)
            page[NameObject("/Contents")] = writer._add_object(stream)
            page.compress_content_streams()
    for fonts, resource_name in font_candidates:
        if (id(fonts), str(resource_name)) not in used_fonts and resource_name in fonts:
            del fonts[resource_name]

    writer.add_metadata(
        {
            "/Title": title,
            "/Subject": "회사 중심 분석 보고서",
            "/Author": "",
        }
    )
    root = writer._root_object
    root[NameObject("/Lang")] = TextStringObject("ko-KR")
    current_preferences = root.get("/ViewerPreferences")
    if isinstance(current_preferences, DictionaryObject):
        preferences = current_preferences
    else:
        preferences = DictionaryObject()
        root[NameObject("/ViewerPreferences")] = preferences
    preferences[NameObject("/DisplayDocTitle")] = BooleanObject(True)
    root[NameObject("/PageMode")] = NameObject("/UseOutlines")
    # PdfWriter는 clone한 ReportLab 문서의 trailer ID를 그대로 보존한다.
    # ReportLab의 invariant 옵션을 써도 그 ID는 프로세스마다 달라질 수 있어,
    # 동일 보고서의 승인 hash가 다음 요청에서 바뀌는 문제가 생긴다. 기존 ID만
    # 고정 토큰으로 치환한 원본 bytes를 seed로 삼아 내용 기반 ID를 다시 만든다.
    identifier_seed = re.sub(
        rb"/ID\s*\[\s*<[^>]*>\s*<[^>]*>\s*\]",
        b"/ID [<deterministic> <deterministic>]",
        raw_pdf,
    )
    identifier = ByteStringObject(hashlib.sha256(identifier_seed).digest()[:16])
    writer._ID = ArrayObject((identifier, identifier))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _build_pdf(report: Report) -> bytes:
    """``build_pdf``의 실제 생성 경로. 공개 경계에서 오류 문구를 정규화한다."""

    # 공개 내보내기는 저장 시점과 관계없이 현재 canonical 게이트를 다시 통과한다.
    # 구형 보고서를 호환 렌더링하면 폐기한 목차·직무 내용이 다시 노출될 수 있다.
    report = build_published_report(report)
    _register_fonts()
    title = f"{_single_line_pdf_text(report.company)} 분석 보고서"
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=constants.PAGE_MARGIN_PT,
        rightMargin=constants.PAGE_MARGIN_PT,
        topMargin=constants.PAGE_TOP_MARGIN_PT,
        bottomMargin=constants.PAGE_BOTTOM_MARGIN_PT,
        title=title,
        author="",
        subject="회사 중심 분석 보고서",
        pageCompression=1,
    )
    setattr(document, "report_title", title)
    setattr(document, "report_company", _single_line_pdf_text(report.company))
    setattr(document, "report_date", _display_report_date(report))
    styles = _styles()
    story: list[Flowable] = _document_header(report, styles, document.width)
    for index, section in enumerate(report.sections):
        _add_section(story, section, styles, document.width, f"section-{index}")

    _add_citations(story, report, styles)
    document.build(
        story,
        onFirstPage=_page_furniture,
        onLaterPages=_page_furniture,
        canvasmaker=_BrandedCanvas,
    )
    return _add_accessibility_metadata(buffer.getvalue(), title)


def build_pdf(report: Report) -> bytes:
    """``Report``를 PDF bytes로 만들고 내부 경로·내용이 담긴 오류를 숨긴다."""

    try:
        return _build_pdf(report)
    except PDFGenerationError:
        raise
    except Exception as exc:
        raise PDFGenerationError("PDF 보고서를 만들지 못했습니다.") from exc


def _safe_filename_stem(value: str) -> str:
    normalized = _normalize_pdf_text(value)
    # Cc: 제어문자, Cf: bidi override/isolate·zero-width 등 표시 형식문자.
    visible = "".join(
        char for char in normalized if unicodedata.category(char) not in {"Cc", "Cf"}
    )
    cleaned = constants.FILENAME_FORBIDDEN_CHARS.sub("", visible)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    return cleaned[: constants.FILENAME_MAX_STEM].rstrip(" ._-") or constants.FILENAME_FALLBACK


def build_download_filename(report: Report) -> str:
    """브라우저에 보낼 정확한 ``{회사명}_분석_보고서.pdf`` 이름을 만든다."""

    return constants.FILENAME_PATTERN.format(company=_safe_filename_stem(report.company))


def build_ascii_filename(filename: str) -> str:
    """옛 브라우저용 순수 ASCII 대체 이름을 만든다."""

    sanitized = constants.FILENAME_FORBIDDEN_CHARS.sub("", filename)
    stem = sanitized[: -len(constants.PDF_SUFFIX)] if sanitized.lower().endswith(constants.PDF_SUFFIX) else sanitized
    cleaned = constants.FILENAME_ASCII_ALLOWED.sub("_", stem).strip("_.-")
    if (
        len(cleaned) < 2
        or not any(char.isalpha() for char in cleaned)
        or constants.WINDOWS_RESERVED_STEM.fullmatch(cleaned) is not None
    ):
        cleaned = constants.FILENAME_ASCII_FALLBACK
    return f"{cleaned[:constants.FILENAME_MAX_STEM]}{constants.PDF_SUFFIX}"


def build_content_disposition(filename: str) -> str:
    """헤더 주입을 차단한 RFC 6266/5987 attachment 값을 만든다."""

    safe_stem = filename
    if safe_stem.lower().endswith(constants.PDF_SUFFIX):
        safe_stem = safe_stem[: -len(constants.PDF_SUFFIX)]
    safe_filename = f"{_safe_filename_stem(safe_stem)}{constants.PDF_SUFFIX}"
    encoded = urllib.parse.quote(safe_filename, safe="")
    return (
        f'attachment; filename="{build_ascii_filename(safe_filename)}"; '
        f"filename*=UTF-8''{encoded}"
    )


__all__ = [
    "PDFGenerationError",
    "build_ascii_filename",
    "build_content_disposition",
    "build_download_filename",
    "build_pdf",
    "source_summary",
]
