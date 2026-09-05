"""1·6·8장(속성 나열)은 카드로, 2·5·7장(진짜 인과)은 화살표로 — PDF도 웹과
같은 판정을 따르는지 확인한다.

★ 왜 파일을 따로 두나 — test_export_pdf.py는 canonical 보고서 전체를
  왕복하는 굵은 시험 위주다. 이 파일은 report_standard.visualization의
  card/flow 판정이 export_pdf 렌더러 «두 갈래»(카드 표/_FlowGraphic)로
  올바르게 갈라지는지만 좁게 본다 — 실패했을 때 원인을 바로 좁힐 수 있다.
"""

from __future__ import annotations

import io

import pdfplumber
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, KeepTogether, SimpleDocTemplate, Table

from src.features.export_pdf.logic import (
    _FLOW_MIN_ROW_HEIGHT_MM,
    _FlowGraphic,
    _RelationGraphic,
    _add_projection_visualization,
    _add_report_visualization,
    _register_fonts,
    _styles,
)
from src.features.pipeline.port import ReportTable
from src.features.report_standard.visualization import table_visualization
from src.shared.report_generation.public_projection import PublicTableBlock, PublicVisualBlock

_register_fonts()

_WIDTH = A4[0] - 124


def _flatten(flowables: list[Flowable]) -> list[Flowable]:
    """KeepTogether 안에 감춰진 flowable까지 한 줄로 편다."""

    out: list[Flowable] = []
    for item in flowables:
        if isinstance(item, KeepTogether):
            out.extend(_flatten(item._content))
        else:
            out.append(item)
    return out


def _render(flowables: list[Flowable]) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=62, rightMargin=62, topMargin=62, bottomMargin=62
    )
    document.build(list(flowables))
    return buffer.getvalue()


# 1장 정체성 — composer.constants.IDENTITY_TABLE_HEADERS. 칸끼리 안 이어지므로 카드.
_CARD_TABLE = ReportTable(
    caption="회사가 스스로를 어떻게 규정하나",
    headers=["공식 자기정의", "사업 범위", "이 보고서의 해석"],
    rows=[["소재 가공 회사", "가구·가전용 시트", "B2B 소재 회사"]],
    cite="[2]",
    presentation="flow",
)

# 7장 운영 경로 — composer.constants.OPERATIONS_FLOW_HEADERS. 시작→행위→도달로
# 실제 이어지므로 화살표 유지(test_e2e_offline.py가 웹에서도 이 칸 이름을 못 박는다).
_CAUSAL_FLOW_TABLE = ReportTable(
    caption="사업이 돌아가는 경로",
    headers=["무엇으로 시작하나", "회사가 하는 일", "누구에게 닿나"],
    rows=[["원재료", "생산", "제품"]],
    cite="[1]",
    presentation="flow",
)

_RELATION_TABLE = ReportTable(
    caption="과제와 대응",
    headers=["지금 겪는 과제", "회사가 밝힌 대응"],
    rows=[
        ["원가 부담", "공정 효율화"],
        ["고객 집중", "판매처 다변화"],
    ],
    cite="[1]",
    presentation="flow",
)


def test_card_kind_renders_a_table_not_a_flow_graphic() -> None:
    visual = table_visualization(_CARD_TABLE)
    assert visual is not None and visual.kind == "card"

    story: list[Flowable] = []
    handled = _add_report_visualization(story, _CARD_TABLE, _styles(), _WIDTH)
    assert handled is True

    flattened = _flatten(story)
    assert not any(isinstance(item, _FlowGraphic) for item in flattened), (
        "카드로 판정된 표가 여전히 화살표 그래픽(_FlowGraphic)을 씁니다"
    )
    assert any(isinstance(item, Table) for item in flattened), (
        "카드 표(Table)가 하나도 안 생겼습니다"
    )


def test_causal_flow_kind_still_uses_flow_graphic() -> None:
    visual = table_visualization(_CAUSAL_FLOW_TABLE)
    assert visual is not None and visual.kind == "flow"

    story: list[Flowable] = []
    handled = _add_report_visualization(story, _CAUSAL_FLOW_TABLE, _styles(), _WIDTH)
    assert handled is True

    flattened = _flatten(story)
    assert any(isinstance(item, _FlowGraphic) for item in flattened), (
        "진짜 흐름(7장 운영 경로)이 화살표 그래픽을 안 씁니다 — "
        "e2e 시험(test_e2e_offline.py)이 요구하는 class=\"flow-row\"와 대응하는 "
        "PDF 쪽 그림이 사라졌습니다"
    )


def test_relation_pairs_kind_uses_relation_graphic_and_prints_exact_cells() -> None:
    visual = table_visualization(_RELATION_TABLE)
    assert visual is not None and visual.kind == "relation_pairs"

    story: list[Flowable] = []
    handled = _add_report_visualization(story, _RELATION_TABLE, _styles(), _WIDTH)
    assert handled is True
    assert any(isinstance(item, _RelationGraphic) for item in _flatten(story))

    pdf = _render(story)
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)

    for row in _RELATION_TABLE.rows:
        for cell in row:
            assert cell in text


