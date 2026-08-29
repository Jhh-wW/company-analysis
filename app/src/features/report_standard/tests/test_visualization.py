from __future__ import annotations

import pytest

from src.features.pipeline.port import ReportTable
from src.features.report_standard.visualization import (
    COMPOSITION_MAX_ITEMS,
    COMPOSITION_MIN_ITEMS,
    COMPOSITION_TONE_STEPS,
    CardField,
    _CARD_HEADER_KEY_SETS,
    _CARD_HEADER_SETS,
    _CARD_LIMITATION_LABEL,
    _CARD_LIMITATION_TEXT_BY_HEADER_KEY,
    _CARD_TITLE_COLUMN_BY_HEADER_KEY,
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


# ══════════════════════════════════════════════════════════
# ⑦-2 흐름표를 카드로 — 화살표가 안 맞는 속성 나열
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 (목업 실측·2026-08-25) — 1·6·8장은 composer가 5·7장과
#   «같은 그릇»(경로표)을 쓰지만, 칸끼리 이어지지 않는 독립된 답이다.
#   화살표를 그리면 없는 인과를 있는 것처럼 보인다. 목업은 이 세 장을
#   화살표 없는 라벨:값 카드로 낸다 — 아래 시험이 그 판정을 코드로 고정한다.


def test_identity_headers_become_a_card_not_an_arrow() -> None:
    """1장 «회사가 스스로를 어떻게 규정하나»는 카드다."""
    visualization = table_visualization(
        ReportTable(
            caption="회사가 스스로를 어떻게 규정하나",
            headers=["공식 자기정의", "사업 범위", "이 보고서의 해석"],
            rows=[["소재 가공 회사", "가구·가전용 시트", "B2B 소재 회사"]],
            cite="[3]",
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card"
    assert visualization.flows == ()
    assert len(visualization.cards) == 1
    card = visualization.cards[0]
    assert card.title == ""
    assert card.fields == (
        CardField(label="공식 자기정의", value="소재 가공 회사"),
        CardField(label="사업 범위", value="가구·가전용 시트"),
        CardField(label="이 보고서의 해석", value="B2B 소재 회사"),
    )


@pytest.mark.parametrize(
    "headers",
    [
        ["계획", "시점", "공시된 내용"],  # 6장 성장 계획
        ["내건 가치", "일하는 원칙", "확인된 사례"],  # 8장 인재상
    ],
)
def test_strategy_and_culture_headers_also_become_cards(headers: list[str]) -> None:
    visualization = table_visualization(
        ReportTable(
            caption="속성 나열 표",
            headers=headers,
            rows=[["가", "나", "다"]],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card"


def test_old_column_order_from_stored_reports_still_becomes_a_card() -> None:
    """★ 회귀 시험 (팀장 실측·2026-08-25) — 저장된 보고서 31건은 6장 칸이
    옛 순서(「시점, 계획, 공시된 내용」)다. composer가 «값은 그대로 두고
    순서만» 「계획, 시점, 공시된 내용」으로 바꿨는데, tuple 완전일치로
    판정하면 그 31건의 6장이 배포 즉시 «카드 → 화살표»로 되돌아간다 —
    사용자가 이미 받아 본 보고서의 모양이 바뀌는 것이라 실제 회귀다.

    frozenset 비교(_CARD_HEADER_KEY_SETS)로 바꾼 뒤에는 옛 순서도 새
    순서도 같은 칸 «구성»이므로 둘 다 카드여야 한다 — 이 시험이 그걸
    고정한다. 순서는 composer 쪽 시험(test_section_tables.py)이 지키므로
    여기서 순서까지 다시 검사하지 않는다.
    """
    visualization = table_visualization(
        ReportTable(
            caption="회사가 밝힌 성장 계획",
            headers=["시점", "계획", "공시된 내용"],  # 옛 순서 — 저장본 그대로
            rows=[["2026년 하반기", "열분해 설비", "중장기 기업가치 제고 계획"]],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card", (
        "옛 순서 저장본의 6장이 카드가 아니라 흐름(화살표)으로 나옵니다 — "
        "회귀가 되살아났습니다"
    )
    assert visualization.cards[0].fields == (
        CardField(label="시점", value="2026년 하반기"),
        CardField(label="계획", value="열분해 설비"),
        CardField(label="공시된 내용", value="중장기 기업가치 제고 계획"),
        CardField(label="범위·한계", value="실행 여부는 확인하지 않았습니다"),
    )


# ══════════════════════════════════════════════════════════
# ⑦-3 3장 핵심 제품·서비스 — 4칸 + «제목 칸이 있는» 유일한 카드
# ══════════════════════════════════════════════════════════
#
# ★ 왜 다른가 (팀장 지시·2026-08-25) — composer.constants.PORTFOLIO_TABLE_HEADERS는
#   4칸이고, 1번 칸(제품·서비스명)이 «그 줄의 주제»다. 목업 3장도 제품마다
#   카드 1장(제목=제품명)씩 나온다. 1·6·8장은 표 자신이 주제 칸을 안
#   알려줘 제목을 비워 뒀지만, 3장은 «제품·서비스명»이라는 이름으로 주제
#   칸이 명시돼 있으므로 지어내는 것이 아니라 표가 준 정보를 쓰는 것이다.


def test_portfolio_four_column_headers_become_a_card_with_a_title() -> None:
    visualization = table_visualization(
        ReportTable(
            caption="지금 무엇을 미는가 — 핵심 제품·서비스와 역할",
            headers=["제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할"],
            rows=[
                [
                    "리얼 알루미늄 합지 필름",
                    "가전 표면재 납품 제품",
                    "생산확대·투자",
                    "전사 수익 경로",
                ]
            ],
            cite="[1]",
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card"
    assert len(visualization.cards) == 1
    card = visualization.cards[0]
    # 제품·서비스명이 제목으로 «빠지고», 나머지 3칸만 라벨:값으로 남는다.
    assert card.title == "리얼 알루미늄 합지 필름"
    assert card.fields == (
        CardField(label="제품·서비스 범위", value="가전 표면재 납품 제품"),
        CardField(label="중점 추진 근거", value="생산확대·투자"),
        CardField(label="사업적 역할", value="전사 수익 경로"),
        CardField(label="범위·한계", value="공식 근거가 확인한 범위로 한정합니다"),
    )


def test_portfolio_multiple_products_become_multiple_titled_cards() -> None:
    """근거가 확인된 제품이 여럿이면 카드도 여럿이고, 각 카드 제목이 다르다."""
    visualization = table_visualization(
        ReportTable(
            caption="지금 무엇을 미는가 — 핵심 제품·서비스와 역할",
            headers=["제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할"],
            rows=[
                ["리얼 알루미늄 합지 필름", "가전 표면재", "생산확대", "전사 수익 경로"],
                ["폐플라스틱 열분해유", "자원순환 판매 제품", "투자·증설", "전사 수익 경로"],
            ],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card"
    assert [card.title for card in visualization.cards] == [
        "리얼 알루미늄 합지 필름",
        "폐플라스틱 열분해유",
    ]
    # 3칸(범위·중점근거·역할) + 「범위·한계」 1칸 = 4.
    assert len(visualization.cards[0].fields) == 4
    assert len(visualization.cards[1].fields) == 4
    assert visualization.cards[0].fields[-1] == CardField(
        label="범위·한계", value="공식 근거가 확인한 범위로 한정합니다"
    )


def test_portfolio_blank_product_name_falls_back_to_no_title() -> None:
    """제품명 칸이 비었으면(있을 수 있는 빈 칸) 제목을 지어내지 않는다."""
    visualization = table_visualization(
        ReportTable(
            caption="지금 무엇을 미는가 — 핵심 제품·서비스와 역할",
            headers=["제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할"],
            rows=[["", "가전 표면재", "생산확대", "전사 수익 경로"]],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card"
    assert visualization.cards[0].title == ""
    assert visualization.cards[0].fields == (
        CardField(label="제품·서비스 범위", value="가전 표면재"),
        CardField(label="중점 추진 근거", value="생산확대"),
        CardField(label="사업적 역할", value="전사 수익 경로"),
        CardField(label="범위·한계", value="공식 근거가 확인한 범위로 한정합니다"),
    )


@pytest.mark.parametrize(
    "headers",
    [
        ["핵심 자산", "제품·서비스", "고객 행동·과금", "반복·확장 수익"],  # 2장 — e2e 시험이 flow-row를 요구
        ["무엇으로 시작하나", "회사가 하는 일", "누구에게 닿나"],  # 7장 — e2e 시험이 flow-row를 요구
        ["지금 겪는 과제", "회사가 밝힌 대응"],  # 5장 — 문제→대응 방향성이 있어 화살표 유지
    ],
)
def test_genuinely_causal_headers_stay_as_arrow_flow(headers: list[str]) -> None:
    """★ 2·7장은 test_e2e_offline.py가 literal ``class="flow-row"``를 요구한다.

    이 칸 이름을 실수로 카드 집합에 넣으면 그 이음매 시험이 빨간불이 된다
    — 그래서 여기서도 «카드가 아님»을 못 박는다.
    """
    row = tuple(f"칸{i}" for i in range(len(headers)))
    visualization = table_visualization(
        ReportTable(
            caption="흐름표",
            headers=headers,
            rows=[list(row)],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "flow"
    assert visualization.cards == ()


def test_card_drops_blank_cells_and_titles_multi_row_tables_blank() -> None:
    """줄이 여럿이면 카드도 여럿이다. 빈 칸은 그 카드에서만 빠지고,
    제목은 지어내지 않는다(빈 문자열 — 표 캡션이 이미 제목 역할)."""
    visualization = table_visualization(
        ReportTable(
            caption="회사가 밝힌 성장 계획",
            headers=["계획", "시점", "공시된 내용"],
            rows=[
                ["열분해 설비", "2026년 하반기", ""],
                ["해외 납품 확대", "2026~2028년", "차량 외장재·방염 필름"],
            ],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.kind == "card"
    assert len(visualization.cards) == 2
    assert visualization.cards[0].fields == (
        CardField(label="계획", value="열분해 설비"),
        CardField(label="시점", value="2026년 하반기"),
        CardField(label="범위·한계", value="실행 여부는 확인하지 않았습니다"),
    )
    assert visualization.cards[1].fields == (
        CardField(label="계획", value="해외 납품 확대"),
        CardField(label="시점", value="2026~2028년"),
        CardField(label="공시된 내용", value="차량 외장재·방염 필름"),
        CardField(label="범위·한계", value="실행 여부는 확인하지 않았습니다"),
    )
    assert all(card.title == "" for card in visualization.cards)


# ══════════════════════════════════════════════════════════
# ⑦-4 카드 맨 아래 「범위·한계」 행 — 층2(코드)만, AI 호출 0
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 (사용자 승인·2026-08-25) — 목업 카드는 거의 항상
#   마지막 줄에 「공식 근거가 확인한 범위로 한정」 같은 절차적 사실
#   서술이 있다. `docs/실행계획_엔진v2/11_결정_전수대조_04_범위한계_
#   재현안.md` §2-1이 정리한 v1 폴백 문구를 그대로 옮긴다 — citations
#   개수·section_id만으로 정하고, 가치 판단(좋다/나쁘다/위험 등)은 한
#   글자도 안 쓴다(같은 문서 §5 — v1도 13건 전수에서 0건이었다).


def test_culture_card_gets_the_v1_precedent_phrase() -> None:
    """8장은 v1(_culture_blocks)이 실제로 쓰던 라벨·문구를 그대로 쓴다."""
    visualization = table_visualization(
        ReportTable(
            caption="무엇을 내걸고 어떻게 일하나",
            headers=["내건 가치", "일하는 원칙", "확인된 사례"],
            rows=[["고객 최우선", "공식 경영철학", ""]],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert visualization.cards[0].fields[-1] == CardField(
        label=_CARD_LIMITATION_LABEL, value="전사 공통 공식 기준입니다"
    )


def test_limitation_phrase_stays_neutral_when_a_column_is_blank() -> None:
    """★ 실측(팀장·2026-08-25, 하이브) — 6장 「시점」 칸이 두 행 다 비었다.
    이건 정상이다(사용자 결정: 안 적혀 있으면 칸을 비우고 줄은 살린다).
    「범위·한계」 문구가 그걸 보고 «자료 부족」류로 바뀌면 안 된다 — 빈
    칸은 정직한 결과지 결함이 아니다. 이 문구는 «장 종류»만 보고 정하지
    «어느 칸이 비었는지»는 아예 안 본다 — 이 시험이 그걸 못 박는다.
    """
    both_rows_missing_시점 = table_visualization(
        ReportTable(
            caption="회사가 밝힌 성장 계획",
            headers=["계획", "시점", "공시된 내용"],
            rows=[
                ["글로벌 시장 진출", "", ""],
                ["멀티 레이블 시스템의 글로벌 확대", "", "중장기 기업가치 제고 계획"],
            ],
            presentation="flow",
        )
    )

    assert both_rows_missing_시점 is not None
    for card in both_rows_missing_시점.cards:
        limitation_fields = [f for f in card.fields if f.label == _CARD_LIMITATION_LABEL]
        assert len(limitation_fields) == 1
        assert limitation_fields[0].value == "실행 여부는 확인하지 않았습니다"
        for word in ("자료", "부족", "미확인", "확인되지 않"):
            assert word not in limitation_fields[0].value, (
                f"빈 칸을 보고 「자료 부족」류 문구로 바뀌었습니다: {limitation_fields[0].value!r}"
            )


def test_identity_card_gets_no_limitation_row() -> None:
    """★ 1장은 «일부러» 뺐다 — v1도 목업도 1장 카드엔 이 줄이 없다
    (재현안 문서 §1: "1장은 층2 고정 문구가 없는 유일한 장"). 조건에
    안 걸리면 문구를 지어내지 않고 그냥 없다 — 이 시험이 그걸 못 박는다.
    """
    visualization = table_visualization(
        ReportTable(
            caption="회사가 스스로를 어떻게 규정하나",
            headers=["공식 자기정의", "사업 범위", "이 보고서의 해석"],
            rows=[["소재 가공 회사", "가구·가전용 시트", "B2B 소재 회사"]],
            presentation="flow",
        )
    )

    assert visualization is not None
    assert not any(field.label == _CARD_LIMITATION_LABEL for field in visualization.cards[0].fields)


def test_limitation_text_never_uses_judgmental_words() -> None:
    """★ 가치 판단(평가어) 금지 — 문구 사전 자체에 평가어가 없는지 못
    박는다. 재현안 문서 §5: v1도 13건 전수에서 0건이었다."""
    금지어 = ("좋", "나쁘", "우수", "위험", "우려", "미흡", "훌륭", "탁월")
    for text in _CARD_LIMITATION_TEXT_BY_HEADER_KEY.values():
        for word in 금지어:
            assert word not in text, f"「범위·한계」 문구가 판단을 합니다: 「{word}」 in {text!r}"


def test_card_header_sets_stay_in_sync_with_composer_constants() -> None:
    """★ 취약점 실측 — visualization.py의 _CARD_HEADER_SETS는 composer/
    constants.py의 칸 이름을 «문자열로만» 그대로 옮겼다(장 id가 없어서).
    두 파일이 코드로 이어져 있지 않으므로, 누군가 composer 쪽 칸 이름을
    바꾸면 이 판정이 «조용히» 깨진다(카드가 다시 화살표로 돌아간다) —
    이 시험이 있어야 그 변경이 여기서도 빨간불로 걸린다.

    이 저장소는 「조용히 되돌아가는 것」에 이미 네 번 당했다
    (test_e2e_offline.py 상단 주석 참조) — 다섯 번째를 여기서 막는다.
    """
    from src.features.composer import constants as composer_constants

    # 카드여야 하는 칸 이름 4개 — composer 쪽 상수와 «자모 하나까지» 같아야 한다.
    # (순서까지 정확히 같은지도 기록해 둔다 — 순서만 다르면 아래 frozenset
    # 비교는 통과하므로, 이 줄이 «지금 기록된 순서»가 실제와 같은지 알려 준다.)
    assert composer_constants.IDENTITY_TABLE_HEADERS in _CARD_HEADER_SETS
    assert composer_constants.STRATEGY_TABLE_HEADERS in _CARD_HEADER_SETS
    assert composer_constants.CULTURE_TABLE_HEADERS in _CARD_HEADER_SETS
    assert composer_constants.PORTFOLIO_TABLE_HEADERS in _CARD_HEADER_SETS

    # 화살표를 유지해야 하는 칸 이름 3개 — 실수로 카드 집합에 들어가면 안 된다.
    assert composer_constants.BUSINESS_FLOW_HEADERS not in _CARD_HEADER_SETS
    assert composer_constants.OPERATIONS_FLOW_HEADERS not in _CARD_HEADER_SETS
    assert composer_constants.CHALLENGE_FLOW_HEADERS not in _CARD_HEADER_SETS

    # ★ 실제 판정 코드(_flow())가 쓰는 것은 이 frozenset이다 — 순서가 바뀌어도
    # (값은 그대로면) 안 흔들리는지를 «실행 경로 그대로» 확인한다. 2026-08-25
    # composer가 6장 칸 순서를 바꾼 사건이 실제로 있었다 — 이 비교가 그
    # 실패 모드(순서 변경)를 없앤다. «값»이 바뀌는 실패 모드는 위 tuple
    # 비교가 잡는다 — 둘을 같이 둬야 한다.
    assert frozenset(composer_constants.IDENTITY_TABLE_HEADERS) in _CARD_HEADER_KEY_SETS
    assert frozenset(composer_constants.STRATEGY_TABLE_HEADERS) in _CARD_HEADER_KEY_SETS
    assert frozenset(composer_constants.CULTURE_TABLE_HEADERS) in _CARD_HEADER_KEY_SETS
    assert frozenset(composer_constants.PORTFOLIO_TABLE_HEADERS) in _CARD_HEADER_KEY_SETS
    assert frozenset(composer_constants.BUSINESS_FLOW_HEADERS) not in _CARD_HEADER_KEY_SETS
    assert frozenset(composer_constants.OPERATIONS_FLOW_HEADERS) not in _CARD_HEADER_KEY_SETS
    assert frozenset(composer_constants.CHALLENGE_FLOW_HEADERS) not in _CARD_HEADER_KEY_SETS

    # ★ 3장은 «제목 칸이 있는» 유일한 카드다 — 제목 칸 이름도 실제 상수와
    # 맞아야 한다. composer가 필드명을 바꾸면(예: 「제품·서비스명」→다른
    # 이름) 제목 추출이 조용히 멈춘다 — 이 줄이 그것도 잡는다.
    portfolio_key = frozenset(composer_constants.PORTFOLIO_TABLE_HEADERS)
    assert _CARD_TITLE_COLUMN_BY_HEADER_KEY.get(portfolio_key) in composer_constants.PORTFOLIO_TABLE_HEADERS


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


def test_화살표_장_집합이_카드_판정과_어긋나지_않는다() -> None:
    """★ composer의 `FLOW_ARROW_SECTION_IDS`와 여기 카드 판정은 «서로 다른
    파일의 다른 기준»(장 id vs 칸 이름)이다 — 어긋나면 조용히 망가진다.

    어긋나면 무슨 일이 나나:
      · 카드 장이 화살표 집합에 «들어가면» → 빈 칸이 「미확인」으로 채워져
        「확인된 사례: 미확인」·제목이 「미확인」인 카드가 인쇄된다.
      · 화살표 장이 «빠지면» → 빈 칸이 그대로 나가 화면에 «라벨만 있고 속이
        빈 76px 상자»가 화살표와 함께 그려진다.
    두 실패 모두 예외를 내지 않으므로, 이 시험이 유일한 그물이다.
    """
    from src.features.composer import constants as composer_constants

    for section_id, headers in composer_constants.FLOW_HEADERS_BY_SECTION.items():
        그린다_카드로 = frozenset(headers) in _CARD_HEADER_KEY_SETS
        화살표_장이다 = section_id in composer_constants.FLOW_ARROW_SECTION_IDS
        assert 그린다_카드로 != 화살표_장이다, (
            f"{section_id} 장: 카드 판정={그린다_카드로} 인데 "
            f"FLOW_ARROW_SECTION_IDS 등록={화살표_장이다} 입니다 — "
            f"둘 중 하나가 틀렸습니다"
        )

    # 화살표 장은 정확히 셋이다(2·5·7장). 늘거나 줄면 위 대조가 통과해도
    # 사람이 한 번 더 보게 한다.
    assert len(composer_constants.FLOW_ARROW_SECTION_IDS) == 3


def test_한계_문구는_행마다_달라지는_사실을_단정하지_않는다() -> None:
    """★ 2026-08-29 실측 — 6장 문구가 「아직 실행되지 않은 계획입니다」였다.

    그 문장은 «각 행이 실행됐는지»를 단정하는데, 이 층에는 판정할 재료가 없다.
    조건도 「칸 이름이 6장 것인가」 하나뿐이라 그 표의 «모든 행»에 무조건 붙었다.
    실측: 우리은행 4/4행·현대카드 2/2행에 붙었고 그중 4행은 같은 보고서 본문이
    과거형으로 「출시하여 … 구축했으며」라고 써서 정면으로 어긋났다.

    ★ 이 층의 문구는 «행 내용과 무관하게 참»이어야 한다. 그렇지 않은 문구는
      근거 없는 주장이다. 아래 낱말은 행마다 달라지는 상태를 단정한다.
    ⚠️ 이 시험이 깨지면 보고서가 다시 「본문과 어긋나는 라벨」을 인쇄한다.
    """
    단정하는_말 = ("않은", "미실행", "완료됐", "실행됐", "중단된", "예정입니다")

    for 칸이름, 문구 in _CARD_LIMITATION_TEXT_BY_HEADER_KEY.items():
        for 말 in 단정하는_말:
            assert 말 not in 문구, (
                f"★ 한계 문구가 행별 사실을 단정한다: {sorted(칸이름)} → 「{문구}」"
            )


def test_계획_표_한계_문구는_확인하지_않았다고_말한다() -> None:
    """★ 우리가 실제로 한 일(확인하지 않음)을 그대로 적는다."""
    문구 = _CARD_LIMITATION_TEXT_BY_HEADER_KEY[
        frozenset(("계획", "시점", "공시된 내용"))
    ]

    assert "확인하지 않았습니다" in 문구, f"★ 문구가 바뀌었다: 「{문구}」"
