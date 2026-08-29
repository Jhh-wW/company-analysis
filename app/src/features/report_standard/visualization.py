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

#: 흐름표(presentation="flow")인데 «카드」로 낼 칸 이름 집합.
#:
#: ★ 왜 필요한가 (목업 실측·2026-08-25) — composer.constants.FLOW_HEADERS_BY_SECTION는
#:   1·2·5·6·7·8장을 «전부 같은 그릇»(경로표)에 담는다. 그 그릇의 렌더가
#:   지금까지 화살표 하나뿐이었는데, 아래 세 장은 한 행 안의 칸들이 서로
#:   «이어지는 인과·순서»가 아니라 «한 대상(정체성/계획/가치)에 대한 서로
#:   다른 질문의 답»이다. 화살표는 "A가 B로 이어진다"는 뜻인데 이 칸들은
#:   이어지지 않는다 — 그래서 화살표 없는 카드(라벨:값)로 낸다.
#:     · IDENTITY_TABLE_HEADERS(1장)  — 자기정의·범위·해석은 각각 독립된 답
#:     · STRATEGY_TABLE_HEADERS(6장)  — 시점·계획·공시내용도 마찬가지
#:     · CULTURE_TABLE_HEADERS(8장)   — 가치·원칙·사례도 마찬가지
#:   반대로 2장(BUSINESS_FLOW·자산→제품→고객행동→반복수익)과 7장
#:   (OPERATIONS_FLOW·시작→하는 일→닿는 대상)은 실제로 한 단계가 다음
#:   단계로 «이어지는» 가치사슬이다 — 화살표가 사실과 맞으므로 그대로 둔다.
#:   5장(CHALLENGE_FLOW·과제→대응)도 문제가 대응으로 이어지는 방향성이
#:   있어 화살표를 유지한다.
#: ★ 판정 근거는 이 모듈이 받는 ``ReportTable`` 자신의 «칸 이름»이다 — 장
#:   id를 안 받으므로(ReportTable에는 section_id가 없다) 칸 이름이 가장
#:   안정적인 식별자다. 값은 composer/constants.py가 정한 한글 그대로
#:   옮겼다 — 그쪽 상수가 바뀌면 이 집합도 함께 갱신해야 한다는 뜻을 여기
#:   남긴다(칸 이름이 달라지면 이 규칙은 조용히 안 맞고 카드가 아닌 표로
#:   되돌아간다 — 안전한 실패 방향이다).
_CARD_HEADER_SETS: Final[tuple[tuple[str, ...], ...]] = (
    ("공식 자기정의", "사업 범위", "이 보고서의 해석"),  # 1장 정체성
    ("계획", "시점", "공시된 내용"),  # 6장 성장 계획 (2026-08-25 composer가 열 순서를 「계획→시점」으로 바꿈 — «시점 칸에 시점이 들어간다» 수정과 함께)
    ("내건 가치", "일하는 원칙", "확인된 사례"),  # 8장 인재상
    ("제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할"),  # 3장 핵심 제품·서비스 (2026-08-25 추가)
)

#: 카드로 낼 흐름표 중 «첫 칸이 그 줄의 주제(제목)인» 칸 이름 → 그 주제
#: 칸의 «이름»(자리가 아니다). 값을 자리(0번째)로 찾으면 composer가 열
#: 순서를 바꿀 때(6장에서 실제로 있었던 일) 엉뚱한 칸이 제목이 될 수
#: 있다 — 이름으로 찾으면 순서가 바뀌어도 안 흔들린다.
#: ★ 여기 없는 카드(1·6·8장)는 제목 없이 낸다 — 어느 칸이 주제인지 그
#:   표 자신이 알려 주지 않아서 지어내지 않는다(Card 문서 참조). 3장은
#:   composer가 «제품·서비스명»이라는 이름으로 주제 칸을 명시했으므로
#:   지어내는 것이 아니라 표가 준 정보를 그대로 쓰는 것이다.
_CARD_TITLE_COLUMN_BY_HEADER_KEY: Final[dict[frozenset[str], str]] = {
    frozenset(("제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할")): "제품·서비스명",
}

#: 카드 맨 아래에 붙는 「범위·한계」 줄의 라벨.
#: ★ v1(section_content.py)의 8장 카드가 실제로 쓰는 라벨을 그대로
#:   가져왔다(_culture_blocks의 _field("범위·한계", ...)) — 새로 지은
#:   이름이 아니다.
_CARD_LIMITATION_LABEL: Final[str] = "범위·한계"