def test_sealed_relation_pairs_flows_are_drawn_without_recalculation() -> None:
    table = PublicTableBlock(
        caption=_RELATION_TABLE.caption,
        headers=tuple(_RELATION_TABLE.headers),
        rows=tuple(tuple(row) for row in _RELATION_TABLE.rows),
        cite=_RELATION_TABLE.cite,
        numeric=False,
        presentation="flow",
        display_unit="",
        manifest_ref="a" * 64,
    )
    visual = PublicVisualBlock(
        table_index=0,
        kind="relation_pairs",
        caption=_RELATION_TABLE.caption,
        unit="",
        note="",
        reading="",
        items=(),
        series=(),
        flows=tuple(tuple(row) for row in _RELATION_TABLE.rows),
        cards=(),
    )
    story: list[Flowable] = []

    handled = _add_projection_visualization(story, table, visual, _styles(), _WIDTH)

    assert handled is True
    assert any(isinstance(item, _RelationGraphic) for item in _flatten(story))
    pdf = _render(story)
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    for row in _RELATION_TABLE.rows:
        for cell in row:
            assert cell in text


def test_card_prints_label_left_value_right_without_arrows() -> None:
    """카드는 라벨(왼쪽)·값(오른쪽) 표로 찍힌다 — 목업과 같은 모양."""

    story: list[Flowable] = []
    _add_report_visualization(story, _CARD_TABLE, _styles(), _WIDTH)
    pdf = _render(story)

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        words = [word for page in document.pages for word in page.extract_words()]

    label = next(word for word in words if word["text"] == "정의" or word["text"] == "자기정의")
    value_word = next(word for word in words if word["text"] == "회사")
    # 라벨 칸은 표 왼쪽 29% 폭에 들어간다 — 값(오른쪽 칸)보다 왼쪽에 있어야 한다.
    assert label["x0"] < value_word["x0"]


def test_portfolio_products_print_as_separate_titled_cards() -> None:
    """3장 — 제품마다 카드 1장, 제목은 제품명. 지금까지 모든 카드 제목이
    빈 문자열이었으니 PDF의 제목 행(SPAN + LINEBELOW)이 처음 실행된다."""
    table = ReportTable(
        caption="지금 무엇을 미는가 — 핵심 제품·서비스와 역할",
        headers=["제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할"],
        rows=[
            ["리얼 알루미늄 합지 필름", "가전 표면재", "생산확대", "전사 수익 경로"],
            ["폐플라스틱 열분해유", "자원순환 판매 제품", "투자·증설", "전사 수익 경로"],
        ],
        cite="[1]",
        presentation="flow",
    )
    visual = table_visualization(table)
    assert visual is not None and visual.kind == "card"
    assert [card.title for card in visual.cards] == ["리얼 알루미늄 합지 필름", "폐플라스틱 열분해유"]

    story: list[Flowable] = []
    handled = _add_report_visualization(story, table, _styles(), _WIDTH)
    assert handled is True
    pdf = _render(story)

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        text = "\n".join(page.extract_text() or "" for page in document.pages)

    assert "리얼 알루미늄 합지 필름" in text
    assert "폐플라스틱 열분해유" in text
    assert "제품·서비스 범위" in text
    # 제목으로 빠진 칸("제품·서비스명")은 라벨로 «또» 안 나온다.
    assert "제품·서비스명" not in text


def test_flow_graphic_row_height_grows_with_long_text() -> None:
    """★ 줄 높이가 «내용 길이에 맞춰» 늘어나는지 — 짧은/긴 표를 직접 비교한다.

    회귀 방지: 예전엔 row_height가 18mm 고정이라 긴 값이 상자 밖으로
    겹쳐 나갔다. 이 시험은 긴 값이 있는 표의 높이가 짧은 값만
    있는 표보다 «반드시» 커야 한다고 못 박는다.
    """

    short = ReportTable(
        caption="짧은 흐름",
        headers=["가", "나"],
        rows=[["A", "B"]],
        presentation="flow",
    )
    long_value = "매우 길고 상세한 설명 문장을 " * 12  # 상자 하나가 여러 줄로 접히도록
    long = ReportTable(
        caption="긴 흐름",
        headers=["가", "나"],
        rows=[[long_value, "B"]],
        presentation="flow",
    )

    short_visual = table_visualization(short)
    long_visual = table_visualization(long)
    assert short_visual is not None and long_visual is not None

    short_graphic = _FlowGraphic(short_visual, short.headers, _WIDTH)
    long_graphic = _FlowGraphic(long_visual, long.headers, _WIDTH)

    assert long_graphic.height > short_graphic.height, (
        "긴 값이 있는데도 흐름 상자 높이가 안 늘어났습니다 — "
        "row_height가 다시 고정값으로 되돌아갔을 수 있습니다"
    )


def test_flow_graphic_keeps_the_old_minimum_height_for_short_content() -> None:
    """짧은 내용의 모양은 예전(18mm 고정) 그대로다 — 회귀 없음을 못 박는다."""

    table = ReportTable(
        caption="짧은 흐름",
        headers=["가", "나"],
        rows=[["A", "B"]],
        presentation="flow",
    )
    visual = table_visualization(table)
    assert visual is not None

    graphic = _FlowGraphic(visual, table.headers, _WIDTH)
    assert graphic.height == pytest.approx(_FLOW_MIN_ROW_HEIGHT_MM * mm, abs=0.01)
