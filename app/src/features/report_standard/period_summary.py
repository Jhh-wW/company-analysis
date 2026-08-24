"""4장 «3개년 요약 숫자» 띠 — 4장 실적표 «안에서만» 증감을 만든다.

★ 이 모듈은 계산을 «한다». 대신 재료를 한 곳으로 못 박는다 — 4장 실적표
  (``company_performance.logic.build_three_year_table``가 전자공시 원수치로
  fail-closed 하게 만든 표)의 «첫 행(최신)»과 «마지막 행(가장 과거)»뿐이다.
  v2 Report에는 숫자를 재검산할 사실 장부(fact_records)가 없다
  (``composer/render.py`` 실측). 그래서 검산은 «독자의 눈»이 한다 — 화면에
  이미 인쇄된 표의 두 행만 쓰면, 독자가 표를 보고 그대로 다시 셈할 수 있다.
  표 밖에서 숫자를 끌어오면 그 순간 아무도 검산할 수 없는 주장이 된다.

★ 그래서 이 모듈이 지키는 세 가지:
  1. 재료는 «그 표» 하나. 다른 장·다른 소스의 숫자를 섞지 않는다.
  2. 인쇄된 표시값으로만 셈한다. 원값(``raw_rows``)으로 셈하면 화면의
     숫자끼리 아귀가 안 맞아(반올림 차이) 독자가 재검산에 실패한다.
  3. 계산 근거(어느 해 어느 값)를 결과에 «함께» 담는다. 화면이
     「2023년 5,665 → 2025년 5,940」처럼 보여줄 수 있어야 한다.

★ 부호가 바뀌면 퍼센트는 뜻을 잃는다. 적자가 -1,000 → -1,500으로 «커진» 것을
  「+50%」로 쓰면 좋아진 것처럼 읽힌다. 그래서 비교 시작값이 0 이하이거나
  부호가 바뀌는 경우에는 비율 대신 «말»로 낸다(``_amount_change`` 판정표).

★ 항목 하나를 못 만들면 그 항목만 뺀다. 전부 못 만들면 빈 결과를 돌려
  화면·PDF가 띠를 통째로 안 그리게 한다 — 빈 자리나 ``—``를 남기지 않는 것이
  이 보고서의 계약이다(``cover_metrics.py``와 같은 규칙).

★ 웹 틀과 PDF가 «같은 이 함수»를 쓴다. 두 곳에서 따로 셈하면 화면과 인쇄물의
  숫자가 조용히 갈라진다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Final, Optional


#: 실적표를 알아보는 첫 열 이름. ``build_three_year_table``의 ``headers[0]``이다.
#: ★ ``cover_metrics.PERIOD_HEADER``와 «같은 값»이어야 한다. 두 띠가 서로 다른
#:   표를 고르면 표지와 4장의 숫자가 갈라진다. 시험이 두 값을 묶어 지킨다.
PERIOD_HEADER: Final[str] = "사업연도"

#: 표에 «반드시» 있는 금액 지표. ``company_performance/logic.py``의
#: ``_REQUIRED_METRICS``와 ``cover_metrics.COVER_METRIC_LABELS``를 그대로 옮긴 것이다.
#: ★ 라벨을 새로 짓지 않고 표의 «한국어 열 이름»을 쓴다 — 독자가 띠의 이름을
#:   표의 열에서 바로 찾을 수 있어야 재검산이 된다(``cover_metrics.py``와 같은 이유).
AMOUNT_METRIC_LABELS: Final[tuple[str, ...]] = ("매출액", "영업이익")

#: 회사에 따라 표에 «있을 수도, 없을 수도» 있는 금액 지표.
#: ``company_performance/logic.py``의 ``_ACCOUNT_IDS`` 중 필수가 아닌 것들이다.
#: ★ 목록이 «미리 닫혀» 있기 때문에 「열이 없다」는 사실을 띠에 적을 수 있다.
#:   닫힌 목록이 아니면 「없는 것」을 끝없이 나열하게 되어 안내가 아니라 잡음이 된다.
OPTIONAL_METRIC_LABELS: Final[tuple[str, ...]] = ("당기순이익",)

#: 표의 두 칸(영업이익 ÷ 매출액)으로 만드는 유일한 파생 지표.
#: ★ 여기만 표에 없는 이름을 쓴다. 허용하는 이유 — 두 값이 «같은 표의 같은 행»에
#:   인쇄돼 있어 독자가 나눗셈을 눈으로 확인할 수 있고, 부호가 바뀌어도 %p(차이)는
#:   뜻이 남아 적자 회사에서도 정직하게 읽히기 때문이다(목업이 그렇게 했다).
MARGIN_LABEL: Final[str] = "영업이익률"
MARGIN_NUMERATOR_LABEL: Final[str] = "영업이익"
MARGIN_DENOMINATOR_LABEL: Final[str] = "매출액"
MARGIN_UNIT: Final[str] = "%"

#: 띠 제목의 꼬리말. 표 캡션의 뜻을 벗어나는 새 주장을 담지 않는다.
PERIOD_SUMMARY_TITLE_SUFFIX: Final[str] = "변화 요약"
#: 두 사업연도를 잇는 글자. 제목에 «몇 개년»이라고 숫자를 박지 않는 이유 —
#: 표의 행 수가 셋이 아닐 수도 있고, 그때 제목만 거짓말이 되면 안 된다.
PERIOD_RANGE_JOINER: Final[str] = "~"

#: 표는 최신 → 과거 순서다(``company_performance.logic._periods``가 강제한다).
LATEST_ROW_INDEX: Final[int] = 0
BASE_ROW_INDEX: Final[int] = -1
#: 첫 행과 마지막 행이 «다른 해»여야 비교가 성립한다.
MINIMUM_ROW_COUNT: Final[int] = 2

#: 증감률·증감폭의 소수 자릿수(첫째 자리). 목업과 같은 자릿수다.
PERCENT_QUANTUM: Final[Decimal] = Decimal("0.1")
PERCENT_SUFFIX: Final[str] = "%"
POINT_SUFFIX: Final[str] = "%p"
_HUNDRED: Final[Decimal] = Decimal(100)

#: 부호가 바뀌거나 비교 시작값이 0 이하일 때 쓰는 «말». 비율을 쓰면 뜻이 뒤집힌다.
LOSS_TURN_PHRASE: Final[str] = "적자 전환"
LOSS_WIDENED_PHRASE: Final[str] = "적자 확대"
LOSS_NARROWED_PHRASE: Final[str] = "적자 축소"
LOSS_CONTINUED_PHRASE: Final[str] = "적자 지속"
LOSS_RESOLVED_PHRASE: Final[str] = "적자 해소"
PROFIT_TURN_PHRASE: Final[str] = "흑자 전환"
ZERO_BASE_PHRASE: Final[str] = "증감률 산출 불가"
ZERO_BASE_NOTE: Final[str] = (
    "비교 시작 사업연도 값이 0이라 증감률을 낼 수 없습니다. 두 해의 값만 그대로 봅니다."
)

#: 표에 열 자체가 없는 지표에 붙이는 «없다는 안내».
#: ★ 빈 칸을 남기는 것과 「없음을 알리는 것」은 다르다. 빈 칸은 독자가 우리 실수로
#:   읽지만, 이 문구는 «무엇이 왜 빠졌는지»를 말한다.
#: ★ 문구를 「공시에 없다」가 아니라 「우리 표에 없다」로 쓴 이유 — 이 모듈은 표만
#:   보므로 공시 원문 사정을 알 수 없다. 화면에 함께 인쇄되는 표를 보면 독자가
#:   이 문장을 그 자리에서 확인할 수 있다.
MISSING_CHANGE_TEXT: Final[str] = "미수집"
MISSING_METRIC_NOTE: Final[str] = (
    "3개년 실적표에 이 지표 열이 없어 세 해를 같은 기준으로 비교하지 못했습니다."
)


class ChangeKind:
    """``change`` 글자를 «어떻게 읽어야 하는지». 화면이 색·기호를 고를 때 쓴다."""

    #: 증감률 (예: ``+4.9%``)
    PERCENT: Final[str] = "percent"
    #: 증감폭, 퍼센트포인트 (예: ``-3.8%p``)
    POINT: Final[str] = "point"
    #: 비율 대신 쓰는 말 (예: ``적자 확대``)
    PHRASE: Final[str] = "phrase"
    #: 표에 열이 없어 비교하지 못함 (예: ``미수집``)
    MISSING: Final[str] = "missing"


class Direction:
    """값이 어느 쪽으로 움직였나. ★ 좋고 나쁨이 아니라 «수치의 방향»이다.

    적자가 커지면 값 자체는 내려간 것이므로 ``DOWN``, 적자가 줄면 ``UP``이다.
    좋고 나쁨의 해석은 이 모듈이 하지 않는다(회사·지표마다 다르다).
    """

    UP: Final[str] = "up"
    DOWN: Final[str] = "down"
    FLAT: Final[str] = "flat"
    #: 비교 자체를 못 한 경우
    UNKNOWN: Final[str] = "unknown"


#: 공개 표시값의 모양. 천 단위 콤마와 소수 자릿수(표의 ``scale_places``)를 허용한다.
#: ★ ``cover_metrics._DISPLAY_NUMBER``와 같은 모양이다. 옛 저장본이나 손상된
#:   payload의 이상한 글자로 셈하지 않으려는 «모양 검사»다.
_DISPLAY_NUMBER: Final[re.Pattern[str]] = re.compile(
    r"^[-+]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$"
)
#: 사업연도 칸의 모양. 최신 → 과거 순서를 «연도 숫자»로 확인하는 데 쓴다.
_FISCAL_YEAR: Final[re.Pattern[str]] = re.compile(r"^(?:19|20)\d{2}$")


@dataclass(frozen=True)
class PeriodSummaryItem:
    """요약 띠의 칸 하나 — 값 두 개와 그 둘을 비교한 결과, 그리고 근거."""

    #: 표의 열 이름 그대로(예: ``매출액``). 파생 지표만 ``MARGIN_LABEL``을 쓴다.
    label: str
    #: 비교 시작 사업연도와 그때 값 (표 «마지막 행»). 없으면 빈 글자.
    base_period: str
    base_value: str
    #: 비교 끝 사업연도와 그때 값 (표 «첫 행»). 없으면 빈 글자.
    latest_period: str
    latest_value: str
    #: 두 값의 단위. 금액은 표의 공개 단위, 영업이익률은 ``%``.
    unit: str
    #: 비교 결과 글자 (예: ``+4.9%``·``-3.8%p``·``적자 확대``·``미수집``)
    change: str
    #: ``change``를 읽는 법 — ``ChangeKind``
    change_kind: str
    #: 값이 움직인 방향 — ``Direction``
    direction: str
    #: 독자에게 덧붙일 한 줄. 없으면 빈 글자.
    note: str = ""

    @property
    def basis_text(self) -> str:
        """계산 근거를 한 줄로 — 예: ``2023년 5,665 → 2025년 5,940``.

        화면·PDF가 각자 문장을 지어내면 두 채널이 갈라지므로 여기서 한 번만 만든다.
        비교할 값이 없으면 빈 글자를 돌려주고, 화면은 그 줄을 그리지 않는다.
        """

        if not (self.base_value and self.latest_value):
            return ""
        return (
            f"{self.base_period}년 {self.base_value}"
            f" → {self.latest_period}년 {self.latest_value}"
        )


@dataclass(frozen=True)
class PeriodSummary:
    """요약 띠 하나. 만들 수 있는 칸이 없으면 ``items``가 비고 거짓으로 평가된다."""

    #: 띠 제목 (예: ``2023~2025 사업연도 변화 요약``)
    title: str = ""
    items: tuple[PeriodSummaryItem, ...] = ()
    #: 4장 실적표와 «같은» 출처 표기. 띠가 새 출처를 만들지 않는다.
    cite: str = ""

    def __bool__(self) -> bool:
        """칸이 하나도 없으면 띠를 그리지 않는다 (빈 자리 금지)."""

        return bool(self.items)


#: 만들지 못한 경우의 유일한 반환값. ``None`` 대신 총함수로 두어 화면·PDF가
#: 같은 모양으로 「없음」을 다룬다(``EMPTY_COVER_METRICS``와 같은 방식).
EMPTY_PERIOD_SUMMARY: Final[PeriodSummary] = PeriodSummary()


def _cells(row: Any) -> list[str]:
    return [str(cell).strip() for cell in (row or ())]


def _is_performance_table(table: Any) -> bool:
    """4장 실적표인지 «표 자신의 모양»으로만 판정한다.

    ★ ``cover_metrics._is_performance_table``과 «같은 판정»이다. 장 ID로 찾지
      않는 이유 — v1(canonical)과 v2(composer)가 표를 넣는 경로가 서로 다르고,
      장 ID가 바뀌면 띠가 조용히 사라진다. 첫 열이 ``사업연도``이고 필수 두 지표
      열을 모두 가진 숫자표에 공개 단위까지 있는 표는 이 제품에서
      ``build_three_year_table``의 결과뿐이다.
    """

    headers = _cells(getattr(table, "headers", None))
    rows = [_cells(row) for row in (getattr(table, "rows", None) or ())]
    if not headers or not rows:
        return False
    if headers[0] != PERIOD_HEADER:
        return False
    if not set(AMOUNT_METRIC_LABELS).issubset(headers):
        return False
    if not bool(getattr(table, "numeric", False)):
        return False
    if not str(getattr(table, "display_unit", "") or "").strip():
        return False
    return all(len(row) == len(headers) for row in rows)


def _performance_table(report: Any) -> Optional[Any]:
    """보고서 안에서 완료 사업연도 실적표를 찾는다. 없으면 ``None``."""

    for section in getattr(report, "sections", None) or ():
        for table in getattr(section, "tables", None) or ():
            if _is_performance_table(table):
                return table
    return None


def _number(text: str) -> Optional[Decimal]:
    """표에 인쇄된 글자를 숫자로 읽는다. 모양이 어긋나면 ``None``."""

    if not _DISPLAY_NUMBER.fullmatch(text):
        return None
    return Decimal(text.replace(",", ""))


def _rounded(value: Decimal) -> Decimal:
    """소수 첫째 자리로 맞춘다. ``-0.0``은 ``0``으로 정규화한다."""

    shown = value.quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    return Decimal(0) if shown == 0 else shown


def _signed_text(value: Decimal, suffix: str) -> str:
    """부호를 앞에 붙인 표시 글자. 0에는 부호를 붙이지 않는다."""

    shown = _rounded(value)
    sign_format = "+.1f" if shown != 0 else ".1f"
    return f"{shown:{sign_format}}{suffix}"


def _direction(base: Decimal, latest: Decimal) -> str:
    """수치가 움직인 방향 — 적자·흑자 구분 없이 «값의 크기»로만 정한다."""

    if latest > base:
        return Direction.UP
    if latest < base:
        return Direction.DOWN
    return Direction.FLAT


def _amount_change(base: Decimal, latest: Decimal) -> tuple[str, str]:
    """금액 두 개를 비교해 ``(표시 글자, ChangeKind)``를 만든다.

    ★ 판정표 — 위에서부터 먼저 걸리는 것을 쓴다. 이 순서가 곧 규칙이다.

    ===============================  ==========================================
    조건                              결과
    ===============================  ==========================================
    끝 < 0 이고 시작 < 0              적자 확대 / 축소 / 지속 (크기 비교)
    끝 < 0 이고 시작 >= 0             적자 전환
    시작 < 0 이고 끝 == 0             적자 해소
    시작 < 0 이고 끝 > 0              흑자 전환
    시작 == 0                         증감률 산출 불가 (0으로 나눌 수 없다)
    그 밖 (시작 > 0, 끝 >= 0)         증감률 = (끝 - 시작) ÷ 시작 × 100
    ===============================  ==========================================

    ★ 왜 부호가 바뀌면 퍼센트를 안 쓰나 — 적자 -1,000이 -1,500으로 «커진» 것을
      비율로 쓰면 ``+50%``가 되어 좋아진 것처럼 읽힌다. 비율은 시작값이 양수일
      때만 뜻이 통한다.
    """

    if latest < 0 and base < 0:
        if latest < base:
            return LOSS_WIDENED_PHRASE, ChangeKind.PHRASE
        if latest > base:
            return LOSS_NARROWED_PHRASE, ChangeKind.PHRASE
        return LOSS_CONTINUED_PHRASE, ChangeKind.PHRASE
    if latest < 0:
        return LOSS_TURN_PHRASE, ChangeKind.PHRASE
    if base < 0:
        phrase = LOSS_RESOLVED_PHRASE if latest == 0 else PROFIT_TURN_PHRASE
        return phrase, ChangeKind.PHRASE
    if base == 0:
        return ZERO_BASE_PHRASE, ChangeKind.PHRASE
    ratio = (latest - base) / base * _HUNDRED
    return _signed_text(ratio, PERCENT_SUFFIX), ChangeKind.PERCENT


def _amount_item(
    label: str,
    *,
    periods: tuple[str, str],
    values: tuple[str, str],
    unit: str,
) -> Optional[PeriodSummaryItem]:
    """금액 한 지표의 칸을 만든다. 값 모양이 어긋나면 ``None``(그 칸만 뺀다)."""

    base_period, latest_period = periods
    base_text, latest_text = values
    base = _number(base_text)
    latest = _number(latest_text)
    if base is None or latest is None:
        return None

    change, kind = _amount_change(base, latest)
    return PeriodSummaryItem(
        label=label,
        base_period=base_period,
        base_value=base_text,
        latest_period=latest_period,
        latest_value=latest_text,
        unit=unit,
        change=change,
        change_kind=kind,
        direction=_direction(base, latest),
        note=ZERO_BASE_NOTE if change == ZERO_BASE_PHRASE else "",
    )


def _margin(income_text: str, revenue_text: str) -> Optional[Decimal]:
    """한 해의 영업이익률 = 영업이익 ÷ 매출액 × 100. 매출액이 0 이하면 ``None``.

    ★ 표에 인쇄된 값으로 나눈다. 원값으로 나누면 화면의 두 숫자로는 이 비율이
      안 나와 독자가 재검산에 실패한다.
    ★ 매출액이 0 이하면 나눗셈이 성립하지 않거나 뜻이 뒤집히므로 칸을 만들지 않는다.
    """

    income = _number(income_text)
    revenue = _number(revenue_text)
    if income is None or revenue is None or revenue <= 0:
        return None
    return income / revenue * _HUNDRED


def _margin_item(
    *,
    periods: tuple[str, str],
    base_row: dict[str, str],
    latest_row: dict[str, str],
) -> Optional[PeriodSummaryItem]:
    """영업이익률 칸을 만든다. 두 해 중 하나라도 못 만들면 ``None``."""

    base = _margin(
        base_row.get(MARGIN_NUMERATOR_LABEL, ""),
        base_row.get(MARGIN_DENOMINATOR_LABEL, ""),
    )
    latest = _margin(
        latest_row.get(MARGIN_NUMERATOR_LABEL, ""),
        latest_row.get(MARGIN_DENOMINATOR_LABEL, ""),
    )
    if base is None or latest is None:
        return None

    # ★ 화면에 찍는 것은 «반올림한» 두 비율이다. 증감폭도 그 두 값으로 빼야
    #   「29.9 → 26.1, -3.8%p」처럼 독자가 눈으로 더하고 뺄 수 있다.
    #   반올림 전 값으로 빼면 0.1만큼 어긋나 숫자끼리 아귀가 안 맞는다.
    base_shown = _rounded(base)
    latest_shown = _rounded(latest)
    base_period, latest_period = periods
    return PeriodSummaryItem(
        label=MARGIN_LABEL,
        base_period=base_period,
        base_value=f"{base_shown:.1f}",
        latest_period=latest_period,
        latest_value=f"{latest_shown:.1f}",
        unit=MARGIN_UNIT,
        change=_signed_text(latest_shown - base_shown, POINT_SUFFIX),
        change_kind=ChangeKind.POINT,
        direction=_direction(base_shown, latest_shown),
    )


def _missing_item(label: str) -> PeriodSummaryItem:
    """표에 열이 없는 지표를 «없다고 말하는» 칸으로 만든다.

    ★ 값 자리를 빈 글자로 둔다 — ``—``나 ``0`` 같은 가짜 값을 채우면 독자가
      그것을 실제 값으로 읽는다. 여기서는 「값이 없다」가 곧 내용이다.
    """

    return PeriodSummaryItem(
        label=label,
        base_period="",
        base_value="",
        latest_period="",
        latest_value="",
        unit="",
        change=MISSING_CHANGE_TEXT,
        change_kind=ChangeKind.MISSING,
        direction=Direction.UNKNOWN,
        note=MISSING_METRIC_NOTE,
    )


def _row_map(headers: list[str], row: list[str]) -> dict[str, str]:
    return dict(zip(headers, row))


def period_summary_from_table(table: Any) -> PeriodSummary:
    """4장 실적표 하나로 요약 띠를 만든다 — 만들 수 없으면 빈 결과.

    Args:
        table: 4장 실적표(``pipeline.port.ReportTable``). 옛 저장본도 안전하게
            읽으려고 속성 접근만 쓴다.

    Returns:
        표의 첫 행(최신)과 마지막 행(가장 과거)만으로 만든 ``PeriodSummary``.
        칸을 하나도 못 만들면 ``EMPTY_PERIOD_SUMMARY``.
    """

    if not _is_performance_table(table):
        return EMPTY_PERIOD_SUMMARY

    headers = _cells(getattr(table, "headers", None))
    rows = [_cells(row) for row in (getattr(table, "rows", None) or ())]
    if len(rows) < MINIMUM_ROW_COUNT:
        return EMPTY_PERIOD_SUMMARY

    latest_row = rows[LATEST_ROW_INDEX]
    base_row = rows[BASE_ROW_INDEX]
    latest_period = latest_row[0]
    base_period = base_row[0]
    # ★ 최신 → 과거 순서를 «연도 숫자»로 확인한다. 뒤집힌 표를 그대로 쓰면
    #   증감 방향이 조용히 반대가 되고, 화면에는 아무 경고도 안 뜬다.
    if not (
        _FISCAL_YEAR.fullmatch(latest_period)
        and _FISCAL_YEAR.fullmatch(base_period)
        and int(base_period) < int(latest_period)
    ):
        return EMPTY_PERIOD_SUMMARY

    unit = str(getattr(table, "display_unit", "") or "").strip()
    periods = (base_period, latest_period)
    latest_cells = _row_map(headers, latest_row)
    base_cells = _row_map(headers, base_row)

    items: list[PeriodSummaryItem] = []
    # ① 표에 반드시 있는 금액 지표 → ② 파생 지표 → ③ 있을 수도 없을 수도 있는 지표.
    #    목업의 칸 순서(매출액·영업손익·영업이익률·당기순이익)와 같다.
    for label in AMOUNT_METRIC_LABELS:
        item = _amount_item(
            label,
            periods=periods,
            values=(base_cells.get(label, ""), latest_cells.get(label, "")),
            unit=unit,
        )
        if item is not None:
            items.append(item)

    margin = _margin_item(
        periods=periods, base_row=base_cells, latest_row=latest_cells
    )
    if margin is not None:
        items.append(margin)

    missing: list[PeriodSummaryItem] = []
    for label in OPTIONAL_METRIC_LABELS:
        if label in headers:
            item = _amount_item(
                label,
                periods=periods,
                values=(base_cells.get(label, ""), latest_cells.get(label, "")),
                unit=unit,
            )
            if item is not None:
                items.append(item)
            continue
        missing.append(_missing_item(label))

    # ★ 실제로 비교한 칸이 하나도 없으면 「없음」 칸도 내지 않는다.
    #   전부 「없음」인 띠는 안내가 아니라 잡음이고, 없는 것보다 나쁘다.
    if not items:
        return EMPTY_PERIOD_SUMMARY
    items.extend(missing)

    title = (
        f"{base_period}{PERIOD_RANGE_JOINER}{latest_period}"
        f" {PERIOD_HEADER} {PERIOD_SUMMARY_TITLE_SUFFIX}"
    )
    return PeriodSummary(
        title=title,
        items=tuple(items),
        cite=str(getattr(table, "cite", "") or "").strip(),
    )


def period_summary(report: Any) -> PeriodSummary:
    """보고서에서 4장 실적표를 찾아 요약 띠를 만든다 — 없으면 빈 결과.

    Args:
        report: 완성된 보고서(``pipeline.port.Report``). 옛 저장본도 안전하게
            읽으려고 속성 접근만 쓴다.

    Returns:
        ``period_summary_from_table``의 결과. 실적표가 없으면
        ``EMPTY_PERIOD_SUMMARY``.
    """

    table = _performance_table(report)
    if table is None:
        return EMPTY_PERIOD_SUMMARY
    return period_summary_from_table(table)