#: 카드로 낼 흐름표 중 «범위·한계」 줄을 붙일 칸 이름 집합 → 그 장의 고정
#: 문구. AI가 아니라 코드가 정한다(사용자 승인 조건 — 층2만, 층1 AI
#: 확장은 나중에 결정). 문구는 전부 `docs/실행계획_엔진v2/
#: 11_결정_전수대조_04_범위한계_재현안.md` §2-1 "빈틈 채운 문구" 표를
#: 그대로 옮겼다 — 지어낸 말이 아니라 v1 폴백(§1)이 실제로 쓰던 절차적
#: 사실 서술이다. 가치 판단(좋다·나쁘다·위험 등)은 한 글자도 없다 —
#: v1도 13건 전수에서 0건이었다(같은 문서 §5).
#:
#: ★ 왜 «citations 개수」 규칙(문서 §2-1의 최종 대체)은 안 쓰나 —
#:   ``ReportTable.cite``는 표 하나에 «단일 문자열」(예: "[2]")이고, 원래
#:   여러 인용 중 «최솟값 하나»로 이미 뭉개져 있다(composer/render.py의
#:   `_flow_report_table`: ``cite=f"[{min(cited)}]"``). 줄마다 몇 건을
#:   인용했는지는 이 단계에서 이미 사라진 정보라 셀 수 없다. 다행히
#:   아래 4개 카드 전부 section_id 전용 문구가 있어(문서 §2-1) citations
#:   개수로 갈라야 하는 경우가 없다 — 그래서 그 규칙은 구현하지 않았다.
#:   (자세한 사유는 진행 보고에 남긴다.)
#: ★ 1장(identity)은 «일부러» 뺐다 — v1도 목업도 1장 카드에는 이 줄이
#:   없다(문서 §1 마지막 줄: "1장은 층2 고정 문구가 없는 유일한 장").
#:   문서 §2-1은 1장에 신규 문구를 «지어낼 수 있다»고 적어 뒀지만, v1
#:   선례가 없는 신규 문구를 넣으면 목업과 달라진다 — 그래서 뺐다.
_CARD_LIMITATION_TEXT_BY_HEADER_KEY: Final[dict[frozenset[str], str]] = {
    # 3장 — 문서 §2-1: portfolio, v1 선례 #3 그대로.
    frozenset(("제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할")): (
        "공식 근거가 확인한 범위로 한정합니다"
    ),
    # 6장 — future_strategy.
    # ★ 2026-08-29 — 「아직 실행되지 않은 계획입니다」에서 바꿨다. 그 문장은
    #   행마다 «실행됐는지»를 단정하는데, 이 층에는 그걸 판정할 재료가 없다:
    #   `ReportTable` 에는 장 id·날짜·시간상태가 없고(`pipeline/port.py`),
    #   한 행의 원본 `FlowRow` 도 칸 문자열과 인용뿐이다(`composer/port.py`).
    #   조건도 «칸 이름이 6장 것인가» 하나뿐이라 그 표의 «모든 행»에 무조건 붙었다.
    #   실측(2026-08-29): 우리은행 4행 중 4행·현대카드 2행 중 2행에 붙었고,
    #   그중 4행은 같은 보고서 본문이 「출시하여 … 구축했으며」라고 과거형으로
    #   쓰고 3장 표에도 「2025년 6월 출시」로 실려 있었다 — 정면으로 어긋났다.
    #   눈가림 독립 평가에서 평가자 2명이 각각 이 모순을 지적했다.
    # ★ 그래서 «판정»을 «사실»로 바꾼다. 우리는 실행 여부를 확인하지 않았고,
    #   확인하지 않았다고 적는 것이 정직하다. 3·8장 문구처럼 행 내용과
    #   무관하게 «참»이므로 이 층에 둘 자격이 있다.
    frozenset(("계획", "시점", "공시된 내용")): "실행 여부는 확인하지 않았습니다",
    # 8장 — 문서 §2-1: culture, v1 선례 #13 그대로("전사 공통 공식 기준").
    frozenset(("내건 가치", "일하는 원칙", "확인된 사례")): "전사 공통 공식 기준입니다",
}

