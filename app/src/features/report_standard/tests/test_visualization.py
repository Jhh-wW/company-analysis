from __future__ import annotations

import pytest

from src.features.pipeline.port import ReportTable
from src.features.report_standard.visualization import (
    COMPOSITION_MAX_ITEMS,
    COMPOSITION_MIN_ITEMS,
    COMPOSITION_TONE_STEPS,
    composition_tone,
)
from src.features.report_standard.visualization import table_visualization


def _composition_table(rows: list[list[str]]) -> ReportTable:
    return ReportTable(
        caption="매출 구성",
        headers=["사업", "매출 비중"],
        rows=rows,
        presentation="composition",
    )


def test_composition_with_a_public_total_row_falls_back_to_the_complete_table() -> None:
    visualization = table_visualization(
        _composition_table(
            [
                ["제품", "55.5%"],
                ["합계", "100.0%"],
                ["서비스", "30.0%"],
                ["기타", "14.4%"],
            ]
        )
    )

    assert visualization is None


@pytest.mark.parametrize(
    ("values", "accepted"),
    [
        (("40", "30", "28.5"), True),
        (("40", "30", "31.5"), True),
        (("40", "30", "28.49"), False),
        (("40", "30", "31.51"), False),
    ],
)
def test_composition_only_accepts_a_complete_approximately_100_percent_total(
    values: tuple[str, str, str], accepted: bool
) -> None:
    visualization = table_visualization(
        _composition_table(
            [["제품", values[0]], ["서비스", values[1]], ["기타", values[2]]]
        )
    )

    assert (visualization is not None) is accepted


@pytest.mark.parametrize(
    "rows",
    [
        [["제품", "60%"], ["서비스", "미정"], ["기타", "40%"]],
        [["제품", "-1%"], ["서비스", "61%"], ["기타", "40%"]],
        [["제품", "60%"], ["서비스", "20%"], ["기타", "10%"]],
        [
            ["제품", "40%"],
            ["서비스", "30%"],
            ["기타", "30%"],
            ["부분합", "100%"],
        ],
        [["제품", "100%"], ["합계", "100%"]],
    ],
)
def test_composition_rejects_bad_values_partial_totals_and_incomplete_categories(
    rows: list[list[str]],
) -> None:
    assert table_visualization(_composition_table(rows)) is None


def test_composition_accepts_six_categories() -> None:
    """★ 하이브 실측 — 6개 부문, 비중 합계 정확히 100.00%인데 도식이 안 나왔다.

    막은 것은 자료가 아니라 「최대 5개」라는 숫자 하나였다. 색 계단도 함께
    넓혀야 6번째 칸이 첫 칸과 같은 색이 되지 않는다(웹·PDF 모두).
    """
    table = _composition_table(
        [
            [f"범주-{index}", value]
            for index, value in enumerate(("20", "20", "20", "20", "10", "10"), 1)
        ]
    )

    visualization = table_visualization(table)

    assert visualization is not None
    assert visualization.kind == "composition"
    assert len(visualization.items) == 6


def test_composition_rejects_more_than_seven_categories() -> None:
    """상한은 여전히 있다 — 무한정 늘리면 막대가 읽히지 않는다."""
    table = _composition_table(
        [
            [f"범주-{index}", "12.5"]
            for index in range(1, 9)  # 8개 · 합계 100%
        ]
    )

    assert table_visualization(table) is None


def test_composition_tone_always_ends_pale_and_never_repeats() -> None:
    """★ 색 규칙을 못 박는다 — 웹 틀과 PDF가 «이 함수 하나»를 함께 쓴다.

    항목이 3개든 7개든 「가장 진한 것 → 가장 옅은 것」으로 끝나야 회사가 달라도
    같은 장의 도식 인상이 같다(사용자 요구). 그리고 한 도식 안에서 같은 색이
    두 번 나오면 두 부문이 한 덩어리로 보인다.
    """
    for count in range(COMPOSITION_MIN_ITEMS, COMPOSITION_MAX_ITEMS + 1):
        tones = [composition_tone(index, count) for index in range(count)]
        assert tones[0] == 0, count
        assert tones[-1] == COMPOSITION_TONE_STEPS - 1, count
        assert len(set(tones)) == count, f"{count}칸에서 색이 겹칩니다: {tones}"
        assert tones == sorted(tones), f"{count}칸에서 색이 진하기 순이 아닙니다"


