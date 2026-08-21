from __future__ import annotations

import pytest

from src.features.pipeline.port import ReportTable
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


def test_composition_rejects_more_than_five_categories() -> None:
    table = _composition_table(
        [
            [f"범주-{index}", value]
            for index, value in enumerate(("20", "20", "20", "20", "10", "10"), 1)
        ]
    )

    assert table_visualization(table) is None


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
