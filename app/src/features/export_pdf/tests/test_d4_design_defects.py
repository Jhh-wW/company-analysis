"""실제 산출 PDF에서 눈으로 잡은 디자인 결함(D-4)을 되돌아오지 못하게 막는다.

근거(실측): 하이브 5쪽 4장 당기순이익 막대가 «빨강»이고 「-2,544」가 축 라벨
「2025」와 겹쳐 찍혔다. 인이지 3쪽 2장 쉐브론의 「미확인」이 가장 진한 칸으로
그려지고, 열 이름이 줄마다 다시 찍혔다. 하이브 3쪽은 3장 캡션만 남고 카드가
다음 쪽으로 넘어갔다.

여기서는 «그린 결과»를 본다 — 상수를 다시 읽는 대신 한 장짜리 PDF로 뽑아
좌표·색·글자를 재는 방식이라, 그리기 코드가 되돌아가면 반드시 깨진다.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Final, Sequence

import pdfplumber
import pytest
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, KeepTogether, Paragraph, Spacer

from src.features.composer.constants import FLOW_UNCONFIRMED_CELL
from src.features.export_pdf import constants
from src.features.export_pdf.logic import (
    _add_flow_card_visualization,
    _FlowGraphic,
    _keep_together_items,
    _lead_with_heading,
    _register_fonts,
    _TrendGraphic,
    build_pdf,
)
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.report_standard.visualization import (
    Card,
    CardField,
    ChartPoint,
    ChartSeries,
    TableVisualization,
)


_PAGE_WIDTH = 420.0
_PAGE_HEIGHT = 260.0
_TOLERANCE = 0.02
_BOUNDARY_MARGIN_PT = 62
_BOUNDARY_TIGHT_LEAD_PT = 640
_BOUNDARY_ROOMY_LEAD_PT = 600
_OVERSIZED_PARAGRAPH_REPETITIONS = 2000


@pytest.fixture(autouse=True)
def _fonts() -> None:
    """도형만 따로 그릴 때도 ``build_pdf``와 같은 글꼴을 쓴다."""

    _register_fonts()


def _render(flowable: Flowable, width: float = _PAGE_WIDTH) -> bytes:
    """도형 하나만 담은 한 장짜리 PDF를 만든다 (좌표·색을 그대로 재려고)."""

    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(width, _PAGE_HEIGHT))
    flowable.wrapOn(canvas, width, _PAGE_HEIGHT)
    flowable.drawOn(canvas, 0, 0)
    canvas.save()
    return buffer.getvalue()


def _page(pdf: bytes) -> Any:
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        page = document.pages[0]
        return {
            "rects": list(page.rects),
            "curves": list(page.curves),
            "chars": list(page.chars),
            "height": float(page.height),
        }


def _rgb(color: str) -> tuple[float, float, float]:
    parsed = HexColor(color)
    return (parsed.red, parsed.green, parsed.blue)


def _same_color(value: Any, expected: tuple[float, float, float]) -> bool:
    if not isinstance(value, (tuple, list)) or len(value) != len(expected):
        return False
    return all(abs(float(a) - b) <= _TOLERANCE for a, b in zip(value, expected))


def _word_runs(chars: Sequence[Any], text: str) -> list[list[Any]]:
    """글자들을 읽는 순서로 이어 ``text``가 나타나는 구간들을 돌려준다."""

    ordered = sorted(chars, key=lambda char: (round(char["top"], 1), char["x0"]))
    joined = "".join(char["text"] for char in ordered)
    runs: list[list[Any]] = []
    start = joined.find(text)
    while start != -1:
        runs.append(ordered[start : start + len(text)])
        start = joined.find(text, start + 1)
    return runs


def _word_boxes(chars: Sequence[Any], text: str) -> list[tuple[float, float, float]]:
    """``text``가 나타나는 (top, bottom, x0) 상자를 찾는다."""

    return [
        (
            min(float(char["top"]) for char in run),
            max(float(char["bottom"]) for char in run),
            min(float(char["x0"]) for char in run),
        )
        for run in _word_runs(chars, text)
    ]


# ══════════════════════════════════════════════════════════
# ① 음수 막대 — 무채색 + 축 라벨과 안 겹치는 값 라벨
# ══════════════════════════════════════════════════════════


def _mixed_trend() -> TableVisualization:
    """흑자 한 해와 적자 두 해가 섞인 계열 (하이브 당기순이익과 같은 모양)."""

    return TableVisualization(
        kind="trend",
        caption="완료 사업연도 연결 실적",
        unit="억원",
        series=(
            ChartSeries(
                label="당기순이익",
                risk=False,
                points=(
                    ChartPoint(
                        label="2023",
                        value=1834.0,
                        display="1,834",
                        ratio=72.1,
                        below=False,
                    ),
                    ChartPoint(
                        label="2024", value=-34.0, display="-34", ratio=1.3, below=True
                    ),
                    ChartPoint(
                        label="2025",
                        value=-2544.0,
                        display="-2,544",
                        ratio=100.0,
                        below=True,
                    ),
                ),
            ),
        ),
    )


def test_음수_막대는_붉은색을_쓰지_않고_가장_진한_회색으로_그린다() -> None:
    page = _page(_render(_TrendGraphic(_mixed_trend(), _PAGE_WIDTH)))

    risk = _rgb(constants.COLOR_RISK)
    assert not any(
        _same_color(rect.get("non_stroking_color"), risk) for rect in page["rects"]
    ), "0선 아래 막대에 여전히 붉은색(COLOR_RISK)을 씁니다"

    dark = _rgb(constants.COLOR_CHART_DARK)
    down_bars = [
        rect
        for rect in page["rects"]
        if _same_color(rect.get("non_stroking_color"), dark)
    ]
    assert len(down_bars) == 2, (
        "적자 두 해의 막대가 가장 진한 회색으로 그려지지 않았습니다: "
        f"{len(down_bars)}개"
    )


def test_음수_값_라벨은_축_연도_라벨과_겹치지_않는다() -> None:
    page = _page(_render(_TrendGraphic(_mixed_trend(), _PAGE_WIDTH)))

    value_boxes = _word_boxes(page["chars"], "-2,544")
    axis_boxes = _word_boxes(page["chars"], "2025")
    assert value_boxes, "값 라벨 「-2,544」를 찾지 못했습니다"
    # 「2025」는 축 라벨과 값 라벨(「-2,544」에는 없다) 중 축 라벨만 나온다.
    assert axis_boxes, "축 라벨 「2025」를 찾지 못했습니다"

    value_top, value_bottom, _ = value_boxes[0]
    axis_top, axis_bottom, _ = axis_boxes[0]
    assert value_bottom < axis_top, (
        "음수 값 라벨이 축 연도 라벨과 세로로 겹칩니다 "
        f"(값 {value_top:.1f}~{value_bottom:.1f}, 축 {axis_top:.1f}~{axis_bottom:.1f})"
    )


def test_음수_값_라벨은_0선_위에_있어_칸_바닥에_붙지_않는다() -> None:
    page = _page(_render(_TrendGraphic(_mixed_trend(), _PAGE_WIDTH)))

    dark = _rgb(constants.COLOR_CHART_DARK)
    tallest = max(
        (
            rect
            for rect in page["rects"]
            if _same_color(rect.get("non_stroking_color"), dark)
        ),
        key=lambda rect: float(rect["height"]),
    )
    value_top, value_bottom, _ = _word_boxes(page["chars"], "-2,544")[0]
    # pdfplumber 좌표는 위에서 아래로 커진다 — 라벨이 막대 «시작(0선)»보다
    # 위에 있어야 한다.
    assert value_bottom <= float(tallest["top"]) + 1.0, (
        "음수 값 라벨이 0선 위가 아니라 막대 아래쪽에 찍혔습니다"
    )


# ══════════════════════════════════════════════════════════
# ② 쉐브론 — 머리글 1회, 「미확인」은 옅은 빈 칸
# ══════════════════════════════════════════════════════════

_FLOW_HEADERS = ("핵심 자산", "제품·서비스", "고객 행동·과금", "반복·확장 수익")


def _two_row_flow(last_cell: str) -> TableVisualization:
    return TableVisualization(
        kind="flow",
        caption="가치와 수익이 흐르는 경로",
        flows=(
            ("개발 역량", "응용 개발", "용역료 지급", "대형 계약"),
            ("수행 능력", "정부지원 개발", "보조금 수령", last_cell),
        ),
    )


def _flow_graphic(last_cell: str) -> _FlowGraphic:
    return _FlowGraphic(_two_row_flow(last_cell), _FLOW_HEADERS, _PAGE_WIDTH)


def test_쉐브론_열_이름은_여러_줄이어도_첫_줄_위에만_한_번_찍힌다() -> None:
    graphic = _flow_graphic("진행 중 계약")
    assert graphic._chevron_rows == [True, True], "두 줄 다 쉐브론이어야 하는 표본입니다"

    page = _page(_render(graphic))
    for header in _FLOW_HEADERS:
        assert len(_word_boxes(page["chars"], header)) == 1, (
            f"열 이름 「{header}」이 줄마다 반복해서 찍혔습니다"
        )


def test_머리글을_한_번만_찍으면_도형_높이가_줄어든다() -> None:
    graphic = _flow_graphic("진행 중 계약")
    body_only = graphic._measure_row_height(
        graphic.visual.flows[1], chevrons=True, header=False
    )
    with_header = graphic._measure_row_height(
        graphic.visual.flows[0], chevrons=True, header=True
    )
    assert body_only < with_header
    assert graphic._row_heights == [with_header, body_only]


def test_미확인_칸은_짙게_채우지_않고_옅은_회색_글자로_그린다() -> None:
    page = _page(_render(_flow_graphic(FLOW_UNCONFIRMED_CELL)))

    runs = _word_runs(page["chars"], FLOW_UNCONFIRMED_CELL)
    assert len(runs) == 1, f"「{FLOW_UNCONFIRMED_CELL}」 글자를 찾지 못했습니다"

    weak = _rgb(constants.COLOR_WEAK)
    assert all(
        _same_color(char.get("non_stroking_color"), weak) for char in runs[0]
    ), "「미확인」 글자가 회색(COLOR_WEAK)이 아닙니다 — 흰 글자면 짙게 채운 칸입니다"
    assert all(
        abs(float(char["size"]) - 8.0) <= 0.05 for char in runs[0]
    ), "「미확인」 글자 크기가 8pt가 아닙니다"


def test_미확인_칸은_채운_쉐브론_한_개를_없앤다() -> None:
    """빈 칸을 «칠하지 않는다»는 것이 이 수정의 핵심이다."""

    filled = _page(_render(_flow_graphic("진행 중 계약")))
    unconfirmed = _page(_render(_flow_graphic(FLOW_UNCONFIRMED_CELL)))

    def filled_shapes(page: dict[str, Any]) -> int:
        return sum(
            1
            for curve in page["curves"]
            if curve.get("fill")
            and any(
                _same_color(curve.get("non_stroking_color"), _rgb(color))
                for color in constants.CHART_PALETTE
            )
        )

    assert filled_shapes(unconfirmed) == filled_shapes(filled) - 1, (
        "「미확인」 칸이 여전히 회색 단계로 채워집니다"
    )
    dashed = [
        curve
        for curve in unconfirmed["curves"]
        if curve.get("stroke")
        and _same_color(curve.get("stroking_color"), _rgb(constants.COLOR_LINE))
    ]
    assert dashed, "「미확인」 칸에 옅은 회색 테두리가 없습니다"


# ══════════════════════════════════════════════════════════
# ③ 빈 쪽 낭비 — 캡션은 첫 카드와, 장 제목은 첫 묶음과
# ══════════════════════════════════════════════════════════


class _StubTable:
    caption = "지금 무엇을 파는가 — 핵심 제품·서비스와 역할"
    cite = "15"


def _card_visual(count: int) -> TableVisualization:
    return TableVisualization(
        kind="card",
        caption=_StubTable.caption,
        cards=tuple(
            Card(
                title=f"제품 {index}",
                fields=(CardField(label="제품·서비스 범위", value="설명"),),
            )
            for index in range(1, count + 1)
        ),
    )


def _styles() -> dict[str, Any]:
    from src.features.export_pdf.logic import _styles as build_styles  # noqa: PLC0415

    return build_styles()


def test_카드_캡션은_첫_카드와_같은_묶음에_들어간다() -> None:
    story: list[Flowable] = []
    _add_flow_card_visualization(
        story, _StubTable(), _card_visual(2), _styles(), _PAGE_WIDTH
    )

    groups = [item for item in story if isinstance(item, KeepTogether)]
    assert groups, "카드 묶음이 하나도 없습니다"
    first = _keep_together_items(groups[0])
    captions = [item for item in first if isinstance(item, Paragraph)]
    assert captions, "첫 묶음에 캡션이 없습니다 — 캡션만 따로 떨어져 나갑니다"
    assert any(not isinstance(item, (Paragraph, Spacer)) for item in first), (
        "첫 묶음에 카드 표가 없습니다 — 캡션만 남고 카드가 다음 쪽으로 넘어갑니다"
    )


def test_장_제목_묶음은_KeepTogether를_중첩하지_않는다() -> None:
    """중첩하면 ReportLab이 «남은 자리와 무관하게» 늘 다음 쪽으로 넘긴다."""

    heading = [Spacer(1, 1)]
    body = [
        KeepTogether([Paragraph("캡션", _styles()["small_bold"]), Spacer(1, 3)]),
        KeepTogether([Spacer(1, 8)]),
    ]
    merged = _lead_with_heading(heading, body)

    assert len(merged) == 2
    inner = _keep_together_items(merged[0])
    assert not any(isinstance(item, KeepTogether) for item in inner), (
        "장 제목 묶음 안에 또 다른 KeepTogether가 들어 있습니다"
    )
    assert heading[0] in inner


#: 1장 정체성 표 — 칸끼리 이어지지 않아 «카드»로 판정되는 실제 모양이다.
_CARD_TABLE = ReportTable(
    caption="회사가 스스로를 어떻게 규정하나",
    headers=["공식 자기정의", "사업 범위", "이 보고서의 해석"],
    rows=[["소재 가공 회사", "가구·가전용 시트", "B2B 소재 회사"]],
    cite="[2]",
    presentation="flow",
)


def test_카드로_시작하는_장은_앞_쪽의_남은_자리를_그대로_쓴다() -> None:
    """빈 쪽 낭비의 «실물» 대조 — 반 쪽이 남았는데 새 쪽으로 넘어가면 안 된다.

    ★ 되돌아가면 무엇이 보이나 — 인이지 3쪽처럼 앞 쪽 3/4가 빈 채로 남고
      장 제목이 다음 쪽 맨 위에서 다시 시작한다.
    """

    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.platypus import SimpleDocTemplate  # noqa: PLC0415

    from src.features.export_pdf.logic import (  # noqa: PLC0415
        _add_section,
        _OutlineAnchor,
    )
    from src.features.pipeline.port import ReportSection  # noqa: PLC0415

    styles = _styles()
    width = A4[0] - 124
    report = build_demo_report()
    section = ReportSection(
        cell="identity",
        title="기업 정체성",
        display_number="1",
        tables=[_CARD_TABLE],
        lines=[("근거", "[2]")],
    )

    story: list[Flowable] = [
        # 장 책갈피(level 1)에는 위 단계가 필요하다 — 본 PDF의 「분석 본문」 자리.
        _OutlineAnchor("d4-root", "분석 본문", level=0),
        Paragraph("앞 장의 문단입니다. " * 40, styles["body"]),
        Spacer(1, 120),
    ]
    _add_section(story, report, section, styles, width, "d4-section")

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=62,
        rightMargin=62,
        topMargin=62,
        bottomMargin=62,
    ).build(story)
    with pdfplumber.open(io.BytesIO(buffer.getvalue())) as document:
        pages = len(document.pages)
    assert pages == 1, (
        f"카드로 시작하는 장이 남은 자리를 안 쓰고 새 쪽으로 넘어갑니다 ({pages}쪽)"
    )


#: 아래 세 시험이 공유하는 경계 fixture — 제목 뒤 첫 문단이 표(번호+본문 2열)로
#: 조립되는 실제 경로(prose_paragraphs + tables 모두 있음)를 그대로 태운다.
def _boundary_section():
    table = ReportTable(
        caption="표", headers=["a", "b"], rows=[["1", "2"]], cite="[2]", presentation="flow"
    )
    return replace(
        build_demo_report().sections[0],
        cell="identity", title="경계 시험 장", display_number="1",
        prose_paragraphs=[
            "첫 문단입니다. 조금 길게 써서 표 한 줄 분량으로 만듭니다. 조금 더 길게 씁니다.",
            "둘째 문단",
        ],
        tables=[table], lines=[("근거", "[2]")],
        empty_reason="", prose_lines=[], guidance_lines=[], tag="", fact_ids=[],
    )


def _render_boundary_story(leading_spacer_pt: float) -> bytes:
    """앞에 정확한 높이의 Spacer만 두어 장 진입 시 남은 자리를 통제한다."""
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.platypus import SimpleDocTemplate  # noqa: PLC0415

    from src.features.export_pdf.logic import _add_section, _OutlineAnchor  # noqa: PLC0415

    styles = _styles()
    width = A4[0] - _BOUNDARY_MARGIN_PT * 2
    report = build_demo_report()
    story: list[Flowable] = [
        _OutlineAnchor("root", "분석 본문", level=0), Spacer(1, leading_spacer_pt),
    ]
    _add_section(story, report, _boundary_section(), styles, width, "sec")
    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=_BOUNDARY_MARGIN_PT, rightMargin=_BOUNDARY_MARGIN_PT,
        topMargin=_BOUNDARY_MARGIN_PT, bottomMargin=_BOUNDARY_MARGIN_PT,
    ).build(story)
    return buffer.getvalue()


def test_heading_keeps_first_paragraph_when_only_heading_fits() -> None:
    """실측 경계값(leading_spacer_pt=640) — 고아 제목이 실제로 재현되는 자리다.

    되돌아가면 무엇이 보이나 — 제목만 이번 쪽 맨 아래 혼자 남고 첫 문단(번호+
    본문 표 1행)은 다음 쪽 맨 위에서 시작한다(WOORI PDF 5쪽 실측과 같은 모양).
    """
    with pdfplumber.open(io.BytesIO(_render_boundary_story(_BOUNDARY_TIGHT_LEAD_PT))) as document:
        pages = [page.extract_text() or "" for page in document.pages]
    heading_pages = [i for i, text in enumerate(pages) if "경계 시험 장" in text]
    body_pages = [i for i, text in enumerate(pages) if "첫 문단입니다" in text]
    assert heading_pages and body_pages, "제목·첫 문단 중 하나가 어느 쪽에도 없습니다"
    assert heading_pages == body_pages, (
        f"제목과 첫 문단이 서로 다른 쪽에 있습니다(제목={heading_pages}, 첫 문단={body_pages}) "
        "— 제목만 남는 고아 제목입니다"
    )


def test_heading_stays_on_page_when_first_paragraph_also_fits() -> None:
    """여유 있는 자리(leading_spacer_pt=600)에서 불필요하게 다음 쪽으로 넘기지 않는다."""
    with pdfplumber.open(io.BytesIO(_render_boundary_story(_BOUNDARY_ROOMY_LEAD_PT))) as document:
        first_page_text = document.pages[0].extract_text() or ""
    assert "경계 시험 장" in first_page_text and "첫 문단입니다" in first_page_text, (
        "여유가 있는데도 제목·첫 문단이 첫 쪽에 함께 놓이지 않았습니다"
    )


def test_oversized_first_paragraph_still_fails_with_layout_error() -> None:
    """CondPageBreak 요구 높이가 한 쪽 전체보다 커도 무한 FrameBreak로 멈추지 않는다.

    이 조건은 이번 수정 이전에도 같은 예외(LayoutError)로 끝났다 — 이번 수정이
    새로 만든 실패가 아니라 기존 «너무 큰 flowable은 명확히 실패한다» 계약을
    그대로 유지한다는 것만 못 박는다.
    """
    from reportlab.platypus.doctemplate import LayoutError  # noqa: PLC0415

    from src.features.export_pdf.logic import _add_section, _OutlineAnchor  # noqa: PLC0415
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.platypus import SimpleDocTemplate  # noqa: PLC0415

    styles = _styles()
    width = A4[0] - _BOUNDARY_MARGIN_PT * 2
    report = build_demo_report()
    oversized_section = replace(
        _boundary_section(),
        prose_paragraphs=["이 문장은 아주 깁니다. " * _OVERSIZED_PARAGRAPH_REPETITIONS, "둘째 문단"],
    )
    story: list[Flowable] = [_OutlineAnchor("root", "분석 본문", level=0)]
    _add_section(story, report, oversized_section, styles, width, "sec")
    buffer = io.BytesIO()
    with pytest.raises(LayoutError):
        SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=_BOUNDARY_MARGIN_PT, rightMargin=_BOUNDARY_MARGIN_PT,
            topMargin=_BOUNDARY_MARGIN_PT, bottomMargin=_BOUNDARY_MARGIN_PT,
        ).build(story)


def test_KeepTogether_내용_읽기가_ReportLab_판올림에도_살아_있다() -> None:
    """``_keep_together_items``가 빈 목록으로 조용히 무력해지지 않게 못 박는다."""

    first = Spacer(1, 1)
    second = Spacer(1, 2)
    assert _keep_together_items(KeepTogether([first, second])) == [first, second]


# ══════════════════════════════════════════════════════════
# ④ 표지 수치 카드 — 값이 셋이면 세 칸을 그린다
# ══════════════════════════════════════════════════════════


def test_표지_실적_띠는_당기순이익까지_세_칸을_그린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """고르는 쪽이 셋을 넘겨주면 표지는 그대로 세 칸으로 그린다.

    ★ «고르는» 쪽(report_standard.cover_metrics)이 실제로 셋을 고르는지는
      report_standard 시험이 본다. 여기서는 그리는 쪽(export_pdf)만 떼어
      확인한다 — 고르는 함수를 갈아 끼워, 값이 셋이면 칸도 셋인지만 본다.
    """

    from src.features.export_pdf import logic as pdf_logic  # noqa: PLC0415

    class _Metrics:
        title = "2025 사업연도 실적"
        cite = "9"
        items = (
            _CoverItem("매출액", "26,499", "억원"),
            _CoverItem("영업이익", "493", "억원"),
            _CoverItem("당기순이익", "-2,544", "억원"),
        )

        def __bool__(self) -> bool:
            return True

    monkeypatch.setattr(pdf_logic, "cover_metrics", lambda report: _Metrics())
    pdf = build_pdf(build_demo_report())

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        cover = document.pages[0]
        text = " ".join((cover.extract_text() or "").split())
        cards = [
            rect
            for rect in cover.rects
            if not rect.get("fill")
            and rect.get("stroke")
            and 20.0 < float(rect["height"]) < 90.0
        ]

    for label in ("매출액", "영업이익", "당기순이익"):
        assert label in text, f"표지에 「{label}」 칸이 없습니다"
    assert "-2,544" in text, "표지에 당기순이익 값이 없습니다"
    assert len(cards) == 3, f"표지 수치 카드가 세 칸이 아닙니다: {len(cards)}개"


class _CoverItem:
    """``CoverMetric``과 같은 모양의 최소 표본 (label·value·unit)."""

    def __init__(self, label: str, value: str, unit: str) -> None:
        self.label = label
        self.value = value
        self.unit = unit


#: SM(에스엠엔터테인먼트) 실측 3~4쪽 회귀 — 표 없는 짧은 장(text_only 경로)이
#: ``_paragraphs_with_heading``의 단일 ``KeepTogether``만 돌려받아, d396이 고친
#: ``CondPageBreak`` 보강(표 있는 긴 장 경로)의 보호를 받지 못했다.
#: fixtures/sm_section9_short_no_table.json은 그 실측 payload에서 이 장(9장,
#: cell=competitive_position)만 그대로 옮긴 것이다 — DB·다른 로컬 산출물 의존 없이
#: 어느 체크아웃에서도 이 파일 하나로 재현·회귀 검증한다.
_SM_SECTION9_FIXTURE_PATH: Final = Path(__file__).resolve().parent / "fixtures/sm_section9_short_no_table.json"


def _load_sm_section9_report() -> Report:
    """고정 JSON 표본에서 실제 ``Report``/``ReportSection`` 생성자로 재구성한다."""

    payload = json.loads(_SM_SECTION9_FIXTURE_PATH.read_text(encoding="utf-8"))
    section = ReportSection(**{
        key: (value if key != "lines" and key != "prose_lines" else [tuple(pair) for pair in value])
        for key, value in payload["section"].items()
    })
    report_fields = {**payload["report"], "grade": Grade(payload["report"]["grade"])}
    return Report(sections=[section], **report_fields)


def _render_sm_section9(leading_spacer_pt: float) -> bytes:
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.platypus import SimpleDocTemplate  # noqa: PLC0415

    from src.features.export_pdf.logic import _add_section, _OutlineAnchor  # noqa: PLC0415

    report = _load_sm_section9_report()
    section = report.sections[0]  # competitive_position · 「회사가 밝힌 차별점」, tables=()
    styles = _styles()
    width = A4[0] - _BOUNDARY_MARGIN_PT * 2
    story: list[Flowable] = [
        _OutlineAnchor("root", "분석 본문", level=0), Spacer(1, leading_spacer_pt),
    ]
    _add_section(story, report, section, styles, width, "sec")
    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=_BOUNDARY_MARGIN_PT, rightMargin=_BOUNDARY_MARGIN_PT,
        topMargin=_BOUNDARY_MARGIN_PT, bottomMargin=_BOUNDARY_MARGIN_PT,
    ).build(story)
    return buffer.getvalue()


def test_no_table_short_section_keeps_heading_with_first_paragraph() -> None:
    """실측 경계값(leading_spacer_pt=595) — SM 9장 고아 제목이 재현되는 자리다.

    되돌아가면 무엇이 보이나 — 「9. 회사가 밝힌 차별점」 제목만 이번 쪽 맨 아래
    혼자 남고, 번호 1번 본문(확인 범위 안내 포함)은 다음 쪽 맨 위에서 새로
    시작한다(SM PDF 3~4쪽 실측과 같은 모양).
    """

    with pdfplumber.open(io.BytesIO(_render_sm_section9(595))) as document:
        pages = [page.extract_text() or "" for page in document.pages]
    heading_pages = [i for i, text in enumerate(pages) if "차별점" in text and "9." in text]
    body_pages = [i for i, text in enumerate(pages) if "초안 문장들" in text]
    assert heading_pages and body_pages, "제목·본문 중 하나가 어느 쪽에도 없습니다"
    assert heading_pages == body_pages, (
        f"제목과 본문이 서로 다른 쪽에 있습니다(제목={heading_pages}, 본문={body_pages}) "
        "— 표 없는 짧은 장에서도 고아 제목이 재현됩니다"
    )


def test_no_table_short_section_stays_on_page_when_room_enough() -> None:
    """여유 있는 자리(leading_spacer_pt=500)에서 불필요하게 다음 쪽으로 넘기지 않는다."""

    with pdfplumber.open(io.BytesIO(_render_sm_section9(500))) as document:
        first_page_text = document.pages[0].extract_text() or ""
    assert "차별점" in first_page_text and "초안 문장들" in first_page_text, (
        "여유가 있는데도 제목·본문이 첫 쪽에 함께 놓이지 않았습니다"
    )
