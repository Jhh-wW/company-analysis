"""열쇠 링크의 «판단»만 모아 둔다 (문제로그 P-94).

★ 여기에는 시계도 저장소도 없다. 시각·상태를 인자로 받는 순수 함수뿐이다.
  「하루가 바뀌면 예산이 되살아나는가」를 시험에서 실제로 돌려보기 위해서다.

★ 예산을 «링크마다» 따로 세는 것이 이 파일의 핵심이다.
  전역 장부(`budget/logic.py`)와 모양은 같지만, 열쇠별로 하나씩 있다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

#: 열쇠로 인정할 글자 모양. 16진수만 받는다 —
#: ★ 무엇이든 열쇠로 받아주면 주소창에 아무 글자나 넣어 «새 통장»을 무한히 만들 수 있다.
#:   그러면 링크당 상한이 아무 의미가 없어진다.
_KEY_RE = re.compile(r"^[0-9a-f]{8,64}$")


def is_valid_key(key: str) -> bool:
    """열쇠로 인정할 모양인가.

    Args:
        key: 주소나 쿠키에서 온 글자.

    Returns:
        16진수 8~64자면 True.

    ★ 모양만 본다. 「우리가 발급한 것인가」는 저장소가 판단한다 (`store.py`).
      모양 검사를 먼저 하는 이유는, 이상한 글자가 DB 조회까지 가지 않게 하려는 것이다.
    """
    return bool(_KEY_RE.match((key or "").strip().lower()))


@dataclass
class DailySpend:
    """열쇠별 «오늘» 쓴 돈.

    ★ `day`를 같이 들고 있어야 날짜가 바뀔 때 **저절로** 0으로 돌아간다.
      자정에 비우는 장치를 따로 두면 그게 안 돌 때 상한이 영영 안 풀린다.
    """

    day: dt.date
    by_key: dict[str, float] = field(default_factory=dict)


def rolled_over(spend: DailySpend, today: dt.date) -> DailySpend:
    """날짜가 바뀌었으면 «전부 0원»인 새 장부를 돌려준다."""
    if spend.day == today:
        return spend
    return DailySpend(day=today, by_key={})


def spent_for(spend: DailySpend, key: str, today: dt.date) -> float:
    """이 열쇠가 오늘 쓴 돈."""
    return rolled_over(spend, today).by_key.get(key, 0.0)


def add_spend(
    spend: DailySpend, key: str, today: dt.date, amount_krw: float
) -> DailySpend:
    """이 열쇠 앞으로 쓴 돈을 더한다.

    Args:
        spend: 지금 장부.
        key: 열쇠 (열쇠 없는 손님은 `PUBLIC_BUCKET`).
        today: 오늘 날짜.
        amount_krw: 이번에 쓴 돈. **0 이하는 무시한다** — 환불 개념이 없다.

    Returns:
        더해진 장부.
    """
    rolled = rolled_over(spend, today)
    if amount_krw <= 0:
        return rolled
    rolled.by_key[key] = rolled.by_key.get(key, 0.0) + amount_krw
    return rolled


def budget_left(
    spend: DailySpend, key: str, today: dt.date, cap_krw: float
) -> float:
    """이 열쇠에 오늘 남은 예산(원). 0 밑으로 안 내려간다."""
    return max(0.0, cap_krw - spent_for(spend, key, today))


def can_start_new_run(
    spend: DailySpend, key: str, today: dt.date, cap_krw: float
) -> bool:
    """이 열쇠로 «새 조사»를 시작해도 되는가.

    ★ 이것은 **새로 AI를 부르는 일**에만 건다.
      **이미 만들어 둔 보고서를 여는 것은 여기를 안 거친다** — 그건 0원이고,
      예산이 다 됐어도 계속 열려야 한다 (2026-08-16 사용자 결정).
    """
    return budget_left(spend, key, today, cap_krw) > 0


def total_spent(spend: DailySpend, today: dt.date) -> float:
    """오늘 «모든 링크»가 쓴 돈의 합.

    ★ 전체 상한은 두지 않기로 했지만(사용자 결정), **얼마나 나갔는지는 보여야 한다.**
      상한이 없다는 것과 안 보여도 된다는 것은 다른 말이다.
    """
    return sum(rolled_over(spend, today).by_key.values())
