"""검증된 ``ReportTable``을 웹·PDF가 함께 쓰는 안전한 시각화 자료로 바꾼다.

표 행이 사실의 정본이다. 이 모듈은 숫자를 새로 만들거나 추정하지 않고 표시 모양만
고른다. 데이터가 완전하지 않으면 ``None``을 돌려 렌더러가 원래 표를 보여 주게 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

#: 구성 도식(100% 누적 막대)에 그릴 수 있는 분류 개수.
#:
#: ★ 상한을 5에서 7로 올린 이유 (하이브 실측) — 하이브 매출은 6개 부문이고
#:   비중 합계가 «정확히» 100.00%인데도 도식이 안 그려지고 평범한 표로 나갔다.
#:   막힌 것은 자료의 문제가 아니라 이 숫자 하나였다. 색 계단도 5단계뿐이라
#:   6번째 칸이 첫 칸과 같은 색이 되고 PDF는 색이 아예 모자랐다 — 그래서
#:   상한과 색을 «함께» 올렸다. 한쪽만 올리면 도식이 깨진다.
#: ★ 하한 3은 그대로다. 두 조각짜리 「구성」은 막대로 그릴 값이 없다.
COMPOSITION_MIN_ITEMS: Final[int] = 3
COMPOSITION_MAX_ITEMS: Final[int] = 7

#: 무채색 계단의 단계 수. 웹(style.css .tone-N)과 PDF(COMPOSITION_PALETTE)가
#: 이 수만큼 색을 갖고 있어야 한다 — 시험이 세 곳의 일치를 지킨다.
COMPOSITION_TONE_STEPS: Final[int] = 7


def composition_tone(index: int, count: int) -> int:
    """칸 번호에 색 단계 번호를 준다 — 마지막 칸은 «항상» 가장 옅은 단계다.

    ★ 왜 「앞에서부터 차례로」가 아닌가 — 항목이 3개든 7개든
      「가장 진한 것에서 시작해 흰색으로 끝난다」는 인상을 지키기 위해서다.
      앞에서부터 자르면 항목이 적을 때 흰색이 안 나와 회사마다 도식이
      달라 보인다. 사용자가 「회사가 달라도 같은 장은 비슷한 도식」을
      요구한 이유가 이것이다.

    ★ 웹 템플릿과 PDF가 «같은 이 함수»를 쓴다. 두 벌로 만들면 화면과
      인쇄물의 색이 어긋난다.
    """
    last = max(count - 1, 0)
    if last <= 0:
        return 0
    if index >= last:
        return COMPOSITION_TONE_STEPS - 1
    step = (COMPOSITION_TONE_STEPS - 1) / last
    return min(int(round(index * step)), COMPOSITION_TONE_STEPS - 2)

from src.features.pipeline.port import ReportTable


_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_TOTAL_LABELS = ("합계", "총계", "전체")


@dataclass(frozen=True)
class ChartPoint:
    label: str
    value: float
    display: str
    ratio: float
    #: 이 값을 0선 «아래로» 그려야 하는가.
    #:
    #: ★ 왜 계열이 아니라 «점»마다 두나 (하이브 실측) — 예전에는 계열 전체가
    #:   음수일 때만 아래로 그렸고, 한 계열에 양수·음수가 «섞이면» 도식을
    #:   아예 안 그렸다. 그런데 하이브 당기순이익은 +1,834 → -34 → -2,544로
    #:   «흑자에서 적자로 돌아선» 경우였다. 그건 숨길 사실이 아니라 독자가
    #:   가장 봐야 할 사실이다. 점마다 방향을 두면 그대로 그릴 수 있다.
    below: bool = False


@dataclass(frozen=True)
class ChartSeries:
    label: str
    points: tuple[ChartPoint, ...]
    risk: bool = False


@dataclass(frozen=True)
class TableVisualization:
    kind: str
    caption: str
    unit: str = ""
    note: str = ""
    items: tuple[ChartPoint, ...] = ()
    series: tuple[ChartSeries, ...] = ()
    flows: tuple[tuple[str, ...], ...] = ()


def _number(value: object) -> Decimal | None:
    text = str(value).strip().replace(",", "").replace(" ", "")
    negative_parentheses = text.startswith("(") and text.endswith(")")
    if negative_parentheses:
        text = text[1:-1]
    for suffix in ("%", "억원", "원", "백만원"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    if not text or _NUMBER_RE.fullmatch(text) is None:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative_parentheses else number


def _unit(table: ReportTable) -> str:
    if table.display_unit.strip():
        return table.display_unit.strip()
    caption = table.caption
    match = re.search(r"단위\s*:\s*([^),]+)", caption)
    return match.group(1).strip() if match else ""


def _decimal_place_label(places: int) -> str:
    if places <= 0:
        return "정수"
    labels = {1: "소수 첫째 자리", 2: "소수 둘째 자리", 3: "소수 셋째 자리"}
    return labels.get(places, f"소수 {places}자리")


def _composition(table: ReportTable) -> TableVisualization | None:
    # 그래프가 원래 표의 공개 사실을 숨기면 안 된다. 현재 구성형은 정확히
    # ``항목 + 비중`` 두 열을 모두 표시할 수 있을 때만 쓴다.
    if len(table.headers) != 2 or len(table.rows) < 3:
        return None
    percent_columns = [
        index
        for index, header in enumerate(table.headers)
        if index > 0 and ("비중" in header or "%" in header)
    ]
    if not percent_columns:
        return None
    value_index = percent_columns[-1]
    values: list[tuple[str, Decimal, str]] = []
    for row in table.rows:
        if len(row) <= value_index:
            return None
        label = str(row[0]).strip()
        if not label:
            return None
        # 합계도 공개 행이면 그래프에서 조용히 숨기지 않는다. 중복 행을 제거할 권한은
        # 렌더러에 없으므로 원표로 안전하게 돌아간다.
        if any(token in label for token in _TOTAL_LABELS):
            return None
        number = _number(row[value_index])
        if number is None or number < 0 or number > 100:
            return None
        values.append((label, number, str(row[value_index]).strip()))
    total = sum((value for _label, value, _display in values), Decimal("0"))
    # 구성 그래프는 전체 분류가 공시 합계와 맞을 때만 허용한다. 소수 반올림 오차만 받는다.
    if (
        not COMPOSITION_MIN_ITEMS <= len(values) <= COMPOSITION_MAX_ITEMS
        or not Decimal("98.5") <= total <= Decimal("101.5")
    ):
        return None
    items = tuple(
        ChartPoint(
            label=label,
            value=float(value),
            display=(display if "%" in display else f"{display}%"),
            ratio=float((value / total) * 100) if total else 0.0,
        )
        for label, value, display in values
    )
    return TableVisualization(
        kind="composition",
        caption=table.caption,
        unit="%",
        note=(
            f"원문 비율을 {_decimal_place_label(table.scale_places)}로 반올림해 표시"
            if table.raw_rows
            else "공개 표의 비율을 계산 없이 표시"
        ),
        items=items,
    )


def _trend(table: ReportTable) -> TableVisualization | None:
    if not (3 <= len(table.rows) <= 6) or not (2 <= len(table.headers) <= 4):
        return None
    ordered_rows = list(table.rows)
    # 표는 최신 연도 우선으로 보존될 수 있지만 추이 그래프의 시간축은 과거에서
    # 현재로 흘러야 한다. 첫 열이 모두 완료 사업연도일 때만 표시 순서를 바꾼다.
    if all(
        row and re.fullmatch(r"20\d{2}", str(row[0]).strip())
        for row in ordered_rows
    ):
        ordered_rows.sort(key=lambda row: int(str(row[0]).strip()))
    labels = [str(row[0]).strip() for row in ordered_rows if row]
    if len(labels) != len(ordered_rows) or any(not label for label in labels):
        return None
    series: list[ChartSeries] = []
    for column in range(1, len(table.headers)):
        parsed: list[tuple[str, Decimal, str]] = []
        for row in ordered_rows:
            if len(row) != len(table.headers):
                return None
            value = _number(row[column])
            if value is None:
                return None
            parsed.append((str(row[0]).strip(), value, str(row[column]).strip()))
        maximum = max((abs(value) for _label, value, _display in parsed), default=Decimal("0"))
        if maximum == 0:
            return None
        # ★ 부호가 섞여도 그린다 — 점마다 0선 위/아래로 나눠 그리기 때문이다.
        #   예전에는 여기서 도식을 통째로 포기했는데, 그 조건에 걸리는 것이
        #   하필 «흑자→적자 전환»처럼 가장 중요한 경우였다(하이브 실측).
        has_negative = any(value < 0 for _label, value, _display in parsed)
        header = str(table.headers[column]).strip()
        # 계열 «전체»가 손실일 때만 계열을 위험으로 본다. 섞인 경우는 점마다
        # 방향으로 나타내므로 계열 표시를 바꾸지 않는다 — 흑자 해까지 빨갛게
        # 칠하면 사실보다 나쁘게 읽힌다.
        all_non_positive = all(value <= 0 for _label, value, _display in parsed)
        risk = (all_non_positive and has_negative) or "손실" in header
        points = tuple(
            ChartPoint(
                label=label,
                value=float(value),
                display=display,
                ratio=float((abs(value) / maximum) * 100),
                below=value < 0,
            )
            for label, value, display in parsed
        )
        series.append(ChartSeries(label=header, points=points, risk=risk))
    return TableVisualization(
        kind="trend",
        caption=table.caption,
        unit=_unit(table),
        note=(
            f"원값을 {_unit(table)} 단위로 환산해 표시"
            if table.scale_divisor not in {"", "1"} and _unit(table)
            else "공개 표의 값을 계산 없이 표시"
        ),
        series=tuple(series),
    )


def _flow(table: ReportTable) -> TableVisualization | None:
    # ★ 열 하한이 2다 — 5장 «과제 → 대응»은 두 칸짜리 흐름이다.
    #   렌더러(웹 .flow-row / PDF _FlowGraphic)는 열 수에 무관하게 그린다.
    if not (2 <= len(table.headers) <= 4) or not (1 <= len(table.rows) <= 5):
        return None
    flows: list[tuple[str, ...]] = []
    for row in table.rows:
        if len(row) != len(table.headers):
            return None
        values = tuple(str(value).strip() for value in row)
        if any(not value for value in values):
            return None
        flows.append(values)
    return TableVisualization(
        kind="flow",
        caption=table.caption,
        flows=tuple(flows),
    )


def table_visualization(table: ReportTable) -> TableVisualization | None:
    """명시된 표현과 실제 행이 일치할 때만 시각화 자료를 돌려준다."""

    # 저장 hash와 화면 의미가 일대일이 되도록 대소문자·공백을 묵시 정규화하지 않는다.
    presentation = table.presentation
    if presentation == "composition":
        return _composition(table)
    if presentation == "trend":
        return _trend(table)
    if presentation == "flow":
        return _flow(table)
    return None


__all__ = [
    "ChartPoint",
    "ChartSeries",
    "TableVisualization",
    "table_visualization",
]
