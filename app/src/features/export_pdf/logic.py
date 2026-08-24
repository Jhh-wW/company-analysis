"""검증된 ``Report`` 하나를 검색 가능한 한국어 PDF로 내보낸다.

화면·DOCX·Notion과 별도의 사실을 만들지 않는다. canonical 출고 게이트가
확정한 표지, 핵심 요약, 본문, 표와 출처만 읽으며 직무·수집 과정·AI 제작 같은
내부 메타문구는 넣지 않는다. 다만 부분 보고서는 완성본으로 오해되지 않도록
공개 등급과 표준 미제공 사유를 PDF에도 명시한다.

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
from typing import Final, Iterable, Sequence, cast

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
    KeepTogether,
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
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.validate import validate_v2
from src.features.export_pdf import constants
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.provenance.sources import Source, SourceKind, visible_citations
from src.features.report_standard import build_published_report
from src.features.report_standard.constants import SECTION_BY_ID, TIME_SECTION_IDS
from src.features.report_standard.cover_metrics import CoverMetrics, cover_metrics
from src.features.report_standard.section_content import (
    SectionContentBlock,
    section_content_blocks,
    source_verification_label,
    summary_topic,
)
from src.features.report_standard.visualization import (
    composition_tone,
    TableVisualization,
    table_visualization,
)

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


class _HorizontalRule(Flowable):
    """장 제목 아래에 두는 문서 폭의 얇은 검은 구분선."""

    def __init__(self, width: float, *, thickness: float = 1.0) -> None:
        super().__init__()
        self.width = width
        self.height = thickness
        self.thickness = thickness
        self.keepWithNext = True

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        return (min(self.width, avail_width), self.height)

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        canvas.setStrokeColor(colors.HexColor(constants.COLOR_INK))
        canvas.setLineWidth(self.thickness)
        canvas.line(0, self.height / 2, self.width, self.height / 2)


#: 범례 한 줄의 높이와 열 수. 항목이 늘면 높이도 늘어야 글이 겹치지 않는다.
_LEGEND_ROW_MM: Final[float] = 5.2
_LEGEND_COLUMNS: Final[int] = 2
#: 막대와 여백이 차지하는 고정분 (범례 제외).
_COMPOSITION_FIXED_MM: Final[float] = 15.4


def _composition_color(
    palette: "tuple[str, ...]", index: int, count: int
) -> str:
    """칸 번호에 색을 준다 — 화면과 «같은 규칙»을 쓴다.

    ★ 색 고르는 규칙은 report_standard.visualization에 «한 벌»만 둔다.
      PDF가 따로 계산하면 화면과 인쇄물의 색이 조용히 어긋난다.
    """
    return palette[composition_tone(index, count)]


class _CompositionGraphic(Flowable):
    """100% 누적 막대와 직접 라벨.

    ★ 높이를 «항목 수로 계산»한다. 예전에는 31mm 고정이라 범례가 두 줄
      (항목 4개)까지만 들어갔다 — 6개가 되면 마지막 줄이 도식 밖으로 나가
      다음 문단과 겹친다.
    """

    def __init__(self, visual: TableVisualization, width: float) -> None:
        super().__init__()
        self.visual = visual
        self.width = width
        legend_rows = -(-len(visual.items) // _LEGEND_COLUMNS)  # 올림 나눗셈
        self.height = (
            _COMPOSITION_FIXED_MM + legend_rows * _LEGEND_ROW_MM
        ) * mm

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = min(self.width, avail_width)
        return (self.width, self.height)

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        palette = constants.COMPOSITION_PALETTE
        item_count = len(self.visual.items)
        bar_y = self.height - (9 * mm)
        bar_height = 7 * mm
        x = 0.0
        for index, item in enumerate(self.visual.items):
            segment_width = self.width * max(0.0, item.ratio) / 100.0
            canvas.setFillColor(
                colors.HexColor(_composition_color(palette, index, item_count))
            )
            canvas.setStrokeColor(colors.HexColor(constants.COLOR_MUTED))
            canvas.setLineWidth(0.5)
            # ★ 칸마다 테두리를 두른다. 색 단계가 촘촘해질수록 이웃한 두 칸이
            #   붙어 보이는데, 얇은 선 하나면 몇 칸이든 항상 나뉘어 보인다.
            canvas.rect(
                x,
                bar_y,
                segment_width,
                bar_height,
                fill=1,
                stroke=1,
            )
            x += segment_width

        columns = 2
        column_width = self.width / columns
        for index, item in enumerate(self.visual.items):
            column = index % columns
            row = index // columns
            y = bar_y - (5.2 * mm) - (row * 5.2 * mm)
            color = colors.HexColor(_composition_color(palette, index, item_count))
            canvas.setFillColor(color)
            canvas.setStrokeColor(colors.HexColor(constants.COLOR_MUTED))
            canvas.rect(
                column * column_width,
                y + 1.2,
                7,
                7,
                fill=1,
                stroke=1,
            )
            canvas.setFillColor(colors.HexColor(constants.COLOR_MUTED))
            canvas.setFont(constants.FONT_REGULAR, 7.5)
            label = _single_line_pdf_text(f"{item.label}  {item.display}")
            canvas.drawString((column * column_width) + 11, y, label)


class _TrendGraphic(Flowable):
    """계열별 독립 0축을 쓰는 3~6시점 막대 그래프."""

    def __init__(self, visual: TableVisualization, width: float) -> None:
        super().__init__()
        self.visual = visual
        self.width = width
        self.height = 58 * mm

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = min(self.width, avail_width)
        return (self.width, self.height)

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        gap = 8 * mm
        count = max(1, len(self.visual.series))
        panel_width = (self.width - (gap * (count - 1))) / count
        panel_height = self.height - (2 * mm)
        for series_index, series in enumerate(self.visual.series):
            x0 = series_index * (panel_width + gap)
            canvas.setFillColor(colors.HexColor(constants.COLOR_HEADER))
            canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
            canvas.setLineWidth(0.55)
            canvas.roundRect(x0, 0, panel_width, panel_height, 4, fill=1, stroke=1)
            canvas.setFillColor(colors.HexColor(constants.COLOR_INK))
            canvas.setFont(constants.FONT_SEMIBOLD, 7.7)
            title = series.label
            if self.visual.unit:
                title = f"{title} ({self.visual.unit})"
            canvas.drawString(x0 + 8, panel_height - 12, _single_line_pdf_text(title))

            labels_y = 6
            positive_axis = 17
            negative_axis = panel_height - 24
            chart_height = max(24.0, panel_height - 48)
            group_width = (panel_width - 22) / max(1, len(series.points))
            bar_width = min(18.0, group_width * 0.48)
            axis_y = negative_axis if series.risk else positive_axis
            canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
            canvas.setLineWidth(0.45)
            canvas.line(x0 + 8, axis_y, x0 + panel_width - 8, axis_y)
            for point_index, point in enumerate(series.points):
                center_x = x0 + 11 + (group_width * point_index) + (group_width / 2)
                bar_height = chart_height * max(0.0, point.ratio) / 100.0
                bar_y = axis_y - bar_height if series.risk else axis_y
                canvas.setFillColor(
                    colors.HexColor(
                        constants.COLOR_RISK if series.risk else constants.COLOR_CHART_DARK
                    )
                )
                canvas.rect(
                    center_x - (bar_width / 2),
                    bar_y,
                    bar_width,
                    bar_height,
                    fill=1,
                    stroke=0,
                )
                canvas.setFont(constants.FONT_SEMIBOLD, 7.5)
                value_y = bar_y - 9 if series.risk else bar_y + bar_height + 3
                canvas.drawCentredString(center_x, value_y, _single_line_pdf_text(point.display))
                canvas.setFillColor(colors.HexColor(constants.COLOR_MUTED))
                canvas.setFont(constants.FONT_REGULAR, 7.5)
                canvas.drawCentredString(center_x, labels_y, _single_line_pdf_text(point.label))


class _FlowGraphic(Flowable):
    """표의 각 행을 3~4단계 왼쪽→오른쪽 흐름으로 표시한다."""

    def __init__(self, visual: TableVisualization, headers: Sequence[str], width: float) -> None:
        super().__init__()
        self.visual = visual
        self.headers = tuple(headers)
        self.width = width
        self.row_height = 18 * mm
        self.row_gap = 4 * mm
        self.height = (len(visual.flows) * self.row_height) + (
            max(0, len(visual.flows) - 1) * self.row_gap
        )

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = min(self.width, avail_width)
        return (self.width, self.height)

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        for row_index, flow in enumerate(self.visual.flows):
            gap = 8 * mm
            box_width = (self.width - (gap * (len(flow) - 1))) / len(flow)
            y = self.height - ((row_index + 1) * self.row_height) - (row_index * self.row_gap)
            for column, value in enumerate(flow):
                x = column * (box_width + gap)
                canvas.setFillColor(
                    colors.HexColor(constants.COLOR_HEADER if column % 2 else "#FFFFFF")
                )
                canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
                canvas.setLineWidth(0.55)
                canvas.roundRect(x, y, box_width, self.row_height, 4, fill=1, stroke=1)
                header_style = ParagraphStyle(
                    f"FlowHead-{row_index}-{column}",
                    fontName=constants.FONT_SEMIBOLD,
                    fontSize=7.5,
                    leading=9.5,
                    alignment=TA_CENTER,
                    textColor=colors.HexColor(constants.COLOR_MUTED),
                    wordWrap="CJK",
                )
                value_style = ParagraphStyle(
                    f"FlowValue-{row_index}-{column}",
                    fontName=constants.FONT_REGULAR,
                    fontSize=7.5,
                    leading=9.7,
                    alignment=TA_CENTER,
                    textColor=colors.HexColor(constants.COLOR_INK),
                    wordWrap="CJK",
                )
                header = Paragraph(_escape(self.headers[column]), header_style)
                body = Paragraph(_escape(value), value_style)
                _, header_height = header.wrap(box_width - 10, self.row_height)
                _, body_height = body.wrap(box_width - 10, self.row_height)
                total_height = header_height + body_height + 3
                body_y = y + ((self.row_height - total_height) / 2)
                body.drawOn(canvas, x + 5, body_y)
                header.drawOn(canvas, x + 5, body_y + body_height + 3)
                if column < len(flow) - 1:
                    start = x + box_width + 3
                    end = x + box_width + gap - 3
                    arrow_y = y + (self.row_height / 2)
                    canvas.setStrokeColor(colors.HexColor(constants.COLOR_INK))
                    canvas.setLineWidth(0.8)
                    canvas.line(start, arrow_y, end, arrow_y)
                    canvas.line(end, arrow_y, end - 4, arrow_y + 3)
                    canvas.line(end, arrow_y, end - 4, arrow_y - 3)


#: 표지 실적 띠가 들어가는 영역 (상단에서 mm).
#: 정본: docs/출력물 기준/90_공통_규칙/디자인과_PDF_QA.md 1절·6-1절.
#: 제목 블록(72~118mm)과 핵심 요약(190~262mm) 사이의 «고정» 자리라
#: 회사별 글 길이가 달라도 이 좌표는 움직이지 않는다.
_COVER_METRICS_TOP_MM: Final[float] = 138.0
_COVER_METRICS_BOTTOM_MM: Final[float] = 166.0
#: 그릴 때 쓰는 안쪽 여유(mm). Paragraph glyph ascender가 선언 좌표보다 위에
#: 찍히므로(핵심 요약이 191mm를 쓰는 것과 같은 이유) 이만큼 안쪽에서 시작해야
#: «실제로 보이는 글자»가 정본의 138mm 경계 안에 들어온다. 실측 1.5mm.
_COVER_METRICS_ASCENDER_INSET_MM: Final[float] = 1.5
#: 띠 값 글자 크기. 정본 6-1절의 15~18pt 범위이며 표지 제목(31pt)보다 작고
#: 장 제목(17pt)을 넘지 않는다.
_COVER_METRIC_VALUE_PT: Final[float] = 16.5
_COVER_METRIC_VALUE_LEADING_PT: Final[float] = 20.0
#: 제목 아래 구분선 — 표 테두리와 같은 0.55pt 회색 선을 쓴다.
_COVER_METRICS_RULE_PT: Final[float] = 0.55
#: 칸 사이 좌우 여백(pt). 값이 옆 칸 글자에 붙어 읽히지 않게 한다.
_COVER_METRICS_COLUMN_GAP_PT: Final[float] = 8.0
#: 제목·구분선·라벨·값 사이 세로 간격(pt).
_COVER_METRICS_TITLE_GAP_PT: Final[float] = 3.0
_COVER_METRICS_LABEL_GAP_PT: Final[float] = 7.0
_COVER_METRICS_VALUE_GAP_PT: Final[float] = 2.0


class _CoverContent(Flowable):
    """회사별 글 길이와 무관하게 표지 제목·실적 띠·요약을 정본 영역에 고정한다."""

    def __init__(
        self,
        report: Report,
        styles: dict[str, ParagraphStyle],
        width: float,
        height: float,
    ) -> None:
        super().__init__()
        self.report = report
        self.styles = styles
        self.width = width
        self.height = height

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = min(self.width, avail_width)
        return (self.width, min(self.height, avail_height))

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        title = Paragraph(
            f"{_escape(self.report.company)}<br/>분석 보고서",
            self.styles["cover_title"],
        )
        _, title_height = title.wrap(self.width, 50 * mm)
        title_top_page = A4[1] - (76 * mm)
        title_y = title_top_page - constants.PAGE_BOTTOM_MARGIN_PT - title_height
        title.drawOn(canvas, 0, title_y)

        metadata_bottom = title_y
        metadata = _cover_metadata(self.report)
        if metadata:
            meta = Paragraph(_escape(metadata), self.styles["cover_meta"])
            _, meta_height = meta.wrap(self.width, 15 * mm)
            meta.drawOn(canvas, 0, title_y - meta_height - 7)
            metadata_bottom = title_y - meta_height - 7

        if self.report.grade is Grade.PARTIAL:
            status = Paragraph(
                "<b>검증된 부분 보고서(부분 완성)</b><br/>"
                "공식 근거로 확인된 항목만 수록했습니다.",
                self.styles["cover_meta"],
            )
            _, status_height = status.wrap(self.width, 18 * mm)
            status.drawOn(canvas, 0, metadata_bottom - status_height - 12)

        # 표지 실적 띠 — 화면(result.html)과 «같은 순수 함수»가 고른 값만 쓴다.
        # 값이 없으면 아무것도 그리지 않고 예전처럼 표지 여백으로 남긴다.
        metrics = cover_metrics(self.report)
        if metrics:
            self._draw_cover_metrics(canvas, metrics)

        summary = _summary_table(self.report, self.styles, self.width)
        if summary is None:
            return
        summary_heading = Paragraph("핵심 요약", self.styles["heading"])
        _, heading_height = summary_heading.wrap(self.width, 20 * mm)
        # Paragraph glyph ascender가 선언 좌표보다 약 0.75mm 위에 찍히므로 1mm
        # 안쪽에 두어 실제 보이는 글자도 정본의 190mm 경계 안에 들어오게 한다.
        summary_top_page = A4[1] - (191 * mm)
        heading_y = summary_top_page - constants.PAGE_BOTTOM_MARGIN_PT - heading_height
        summary_heading.drawOn(canvas, 0, heading_y)
        rule_y = heading_y - 5
        canvas.setStrokeColor(colors.HexColor(constants.COLOR_INK))
        canvas.setLineWidth(1.0)
        canvas.line(0, rule_y, self.width, rule_y)
        _, table_height = summary.wrap(self.width, 70 * mm)
        summary.drawOn(canvas, 0, rule_y - 6 - table_height)

    def _draw_cover_metrics(self, canvas: Canvas, metrics: CoverMetrics) -> None:
        """4장 실적표의 최신 사업연도 행을 표지 정본 좌표에 다시 보여 준다.

        여기서 숫자를 만들지 않는다 — ``cover_metrics``가 표에서 글자 그대로
        옮겨 온 값만 배치한다 (정본 6-1절 「새로 계산한 숫자 금지」).
        """

        # 선언 좌표가 아니라 «실제로 보이는 글자»가 정본 영역 안에 들어와야 한다.
        # ascender 여유만큼 안쪽에서 시작한다 (핵심 요약이 191mm를 쓰는 것과 같다).
        band_top = (
            A4[1]
            - ((_COVER_METRICS_TOP_MM + _COVER_METRICS_ASCENDER_INSET_MM) * mm)
            - constants.PAGE_BOTTOM_MARGIN_PT
        )
        # 출처 번호는 4장 실적표와 «같은 것»을 쓴다. 표지에 새 출처를 만들지
        # 않으므로 부록 번호와 1:1이 깨지지 않는다.
        title = Paragraph(
            _escape(_cited_text(metrics.title, metrics.cite)),
            self.styles["cover_meta"],
        )
        _, title_height = title.wrap(self.width, 12 * mm)
        title.drawOn(canvas, 0, band_top - title_height)

        rule_y = band_top - title_height - _COVER_METRICS_TITLE_GAP_PT
        canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
        canvas.setLineWidth(_COVER_METRICS_RULE_PT)
        canvas.line(0, rule_y, self.width, rule_y)

        column_width = self.width / len(metrics.items)
        text_width = column_width - _COVER_METRICS_COLUMN_GAP_PT
        for index, item in enumerate(metrics.items):
            label = Paragraph(
                _escape(item.label), self.styles["cover_metric_label"]
            )
            # 단위는 값보다 작게 붙인다 — 표지에서 크게 읽혀야 하는 것은 숫자다.
            value = Paragraph(
                f"{_escape(item.value)} "
                f'<font name="{constants.FONT_REGULAR}" '
                f'size="{constants.SMALL_FONT_SIZE_PT}" '
                f'color="{constants.COLOR_MUTED}">{_escape(item.unit)}</font>',
                self.styles["cover_metric_value"],
            )
            _, label_height = label.wrap(text_width, 10 * mm)
            _, value_height = value.wrap(text_width, 14 * mm)
            label_y = rule_y - _COVER_METRICS_LABEL_GAP_PT - label_height
            label.drawOn(canvas, index * column_width, label_y)
            value.drawOn(
                canvas,
                index * column_width,
                label_y - _COVER_METRICS_VALUE_GAP_PT - value_height,
            )


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
            return dt.date.fromisoformat(raw).strftime("%Y.%m.%d")
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(clock.KST).date().strftime("%Y.%m.%d")
        return parsed.date().strftime("%Y.%m.%d")
    except ValueError:
        return ""


def _citations(report: Report) -> list[Source]:
    """실제 ``Source``만 쓰고 같은 번호는 최종 표에 한 번만 낸다."""

    unique: list[Source] = []
    seen_numbers: set[int] = set()
    for item in visible_citations(report.citations):
        if item.number in seen_numbers:
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
            leading=35.5,
            alignment=TA_LEFT,
            textColor=ink,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=base["Normal"],
            fontName=constants.FONT_REGULAR,
            fontSize=7.7,
            leading=10.8,
            alignment=TA_LEFT,
            textColor=muted,
            spaceAfter=2,
            wordWrap="CJK",
        ),
        # 표지 실적 띠 — 라벨은 카드 본문 크기(8.4pt) 보조색, 값은 SemiBold.
        # 정본: 디자인과_PDF_QA.md 6-1절 「모양」.
        "cover_metric_label": ParagraphStyle(
            "CoverMetricLabel",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.CARD_FONT_SIZE_PT,
            leading=constants.CARD_LEADING_PT,
            alignment=TA_LEFT,
            textColor=muted,
            wordWrap="CJK",
        ),
        "cover_metric_value": ParagraphStyle(
            "CoverMetricValue",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=_COVER_METRIC_VALUE_PT,
            leading=_COVER_METRIC_VALUE_LEADING_PT,
            alignment=TA_LEFT,
            textColor=ink,
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
            spaceAfter=7,
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
        "card_title": ParagraphStyle(
            "ReportCardTitle",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.SUBHEADING_FONT_SIZE_PT,
            leading=14.0,
            textColor=ink,
            wordWrap="CJK",
        ),
        "card_label": ParagraphStyle(
            "ReportCardLabel",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.CARD_FONT_SIZE_PT,
            leading=constants.CARD_LEADING_PT,
            textColor=ink,
            wordWrap="CJK",
        ),
        "card_body": ParagraphStyle(
            "ReportCardBody",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.CARD_FONT_SIZE_PT,
            leading=constants.CARD_LEADING_PT,
            textColor=ink,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "ReportSmall",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.SMALL_FONT_SIZE_PT,
            leading=10.0,
            textColor=muted,
            spaceAfter=5,
            wordWrap="CJK",
        ),
        "small_bold": ParagraphStyle(
            "ReportSmallBold",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=10.0,
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
            leading=constants.TABLE_LEADING_PT,
            textColor=ink,
            wordWrap="CJK",
        ),
        "table_head": ParagraphStyle(
            "ReportTableHead",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=constants.TABLE_LEADING_PT,
            textColor=ink,
            wordWrap="CJK",
        ),
        "table_numeric": ParagraphStyle(
            "ReportTableNumeric",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=constants.TABLE_LEADING_PT,
            textColor=ink,
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
        "table_head_numeric": ParagraphStyle(
            "ReportTableHeadNumeric",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=constants.TABLE_LEADING_PT,
            textColor=ink,
            alignment=TA_RIGHT,
            wordWrap="CJK",
        ),
        "summary_number": ParagraphStyle(
            "SummaryNumber",
            parent=base["BodyText"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.TABLE_FONT_SIZE_PT,
            leading=10.0,
            textColor=colors.white,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "summary_ref": ParagraphStyle(
            "SummaryReference",
            parent=base["BodyText"],
            fontName=constants.FONT_REGULAR,
            fontSize=constants.SMALL_FONT_SIZE_PT,
            leading=10.0,
            textColor=muted,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
    }


def _cited_text(text: str, cite: str) -> str:
    marker = citation_marker(cite)
    return f"{text} {marker}" if marker else text


def _split_wide_table(table: ReportTable, *, max_columns: int = 5) -> list[ReportTable]:
    """일반표를 첫 열을 반복하는 최대 5열 표들로 나눈다."""

    width = len(table.headers)
    if width <= max_columns:
        return [table]
    chunks: list[ReportTable] = []
    value_columns = list(range(1, width))
    groups = [
        value_columns[start : start + (max_columns - 1)]
        for start in range(0, len(value_columns), max_columns - 1)
    ]
    for index, group in enumerate(groups, start=1):
        columns = [0, *group]
        suffix = "" if index == 1 else f" (계속 {index}/{len(groups)})"
        chunks.append(
            ReportTable(
                caption=f"{table.caption}{suffix}",
                headers=[table.headers[column] for column in columns],
                rows=[[row[column] for column in columns] for row in table.rows],
                cite=table.cite,
                numeric=table.numeric,
                raw_rows=(
                    [[row[column] for column in columns] for row in table.raw_rows]
                    if table.raw_rows
                    else []
                ),
                scale_divisor=table.scale_divisor,
                scale_places=table.scale_places,
                display_unit=table.display_unit,
                presentation="table",
                evidence_rows=list(table.evidence_rows),
            )
        )
    return chunks


def _add_report_visualization(
    story: list[Flowable],
    table: ReportTable,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> bool:
    visual = table_visualization(table)
    if visual is None:
        return False
    caption = _cited_text(table.caption, table.cite)
    if visual.kind == "composition":
        graphic: Flowable = _CompositionGraphic(visual, width)
    elif visual.kind == "trend":
        graphic = _TrendGraphic(visual, width)
    elif visual.kind == "flow":
        graphic = _FlowGraphic(visual, table.headers, width)
    else:
        return False
    source_note = _cited_text("자료", table.cite)
    note = f"{source_note} · {visual.note}" if visual.note else source_note
    story.append(
        KeepTogether(
            [
                Paragraph(_escape(caption), styles["small_bold"]),
                Spacer(1, 3),
                graphic,
                Spacer(1, 5),
                Paragraph(_escape(note), styles["small"]),
                Spacer(1, 12),
            ]
        )
    )
    return True


def _add_report_table(
    story: list[Flowable], table: ReportTable, styles: dict[str, ParagraphStyle], width: float
) -> None:
    if _add_report_visualization(story, table, styles, width):
        return
    chunks = _split_wide_table(table)
    if len(chunks) > 1:
        for chunk in chunks:
            _add_report_table(story, chunk, styles, width)
        return
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
    report: Report,
    section: ReportSection,
    styles: dict[str, ParagraphStyle],
    width: float,
    anchor: str,
) -> None:
    title = _section_heading(section)
    heading_flowables: list[Flowable] = [
        _OutlineAnchor(anchor, title, level=1),
        Paragraph(_section_heading_markup(section), styles["heading"]),
        _HorizontalRule(width),
        Spacer(1, 10),
    ]
    if not section.is_filled:
        story.extend(heading_flowables)
        return

    detail_blocks = section_content_blocks(report, section)
    if detail_blocks:
        first_content: list[Flowable] = []
        _add_section_content_block(
            first_content,
            detail_blocks[0],
            styles,
            width,
            wrap_card=False,
        )
        story.append(KeepTogether([*heading_flowables, *first_content]))
        for block in detail_blocks[1:]:
            _add_section_content_block(story, block, styles, width)
    elif section.prose_paragraphs:
        # ★ 문단 단위로 낸다 — 예전에는 한 장의 문장을 전부 이어 붙여 한
        #   덩어리로 냈다. 첫 문단만 제목과 함께 묶어 쪽 넘김에서 떨어지지
        #   않게 하고, 나머지는 이어서 흘린다.
        paragraphs = [
            Paragraph(_escape(text), styles["body"])
            for text in section.prose_paragraphs
        ]
        story.append(KeepTogether([*heading_flowables, paragraphs[0]]))
        story.extend(paragraphs[1:])
    elif section.prose_lines:
        prose = " ".join(_cited_text(text, cite) for text, cite in section.prose_lines)
        story.append(
            KeepTogether(
                [*heading_flowables, Paragraph(_escape(prose), styles["body"])]
            )
        )
    elif section.tables:
        first_content = []
        _add_report_table(first_content, section.tables[0], styles, width)
        story.append(KeepTogether([*heading_flowables, *first_content]))
    else:
        story.extend(heading_flowables)

    table_start = (
        1
        if not detail_blocks
        and not section.prose_lines
        and not section.prose_paragraphs
        else 0
    )
    for item in section.tables[table_start:]:
        _add_report_table(story, item, styles, width)


def _add_section_content_block(
    story: list[Flowable],
    block: SectionContentBlock,
    styles: dict[str, ParagraphStyle],
    width: float,
    *,
    wrap_card: bool = True,
) -> None:
    """공통 장별 블록을 정본 크기·여백을 지키는 한 개의 PDF 카드로 낸다."""

    markers = " ".join(f"[{number}]" for number in block.source_numbers)
    caption = " ".join(
        value for value in (block.title, markers) if str(value).strip()
    )
    if block.tone == "limitation":
        caption = f"확인 범위 · {caption}"
    data: list[list[Paragraph]] = [
        [
            Paragraph(_escape(caption), styles["card_title"]),
            Paragraph("", styles["card_body"]),
        ]
    ]
    data.extend(
        [
            Paragraph(_escape(field.label), styles["card_label"]),
            Paragraph(_escape(field.value), styles["card_body"]),
        ]
        for field in block.fields
    )
    card = Table(
        data,
        colWidths=[width * 0.29, width * 0.71],
        hAlign="LEFT",
    )
    card.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (1, 0)),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(constants.COLOR_LINE)),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(constants.COLOR_LINE)),
                ("LINEBELOW", (0, 1), (-1, -2), 0.45, colors.HexColor(constants.COLOR_LINE)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    card_flowables: list[Flowable] = [card, Spacer(1, 14)]
    if wrap_card:
        story.append(KeepTogether(card_flowables))
    else:
        story.extend(card_flowables)


def _section_heading(section: ReportSection) -> str:
    if section.display_number:
        return f"{section.display_number}. {section.title}"
    return section_display_heading(section.cell, section.title)


def _section_heading_markup(section: ReportSection) -> str:
    title = _escape(_section_heading(section))
    if section.cell not in TIME_SECTION_IDS or not section.tag.strip():
        return title
    tag = _escape(section.tag.strip())
    return (
        f'{title}&nbsp;&nbsp;<font name="{constants.FONT_REGULAR}" '
        f'size="{constants.SMALL_FONT_SIZE_PT}" color="{constants.COLOR_MUTED}">'
        f"{tag}</font>"
    )


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
            Paragraph("기준일·자료 상태", styles["table_head"]),
            Paragraph("사실 검증", styles["table_head"]),
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
                Paragraph(
                    _escape(source_verification_label(report, source.source_id)),
                    styles["table"],
                ),
                Paragraph(_escape(source.location.strip() or "—"), styles["table"]),
                Paragraph(_escape(_source_used_sections(source)), styles["table"]),
            ]
        )
    table = Table(
        rows,
        colWidths=[
            width * 0.06,
            width * 0.27,
            width * 0.20,
            width * 0.15,
            width * 0.18,
            width * 0.14,
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
    display_date = _display_report_date(report)
    values = [
        f"기준일 {display_date}" if display_date else "",
        report.corp_type.strip(),
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
        topic = summary_topic(item.section_id)
        related = f"[{spec.display_number}장]" if spec is not None else ""
        sentence = _escape(item.text.strip())
        if related:
            sentence += (
                f'&nbsp;&nbsp;<font name="{constants.FONT_REGULAR}" '
                f'size="{constants.SMALL_FONT_SIZE_PT}" '
                f'color="{constants.COLOR_MUTED}">{related}</font>'
            )
        rows.append(
            [
                Paragraph(f"{index:02d}", styles["summary_number"]),
                Paragraph(_escape(topic), styles["table_head"]),
                Paragraph(sentence, styles["table"]),
            ]
        )
    table = Table(
        rows,
        colWidths=[12 * mm, 30 * mm, width - (42 * mm)],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor(constants.COLOR_LINE)),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(constants.COLOR_INK)),
                ("ROWBACKGROUNDS", (1, 0), (-1, -1), [colors.white, colors.HexColor(constants.COLOR_HEADER)]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
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

    items: list[Flowable] = [
        _OutlineAnchor(
            "cover", f"{_single_line_pdf_text(report.company)} 분석 보고서", level=0
        ),
        _OutlineAnchor("summary", "핵심 요약", level=0),
        _CoverContent(
            report,
            styles,
            width,
            A4[1] - constants.PAGE_TOP_MARGIN_PT - constants.PAGE_BOTTOM_MARGIN_PT,
        ),
        PageBreak(),
        _OutlineAnchor("report-body", "분석 본문", level=0),
    ]
    if report.grade is Grade.PARTIAL:
        items.extend(
            [
                _OutlineAnchor("partial-notice", "부분 보고서 안내", level=1),
                Paragraph("검증된 부분 보고서(부분 완성)", styles["heading"]),
                Paragraph(
                    "공식 근거로 확인된 항목만 제공합니다. 아래 미제공 사유는 "
                    "해당 사실이 없다는 판정이 아닙니다.",
                    styles["body"],
                ),
                *[
                    Paragraph(f"- {_escape(reason)}", styles["body"])
                    for reason in report.shortfall_reasons
                ],
                Spacer(1, 14),
            ]
        )
    return items


def _page_furniture(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    title = cast(str, getattr(doc, "report_title", "분석 보고서"))
    company = cast(str, getattr(doc, "report_company", ""))
    report_date = cast(str, getattr(doc, "report_date", ""))
    author = cast(str, getattr(doc, "report_author", constants.PDF_AUTHOR))
    subject = cast(str, getattr(doc, "report_subject", title))
    canvas.saveState()
    canvas.setTitle(title)
    canvas.setAuthor(author)
    canvas.setSubject(subject)
    if doc.page > 1:
        canvas.setFont(constants.FONT_REGULAR, constants.META_FONT_SIZE_PT)
        canvas.setFillColor(colors.HexColor(constants.COLOR_MUTED))
        y = A4[1] - 34
        canvas.drawString(constants.PAGE_MARGIN_PT, y, title)
        if report_date:
            canvas.drawRightString(
                A4[0] - constants.PAGE_MARGIN_PT, y, f"기준일 {report_date}"
            )
        canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
        canvas.setLineWidth(0.5)
        canvas.line(constants.PAGE_MARGIN_PT, y - 8, A4[0] - constants.PAGE_MARGIN_PT, y - 8)
        footer_line_y = 32
        canvas.line(
            constants.PAGE_MARGIN_PT,
            footer_line_y,
            A4[0] - constants.PAGE_MARGIN_PT,
            footer_line_y,
        )
        canvas.setFont(constants.FONT_REGULAR, constants.META_FONT_SIZE_PT)
        canvas.setFillColor(colors.HexColor(constants.COLOR_WEAK))
        canvas.drawString(constants.PAGE_MARGIN_PT, 24, company)
        canvas.drawRightString(A4[0] - constants.PAGE_MARGIN_PT, 24, str(doc.page))
    canvas.restoreState()


def _add_accessibility_metadata(
    raw_pdf: bytes, title: str, *, author: str, subject: str
) -> bytes:
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
            "/Subject": subject,
            "/Author": author,
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

    if report.schema_version == ENGINE_V2_SCHEMA_VERSION:
        # v2(엔진 v2 composer): v1 canonical 투영(build_published_report)은
        # v2 구조(빈 fact_records·다른 검증 방식)와 맞지 않아 태우지 않는다.
        # composer 자체 3검사(validate_v2)만 다시 확인하고 검증된 Report를
        # 그대로 조립한다 (실행계획 04장 3-4절 2항 — v1 경로는 무변, 분기는
        # schema_version 비교로만 나눈다).
        validate_v2(report)
    else:
        # 공개 내보내기는 저장 시점과 관계없이 현재 canonical 게이트를 다시 통과한다.
        # 구형 보고서를 호환 렌더링하면 폐기한 목차·직무 내용이 다시 노출될 수 있다.
        report = build_published_report(report)
    _register_fonts()
    title = f"{_single_line_pdf_text(report.company)} 분석 보고서"
    author = f"{constants.PDF_AUTHOR} · {_single_line_pdf_text(report.company)} 분석"
    subject = f"{_single_line_pdf_text(report.company)} 회사 분석 보고서"
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=constants.PAGE_MARGIN_PT,
        rightMargin=constants.PAGE_MARGIN_PT,
        topMargin=constants.PAGE_TOP_MARGIN_PT,
        bottomMargin=constants.PAGE_BOTTOM_MARGIN_PT,
        title=title,
        author=author,
        subject=subject,
        pageCompression=1,
    )
    setattr(document, "report_title", title)
    setattr(document, "report_company", _single_line_pdf_text(report.company))
    setattr(document, "report_date", _display_report_date(report))
    setattr(document, "report_author", author)
    setattr(document, "report_subject", subject)
    styles = _styles()
    story: list[Flowable] = _document_header(report, styles, document.width)
    for index, section in enumerate(report.sections):
        _add_section(
            story,
            report,
            section,
            styles,
            document.width,
            f"section-{index}",
        )

    _add_citations(story, report, styles)
    document.build(
        story,
        onFirstPage=_page_furniture,
        onLaterPages=_page_furniture,
        canvasmaker=_BrandedCanvas,
    )
    return _add_accessibility_metadata(
        buffer.getvalue(), title, author=author, subject=subject
    )


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


def _company_slug(value: str) -> str:
    """임의 영문 음역 없이 회사명의 Unicode 글자·숫자를 안정적인 slug로 만든다."""

    normalized = unicodedata.normalize("NFKC", _normalize_pdf_text(value)).casefold()
    parts: list[str] = []
    pending_separator = False
    for char in normalized:
        if char.isalnum():
            if pending_separator and parts:
                parts.append("-")
            parts.append(char)
            pending_separator = False
        else:
            pending_separator = bool(parts)
    slug = "".join(parts).strip("-")
    return slug[: constants.FILENAME_MAX_STEM].rstrip("-") or constants.FILENAME_FALLBACK


def build_download_filename(report: Report) -> str:
    """브라우저에 보낼 ``<company-slug>-company-analysis.pdf`` 이름을 만든다."""

    return constants.FILENAME_PATTERN.format(company_slug=_company_slug(report.company))


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
