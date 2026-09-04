"""구성 도식(100% 누적 막대) 범례가 «긴 이름»에도 옆 칸과 안 겹치는지 확인한다.

★ 왜 이 시험이 있나 (실측) — 저장된 보고서 PDF에
  「MD 및 라이선싱 공식 상품(MD), IP 라이…」로 잘린 칸이
  있었다. 원인인 revenuemix의 이름 자르기는 이미 고쳤지만,
  «자르지 않은 원문»이 들어왔을 때 이 파일의 ``_CompositionGraphic``
  범례가 ``canvas.drawString`` 한 줄 고정이라 옆 칸 글자와 겹칠 위험이
  그대로 남아 있었다 — 이 시험이 그 위험을 없앴는지 못 박는다.
"""

from __future__ import annotations

import io

import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Flowable, SimpleDocTemplate

from src.features.export_pdf.logic import _CompositionGraphic, _register_fonts
from src.features.report_standard.visualization import table_visualization
from src.features.pipeline.port import ReportTable

_register_fonts()
_WIDTH = A4[0] - 124


def _render(flowables: list[Flowable]) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=62, rightMargin=62, topMargin=62, bottomMargin=62
    )
    document.build(list(flowables))
    return buffer.getvalue()

_SHORT_TABLE = ReportTable(
    caption="짧은 구성",
    headers=["사업", "매출 비중"],
    rows=[["가", "40%"], ["나", "35%"], ["다", "25%"]],
    presentation="composition",
)

# 실측(하이브)과 같은 길이의 이름 — revenuemix가 더 이상 자르지 않게 된 원문.
_LONG_TABLE = ReportTable(
    caption="긴 구성",
    headers=["사업", "매출 비중"],
    rows=[
        ["음반/음원", "29.17%"],
        ["공연", "28.83%"],
        ["광고 수익, 출연료 수익 등", "5.55%"],
        ["MD 및 라이선싱 공식 상품(MD), IP 라이선싱 등", "21.53%"],
        ["콘텐츠", "14.92%"],
    ],
    presentation="composition",
)


def test_legend_row_height_grows_with_a_long_label() -> None:
    short_visual = table_visualization(_SHORT_TABLE)
    long_visual = table_visualization(_LONG_TABLE)
    assert short_visual is not None and long_visual is not None

    short_graphic = _CompositionGraphic(short_visual, _WIDTH)
    long_graphic = _CompositionGraphic(long_visual, _WIDTH)

    assert long_graphic.height > short_graphic.height, (
        "긴 범례 이름이 있는데도 도식 높이가 안 늘어났습니다 — "
        "범례가 다시 한 줄 고정(drawString)으로 되돌아갔을 수 있습니다"
    )


def test_legend_labels_do_not_overlap_each_other() -> None:
    """★ 실제 잘렸던 그 이름으로 렌더해, 옆 칸 단어와 겹치는 자리가
    있는지 pdfplumber 좌표로 직접 확인한다(눈으로만 보지 않는다)."""

    visual = table_visualization(_LONG_TABLE)
    assert visual is not None

    graphic = _CompositionGraphic(visual, _WIDTH)
    pdf_bytes = _render([graphic])

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        words = [word for page in document.pages for word in page.extract_words()]

    assert any("라이선싱" in word["text"] for word in words), "긴 이름이 아예 안 그려졌습니다"
    assert not any("…" in word["text"] for word in words), "범례에 말줄임이 남아 있습니다"

    overlapping = [
        (a["text"], b["text"])
        for i, a in enumerate(words)
        for b in words[i + 1 :]
        if a["text"] != b["text"]
        and a["x0"] < b["x1"]
        and b["x0"] < a["x1"]
        and a["top"] < b["bottom"]
        and b["top"] < a["bottom"]
    ]
    assert not overlapping, f"범례 글자가 겹칩니다: {overlapping}"


def test_short_labels_keep_the_old_minimum_row_height() -> None:
    """짧은 이름의 모양은 예전(5.2mm 최소 줄 높이) 그대로다 — 회귀 없음."""
    from reportlab.lib.units import mm

    visual = table_visualization(_SHORT_TABLE)
    assert visual is not None
    graphic = _CompositionGraphic(visual, _WIDTH)

    # 3항목 · 2열 = 2줄. 전부 짧은 라벨이라 두 줄 다 최소 높이(5.2mm)여야 한다.
    assert len(graphic._row_heights) == 2
    for row_height in graphic._row_heights:
        assert row_height == 5.2 * mm
