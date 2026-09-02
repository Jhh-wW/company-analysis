"""4-3(앞으로 어디로 가려 하나)에 붙는 날짜 확인 경고 (W6).

★ 왜 기간을 안 따지는가
  1년 전 기사로 방향을 말했는데 **6개월 전에 이미 바뀌어 있던** 실제 사고(A7)가
  있다. 「아직 3년 안이니 괜찮다」는 판단은 이 사고를 막지 못한다. 그래서
  방향 정보에는 기간과 무관하게 **항상** 재확인 경고를 붙인다. 「방향만 다시
  확인」하는 재조회 기능은 1차 범위 밖이다.

"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import Optional

from src.features.pipeline.port import ReportSection
from src.features.provenance.constants import (
    DIRECTION_CELL,
    DIRECTION_STALE_YEARS,
    DIRECTION_WARNING_LINES,
)


def append_direction_warning(section: ReportSection) -> ReportSection:
    """4-3 항목에 날짜 확인 경고 두 줄을 덧붙인다.

    ★ 4-3이 아니거나 내용이 비어 있으면 그대로 돌려준다.
      - 4-3이 아닌 칸에는 붙이지 않는다 — W6은 4-3 전용이다.
      - 빈칸에는 붙이지 않는다 — 빈칸에 경고를 붙이면 「내용이 있었는데
        지웠나」로 잘못 읽힌다. 빈칸은 `empty_reason`이 이미 맡고 있다.
      - 이미 붙어 있으면 다시 붙이지 않는다 (idempotent) — 여러 번 불러도
        같은 결과가 나와야 파이프라인 재시도 시 경고가 두 번 쌓이지 않는다.

    Args:
        section: 화면에 낼 4-3 항목.

    Returns:
        경고 두 줄이 `lines` 끝에 붙은 새 `ReportSection`.
        (원본은 바꾸지 않는다 — `ReportSection`은 frozen dataclass다.)
    """
    if section.cell != DIRECTION_CELL or not section.is_filled:
        return section

    tail = tuple(text for text, _cite in section.lines[-len(DIRECTION_WARNING_LINES) :])
    if tail == DIRECTION_WARNING_LINES:
        return section

    warning = [(text, "") for text in DIRECTION_WARNING_LINES]
    return replace(section, lines=[*section.lines, *warning])


def is_stale(
    date_str: str,
    *,
    max_years: int = DIRECTION_STALE_YEARS,
    today: Optional[dt.date] = None,
) -> Optional[bool]:
    """자료 날짜가 상한(년)을 넘었는가.

    ★ 지금은 4-3 경고를 «항상» 붙이므로(위 함수) 이 함수가 그 경고 여부를
      가르지 않는다. 03 수집 단계의 신선도 판정(O9)이나 08 관측 지표처럼,
      날짜 하나로 신선도를 따져야 하는 다른 곳에서 바로 쓰도록 독립 함수로
      둔다.

    Args:
        date_str: `"YYYY-MM-DD"` 형식의 날짜.
        max_years: 상한 (년). 기본값은 방향(4-3) 기준 3년.
        today: 기준 시각. 생략하면 오늘.

    Returns:
        상한을 넘었으면 True, 안 넘었으면 False.
        날짜를 못 읽으면 **None** — 「신선하다」로 잘못 세면 안 되므로
        부른 쪽이 따로 처리해야 한다.
    """
    try:
        parsed = dt.date.fromisoformat(date_str.strip())
    except (ValueError, AttributeError):
        return None

    base = today or dt.date.today()
    try:
        limit = parsed.replace(year=parsed.year + max_years)
    except ValueError:
        # 2월 29일처럼 옮긴 해에 없는 날짜다 — 하루 앞당긴다.
        limit = parsed.replace(month=2, day=28, year=parsed.year + max_years)
    return base > limit
