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
from src.features.export_pdf.content_manifest import (
    CONTENT_MANIFEST_VERSION,
    PDF_MANIFEST_SHA256_KEY,
    PDF_MANIFEST_VERSION_KEY,
    public_content_manifest_sha256,
)
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.provenance.sources import Source, SourceKind, visible_citations
from src.features.report_standard import build_published_report
from src.features.report_standard.constants import SECTION_BY_ID, TIME_SECTION_IDS
from src.features.report_standard.cover_metrics import cover_metrics
from src.features.report_standard.section_content import (
    SectionContentBlock,
    masthead_lines,
    section_content_blocks,
    source_verification_label,
    summary_topic,
)
from src.features.report_standard.period_summary import PeriodSummaryItem
from src.features.report_standard.visualization import (
    Card,
    CardField,
    ChartPoint,
    ChartSeries,
    composition_tone,
    TableVisualization,
    table_visualization,
)
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    PublicCoverMetricsBlock,
    PublicPeriodSummaryBlock,
    PublicReportProjection,
    PublicSectionContentBlock,
    PublicSectionDisplay,
    PublicTableBlock,
    PublicVisualBlock,
    build_report_digest,
)
from src.shared.report_quality.models import PublicationPolicy

_FONT_LOCK = threading.Lock()
_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}(?:$|T|\s)")
_PDF_TEXT_REPLACEMENTS = (
    ("附", "참고"),
    ("⚠️", "주의:"),
    ("⚠", "주의:"),
    ("・", "·"),
)


def _legacy_shadow_publication(report: Report) -> bool:
    return (
        report.publication_policy
        == PublicationPolicy.LEGACY_SHADOW_EXCEPTION.value
    )


def _partial_publication_copy(
    report: Report,
    *,
    detailed: bool,
) -> tuple[str, str]:
    """표지와 본문이 같은 공개 정책을 서로 다른 말로 꾸미지 않게 한다."""

    if _legacy_shadow_publication(report):
        detail = (
            "확인되지 않은 숫자 문장은 제외했지만 모든 문장·표·도식의 새 "
            "검증은 아직 끝나지 않았습니다. 아래에 남은 이유를 표시합니다."
            if detailed
            else "모든 문장·표·도식의 새 검증은 아직 끝나지 않았습니다."
        )
        return "안전 확인 중인 임시 부분 보고서", detail
    detail = (
        "공식 근거로 확인된 항목만 제공합니다. 아래 미제공 사유는 "
        "해당 사실이 없다는 판정이 아닙니다."
        if detailed
        else "공식 근거로 확인된 항목만 수록했습니다."
    )
    return "검증된 부분 보고서(부분 완성)", detail


def _grade_notice(
    report: Report,
    *,
    projection: PublicReportProjection | None,
    detailed: bool,
) -> tuple[str, str]:
    """공개 등급 고지 (제목, 본문). 고지가 없으면 두 값 다 빈 글자다.

    ★ 봉인이 있으면 ``grade_notice`` 한 벌을 표지와 본문이 «똑같이» 쓴다.
      지금까지 웹·PDF·Notion이 각자 사본을 들고 있었고 PDF 안에서도 표지와
      본문이 다른 문장을 썼다 — 같은 정책을 서로 다른 말로 꾸미지 않도록
      봉인 값 하나로 모은다(설계 §01-4 #4). 그래서 이 갈래에서는 ``detailed``가
      문구를 바꾸지 않는다.
    """

    if projection is not None:
        title, detail = projection.grade_notice
        return title, detail
    if report.grade is not Grade.PARTIAL:
        return "", ""
    return _partial_publication_copy(report, detailed=detailed)


def _cover_metric_rows(
    report: Report,
    *,
    projection: PublicReportProjection | None,
) -> tuple[str, str, tuple[tuple[str, str, str], ...]] | None:
    """표지 실적 띠 (제목, 출처, (라벨,값,단위) 행들). 그릴 게 없으면 ``None``."""

    if projection is not None:
        block: PublicCoverMetricsBlock | None = projection.cover_metrics
        if block is None or not block.items:
            return None
        return block.title, block.cite, block.items
    metrics = cover_metrics(report)
    if not metrics:
        return None
    return (
        metrics.title,
        metrics.cite,
        tuple((item.label, item.value, item.unit) for item in metrics.items),
    )


def _public_projection(report: Report) -> PublicReportProjection | None:
    """이 보고서가 «봉인 블록만 그리는» 갈래인지 판정한다.

    ★ v2 FULL 보고서에는 composer가 렌더 뒤 한 번 만든 공개 봉인
      (``report.public_projection``)이 붙어 있다. 붙어 있으면 PDF는 그 블록만
      배치하고 파생 문구를 다시 계산하지 않는다 — 채널마다 다른 글자가 나오던
      원인을 없앤다(설계 017 §7 S4).
    ★ 봉인이 없으면(``None``) v1 canonical·옛 v2 저장본이다. 그 갈래는 한
      글자도 건드리지 않는다(설계 §6 — legacy 무변).
    """

    if report.schema_version != ENGINE_V2_SCHEMA_VERSION:
        return None
    return report.public_projection


def _chart_point_from_block(item: tuple[str, str, str, bool]) -> ChartPoint:
    """봉인된 (label, display, ratio_text, below) 한 점을 도식 좌표로 되살린다.

    ★ ``value``는 PDF 도식이 쓰지 않는다 — 막대 길이는 ``ratio``, 인쇄되는
      글자는 ``display``에서 나온다. 봉인에 없는 값을 지어내지 않으려고 0.0으로
      둔다(이 값이 화면에 나오면 그건 결함이다).
    """

    label, display, ratio_text, below = item
    return ChartPoint(
        label=label,
        value=0.0,
        display=display,
        ratio=float(ratio_text),
        below=below,
    )


