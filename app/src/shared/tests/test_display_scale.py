# -*- coding: utf-8 -*-
"""표시 자리수 규칙 — 문턱 경계와 「표에서 읽는 변동」 불변식을 못 박는다.

★ 왜 이 파일이 생겼나 (2026-09-11 독립 검토) — 앞선 판은 반올림한 표시값의
  «유효숫자 개수»로 자리수를 정했다. 십진수는 반올림하며 생긴 뒤따르는 0도
  자릿수로 들고 있어(``Decimal("1.0")``의 자릿수는 두 개) 0.95억~1.05억 구간이
  자리수 1에서 멈췄고, 1.04억과 0.95억이 둘 다 「1.0」으로 찍혔다. 독자가 읽는
  변동은 0%, 원값 변동은 -8.65%였다.

★ 기대값은 리터럴로 적는다. 생산 상수를 import해 기대값을 만들면 문턱이
  느슨해지는 회귀를 못 잡는 순환 검증이 된다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.shared.display_scale import (
    MAX_DISPLAY_PLACES,
    display_places,
    format_display_value,
)


_EOK = Decimal(100_000_000)

#: 표시값으로 읽은 변동과 원값 변동의 허용 차이(%p).
_MAX_DISPLAY_CHANGE_GAP_POINTS = 1.0


def _won(*values: int) -> list[Decimal]:
    return [Decimal(value) for value in values]


@pytest.mark.parametrize(
    ("가장_작은_값", "기대_자리수"),
    (
        # 10억 «미만» → 두 자리. 경계 바로 아래와 바로 위를 함께 잰다.
        (999_999_999, 2),
        (1_000_000_000, 1),
        # 100억 «미만» → 한 자리.
        (9_999_999_999, 1),
        (10_000_000_000, 0),
        # 실측 값.
        (82_552_618, 2),
        (2_166_028_141, 1),
        (440_620_073_987, 0),
    ),
)
def test_자리수는_가장_작은_값의_크기_문턱으로_정해진다(
    가장_작은_값: int, 기대_자리수: int
) -> None:
    # 큰 값이 함께 있어도 «가장 작은 값»이 자리수를 정한다.
    values = _won(가장_작은_값, 30_000_000_000_000)

    assert display_places(values, _EOK) == 기대_자리수


def test_음수와_0은_크기만_보고_0은_아예_세지_않는다() -> None:
    # 0은 어느 자리로 찍어도 0이라 정밀도를 요구하지 않는다.
    assert display_places(_won(0, 0), _EOK) == 0
    assert display_places(_won(0, 30_000_000_000_000), _EOK) == 0
    # 부호는 자리수 판단에 쓰지 않는다.
    assert display_places(_won(-82_552_618), _EOK) == display_places(
        _won(82_552_618), _EOK
    )


def test_나눗수가_없거나_0이면_정수_표시로_남는다() -> None:
    assert display_places(_won(82_552_618), Decimal(0)) == 0
    assert display_places([], _EOK) == 0


def test_상한을_넘는_자리수는_만들지_않는다() -> None:
    # 아무리 작은 값이 섞여도 표가 읽히지 않을 만큼 자리를 늘리지 않는다.
    assert display_places(_won(1), _EOK) == MAX_DISPLAY_PLACES


@pytest.mark.parametrize(
    ("이전_원값", "이번_원값"),
    (
        # 독립 검토 반례 — 옛 규칙에서 둘 다 「1.0」으로 찍혔다.
        (104_000_000, 95_000_000),
        # 첫 실측 — 옛 정수 표시에서 4와 1로 찍혀 -75%로 읽혔다.
        (366_016_342, 82_552_618),
        # 문턱 바로 위·아래.
        (1_050_000_000, 950_000_000),
        (10_500_000_000, 9_500_000_000),
    ),
)
def test_표시값으로_읽은_변동이_원값_변동과_어긋나지_않는다(
    이전_원값: int, 이번_원값: int
) -> None:
    values = _won(이전_원값, 이번_원값)
    places = display_places(values, _EOK)
    shown = [
        float(format_display_value(value, _EOK, places).replace(",", ""))
        for value in values
    ]

    raw_change = (이번_원값 - 이전_원값) / 이전_원값 * 100
    shown_change = (shown[1] - shown[0]) / shown[0] * 100

    assert abs(shown_change - raw_change) <= _MAX_DISPLAY_CHANGE_GAP_POINTS


def test_동점은_HALF_UP으로_올린다() -> None:
    """★ 반올림 방식이 «실제로 갈리는» 값으로 잰다.

    소수 둘째 자리에서 HALF_UP과 HALF_EVEN이 갈리려면 셋째 자리가 정확히
    5여야 한다. 0.49999999억 같은 값은 어떤 방식으로도 0.50이라 못 가린다.
    """

    assert format_display_value(Decimal(124_500_000), _EOK, 2) == "1.25"
    assert format_display_value(Decimal(-124_500_000), _EOK, 2) == "-1.25"
    assert format_display_value(Decimal(500_000), _EOK, 2) == "0.01"
    assert format_display_value(Decimal(-500_000), _EOK, 2) == "-0.01"
    # 한 자리에서도 갈리는 값 — 0.25억은 HALF_UP 0.3 / HALF_EVEN 0.2다.
    assert format_display_value(Decimal(25_000_000), _EOK, 1) == "0.3"