#: 위 튜플을 «순서 무관» 비교용으로 미리 굳힌다(2026-08-25, 실측 사고 대응).
#:   composer/constants.py가 6장 칸 순서를 «시점→계획」에서 «계획→시점」으로
#:   바꾼 사건에서, 이 모듈이 정확히 이 순서에 걸려 있었다는 사실이 드러났다
#:   ― 값은 그대로인데 «나열 순서»만 바뀌어도 예외·시험 없이 조용히 카드가
#:   화살표로 되돌아갈 뻔했다. frozenset 비교는 그 실패 모드 하나를 없앤다.
#: ★ 이것으로 «전부»가 해결되지는 않는다 ― 칸 이름 «값» 자체가 바뀌거나
#:   지워지면 이 방식도 조용히 못 잡는다. 그건 이 상수와 composer.constants를
#:   직접 대조하는 결합 시험(test_card_header_sets_stay_in_sync_with_composer_constants)
#:   의 몫이다. 둘을 같이 둬야 «순서 변경»과 «값 변경」 두 실패 모드를 모두 막는다.
_CARD_HEADER_KEY_SETS: Final[frozenset[frozenset[str]]] = frozenset(
    frozenset(headers) for headers in _CARD_HEADER_SETS
)


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
class CardField:
    """카드 한 줄 — 라벨과 값. section_content.ContentField와 모양이 같지만,
    이쪽은 AI가 낸 «경로표」(ReportTable)에서 나오고 그쪽은 사실 원장
    (FactRecord)에서 바로 나온다 — 정본이 다르므로 모듈도 분리해 둔다."""

    label: str
    value: str


@dataclass(frozen=True)
class Card:
    """카드 하나 — 표의 행 하나에 대응한다. 제목은 여러 줄일 때만 붙인다.

    ★ 왜 행마다 제목을 지어내지 않나 — 이 표(경로표)에는 «어느 칸이 이
      행의 주제인가»를 알려 주는 정보가 없다. 지어내면 AI가 안 준 사실을
      코드가 만든 것이 된다. 줄이 하나면 표 캡션이 이미 제목 역할을 하므로
      빈 제목으로 둔다.
    """

    title: str
    fields: tuple[CardField, ...]


@dataclass(frozen=True)
class TableVisualization:
    kind: str
    caption: str
    unit: str = ""
    note: str = ""
    #: 그림에서 «무엇을 봐야 하는지» 한 줄. 출처 설명(note)과 다른 자리다.
    #:
    #: ★ 왜 필요한가 (사용자가 완성 기준으로 정한 목업과의 차이) — 목업은
    #:   그림 밑에 「오른쪽 선이 3년 내내 0선 아래에 있다」처럼 «읽는 법»을
    #:   달아 준다. 우리 캡션은 제목뿐이라 독자가 그림을 스스로 해석해야
    #:   했다. 「이해도가 다르다」는 신고의 실체가 이것이다.
    #:
    #: ★ 이 줄은 «AI가 아니라 코드»가 만든다. 그림에 이미 인쇄된 숫자만
    #:   가지고 산술로 만든다 — 새 주장이 아니라 «보이는 것의 요약»이다.
    #:   AI에게 시키면 그림에 없는 말을 붙이고, 그것을 검증할 방법이 없다.
    reading: str = ""
    items: tuple[ChartPoint, ...] = ()
    series: tuple[ChartSeries, ...] = ()
    flows: tuple[tuple[str, ...], ...] = ()
    #: kind == "card"일 때만 채운다. 화살표로 이을 수 없는 흐름표를 라벨:값
    #: 카드로 낼 때 쓴다 — 자세한 이유는 _CARD_HEADER_SETS 주석 참조.
    cards: tuple[Card, ...] = ()


def _composition_reading(items: "tuple[ChartPoint, ...]") -> str:
    """구성 도식 읽는 법 — 가장 큰 몫과 상위 둘의 합만 말한다."""
    if len(items) < 2:
        return ""
    ordered = sorted(items, key=lambda point: point.value, reverse=True)
    top, second = ordered[0], ordered[1]
    two = top.value + second.value
    return (
        f"가장 큰 몫은 「{top.label}」 {top.display}이고, "
        f"위 둘을 합치면 {two:.0f}%다."
    )


def _trend_reading(series: "tuple[ChartSeries, ...]", unit: str) -> str:
    """추이 도식 읽는 법 — 방향과 «0선 아래»만 말한다.

    ★ 판단하지 않는다. 「나쁘다」·「위험하다」를 쓰지 않는다. 그림에 그려진
      막대의 방향과 개수만 말한다 — 독자가 눈으로 셀 수 있는 것이다.
    """
    parts: list[str] = []
    for one in series:
        if len(one.points) < 2:
            continue
        first, last = one.points[0], one.points[-1]
        below = sum(1 for point in one.points if point.below)
        방향 = (
            "늘었다"
            if last.value > first.value
            else "줄었다"
            if last.value < first.value
            else "같다"
        )
        말 = f"「{one.label}」은 {first.label} {first.display}에서 {last.label} {last.display}로 {방향}"
        if below and below < len(one.points):
            말 += f" (0선 아래 {below}개 해)"
        elif below == len(one.points):
            말 += " (세 해 모두 0선 아래)"
        parts.append(말)
    return ". ".join(parts) + "." if parts else ""


