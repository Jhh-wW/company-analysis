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


def test_trend_with_mixed_signs_is_drawn_not_dropped() -> None:
    """★ 예전에는 여기서 도식을 포기했다 — 그게 사용자 신고의 원인이었다.

    「부호가 섞이면 한 축으로 오해 없이 표현할 수 없다」가 옛 이유였다.
    맞는 걱정이지만 답이 틀렸다. 답은 «안 그리기»가 아니라 «0선을 두고
    위아래로 나눠 그리기»다. 실제로 이 조건에 걸리는 것이 하필
    «흑자→적자 전환»이라, 가장 중요한 사실일 때만 그림이 사라졌다.
    """
    table = ReportTable(
        caption="실적 (단위: 억원)",
        headers=["사업연도", "영업손익"],
        rows=[["2023", "-1"], ["2024", "2"], ["2025", "3"]],
        presentation="trend",
    )

    visualization = table_visualization(table)

    assert visualization is not None, "부호가 섞였다고 도식을 포기했습니다"
    아래 = {point.label: point.below for point in visualization.series[0].points}
    assert 아래 == {"2023": True, "2024": False, "2025": False}


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


def test_flow_keeps_rows_with_a_blank_cell() -> None:
    """★ 사용자 결정 (2026-08-24) — 한 칸이 비었다고 표를 버리지 않는다.

    8장 「확인된 사례」처럼 «없을 수 있는» 칸 때문에 표 전체가 사라졌다.
    비었다는 것도 정보다 — 지어낸 값보다 훨씬 낫다.
    """
    visualization = table_visualization(
        ReportTable(
            caption="사업 흐름",
            headers=["입력", "실행", "결과"],
            rows=[["입력", "", "결과"]],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.flows == (("입력", "", "결과"),)


def test_flow_drops_a_row_where_every_cell_is_blank() -> None:
    """빈 칸은 허용하되 «전부» 빈 줄은 아무 말도 하지 않는다."""
    assert (
        table_visualization(
            ReportTable(
                caption="사업 흐름",
                headers=["입력", "실행", "결과"],
                rows=[["", "", ""]],
                presentation="flow",
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "rows",
    [
        [["입력", "결과"]],
    ],
)
def test_flow_rejects_incomplete_rows(rows: list[list[str]]) -> None:
    """칸 «개수»가 머리말과 다른 줄은 여전히 버린다 — 어느 칸인지 알 수 없다."""
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


# ══════════════════════════════════════════════════════════
# ⑦ 부호가 섞여도 그린다 — «흑자 → 적자» 전환
# ══════════════════════════════════════════════════════════
#
# ★ 하이브 실측 — 당기순이익이 +1,834 → -34 → -2,544였다. 예전 규칙은
#   「한 계열에 양수·음수가 섞이면 그리지 않는다」라서 4장 도식이 통째로
#   사라졌다. 그런데 그 조건에 걸리는 것이 하필 «흑자에서 적자로 돌아선»
#   경우다 — 독자가 가장 봐야 할 사실인데 그때만 그림이 없어졌다.
#   지금은 점마다 0선 위/아래를 나눠 그린다.


def _trend_table(rows: list[list[str]], headers: list[str]) -> ReportTable:
    return ReportTable(
        caption="세 사업연도 실적 (단위: 억원)",
        headers=headers,
        rows=rows,
        cite="[1]",
        numeric=True,
        presentation="trend",
    )


def test_trend_draws_series_that_turns_from_profit_to_loss() -> None:
    table = _trend_table(
        [
            ["2025", "26499", "-2544"],
            ["2024", "22556", "-34"],
            ["2023", "21781", "1834"],
        ],
        ["사업연도", "매출액", "당기순이익"],
    )

    visualization = table_visualization(table)

    assert visualization is not None, "흑자→적자 전환 계열이 있다고 도식을 포기했습니다"
    순이익 = next(s for s in visualization.series if s.label == "당기순이익")
    아래 = [point.below for point in 순이익.points]
    assert True in 아래 and False in 아래, f"부호가 점마다 안 나뉘었습니다: {아래}"
    # 흑자 해까지 «위험»으로 칠하면 사실보다 나쁘게 읽힌다.
    assert 순이익.risk is False


def test_trend_marks_each_point_by_its_own_sign() -> None:
    table = _trend_table(
        [["2025", "-100"], ["2024", "50"], ["2023", "-20"]],
        ["사업연도", "영업이익"],
    )

    visualization = table_visualization(table)

    assert visualization is not None
    points = visualization.series[0].points
    by_label = {point.label: point for point in points}
    assert by_label["2025"].below is True
    assert by_label["2024"].below is False
    assert by_label["2023"].below is True


def test_trend_series_that_is_all_loss_stays_marked_risk() -> None:
    """계열 «전체»가 손실이면 예전처럼 계열을 위험으로 본다 (동작 무변)."""
    table = _trend_table(
        [["2025", "-100"], ["2024", "-50"], ["2023", "-20"]],
        ["사업연도", "영업이익"],
    )

    visualization = table_visualization(table)

    assert visualization is not None
    series = visualization.series[0]
    assert series.risk is True
    assert all(point.below for point in series.points)


def test_trend_all_positive_series_is_unchanged() -> None:
    """흔한 경우가 예전과 똑같이 나오는지 — 회귀 방지."""
    table = _trend_table(
        [["2025", "300"], ["2024", "200"], ["2023", "100"]],
        ["사업연도", "매출액"],
    )

    visualization = table_visualization(table)

    assert visualization is not None
    series = visualization.series[0]
    assert series.risk is False
    assert not any(point.below for point in series.points)


# ══════════════════════════════════════════════════════════
# ⑧ 그림 «읽는 법» — 무엇을 봐야 하는지 한 줄
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 — 사용자가 완성 기준으로 정한 목업은 그림 밑에
#   「오른쪽 선이 3년 내내 0선 아래에 있다」처럼 읽는 법을 달아 준다.
#   우리 캡션은 제목뿐이었고, 그것이 「이해도가 다르다」는 신고의 실체였다.
#
# ★ 여기서 지키는 가장 중요한 것 — 이 줄은 «그림에 이미 인쇄된 숫자»만으로
#   만든다. AI가 아니라 코드가 만든다. 그림에 없는 말이 들어가면 그것은
#   검증할 수 없는 주장이 되고, 이 엔진의 근거 추적이 통째로 무너진다.


def test_composition_reading_names_the_largest_share() -> None:
    visualization = table_visualization(
        _composition_table(
            [["가", "40"], ["나", "35"], ["다", "15"], ["라", "10"]]
        )
    )

    assert visualization is not None
    reading = visualization.reading
    assert "가" in reading and "40" in reading, reading
    # 상위 둘의 합(75)도 그림에서 눈으로 더할 수 있는 값이다.
    assert "75" in reading, reading


def test_trend_reading_states_direction_and_zero_line() -> None:
    visualization = table_visualization(
        _trend_table(
            [["2025", "-2544"], ["2024", "-34"], ["2023", "1834"]],
            ["사업연도", "당기순이익"],
        )
    )

    assert visualization is not None
    reading = visualization.reading
    assert "당기순이익" in reading
    assert "2023" in reading and "2025" in reading
    assert "0선 아래" in reading, reading


def test_flow_reading_counts_paths() -> None:
    visualization = table_visualization(
        ReportTable(
            caption="사업 경로",
            headers=["무엇으로 시작하나", "회사가 하는 일", "누구에게 닿나"],
            rows=[["가", "나", "다"], ["라", "마", "바"], ["사", "아", "자"]],
            cite="[1]",
            presentation="flow",
        )
    )

    assert visualization is not None
    assert "3개" in visualization.reading, visualization.reading


def test_reading_never_judges() -> None:
    """★ 코드가 만드는 문장이 «판단»을 하면 검증할 수 없는 주장이 된다.

    적자를 그리되 「나쁘다」·「위험하다」·「우려된다」고 말하지 않는다.
    그림에 그려진 방향과 개수만 말한다.
    """
    금지어 = (
        "나쁘", "위험", "우려", "심각", "부진", "악화", "훌륭", "좋", "우수", "탁월"
    )
    표들 = [
        _trend_table(
            [["2025", "-2544"], ["2024", "-34"], ["2023", "1834"]],
            ["사업연도", "당기순이익"],
        ),
        _trend_table(
            [["2025", "-100"], ["2024", "-50"], ["2023", "-20"]],
            ["사업연도", "영업이익"],
        ),
        _composition_table([["가", "40"], ["나", "35"], ["다", "25"]]),
    ]
    for table in 표들:
        visualization = table_visualization(table)
        assert visualization is not None
        for word in 금지어:
            assert word not in visualization.reading, (
                f"읽는 법이 판단을 합니다: 「{word}」 in {visualization.reading!r}"
            )


def test_reading_is_empty_when_there_is_nothing_to_say() -> None:
    """말할 것이 없으면 빈 줄을 만들지 않는다 — 화면이 자리를 안 남긴다."""
    visualization = table_visualization(
        ReportTable(
            caption="경로",
            headers=["가", "나"],
            rows=[["A", "B"]],
            cite="[1]",
            presentation="flow",
        )
    )

    assert visualization is not None
    # 줄이 하나면 «그 줄 자체»를 말해 준다 — 빈 문자열이 아니다.
    assert visualization.reading
    assert "A" in visualization.reading and "B" in visualization.reading