def _visual_from_block(block: PublicVisualBlock) -> TableVisualization:
    """봉인된 도식 블록을 그리기용 자료형으로 «옮기기만» 한다.

    새 문자열을 만들지 않는다 — 라벨·값·읽는 법·비고가 전부 블록 값 그대로다.
    ``table_visualization``(원본 표에서 다시 계산하는 순수 함수)은 이 갈래에서
    부르지 않는다.
    """

    return TableVisualization(
        kind=block.kind,
        caption=block.caption,
        unit=block.unit,
        note=block.note,
        reading=block.reading,
        items=tuple(_chart_point_from_block(item) for item in block.items),
        series=tuple(
            ChartSeries(
                label=label,
                points=tuple(
                    _chart_point_from_block(
                        (
                            str(point["label"]),
                            str(point["display"]),
                            str(point["ratio_text"]),
                            bool(point["below"]),
                        )
                    )
                    for point in points
                ),
                risk=bool(risk),
            )
            for label, risk, points in block.series
        ),
        flows=block.flows,
        cards=tuple(
            Card(
                title=title,
                fields=tuple(
                    CardField(label=str(label), value=str(value))
                    for label, value in fields["fields"]
                ),
            )
            for title, fields in block.cards
        ),
    )


def _period_summary_items_from_block(
    block: PublicPeriodSummaryBlock,
) -> tuple[PeriodSummaryItem, ...]:
    """봉인된 3개년 띠 열 개 필드를 표시용 자료형으로 되살린다.

    ★ ``PeriodSummaryItem``을 다시 쓰는 이유는 ``basis_text``(「2023년 5,665 →
      2025년 5,940」) 한 줄을 «화면과 같은 한 곳»에서 만들기 위해서다. PDF가
      그 문장을 따로 조립하면 웹과 갈라진다. 값은 전부 블록에서 온다.
    """

    return tuple(
        PeriodSummaryItem(
            label=item[0],
            base_period=item[1],
            base_value=item[2],
            latest_period=item[3],
            latest_value=item[4],
            unit=item[5],
            change=item[6],
            change_kind=item[7],
            direction=item[8],
            note=item[9],
        )
        for item in block.items
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


#: 범례 한 줄의 «최소» 높이(짧은 이름은 이 값 그대로) + 막대~첫 줄 간격,
#: 그리고 열 수. 항목이 늘면(줄 수) «그리고» 이름이 길면(줄 안 줄바꿈)
#: 모두 높이가 늘어야 글이 겹치지 않는다.
_LEGEND_ROW_MM: Final[float] = 5.2
_LEGEND_COLUMNS: Final[int] = 2
#: 막대와 여백이 차지하는 고정분 (범례 제외).
_COMPOSITION_FIXED_MM: Final[float] = 15.4
#: 범례 칸 안에서 스와치·여백이 차지하는 왼쪽 폭 + 다음 칸과의 여유(pt).
#: 글자가 실제로 쓸 수 있는 폭 = 칸 폭 − 이 값. draw()·측정 두 곳이 같은
#: 값을 써야 한다 — 안 맞으면 측정한 높이와 실제로 그리는 줄 수가 어긋난다.
_LEGEND_LABEL_INSET_PT: Final[float] = 17.0


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
    ★ 범례 «한 줄»도 내용 길이에 맞춰 늘어난다(2026-08-25, 팀장 실측 —
      하이브 「MD 및 라이선싱 공식 상품(MD), IP 라이선싱 등」처럼 긴
      이름이 고정폭 한 줄(``canvas.drawString``)로 찍혀 옆 칸 글자와
      겹쳤다). 최소 높이는 예전 5.2mm 그대로라 짧은 이름의 모양은 안
      바뀐다.
    """

    def __init__(self, visual: TableVisualization, width: float) -> None:
        super().__init__()
        self.visual = visual
        self.width = width
        self._legend_style = ParagraphStyle(
            "CompositionLegend",
            fontName=constants.FONT_REGULAR,
            fontSize=7.5,
            leading=9.0,
            textColor=colors.HexColor(constants.COLOR_MUTED),
            wordWrap="CJK",
        )
        self._row_heights = self._measure_legend_row_heights()
        self.height = (_COMPOSITION_FIXED_MM * mm) + sum(self._row_heights)

    @staticmethod
    def _legend_text(item: ChartPoint) -> str:
        return f"{item.label}  {item.display}"

    def _legend_available_width(self) -> float:
        column_width = self.width / _LEGEND_COLUMNS
        return max(10.0, column_width - _LEGEND_LABEL_INSET_PT)

    def _measure_legend_row_heights(self) -> list[float]:
        available = self._legend_available_width()
        items = self.visual.items
        heights: list[float] = []
        for row_start in range(0, len(items), _LEGEND_COLUMNS):
            row_items = items[row_start : row_start + _LEGEND_COLUMNS]
            tallest = _LEGEND_ROW_MM * mm
            for item in row_items:
                paragraph = Paragraph(_escape(self._legend_text(item)), self._legend_style)
                # 세로 공간을 사실상 무한대로 주고 «자연 높이»만 잰다.
                _, needed = paragraph.wrap(available, 200 * mm)
                tallest = max(tallest, needed)
            heights.append(tallest)
        return heights

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

        column_width = self.width / _LEGEND_COLUMNS
        available = self._legend_available_width()
        # 줄마다 «위쪽 y»를 미리 쌓아 둔다 — 줄 높이가 서로 달라서(긴 이름은
        # 여러 줄) row * 고정값으로는 다음 줄이 이전 줄과 겹칠 수 있다.
        row_tops: list[float] = []
        top = bar_y - (_LEGEND_ROW_MM * mm)
        for row_height in self._row_heights:
            row_tops.append(top)
            top -= row_height
        for index, item in enumerate(self.visual.items):
            column = index % _LEGEND_COLUMNS
            row = index // _LEGEND_COLUMNS
            row_top = row_tops[row]
            row_bottom = row_top - self._row_heights[row]
            color = colors.HexColor(_composition_color(palette, index, item_count))
            canvas.setFillColor(color)
            canvas.setStrokeColor(colors.HexColor(constants.COLOR_MUTED))
            canvas.rect(
                column * column_width,
                row_top - 7,
                7,
                7,
                fill=1,
                stroke=1,
            )
            canvas.setFillColor(colors.HexColor(constants.COLOR_MUTED))
            paragraph = Paragraph(_escape(self._legend_text(item)), self._legend_style)
            paragraph.wrap(available, self._row_heights[row])
            paragraph.drawOn(canvas, (column * column_width) + 11, row_bottom)


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
            # ★ 한 계열에 흑자와 적자가 «섞이면» 0선을 가운데에 두고 위아래로
            #   나눠 긋는다. 예전에는 그런 표를 아예 안 그렸는데, 하필 그 조건에
            #   걸리는 것이 «흑자→적자 전환»이라 가장 중요한 사실이 사라졌다.
            mixed = any(point.below for point in series.points) and any(
                not point.below for point in series.points
            )
            if mixed:
                axis_y = positive_axis + (chart_height / 2)
                chart_height = chart_height / 2
            else:
                axis_y = negative_axis if series.risk else positive_axis
            canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
            canvas.setLineWidth(0.45)
            canvas.line(x0 + 8, axis_y, x0 + panel_width - 8, axis_y)
            for point_index, point in enumerate(series.points):
                center_x = x0 + 11 + (group_width * point_index) + (group_width / 2)
                bar_height = chart_height * max(0.0, point.ratio) / 100.0
                # 아래로 그릴 값인가 — 점의 부호가 정본이고, 계열 전체가 손실인
                # 옛 경로(risk)도 같은 뜻이라 함께 본다.
                draws_down = point.below or series.risk
                bar_y = axis_y - bar_height if draws_down else axis_y
                canvas.setFillColor(
                    colors.HexColor(
                        constants.COLOR_RISK if draws_down else constants.COLOR_CHART_DARK
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
                value_y = bar_y - 9 if draws_down else bar_y + bar_height + 3
                canvas.drawCentredString(center_x, value_y, _single_line_pdf_text(point.display))
                canvas.setFillColor(colors.HexColor(constants.COLOR_MUTED))
                canvas.setFont(constants.FONT_REGULAR, 7.5)
                canvas.drawCentredString(center_x, labels_y, _single_line_pdf_text(point.label))


#: 흐름 상자 한 칸의 «최소» 높이. 예전엔 이 값이 고정 높이였다 — 짧은
#: 내용의 모양은 이 값 그대로 유지한다.
_FLOW_MIN_ROW_HEIGHT_MM: Final[float] = 18.0
#: 칸 위아래 안쪽 여백 + 라벨·값 사이 간격의 합(포인트). draw()가 실제로
#: 쓰는 5(좌우)·3(라벨-값 간격) 여백과 맞춰 둔다 — 안 맞으면 글자가
#: 상자 테두리에 닿는다.
_FLOW_ROW_PADDING_PT: Final[float] = 10.0


class _FlowGraphic(Flowable):
    """표의 각 행을 3~4단계 왼쪽→오른쪽 흐름으로 표시한다.

    ★ 줄 높이를 «내용 길이에 맞춰» 계산한다(2026-08-25, 카드 도입과 함께
      고침). 예전엔 18mm 고정이라 값이 길면 글자가 상자 밖으로 겹쳐
      나갔다 — 카드로 뺀 1·6·8장뿐 아니라 «진짜 흐름»으로 남긴 2·5·7장도
      AI가 긴 문장을 넣으면 같은 위험이 있었다. 최소 높이는 예전 18mm를
      그대로 둬서 짧은 내용의 모양은 바뀌지 않는다.
    """

    def __init__(self, visual: TableVisualization, headers: Sequence[str], width: float) -> None:
        super().__init__()
        self.visual = visual
        self.headers = tuple(headers)
        self.width = width
        self.row_gap = 4 * mm
        self._header_style = ParagraphStyle(
            "FlowHead",
            fontName=constants.FONT_SEMIBOLD,
            fontSize=7.5,
            leading=9.5,
            alignment=TA_CENTER,
            textColor=colors.HexColor(constants.COLOR_MUTED),
            wordWrap="CJK",
        )
        self._value_style = ParagraphStyle(
            "FlowValue",
            fontName=constants.FONT_REGULAR,
            fontSize=7.5,
            leading=9.7,
            alignment=TA_CENTER,
            textColor=colors.HexColor(constants.COLOR_INK),
            wordWrap="CJK",
        )
        self._row_heights = [self._measure_row_height(flow) for flow in visual.flows]
        self.height = sum(self._row_heights) + (
            max(0, len(visual.flows) - 1) * self.row_gap
        )

    def _box_width(self, flow: Sequence[str]) -> float:
        gap = 8 * mm
        return (self.width - (gap * (len(flow) - 1))) / len(flow)

    def _measure_row_height(self, flow: Sequence[str]) -> float:
        box_width = self._box_width(flow)
        tallest = _FLOW_MIN_ROW_HEIGHT_MM * mm
        for column, value in enumerate(flow):
            header = Paragraph(_escape(self.headers[column]), self._header_style)
            body = Paragraph(_escape(value), self._value_style)
            # 세로 공간을 사실상 무한대로 주고 «자연 높이»만 잰다 — 실제
            # 그리기는 이 높이로 만든 상자 안에서 다시 wrap()한다.
            _, header_height = header.wrap(box_width - 10, 1000 * mm)
            _, body_height = body.wrap(box_width - 10, 1000 * mm)
            needed = header_height + body_height + _FLOW_ROW_PADDING_PT
            tallest = max(tallest, needed)
        return tallest

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        self.width = min(self.width, avail_width)
        return (self.width, self.height)

    def draw(self) -> None:
        canvas = cast(Canvas, self.canv)
        for row_index, flow in enumerate(self.visual.flows):
            gap = 8 * mm
            row_height = self._row_heights[row_index]
            box_width = self._box_width(flow)
            y = self.height - sum(self._row_heights[: row_index + 1]) - (row_index * self.row_gap)
            for column, value in enumerate(flow):
                x = column * (box_width + gap)
                canvas.setFillColor(
                    colors.HexColor(constants.COLOR_HEADER if column % 2 else "#FFFFFF")
                )
                canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
                canvas.setLineWidth(0.55)
                canvas.roundRect(x, y, box_width, row_height, 4, fill=1, stroke=1)
                header = Paragraph(_escape(self.headers[column]), self._header_style)
                body = Paragraph(_escape(value), self._value_style)
                _, header_height = header.wrap(box_width - 10, row_height)
                _, body_height = body.wrap(box_width - 10, row_height)
                total_height = header_height + body_height + 3
                body_y = y + ((row_height - total_height) / 2)
                body.drawOn(canvas, x + 5, body_y)
                header.drawOn(canvas, x + 5, body_y + body_height + 3)
                if column < len(flow) - 1:
                    start = x + box_width + 3
                    end = x + box_width + gap - 3
                    arrow_y = y + (row_height / 2)
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
        projection: PublicReportProjection | None = None,
    ) -> None:
        super().__init__()
        self.report = report
        self.styles = styles
        self.width = width
        self.height = height
        #: 봉인이 있으면 표지 고지·실적 띠·핵심 요약을 «이 블록에서만» 그린다.
        self.projection = projection

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

        status_title, status_detail = _grade_notice(
            self.report, projection=self.projection, detailed=False
        )
        if status_title or status_detail:
            status = Paragraph(
                f"<b>{status_title}</b><br/>{status_detail}",
                self.styles["cover_meta"],
            )
            _, status_height = status.wrap(self.width, 18 * mm)
            status.drawOn(canvas, 0, metadata_bottom - status_height - 12)

        # 표지 실적 띠 — 화면(result.html)과 «같은 값»만 쓴다. 봉인이 있으면
        # 블록에서, 없으면(legacy) 예전처럼 순수 함수에서 고른다. 값이 없으면
        # 아무것도 그리지 않고 예전처럼 표지 여백으로 남긴다.
        metrics = _cover_metric_rows(self.report, projection=self.projection)
        if metrics is not None:
            self._draw_cover_metrics(canvas, *metrics)

        summary = _summary_table(
            self.report, self.styles, self.width, projection=self.projection
        )
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

    def _draw_cover_metrics(
        self,
        canvas: Canvas,
        title_text: str,
        cite: str,
        items: tuple[tuple[str, str, str], ...],
    ) -> None:
        """4장 실적표의 최신 사업연도 행을 표지 정본 좌표에 다시 보여 준다.

        여기서 숫자를 만들지 않는다 — 봉인 블록(또는 legacy 갈래의
        ``cover_metrics``)이 표에서 글자 그대로 옮겨 온 값만 배치한다
        (정본 6-1절 「새로 계산한 숫자 금지」).
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
            _escape(_cited_text(title_text, cite)),
            self.styles["cover_meta"],
        )
        _, title_height = title.wrap(self.width, 12 * mm)
        title.drawOn(canvas, 0, band_top - title_height)

        rule_y = band_top - title_height - _COVER_METRICS_TITLE_GAP_PT
        canvas.setStrokeColor(colors.HexColor(constants.COLOR_LINE))
        canvas.setLineWidth(_COVER_METRICS_RULE_PT)
        canvas.line(0, rule_y, self.width, rule_y)

        column_width = self.width / len(items)
        text_width = column_width - _COVER_METRICS_COLUMN_GAP_PT
        for index, (item_label, item_value, item_unit) in enumerate(items):
            label = Paragraph(
                _escape(item_label), self.styles["cover_metric_label"]
            )
            # 단위는 값보다 작게 붙인다 — 표지에서 크게 읽혀야 하는 것은 숫자다.
            value = Paragraph(
                f"{_escape(item_value)} "
                f'<font name="{constants.FONT_REGULAR}" '
                f'size="{constants.SMALL_FONT_SIZE_PT}" '
                f'color="{constants.COLOR_MUTED}">{_escape(item_unit)}</font>',
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


def _display_generated_at(report: Report) -> str:
    """저장된 실제 생성 시각이 ISO 형식일 때만 KST 날짜로 표시한다."""

    raw = report.generated_at.strip()
    if not raw or _ISO_DATE_PREFIX.match(raw) is None:
        return ""
    try:
        if len(raw) == 10:
            return dt.date.fromisoformat(raw).strftime("%Y.%m.%d")
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(clock.KST)
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
        # 표지 다음 첫 본문 페이지 맨 위 마스트헤드(D-S4a) 회사명 줄.
        # cover_title(31pt)보다 작고 heading(17pt)보다 큰 좌측 정렬 밴드다.
        "masthead_title": ParagraphStyle(
            "MastheadTitle",
            parent=base["Title"],
            fontName=constants.FONT_SEMIBOLD,
            fontSize=constants.MASTHEAD_TITLE_FONT_SIZE_PT,
            leading=constants.MASTHEAD_TITLE_LEADING_PT,
            alignment=TA_LEFT,
            textColor=ink,
            spaceAfter=3,
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


#: 한 조각에 넣는 최대 열 수. 이보다 넓은 표는 첫 열을 반복하며 나눈다.
_MAX_TABLE_COLUMNS: Final[int] = 5


def _wide_table_column_groups(width: int, max_columns: int) -> list[list[int]]:
    """넓은 표를 나눌 «값 열» 묶음. v1·v2가 같은 규칙으로 나누게 한 곳에 둔다."""

    value_columns = list(range(1, width))
    return [
        value_columns[start : start + (max_columns - 1)]
        for start in range(0, len(value_columns), max_columns - 1)
    ]


def _continued_caption(caption: str, index: int, total: int) -> str:
    """둘째 조각부터 「(계속 2/3)」을 붙인다 — 첫 조각 캡션은 원문 그대로."""

    return caption if index == 1 else f"{caption} (계속 {index}/{total})"


def _split_wide_table(
    table: ReportTable, *, max_columns: int = _MAX_TABLE_COLUMNS
) -> list[ReportTable]:
    """일반표를 첫 열을 반복하는 최대 5열 표들로 나눈다."""

    width = len(table.headers)
    if width <= max_columns:
        return [table]
    chunks: list[ReportTable] = []
    groups = _wide_table_column_groups(width, max_columns)
    for index, group in enumerate(groups, start=1):
        columns = [0, *group]
        chunks.append(
            ReportTable(
                caption=_continued_caption(table.caption, index, len(groups)),
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


def _flow_card_table(
    card: Card, styles: dict[str, ParagraphStyle], width: float
) -> Table | None:
    """카드 하나를 라벨:값 2열 표로 만든다 — ``_add_section_content_block``과
    같은 모양(제목 행 + 구분선 있는 라벨·값 행)이다. 두 함수가 코드를
    공유하지 않는 것은 의도적이다: SectionContentBlock 카드는 사실
    원장에서, 이 카드는 AI가 낸 경로표에서 나와 정본이 다르므로, 한쪽을
    고치다 다른 쪽이 조용히 어긋나는 사고를 막기 위해 분리해 둔다.
    """
    if not card.fields:
        return None
    data: list[list[Paragraph]] = []
    commands: list[tuple[object, ...]] = [
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(constants.COLOR_LINE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if card.title:
        data.append(
            [Paragraph(_escape(card.title), styles["card_title"]), Paragraph("", styles["card_body"])]
        )
        commands.append(("SPAN", (0, 0), (1, 0)))
        commands.append(("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(constants.COLOR_LINE)))
    field_start = len(data)
    data.extend(
        [
            [Paragraph(_escape(field.label), styles["card_label"]), Paragraph(_escape(field.value), styles["card_body"])]
            for field in card.fields
        ]
    )
    # 마지막 줄 밑에는 선을 안 긋는다(바깥 BOX 테두리와 겹친다) — 줄이
    # 하나뿐이면 그을 «가운데» 선도 없다.
    if len(data) - field_start > 1:
        commands.append(
            ("LINEBELOW", (0, field_start), (-1, -2), 0.45, colors.HexColor(constants.COLOR_LINE))
        )
    card_table = Table(data, colWidths=[width * 0.29, width * 0.71], hAlign="LEFT")
    card_table.setStyle(TableStyle(commands))
    return card_table


def _add_flow_card_visualization(
    story: list[Flowable],
    table: ReportTable | PublicTableBlock,
    visual: TableVisualization,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> None:
    """흐름표가 «카드」로 판정됐을 때 — 화살표 없이 라벨:값 카드로 낸다.

    ★ 화면(.section-content-card)과 같은 모양을 재현한다 — 웹·PDF가
      어긋나면 「한쪽만 고친」 사고가 되므로, 목업이 보여 준 카드 모양
      (제목·구분선 있는 라벨·값 행)을 여기서도 그대로 따른다.
    """
    caption = _cited_text(table.caption, table.cite)
    flowables: list[Flowable] = [
        Paragraph(_escape(caption), styles["small_bold"]),
        Spacer(1, 3),
    ]
    story.append(KeepTogether(flowables))
    for card in visual.cards:
        card_table = _flow_card_table(card, styles, width)
        if card_table is None:
            continue
        story.append(KeepTogether([card_table, Spacer(1, 8)]))
    story.append(Spacer(1, 4))


def _add_report_visualization(
    story: list[Flowable],
    table: ReportTable,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> bool:
    visual = table_visualization(table)
    if visual is None:
        return False
    if visual.kind == "card":
        _add_flow_card_visualization(story, table, visual, styles, width)
        return True
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
    _add_grid_table(
        story,
        caption=table.caption,
        cite=table.cite,
        headers=tuple(table.headers),
        rows=tuple(tuple(row) for row in table.rows),
        numeric=table.numeric,
        styles=styles,
        width=width,
    )


def _add_grid_table(
    story: list[Flowable],
    *,
    caption: str,
    cite: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    numeric: bool,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> None:
    """캡션 + 격자 표 한 장을 그린다 (v1·v2 공용 — 도식이 없을 때의 기본 모양).

    값을 만들지 않는다. 넘겨받은 글자만 배치하므로 봉인 블록도 canonical 표도
    «같은 코드»로 그려진다 — 두 벌로 갈라지면 채널이 조용히 어긋난다.
    """

    story.append(Paragraph(_escape(_cited_text(caption, cite)), styles["small_bold"]))

    max_columns = max(
        [len(headers), *(len(row) for row in rows)],
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
    if headers:
        header_styles = [
            styles["table_head_numeric"] if numeric and index > 0 else styles["table_head"]
            for index in range(max_columns)
        ]
        header_rows = chunk_row(headers, header_styles)
        data.extend(header_rows)
        repeat_rows = len(header_rows)
    body_styles = [
        styles["table_numeric"] if numeric and index > 0 else styles["table"]
        for index in range(max_columns)
    ]
    for row in rows:
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
    if headers:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(constants.COLOR_HEADER)))
    if numeric and max_columns > 1:
        commands.append(("ALIGN", (1, 0), (-1, -1), "RIGHT"))
    report_table.setStyle(TableStyle(commands))
    story.extend([report_table, Spacer(1, 9)])


#: 문단 번호 접두어의 글자 크기(pt). 본문(BODY_FONT_SIZE_PT)보다 작게 —
#: 웹의 ``.pno``도 본문보다 작은 10.x px다(style.css). 값 자체가 사실이
#: 아니라 «표시 크기»이므로 매직 넘버로 두지 않고 여기 상수로 뽑는다.
_PARAGRAPH_NUMBER_FONT_SIZE_PT: Final[float] = 8.3
#: 번호 열과 본문 열을 분리하는 고정 폭. 한 Paragraph에 접두어로 넣으면 둘째
#: 줄이 번호 아래로 되돌아가므로 실제 두 열로 배치한다.
_PARAGRAPH_NUMBER_COLUMN_PT: Final[float] = 18.0
_PARAGRAPH_NUMBER_GAP_PT: Final[float] = 5.0


def _paragraph_number_markup(position: int | str) -> str:
    """웹(result.html의 ``.pno``)과 «같은 계산식»으로 문단 앞에 «1.» «2.»를 붙인다.

    ★ 왜 코드가 붙이나 — v2-32와 같은 이유. 번호는 «표시 방식»이지
      «사실»이 아니다. AI에게 시키면 번호가 본문 글자로 들어가 인용
      추적·중복 검사·부록 1:1 검사가 그 번호를 사실로 오인한다. 여기서
      붙이면 ``_escape(text)``가 받는 문장은 한 글자도 안 바뀐다.
    ★ PDF가 «다운로드 정본»이다 — 웹에만 있고 PDF에 없으면 「3번 문단
      보세요」가 성립하지 않는다(팀장 실측: 웹 25개 · PDF 0개).
    ★ 2026-08-25에 «장번호-문단번호»(예: 2-1)에서 «문단번호만»으로 바꿨다.
      이유(사용자): 이미 「2. 사업 구조와 수익 모델」이라는 장 제목 아래에
      있으므로 장 번호를 문단마다 되풀이할 이유가 없다.
      부수 효과로 옛 quirk가 사라진다 — 전에는 ``display_number``가 비면
      웹이 «문단 자신의 0-기준 순번»을 장번호 자리에 대신 써서 「0-1」
      「1-2」 같은 번호가 나왔다. 이제 장번호를 아예 안 쓰므로 그 자리가
      없어졌고, 웹·PDF가 어긋날 여지도 같이 없어졌다.
    ★ 정수를 주면 예전처럼 «여기서» 「1.」을 만든다(v1·legacy 갈래와, 웹과
      형식이 같은지 맞대 보는 시험이 그렇게 부른다). 글자를 주면 봉인 블록이
      이미 매긴 번호이므로 그대로 쓴다 — 봉인이 있는데 다시 세지 않는다.
    """
    number_text = f"{position}." if isinstance(position, int) else position
    return (
        f'<font name="{constants.FONT_SEMIBOLD}" '
        f'size="{_PARAGRAPH_NUMBER_FONT_SIZE_PT}" '
        f'color="{constants.COLOR_MUTED}">{_escape(number_text)}'
        f"</font>"
    )


def _numbered_paragraph(
    position: int,
    text: str,
    styles: dict[str, ParagraphStyle],
    width: float,
    *,
    number_text: str = "",
) -> Table:
    """번호와 본문을 실제 두 열로 놓아 모든 줄의 본문 시작선을 맞춘다.

    ``number_text``가 있으면(v2 봉인 갈래) 그 번호를 «그대로» 쓴다 — 봉인이
    이미 매긴 번호를 렌더가 다시 세면 웹·PDF가 갈라질 수 있다.
    """

    number_style = ParagraphStyle(
        f"ReportParagraphNumber{position}",
        parent=styles["body"],
        fontName=constants.FONT_SEMIBOLD,
        fontSize=_PARAGRAPH_NUMBER_FONT_SIZE_PT,
        textColor=colors.HexColor(constants.COLOR_MUTED),
        alignment=TA_RIGHT,
        spaceAfter=0,
    )
    body_style = ParagraphStyle(
        f"ReportNumberedBody{position}",
        parent=styles["body"],
        spaceAfter=0,
    )
    number = Paragraph(
        _paragraph_number_markup(number_text or position), number_style
    )
    body = Paragraph(_escape(text), body_style)
    number_column = min(_PARAGRAPH_NUMBER_COLUMN_PT, width)
    table = Table(
        [[number, body]],
        colWidths=[number_column, max(1.0, width - number_column)],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), _PARAGRAPH_NUMBER_GAP_PT),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    table.spaceAfter = styles["body"].spaceAfter
    return table


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

    # v2의 FactRecord와 fact_ids는 검증·감사용 장부다. 웹 v2는 공개 산문과
    # 표·도식만 표시하는데 PDF가 이 장부를 v1 카드로 다시 투영하면, 같은
    # 보고서가 채널마다 다른 글자를 보여 주고 public digest에서 제외한 내부
    # 값이 PDF에 새어 나온다. v2는 웹과 같은 공개 projection만 소비한다.
    detail_blocks = (
        ()
        if report.schema_version == ENGINE_V2_SCHEMA_VERSION
        else section_content_blocks(report, section)
    )
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
            _numbered_paragraph(position, text, styles, width)
            for position, text in enumerate(section.prose_paragraphs, start=1)
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


# ══════════════════════════════════════════════════════════
# v2 봉인 갈래 — 블록을 «배치만» 한다
# ══════════════════════════════════════════════════════════


def _split_wide_projection_table(
    table: PublicTableBlock,
) -> list[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...]]]:
    """봉인된 표를 v1과 «같은 규칙»으로 최대 5열 조각으로 나눈다.

    나눈 조각은 (캡션, 머리글, 행들)뿐이다 — 봉인 블록을 새로 만들지 않는다.
    봉인은 composer가 한 번 만든 것이고, 여기서 만든 값은 화면 배치용이다.
    """

    width = len(table.headers)
    if width <= _MAX_TABLE_COLUMNS:
        return [(table.caption, table.headers, table.rows)]
    groups = _wide_table_column_groups(width, _MAX_TABLE_COLUMNS)
    chunks: list[tuple[str, tuple[str, ...], tuple[tuple[str, ...], ...]]] = []
    for index, group in enumerate(groups, start=1):
        columns = [0, *group]
        chunks.append(
            (
                _continued_caption(table.caption, index, len(groups)),
                tuple(table.headers[column] for column in columns),
                tuple(
                    tuple(row[column] for column in columns) for row in table.rows
                ),
            )
        )
    return chunks


def _add_projection_visualization(
    story: list[Flowable],
    table: PublicTableBlock,
    visual: PublicVisualBlock,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> bool:
    """봉인된 도식 하나를 그린다. 그릴 수 없는 종류면 거짓을 돌려 표로 넘긴다."""

    chart = _visual_from_block(visual)
    if chart.kind == "card":
        _add_flow_card_visualization(story, table, chart, styles, width)
        return True
    if chart.kind == "composition":
        graphic: Flowable = _CompositionGraphic(chart, width)
    elif chart.kind == "trend":
        graphic = _TrendGraphic(chart, width)
    elif chart.kind == "flow":
        graphic = _FlowGraphic(chart, table.headers, width)
    else:
        return False
    caption = _cited_text(table.caption, table.cite)
    source_note = _cited_text("자료", table.cite)
    note = f"{source_note} · {chart.note}" if chart.note else source_note
    flowables: list[Flowable] = [
        Paragraph(_escape(caption), styles["small_bold"]),
        Spacer(1, 3),
        graphic,
        Spacer(1, 5),
    ]
    # ★ 「읽는 법」 — 화면(result.html의 .chart-reading)에만 있던 줄을 PDF에도
    #   낸다(설계 결정 D5). 다운로드본이 정본인데 그림 해석이 화면에만 있으면
    #   같은 보고서가 채널마다 다른 이해도를 준다. 글자는 봉인 값 그대로다.
    if chart.reading.strip():
        flowables.extend(
            [Paragraph(_escape(chart.reading), styles["small"]), Spacer(1, 4)]
        )
    flowables.extend([Paragraph(_escape(note), styles["small"]), Spacer(1, 12)])
    story.append(KeepTogether(flowables))
    return True


def _add_projection_table(
    story: list[Flowable],
    table: PublicTableBlock,
    visual: PublicVisualBlock | None,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> None:
    if visual is not None and _add_projection_visualization(
        story, table, visual, styles, width
    ):
        return
    for caption, headers, rows in _split_wide_projection_table(table):
        _add_grid_table(
            story,
            caption=caption,
            cite=table.cite,
            headers=headers,
            rows=rows,
            numeric=table.numeric,
            styles=styles,
            width=width,
        )


#: 3개년 변화 요약 띠의 열 폭 비율 — 지표·변화·근거(·비고). 비고가 있는
#: 보고서에서만 넷째 칸을 쓴다(빈 칸을 남기지 않는다).
_PERIOD_SUMMARY_WIDTHS: Final[tuple[float, float, float]] = (0.22, 0.18, 0.60)
_PERIOD_SUMMARY_WIDTHS_WITH_NOTE: Final[tuple[float, float, float, float]] = (
    0.18,
    0.15,
    0.42,
    0.25,
)


def _add_projection_period_summary(
    story: list[Flowable],
    block: PublicPeriodSummaryBlock,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> None:
    """3개년 변화 요약 띠 — 화면(.period-summary)이 그리던 줄을 PDF에도 낸다.

    숫자를 만들지 않는다. 봉인된 열 개 필드를 배치하고, 계산 근거 한 줄만
    화면과 «같은 함수»(``PeriodSummaryItem.basis_text``)로 잇는다.
    """

    items = _period_summary_items_from_block(block)
    if not items:
        return
    with_note = any(item.note.strip() for item in items)
    ratios = (
        _PERIOD_SUMMARY_WIDTHS_WITH_NOTE if with_note else _PERIOD_SUMMARY_WIDTHS
    )
    rows: list[list[Paragraph]] = []
    for item in items:
        row = [
            Paragraph(_escape(item.label), styles["table_head"]),
            Paragraph(_escape(item.change), styles["small_bold"]),
            Paragraph(_escape(item.basis_text), styles["table"]),
        ]
        if with_note:
            row.append(Paragraph(_escape(item.note), styles["table"]))
        rows.append(row)
    band = Table(rows, colWidths=[width * ratio for ratio in ratios], hAlign="LEFT")
    band.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                (
                    "LINEBELOW",
                    (0, 0),
                    (-1, -2),
                    0.45,
                    colors.HexColor(constants.COLOR_LINE),
                ),
            ]
        )
    )
    story.append(
        KeepTogether(
            [
                Paragraph(
                    _escape(_cited_text(block.title, block.cite)),
                    styles["small_bold"],
                ),
                Spacer(1, 3),
                band,
                Spacer(1, 10),
            ]
        )
    )


def _add_projection_section(
    story: list[Flowable],
    display: PublicSectionDisplay,
    styles: dict[str, ParagraphStyle],
    width: float,
    anchor: str,
) -> None:
    """장 하나를 봉인 블록 «만» 읽어 배치한다.

    ★ ``.ledger``(FactRecord·fact_id·등급 기여)는 여기서 한 번도 읽지 않는다.
      그건 감사 장부이지 공개 본문이 아니다 — 장부를 바꿔도 PDF 글자가
      안 바뀐다는 성질이 이 갈래의 계약이다.
    """

    title = _section_heading(display)
    heading_flowables: list[Flowable] = [
        _OutlineAnchor(anchor, title, level=1),
        Paragraph(_section_heading_markup(display), styles["heading"]),
        _HorizontalRule(width),
        Spacer(1, 10),
    ]
    if not (display.paragraphs or display.tables):
        story.extend(heading_flowables)
        return

    visuals_by_index = {visual.table_index: visual for visual in display.visuals}
    band: list[Flowable] = []
    if display.period_summary is not None:
        _add_projection_period_summary(band, display.period_summary, styles, width)

    if display.paragraphs:
        paragraphs = [
            _numbered_paragraph(position, text, styles, width, number_text=ordinal)
            for position, (ordinal, text) in enumerate(display.paragraphs, start=1)
        ]
        story.append(KeepTogether([*heading_flowables, paragraphs[0]]))
        story.extend(paragraphs[1:])
        story.extend(band)
        table_start = 0
    else:
        first_content: list[Flowable] = list(band)
        table_start = 0
        if not first_content and display.tables:
            _add_projection_table(
                first_content, display.tables[0], visuals_by_index.get(0), styles, width
            )
            table_start = 1
        story.append(KeepTogether([*heading_flowables, *first_content]))

    for index in range(table_start, len(display.tables)):
        _add_projection_table(
            story, display.tables[index], visuals_by_index.get(index), styles, width
        )


def _section_heading(section: ReportSection | PublicSectionDisplay) -> str:
    if section.display_number:
        return f"{section.display_number}. {section.title}"
    return section_display_heading(section.cell, section.title)


def _section_heading_markup(section: ReportSection | PublicSectionDisplay) -> str:
    title = _escape(_section_heading(section))
    if section.cell not in TIME_SECTION_IDS or not section.tag.strip():
        return title
    tag = _escape(section.tag.strip())
    return (
        f'{title}&nbsp;&nbsp;<font name="{constants.FONT_REGULAR}" '
        f'size="{constants.SMALL_FONT_SIZE_PT}" color="{constants.COLOR_MUTED}">'
        f"{tag}</font>"
    )


def _add_citations(
    story: list[Flowable],
    report: Report,
    styles: dict[str, ParagraphStyle],
    *,
    projection: PublicReportProjection | None = None,
) -> None:
    """부록 「출처와 검증 상태」 표.

    봉인이 있으면 행 여섯 칸이 전부 봉인 값이다 — 특히 「사실 검증」 라벨을
    렌더 시점에 다시 세지 않는다(``source_verification_label`` 미호출).
    """

    #: (번호, 자료 마크업, 기준일·상태, 사실 검증, 원문 위치, 본문 사용 장)
    entries: list[tuple[str, str, str, str, str, str]]
    if projection is not None:
        entries = [
            (
                str(row.number),
                _link_markup(row.label_display, row.url),
                row.status_display,
                row.verification_label,
                row.location,
                row.used_in_display,
            )
            for row in projection.citations
        ]
    else:
        entries = [
            (
                str(source.number),
                _source_label_markup(source),
                _source_status(source),
                source_verification_label(report, source.source_id),
                source.location.strip() or "—",
                _source_used_sections(source),
            )
            for source in _citations(report)
        ]
    if not entries:
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
    for number, label_markup, status, verification, location, used_in in entries:
        rows.append(
            [
                Paragraph(_escape(number), styles["table"]),
                Paragraph(label_markup, styles["table"]),
                Paragraph(_escape(status), styles["table"]),
                Paragraph(_escape(verification), styles["table"]),
                Paragraph(_escape(location), styles["table"]),
                Paragraph(_escape(used_in), styles["table"]),
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


def _link_markup(label_text: str, url_text: str) -> str:
    """문서명을 원문 링크로 감싼다 — 링크로 못 만들면 같은 문서명 그대로.

    v1(Source)과 v2(봉인된 부록 행)가 «같은 코드»로 같은 마크업을 만든다.
    """

    label = _escape(label_text)
    url = url_text.strip()
    if not url.startswith(("https://", "http://")):
        return label
    escaped_url = html.escape(_normalize_pdf_text(url), quote=True)
    return (
        f'<link href="{escaped_url}" color="{constants.COLOR_INK}">'
        f"<u>{label}</u></link>"
    )


def _source_label_markup(source: Source) -> str:
    """원문 URL이 있으면 무채색 밑줄 링크로, 없으면 같은 문서명으로 낸다."""

    return _link_markup(_source_label(source), source.url)


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
    generated_date = _display_generated_at(report)
    values = [
        f"기준일 {display_date}" if display_date else "",
        f"내용 생성 {generated_date}" if generated_date else "",
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
    *,
    projection: PublicReportProjection | None = None,
) -> Table | None:
    """표지 「핵심 요약」 표.

    봉인이 있으면 번호·주제어·장 번호를 «다시 만들지 않고» 봉인 값을 쓴다 —
    세 채널이 각자 세던 것을 한 번만 세게 한 것이 봉인의 목적이다.
    """

    #: (번호, 주제어, 장 번호(없으면 빈 글자), 문장)
    entries: list[tuple[str, str, str, str]]
    if projection is not None:
        entries = [
            (row.ordinal, row.topic, row.section_display_number, row.text)
            for row in projection.summary
        ]
    else:
        entries = []
        for index, item in enumerate(report.summary_items, start=1):
            spec = SECTION_BY_ID.get(item.section_id)
            entries.append(
                (
                    f"{index:02d}",
                    summary_topic(item.section_id),
                    "" if spec is None else spec.display_number,
                    item.text.strip(),
                )
            )
    if not entries:
        return None
    rows: list[list[Paragraph]] = []
    for ordinal, topic, display_number, text in entries:
        sentence = _escape(text)
        if display_number:
            sentence += (
                f'&nbsp;&nbsp;<font name="{constants.FONT_REGULAR}" '
                f'size="{constants.SMALL_FONT_SIZE_PT}" '
                f'color="{constants.COLOR_MUTED}">[{display_number}장]</font>'
            )
        rows.append(
            [
                Paragraph(ordinal, styles["summary_number"]),
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


def _masthead_flowables(
    report: Report,
    styles: dict[str, ParagraphStyle],
    width: float,
) -> list[Flowable]:
    """표지 다음 첫 본문 페이지 맨 위에 회사명 마스트헤드를 좌측 정렬 밴드로 그린다(D-S4a).

    표지(``_CoverContent``)는 페이지 중앙께에 큰 제목·실적 띠·핵심 요약을
    모아 두므로, 여기서는 같은 인상을 주지 않도록 두 줄 + 얇은 구분선짜리
    작은 밴드로만 표시한다. 세 채널이 함께 쓰는 ``masthead_lines``의
    문자열을 그대로 옮기고 새 문장을 짓지 않는다.
    """

    company_line, meta_line = masthead_lines(report)
    return [
        Paragraph(_escape(company_line), styles["masthead_title"]),
        Paragraph(_escape(meta_line), styles["small"]),
        _HorizontalRule(width),
        Spacer(1, 12),
    ]


def _shortfall_reasons(
    report: Report, *, projection: PublicReportProjection | None
) -> tuple[str, ...]:
    """미제공 사유 목록. 봉인이 있으면 봉인된 header 값을 쓴다."""

    if projection is not None:
        reasons = projection.header.get("shortfall_reasons", ())
        if isinstance(reasons, (list, tuple)):
            return tuple(str(reason) for reason in reasons)
        return ()
    return tuple(report.shortfall_reasons)


def _document_header(
    report: Report,
    styles: dict[str, ParagraphStyle],
    width: float,
    *,
    projection: PublicReportProjection | None = None,
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
            projection=projection,
        ),
        PageBreak(),
        _OutlineAnchor("report-body", "분석 본문", level=0),
        *_masthead_flowables(report, styles, width),
    ]
    partial_title, partial_detail = _grade_notice(
        report, projection=projection, detailed=True
    )
    if partial_title or partial_detail:
        items.extend(
            [
                _OutlineAnchor("partial-notice", "부분 보고서 안내", level=1),
                Paragraph(partial_title, styles["heading"]),
                Paragraph(partial_detail, styles["body"]),
                *[
                    Paragraph(f"- {_escape(reason)}", styles["body"])
                    for reason in _shortfall_reasons(report, projection=projection)
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
    raw_pdf: bytes,
    title: str,
    *,
    author: str,
    subject: str,
    content_manifest_version: str,
    content_manifest_sha256: str,
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
            PDF_MANIFEST_VERSION_KEY: content_manifest_version,
            PDF_MANIFEST_SHA256_KEY: content_manifest_sha256,
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
        projection = _public_projection(report)
    else:
        projection = None
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
    story: list[Flowable] = _document_header(
        report, styles, document.width, projection=projection
    )
    if projection is not None:
        # v2 FULL — 봉인 블록만 배치한다(장 순서는 봉인이 정본 아홉 장으로 고정).
        for index, block in enumerate(projection.sections):
            _add_projection_section(
                story,
                block.display,
                styles,
                document.width,
                f"section-{index}",
            )
    else:
        for index, section in enumerate(report.sections):
            _add_section(
                story,
                report,
                section,
                styles,
                document.width,
                f"section-{index}",
            )

    _add_citations(story, report, styles, projection=projection)
    document.build(
        story,
        onFirstPage=_page_furniture,
        onLaterPages=_page_furniture,
        canvasmaker=_BrandedCanvas,
    )
    return _add_accessibility_metadata(
        buffer.getvalue(),
        title,
        author=author,
        subject=subject,
        content_manifest_version=CONTENT_MANIFEST_VERSION,
        content_manifest_sha256=public_content_manifest_sha256(report),
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
