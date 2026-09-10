"""표시 단위로 줄인 금액의 «소수 자리»를 값 크기로 정한다.

★ 왜 필요한가 (2026-09-11 소규모 회사 실측) — 실적표는 원 단위 값을 억원으로
  줄여 «정수»로만 찍었다. 당기순이익 82,552,618원은 0.83억인데 표에는 1이
  찍혔고, 전기 366,016,342원(3.66억)은 4가 찍혔다. 독자가 표에서 읽는 변동은
  -75%인데 실제는 -77.45%다. 순이익이 4천만원인 회사면 표에 아예 0이 찍힌다.
  자리수가 상수로 못 박혀 있어 값 크기에 따라 정밀도를 바꿀 수 없었다.

★ 왜 shared 인가 — 같은 표를 만드는 생산자가 둘이다. 감사보고서 파서
  (`features/audit_financials`)와 DART 재무 API 경로
  (`features/company_performance`). 잣대가 두 벌이면 어느 경로로 만들어졌느냐에
  따라 같은 회사의 같은 값이 다르게 찍힌다.

★ 회사·업종 분기는 없다. 규칙은 「표에 실릴 0이 아닌 값 중 가장 작은 것이
  유효숫자 두 자리를 갖는 최소 자리수」 하나뿐이다.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Final, Sequence


#: 표시값이 못 지키면 자리를 늘리는 «유효숫자» 하한.
#:
#: ★ 왜 2인가 — 유효숫자가 하나면 독자가 두 값의 «변동»을 읽을 수 없다. 1과
#:   4는 -75%로 읽히지만 실제 0.83과 3.66은 -77.45%다. 두 자리면 표에서 읽는
#:   변동과 원값의 변동이 소수 첫째 자리까지 일치한다.
MIN_SIGNIFICANT_DIGITS: Final[int] = 2

#: 자리수 상한. 더 늘리면 표가 읽히지 않는다.
MAX_DISPLAY_PLACES: Final[int] = 2

#: 시작 자리수. 값이 충분히 크면 여기서 멈춰 «종전과 같은 정수 표시»가 된다.
BASE_DISPLAY_PLACES: Final[int] = 0


def _significant_digits(value: Decimal) -> int:
    """0이 아닌 십진수의 유효숫자 개수. 0은 0으로 센다."""

    if value == 0:
        return 0
    return len(value.as_tuple().digits)


def quantum_for(places: int) -> Decimal:
    """``places`` 자리로 반올림할 때 쓰는 최소 단위."""

    return Decimal(1).scaleb(-places)


def display_places(values: Sequence[Decimal], divisor: Decimal) -> int:
    """표 전체가 함께 쓸 소수 자리수를 «가장 작은 값»으로 정한다.

    Args:
        values: 표에 실릴 원 단위 값 전부. 0은 자리수 판단에서 뺀다 — 0은
            어느 자리로 찍어도 0이라 정밀도를 요구하지 않는다.
        divisor: 표시 단위로 줄이는 나눗수(억원이면 100,000,000).

    Returns:
        ``BASE_DISPLAY_PLACES`` 이상 ``MAX_DISPLAY_PLACES`` 이하의 자리수.
        값이 모두 0이거나 나눗수가 쓸 수 없는 값이면 시작 자리수를 그대로 준다.

    ★ 표 전체가 «한 자리수»를 쓴다. 칸마다 다른 자리수를 쓰면 열이 어긋나
      읽기 어렵고, 표시값을 원값으로 되돌리는 하류 재검산
      (`composer.public_manifest`)이 칸마다 다른 규칙을 알아야 한다.
    """

    if divisor is None or divisor == 0:
        return BASE_DISPLAY_PLACES
    non_zero = [abs(value) for value in values if value != 0]
    if not non_zero:
        return BASE_DISPLAY_PLACES
    smallest = min(non_zero)
    places = BASE_DISPLAY_PLACES
    while places < MAX_DISPLAY_PLACES:
        shown = (smallest / divisor).quantize(
            quantum_for(places), rounding=ROUND_HALF_UP
        )
        if _significant_digits(shown) >= MIN_SIGNIFICANT_DIGITS:
            break
        places += 1
    return places


def format_display_value(value: Decimal, divisor: Decimal, places: int) -> str:
    """원 단위 값을 표시 단위·정해진 자리수의 공개 문자열로 만든다.

    ★ 하류 재검산과 «같은 계산»이어야 한다 —
      `composer.public_manifest._generic_evidence_matches_row`가
      ``(원값 / 나눗수).quantize(10^-자리수, ROUND_HALF_UP)``로 다시 검산해
      공개 표를 통과시킨다. 여기서 다른 반올림을 쓰면 표가 조용히 막힌다.
    """

    shown = (value / divisor).quantize(
        quantum_for(places), rounding=ROUND_HALF_UP
    )
    # ``-0``은 수치상 0과 같지만 표시·해시가 불필요하게 갈라지므로 정규화한다.
    if shown == 0:
        shown = Decimal(0).quantize(quantum_for(places))
    return f"{shown:,.{places}f}"