def test_web_css_and_pdf_palette_have_enough_tones() -> None:
    """★ 세 곳(판정 상한·웹 CSS·PDF 팔레트)이 «같은 수»의 색을 가져야 한다.

    한 곳만 늘리면 조용히 깨진다 — PDF는 IndexError로 보고서가 통째로 막히고,
    웹은 색이 되돌아가 첫 칸과 마지막 칸이 같아진다.
    """
    from pathlib import Path

    from src.features.export_pdf.constants import COMPOSITION_PALETTE

    assert len(COMPOSITION_PALETTE) == COMPOSITION_TONE_STEPS
    assert COMPOSITION_MAX_ITEMS <= COMPOSITION_TONE_STEPS

    css = Path(__file__).resolve().parents[3] / "web" / "static" / "style.css"
    style = css.read_text(encoding="utf-8")
    for step in range(COMPOSITION_TONE_STEPS):
        assert f".tone-{step} " in style, f"웹 CSS에 .tone-{step}이 없습니다"


def test_trend_preserves_rows_calculates_ratios_marks_negative_risk_and_reads_unit() -> None:
    visualization = table_visualization(
        ReportTable(
            caption="완료 사업연도 연결 실적 (단위: 억원)",
            headers=["사업연도", "매출", "영업손익"],
            rows=[
                ["2025", "300", "-30"],
                ["2023", "100", "-10"],
                ["2024", "200", "-20"],
            ],
            numeric=True,
            presentation="trend",
        )
    )

    assert visualization is not None
    assert visualization.kind == "trend"
    assert visualization.unit == "억원"
    assert [series.label for series in visualization.series] == ["매출", "영업손익"]

    revenue, operating = visualization.series
    assert [point.label for point in revenue.points] == ["2023", "2024", "2025"]
    assert [point.value for point in revenue.points] == [100.0, 200.0, 300.0]
    assert [point.ratio for point in revenue.points] == pytest.approx(
        [100 / 3, 200 / 3, 100]
    )
    assert revenue.risk is False

    assert [point.label for point in operating.points] == ["2023", "2024", "2025"]
    assert [point.value for point in operating.points] == [-10.0, -20.0, -30.0]
    assert [point.ratio for point in operating.points] == pytest.approx(
        [100 / 3, 200 / 3, 100]
    )
    assert operating.risk is True


def test_trend_prefers_explicit_display_unit_and_rejects_non_numeric_series() -> None:
    explicit_unit = table_visualization(
        ReportTable(
            caption="실적 (단위: 원)",
            headers=["사업연도", "매출"],
            rows=[["2023", "500"], ["2024", "1,000"], ["2025", "2,000"]],
            display_unit="백만원",
            presentation="trend",
        )
    )
    invalid = table_visualization(
        ReportTable(
            caption="실적",
            headers=["사업연도", "매출"],
            rows=[["2023", "500"], ["2024", "1,000"], ["2025", "미정"]],
            presentation="trend",
        )
    )

    assert explicit_unit is not None
    assert explicit_unit.unit == "백만원"
    assert invalid is None


def test_trend_with_mixed_signs_falls_back_to_a_table() -> None:
    table = ReportTable(
        caption="실적 (단위: 억원)",
        headers=["사업연도", "영업손익"],
        rows=[["2023", "-1"], ["2024", "2"], ["2025", "3"]],
        presentation="trend",
    )

    assert table_visualization(table) is None


def test_flow_preserves_each_complete_left_to_right_row() -> None:
    visualization = table_visualization(
        ReportTable(
            caption="사업 흐름",
            headers=["입력", "실행", "결과"],
            rows=[
                ["원재료", "생산", "제품"],
                ["고객 요청", "맞춤 가공", "납품"],
            ],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "flow"
    assert visualization.flows == (
        ("원재료", "생산", "제품"),
        ("고객 요청", "맞춤 가공", "납품"),
    )


@pytest.mark.parametrize(
    "rows",
    [
        [["입력", "", "결과"]],
        [["입력", "결과"]],
    ],
)
def test_flow_rejects_blank_or_incomplete_rows(rows: list[list[str]]) -> None:
    table = ReportTable(
        caption="사업 흐름",
        headers=["입력", "실행", "결과"],
        rows=rows,
        presentation="flow",
    )

    assert table_visualization(table) is None


@pytest.mark.parametrize("presentation", ["table", "", "radar", "unknown", "  FLOW "])
def test_default_or_unknown_presentation_falls_back_to_the_original_table(
    presentation: str,
) -> None:
    table = ReportTable(
        caption="원래 표",
        headers=["항목", "값"],
        rows=[["A", "1"], ["B", "2"]],
        presentation=presentation,
    )

    assert table_visualization(table) is None