def _flow_reading(flows: "tuple[tuple[str, ...], ...]", headers: list[str]) -> str:
    """흐름 도식 읽는 법 — 줄 수와 «끝 칸이 몇 가지인가»만 말한다."""
    if not flows:
        return ""
    끝칸 = {row[-1] for row in flows if row}
    끝이름 = headers[-1] if headers else ""
    if len(flows) == 1:
        return f"경로가 하나다: {' → '.join(flows[0])}."
    말 = f"경로가 {len(flows)}개다"
    if 끝이름 and 끝칸:
        말 += f". 「{끝이름}」이 {len(끝칸)}가지로 갈린다"
    return 말 + "."


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
        reading=_composition_reading(items),
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
        reading=_trend_reading(tuple(series), _unit(table)),
        unit=_unit(table),
        note=(
            f"원값을 {_unit(table)} 단위로 환산해 표시"
            if table.scale_divisor not in {"", "1"} and _unit(table)
            else "공개 표의 값을 계산 없이 표시"
        ),
        series=tuple(series),
    )


def _flow_cards(
    flows: "tuple[tuple[str, ...], ...]", headers: list[str]
) -> "tuple[Card, ...]":
    """흐름 줄을 카드로 바꾼다 — 칸마다 라벨:값 한 줄, 빈 칸은 뺀다.

    ★ 제목 칸이 있으면(_CARD_TITLE_COLUMN_BY_HEADER_KEY에 등록된 표) 그
      칸의 값을 카드 제목으로 쓰고, «나머지» 칸만 라벨:값 줄로 낸다(3장 —
      제품·서비스명이 제목, 나머지 3칸이 그 제품의 속성).
    ★ 제목 칸이 없으면(1·6·8장) 줄이 하나뿐일 때 표 캡션이 이미 카드
      제목 역할을 한다(예: 「회사가 스스로를 어떻게 규정하나」). 줄이
      여러 개여도 어느 칸이 그 줄의 «주제»인지 이 표는 알려 주지
      않으므로 제목을 지어내지 않는다(빈 제목 — Card 문서 참조).
    ★ 「범위·한계」 줄이 등록된 표(3·6·8장)는 카드 맨 아래에 그 장의
      고정 문구를 한 줄 더 붙인다 — _CARD_LIMITATION_TEXT_BY_HEADER_KEY
      주석 참조. AI가 쓴 값이 아니라 이 함수(코드)가 붙인 값이다.
    """
    key = frozenset(headers)
    title_column = _CARD_TITLE_COLUMN_BY_HEADER_KEY.get(key, "")
    limitation_text = _CARD_LIMITATION_TEXT_BY_HEADER_KEY.get(key, "")
    return tuple(
        Card(
            title=(
                next(
                    (value for index, value in enumerate(row) if headers[index] == title_column),
                    "",
                )
                if title_column
                else ""
            ),
            fields=(
                tuple(
                    CardField(label=headers[index], value=value)
                    for index, value in enumerate(row)
                    if value and headers[index] != title_column
                )
                + (
                    (CardField(label=_CARD_LIMITATION_LABEL, value=limitation_text),)
                    if limitation_text
                    else ()
                )
            ),
        )
        for row in flows
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
        # ★ 빈 칸을 허용한다 (사용자 결정 2026-08-24). 8장 「확인된 사례」처럼
        #   «없을 수 있는» 칸 때문에 표 전체가 사라지던 것을 막는다.
        #   전부 빈 줄만 버린다 — 그런 줄은 아무 말도 하지 않는다.
        if not any(values):
            continue
        flows.append(values)
    if not flows:
        return None
    # ★ «화살표로 이을 수 없는» 흐름표는 카드로 낸다 — _CARD_HEADER_KEY_SETS 주석
    #   참조. frozenset 비교라 칸 «순서»가 바뀌어도(값이 그대로면) 안 흔들린다.
    headers = tuple(str(value).strip() for value in table.headers)
    if frozenset(headers) in _CARD_HEADER_KEY_SETS:
        return TableVisualization(
            kind="card",
            caption=table.caption,
            cards=_flow_cards(tuple(flows), list(table.headers)),
        )
    return TableVisualization(
        kind="flow",
        caption=table.caption,
        reading=_flow_reading(tuple(flows), list(table.headers)),
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
    "Card",
    "CardField",
    "ChartPoint",
    "ChartSeries",
    "TableVisualization",
    "table_visualization",
]
