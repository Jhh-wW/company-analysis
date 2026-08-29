"""4장 «3개년 요약 숫자» 계약 — 표 안에서만 계산·근거 동봉·빈 자리 금지.

★ 왜 실제 ``build_three_year_table`` 출력을 쓰나 — 요약 숫자의 재료로 허용된
  표는 그 함수가 전자공시 원수치로 fail-closed 하게 만든 4장 실적표뿐이다.
  손으로 지어낸 표로만 시험하면 「표가 실제로 만드는 모양」이 바뀌어도 시험이
  안 깨진다. 손으로 만든 표는 실제 표가 만들지 «못하는» 모양(다른 단위, 두 해,
  뒤집힌 순서)을 볼 때만 쓴다.

★ 이 시험은 «순수 함수»만 본다. 화면·PDF 배선은 본체 담당이라 여기서 보지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.features.company_performance.logic import build_three_year_table
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.report_standard.cover_metrics import (
    COVER_METRIC_CANDIDATES,
    COVER_METRIC_COUNT,
    COVER_METRIC_LABELS,
    PERIOD_HEADER,
    cover_metrics,
)
from src.features.report_standard.period_summary import (
    AMOUNT_METRIC_LABELS,
    KNOWN_METRIC_LABELS,
    MIN_METRICS_FOR_BAND,
    EMPTY_PERIOD_SUMMARY,
    LOSS_CONTINUED_PHRASE,
    LOSS_NARROWED_PHRASE,
    LOSS_RESOLVED_PHRASE,
    LOSS_TURN_PHRASE,
    LOSS_WIDENED_PHRASE,
    MARGIN_LABEL,
    MARGIN_UNIT,
    MISSING_CHANGE_TEXT,
    MISSING_METRIC_NOTE,
    OPTIONAL_METRIC_LABELS,
    PROFIT_TURN_PHRASE,
    ZERO_BASE_NOTE,
    ZERO_BASE_PHRASE,
    ChangeKind,
    Direction,
    PeriodSummary,
    period_summary,
    period_summary_from_table,
)


_CITE = "조각 1·재무"
#: DART 응답의 기간 3개. 순서는 최신 → 과거다(``_PERIOD_FIELDS`` 순서).
_PERIODS = (
    "2025.01.01 ~ 2025.12.31",
    "2024.01.01 ~ 2024.12.31",
    "2023.01.01 ~ 2023.12.31",
)
_HUNDRED_MILLION = 100_000_000


def _won(hundred_millions: int) -> str:
    """억원 정수를 원 단위 문자열로 바꾼다 — 표시값 반올림을 피하려 정수만 쓴다."""

    return str(hundred_millions * _HUNDRED_MILLION)


def _dart_row(
    account_id: str, account_nm: str, amounts: tuple[int, int, int]
) -> dict[str, Any]:
    """최신 → 과거 순서의 억원 정수 3개를 DART 한 행으로 만든다."""

    return {
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": account_id,
        "account_nm": account_nm,
        "bsns_year": "2025",
        "reprt_code": "11011",
        "currency": "KRW",
        "thstrm_dt": _PERIODS[0],
        "thstrm_amount": _won(amounts[0]),
        "frmtrm_dt": _PERIODS[1],
        "frmtrm_amount": _won(amounts[1]),
        "bfefrmtrm_dt": _PERIODS[2],
        "bfefrmtrm_amount": _won(amounts[2]),
    }


def _table(
    *,
    revenue: tuple[int, int, int] = (5940, 5800, 5665),
    operating: tuple[int, int, int] = (1552, 1600, 1694),
    net: tuple[int, int, int] | None = None,
) -> ReportTable:
    """실제 4장 실적표를 만든다 (억원 정수 3개씩, 최신 → 과거 순서)."""

    rows = [
        _dart_row("ifrs-full_Revenue", "매출액", revenue),
        _dart_row("dart_OperatingIncomeLoss", "영업이익", operating),
    ]
    if net is not None:
        rows.append(_dart_row("ifrs-full_ProfitLoss", "당기순이익", net))
    table = build_three_year_table({"status": "000", "list": rows}, cite=_CITE)
    assert table is not None, "시험 전제가 깨졌다 — 실적표가 만들어져야 한다."
    return table


def _bank_table(
    *,
    operating: tuple[int, int, int] = (1552, 1600, 1694),
    net: tuple[int, int, int] = (1184, 973, 1299),
) -> ReportTable:
    """은행 손익계산서 모양 — 「매출액」 계정이 «아예 없다».

    실측(우리은행 2026-08-29): 17개 행에서 영업이익·당기순이익만 나왔다.
    """

    table = build_three_year_table(
        {
            "status": "000",
            "list": [
                _dart_row("dart_OperatingIncomeLoss", "영업이익", operating),
                _dart_row("ifrs-full_ProfitLoss", "당기순이익", net),
            ],
        },
        cite=_CITE,
    )
    assert table is not None, "시험 전제 — 은행 모양 표가 만들어져야 한다"
    return table


def test_매출액이_없는_표도_변화_요약_띠를_그린다() -> None:
    """★ 2026-08-29 사용자 결정 ① — 없는 지표를 지어내지 말고 있는 것으로 그린다.

    예전 관문은 「매출액·영업이익이 «둘 다» 표에 있어야 한다」였다. 매출액 계정이
    없는 은행은 4장 변화 요약 띠가 통째로 사라졌다.
    ⚠️ 이 시험이 깨지면 은행·보험 업종의 4장이 다시 비어 나간다.
    """
    summary = period_summary(_report(_bank_table()))

    labels = [item.label for item in summary.items]
    assert "영업이익" in labels
    assert "당기순이익" in labels


def test_없는_매출액은_조용히_빠지지_않고_없다고_적힌다() -> None:
    """★ 정직성 안전선 — 열이 빠진 것과 값이 0인 것은 다르다.

    조용히 건너뛰면 독자는 왜 빠졌는지 알 수 없다.
    """
    summary = period_summary(_report(_bank_table()))

    빠진칸 = [item for item in summary.items if item.label == "매출액"]
    assert 빠진칸, "★ 없는 지표를 아무 말 없이 뺐다"
    assert 빠진칸[0].note == MISSING_METRIC_NOTE
    assert 빠진칸[0].latest_value == "", "★ 없는 값에 가짜 숫자를 채웠다"


def test_매출액이_없으면_영업이익률도_만들지_않는다() -> None:
    """★ 안전선 — 나눗셈의 분모가 없으면 비율을 지어내지 않는다."""
    summary = period_summary(_report(_bank_table()))

    assert MARGIN_LABEL not in [item.label for item in summary.items]


def test_두_띠와_표의_최소_지표_수가_같다() -> None:
    """★ 표는 만들어졌는데 띠만 안 나오는 어긋남을 막는다."""
    from src.features.company_performance.logic import MIN_METRICS_FOR_TABLE

    assert MIN_METRICS_FOR_BAND == MIN_METRICS_FOR_TABLE
    assert MIN_METRICS_FOR_BAND == COVER_METRIC_COUNT
    assert KNOWN_METRIC_LABELS == COVER_METRIC_CANDIDATES


def _manual_table(
    rows: list[list[str]],
    *,
    headers: list[str] | None = None,
    display_unit: str = "억원",
    numeric: bool = True,
) -> ReportTable:
    """실제 표가 «만들 수 없는» 모양을 보기 위한 손 조립 표."""

    return ReportTable(
        caption="손으로 만든 시험용 표",
        headers=headers or [PERIOD_HEADER, "매출액", "영업이익"],
        rows=rows,
        cite=_CITE,
        numeric=numeric,
        display_unit=display_unit,
        presentation="trend",
    )


def _report(table: ReportTable | None) -> Report:
    """4장에 실적표만 얹은 최소 보고서."""

    return Report(
        company="시험상사",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            ReportSection(
                cell="past_changes",
                title="3개년 주요 변화와 실행",
                display_number="4",
                tag="#과거",
                tables=[table] if table is not None else [],
            )
        ],
    )


def _by_label(summary: PeriodSummary) -> dict[str, Any]:
    return {item.label: item for item in summary.items}


# ══════════════════════════════════════════════════════════
# ① 정상 — 표 첫 행·마지막 행만으로 증감을 만든다
# ══════════════════════════════════════════════════════════


def test_정상이면_매출액_영업이익_영업이익률_세_항목이_나온다() -> None:
    summary = period_summary(_report(_table()))

    assert summary
    assert [item.label for item in summary.items][:3] == [
        "매출액",
        "영업이익",
        MARGIN_LABEL,
    ]
    assert summary.cite == _CITE


def test_증감률은_표의_첫_행과_마지막_행_값으로만_계산한다() -> None:
    """5,665 → 5,940 = +4.9%, 1,694 → 1,552 = -8.4%. 눈으로 재검산 가능해야 한다."""

    table = _table()
    summary = period_summary_from_table(table)
    items = _by_label(summary)

    revenue = items["매출액"]
    assert revenue.base_period == "2023" and revenue.base_value == "5,665"
    assert revenue.latest_period == "2025" and revenue.latest_value == "5,940"
    assert revenue.change == "+4.9%"
    assert revenue.change_kind == ChangeKind.PERCENT
    assert revenue.direction == Direction.UP
    assert revenue.unit == table.display_unit

    operating = items["영업이익"]
    assert operating.base_value == "1,694" and operating.latest_value == "1,552"
    assert operating.change == "-8.4%"
    assert operating.direction == Direction.DOWN


def test_영업이익률은_같은_해_두_칸으로_만들고_증감은_퍼센트포인트다() -> None:
    """1,694/5,665 = 29.9%, 1,552/5,940 = 26.1% → -3.8%p."""

    summary = period_summary_from_table(_table())
    margin = _by_label(summary)[MARGIN_LABEL]

    assert margin.base_value == "29.9" and margin.latest_value == "26.1"
    assert margin.unit == MARGIN_UNIT
    assert margin.change == "-3.8%p"
    assert margin.change_kind == ChangeKind.POINT
    assert margin.direction == Direction.DOWN


def test_인쇄된_퍼센트끼리_더하고_빼면_퍼센트포인트가_맞는다() -> None:
    """화면에 함께 찍히는 세 숫자가 서로 안 맞으면 독자가 재검산에 실패한다."""

    from decimal import Decimal

    margin = _by_label(period_summary_from_table(_table()))[MARGIN_LABEL]
    delta = Decimal(margin.latest_value) - Decimal(margin.base_value)

    assert margin.change == f"{delta:+.1f}%p"


def test_요약_숫자의_근거는_중간_사업연도에서_오지_않는다() -> None:
    table = _table(revenue=(5940, 9999, 5665), operating=(1552, 8888, 1694))
    summary = period_summary_from_table(table)
    middle = set(table.rows[1])

    for item in summary.items:
        assert item.base_value not in middle - set(table.rows[-1])
        assert item.latest_value not in middle - set(table.rows[0])
        assert item.base_period != table.rows[1][0]


def test_금액_항목의_근거_값은_표_셀_글자_그대로다() -> None:
    """새 숫자를 만들지 않았다는 것을 표 셀 집합으로 확인한다."""

    table = _table(net=(300, 320, 280))
    summary = period_summary_from_table(table)
    latest_cells = set(table.rows[0])
    base_cells = set(table.rows[-1])

    for item in summary.items:
        if item.label == MARGIN_LABEL:
            continue
        assert item.latest_value in latest_cells
        assert item.base_value in base_cells
        assert item.unit == table.display_unit


def test_제목은_비교한_두_사업연도를_그대로_밝힌다() -> None:
    summary = period_summary_from_table(_table())

    assert "2023" in summary.title and "2025" in summary.title
    assert PERIOD_HEADER in summary.title


# ══════════════════════════════════════════════════════════
# ② 부호가 바뀌면 퍼센트가 뜻을 잃는다 — 말로 낸다
# ══════════════════════════════════════════════════════════


def test_흑자에서_적자로_바뀌면_적자_전환이라고_말한다() -> None:
    summary = period_summary_from_table(
        _table(operating=(-210, 400, 1694))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == LOSS_TURN_PHRASE
    assert operating.change_kind == ChangeKind.PHRASE
    assert operating.direction == Direction.DOWN
    assert "%" not in operating.change


def test_적자가_이어지고_커지면_적자_확대라고_말한다() -> None:
    summary = period_summary_from_table(
        _table(operating=(-1500, -1200, -1000))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == LOSS_WIDENED_PHRASE
    assert operating.change_kind == ChangeKind.PHRASE
    assert operating.direction == Direction.DOWN


def test_적자가_이어지고_줄면_적자_축소라고_말한다() -> None:
    summary = period_summary_from_table(
        _table(operating=(-1000, -1200, -1500))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == LOSS_NARROWED_PHRASE
    assert operating.direction == Direction.UP


def test_적자_규모가_같으면_적자_지속이라고_말한다() -> None:
    summary = period_summary_from_table(
        _table(operating=(-1000, -1100, -1000))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == LOSS_CONTINUED_PHRASE
    assert operating.direction == Direction.FLAT


def test_적자에서_흑자로_바뀌면_흑자_전환이라고_말한다() -> None:
    summary = period_summary_from_table(
        _table(operating=(500, -200, -1000))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == PROFIT_TURN_PHRASE
    assert operating.direction == Direction.UP


def test_적자에서_손익_0이_되면_적자_해소라고_말한다() -> None:
    summary = period_summary_from_table(
        _table(operating=(0, -200, -1000))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == LOSS_RESOLVED_PHRASE
    assert operating.direction == Direction.UP


def test_적자여도_영업이익률_증감폭은_퍼센트포인트로_낸다() -> None:
    """%p는 «차이»라서 부호가 바뀌어도 뜻이 남는다 — 목업이 그렇게 했다."""

    summary = period_summary_from_table(
        _table(revenue=(5940, 5800, 5665), operating=(-210, 400, 1694))
    )
    margin = _by_label(summary)[MARGIN_LABEL]

    assert margin.base_value == "29.9" and margin.latest_value == "-3.5"
    assert margin.change == "-33.4%p"
    assert margin.change_kind == ChangeKind.POINT


# ══════════════════════════════════════════════════════════
# ③ 값이 0
# ══════════════════════════════════════════════════════════


def test_비교_시작_값이_0이면_증감률을_만들지_않는다() -> None:
    summary = period_summary_from_table(
        _table(operating=(500, 100, 0))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == ZERO_BASE_PHRASE
    assert operating.change_kind == ChangeKind.PHRASE
    assert operating.note == ZERO_BASE_NOTE
    assert operating.direction == Direction.UP
    assert operating.base_value == "0"


def test_비교_시작_값이_0이고_끝도_0이면_방향이_없다() -> None:
    summary = period_summary_from_table(
        _table(operating=(0, 0, 0))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == ZERO_BASE_PHRASE
    assert operating.direction == Direction.FLAT


def test_끝_값이_0이면_증감률은_마이너스_100퍼센트다() -> None:
    summary = period_summary_from_table(
        _table(operating=(0, 800, 1694))
    )
    operating = _by_label(summary)["영업이익"]

    assert operating.change == "-100.0%"
    assert operating.change_kind == ChangeKind.PERCENT


def test_매출액이_0인_해가_있으면_영업이익률만_빠지고_나머지는_남는다() -> None:
    summary = period_summary_from_table(
        _table(revenue=(5940, 3000, 0), operating=(1552, 800, 100))
    )
    labels = [item.label for item in summary.items]

    assert MARGIN_LABEL not in labels
    assert "매출액" in labels and "영업이익" in labels


def test_증감률이_0이면_부호를_붙이지_않는다() -> None:
    summary = period_summary_from_table(
        _table(revenue=(5665, 5800, 5665))
    )
    revenue = _by_label(summary)["매출액"]

    assert revenue.change == "0.0%"
    assert revenue.direction == Direction.FLAT


# ══════════════════════════════════════════════════════════
# ④ 표가 없거나 모양이 어긋난 경우 — 띠를 통째로 그리지 않는다
# ══════════════════════════════════════════════════════════


def test_실적표가_없으면_빈_결과를_돌려준다() -> None:
    summary = period_summary(_report(None))

    assert summary is EMPTY_PERIOD_SUMMARY or not summary
    assert summary.items == ()
    assert summary.title == ""
    assert summary.cite == ""
    assert not summary


def test_실적표가_아닌_표는_재료로_쓰지_않는다() -> None:
    other = _manual_table(
        [["항목", "설명"], ["채널", "직영"]],
        headers=["비교 항목", "내용"],
        numeric=False,
        display_unit="",
    )

    assert not period_summary(_report(other))


def test_행이_하나뿐이면_비교할_수_없어_빈_결과다() -> None:
    table = _manual_table([["2025", "5,940", "1,552"]])

    assert not period_summary_from_table(table)


def test_사업연도가_최신에서_과거_순서가_아니면_빈_결과다() -> None:
    """뒤집힌 표를 그대로 쓰면 증감 방향이 조용히 반대가 된다."""

    table = _manual_table(
        [
            ["2023", "5,665", "1,694"],
            ["2024", "5,800", "1,600"],
            ["2025", "5,940", "1,552"],
        ]
    )

    assert not period_summary_from_table(table)


def test_값_모양이_어긋난_항목만_빠지고_나머지는_남는다() -> None:
    table = _manual_table(
        [
            ["2025", "5,940", "미상"],
            ["2024", "5,800", "1,600"],
            ["2023", "5,665", "1,694"],
        ]
    )
    summary = period_summary_from_table(table)
    compared = [
        item.label
        for item in summary.items
        if item.change_kind != ChangeKind.MISSING
    ]

    # 영업이익 값이 「미상」이라 영업이익도, 그것을 재료로 쓰는 영업이익률도 빠진다.
    assert compared == ["매출액"]


def test_모든_항목을_못_만들면_빈_결과다() -> None:
    table = _manual_table(
        [
            ["2025", "미상", "미상"],
            ["2023", "미상", "미상"],
        ]
    )

    assert not period_summary_from_table(table)


# ══════════════════════════════════════════════════════════
# ⑤ 세 해가 아닌 표
# ══════════════════════════════════════════════════════════


def test_두_해뿐인_표도_첫_행과_마지막_행으로_비교한다() -> None:
    table = _manual_table(
        [["2025", "5,940", "1,552"], ["2024", "5,665", "1,694"]]
    )
    revenue = _by_label(period_summary_from_table(table))["매출액"]

    assert revenue.base_period == "2024" and revenue.latest_period == "2025"
    assert revenue.change == "+4.9%"


def test_네_해가_있어도_첫_행과_마지막_행만_쓴다() -> None:
    table = _manual_table(
        [
            ["2025", "5,940", "1,552"],
            ["2024", "9,999", "9,999"],
            ["2023", "8,888", "8,888"],
            ["2022", "5,665", "1,694"],
        ]
    )
    revenue = _by_label(period_summary_from_table(table))["매출액"]

    assert revenue.base_period == "2022" and revenue.base_value == "5,665"
    assert revenue.change == "+4.9%"


# ══════════════════════════════════════════════════════════
# ⑥ 단위
# ══════════════════════════════════════════════════════════


def test_금액_단위는_표의_공개_단위를_그대로_따른다() -> None:
    table = _manual_table(
        [
            ["2025", "100,000", "12,000"],
            ["2023", "95,000", "9,500"],
        ],
        display_unit="백만원",
    )
    items = _by_label(period_summary_from_table(table))

    assert items["매출액"].unit == "백만원"
    assert items["영업이익"].unit == "백만원"
    assert items["매출액"].change == "+5.3%"


def test_영업이익률_단위는_금액_단위와_무관하게_퍼센트다() -> None:
    table = _manual_table(
        [
            ["2025", "100,000", "12,000"],
            ["2023", "95,000", "9,500"],
        ],
        display_unit="백만원",
    )
    margin = _by_label(period_summary_from_table(table))[MARGIN_LABEL]

    assert margin.unit == MARGIN_UNIT == "%"
    assert margin.base_value == "10.0" and margin.latest_value == "12.0"
    assert margin.change == "+2.0%p"


def test_공개_단위가_없는_표는_재료로_쓰지_않는다() -> None:
    table = _manual_table(
        [["2025", "5,940", "1,552"], ["2023", "5,665", "1,694"]],
        display_unit="",
    )

    assert not period_summary_from_table(table)


def test_소수_자리가_있는_표시값도_그대로_받는다() -> None:
    table = _manual_table(
        [["2025", "5,940.5", "1,552.5"], ["2023", "5,665.0", "1,694.0"]]
    )
    revenue = _by_label(period_summary_from_table(table))["매출액"]

    assert revenue.base_value == "5,665.0"
    assert revenue.change == "+4.9%"


# ══════════════════════════════════════════════════════════
# ⑦ 열이 없는 지표 — 빈 자리가 아니라 「없다」고 말한다
# ══════════════════════════════════════════════════════════


def test_당기순이익_열이_없으면_미수집_항목으로_알린다() -> None:
    summary = period_summary_from_table(_table())
    net = _by_label(summary)["당기순이익"]

    assert net.change == MISSING_CHANGE_TEXT
    assert net.change_kind == ChangeKind.MISSING
    assert net.direction == Direction.UNKNOWN
    assert net.note == MISSING_METRIC_NOTE
    # 빈 «값»을 넣지 않는다 — 값이 없다는 사실 자체가 내용이다.
    assert net.base_value == "" and net.latest_value == ""
    assert net.base_period == "" and net.latest_period == ""
    assert net.unit == ""


def test_당기순이익_열이_있으면_미수집이_아니라_증감률을_낸다() -> None:
    summary = period_summary_from_table(_table(net=(3000, 2800, 2000)))
    net = _by_label(summary)["당기순이익"]

    assert net.change_kind == ChangeKind.PERCENT
    assert net.change == "+50.0%"
    assert net.note == ""


def test_미수집_항목은_닫힌_목록_안에서만_만든다() -> None:
    """모르는 지표를 「없다」고 늘어놓으면 띠가 안내판이 아니라 잡음이 된다."""

    summary = period_summary_from_table(_table())
    missing = [
        item.label
        for item in summary.items
        if item.change_kind == ChangeKind.MISSING
    ]

    assert missing == list(OPTIONAL_METRIC_LABELS)


def test_만들_수_있는_항목이_하나도_없으면_미수집_항목도_내지_않는다() -> None:
    """전부 「없음」인 띠는 없는 것보다 나쁘다."""

    table = _manual_table(
        [
            ["2025", "미상", "미상"],
            ["2023", "미상", "미상"],
        ]
    )
    summary = period_summary_from_table(table)

    assert summary.items == ()
    assert not summary


# ══════════════════════════════════════════════════════════
# ⑧ 표지 띠와 같은 규칙을 쓴다 (두 파일이 갈라지지 않게)
# ══════════════════════════════════════════════════════════


def test_표지_띠와_같은_필수_열_이름을_쓴다() -> None:
    assert AMOUNT_METRIC_LABELS == COVER_METRIC_LABELS


def test_표지_띠와_같은_표를_고른다() -> None:
    report = _report(_table())

    assert cover_metrics(report).cite == period_summary(report).cite
    assert bool(cover_metrics(report)) == bool(period_summary(report))


def test_실적표가_없으면_표지_띠도_요약_띠도_함께_비어_있다() -> None:
    report = _report(None)

    assert not cover_metrics(report)
    assert not period_summary(report)


# ══════════════════════════════════════════════════════════
# ⑨ 옛 저장본·손상된 payload에도 터지지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("broken", [None, object(), "보고서", 3])
def test_보고서가_아닌_값을_받아도_빈_결과를_돌려준다(broken: Any) -> None:
    assert not period_summary(broken)


def test_표가_아닌_값을_받아도_빈_결과를_돌려준다() -> None:
    assert not period_summary_from_table(None)
    assert not period_summary_from_table(object())
