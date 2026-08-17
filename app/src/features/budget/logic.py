"""돈·횟수를 막는 «판단»만 모아 둔다 (문제로그 P-92).

★ 여기에는 시계도 없고 저장소도 없다. **시각과 상태를 인자로 받는** 순수 함수뿐이다.
  그래야 「하루가 바뀌면」·「10분이 지나면」 같은 것을 시험에서 실제로 돌려볼 수 있다.
  시계를 안에서 부르면 그 시험을 못 쓴다.

★ 정책 값(얼마·몇 번)은 여기 없다 — `constants.py`에 있다.
  판단과 값을 섞으면 값을 바꿀 때 판단까지 건드리게 된다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from enum import Enum


class Verdict(str, Enum):
    """조사를 시작해도 되는가."""

    OK = "ok"                    #: 시작해도 된다
    BUDGET = "budget"            #: 오늘 예산을 다 썼다
    RATE = "rate"                #: 짧은 시간에 너무 많이 요청했다
    BUSY = "busy"                #: 동시 실행이 꽉 찼다


@dataclass(frozen=True)
class Ledger:
    """오늘 쓴 돈.

    ★ `day`를 같이 들고 다니는 이유 — 날짜가 바뀌면 «저절로» 0으로 돌아가야 한다.
      따로 자정에 비우는 장치를 두면 그게 안 돌 때 상한이 영영 안 풀린다.
    """

    day: dt.date
    spent_krw: float = 0.0


def rolled_over(ledger: Ledger, today: dt.date) -> Ledger:
    """날짜가 바뀌었으면 새 장부를 돌려준다.

    Args:
        ledger: 지금 장부.
        today: 오늘 날짜.

    Returns:
        같은 날이면 그대로, 날이 바뀌었으면 **0원짜리 새 장부**.
    """
    if ledger.day == today:
        return ledger
    return Ledger(day=today, spent_krw=0.0)


def add_spend(ledger: Ledger, today: dt.date, amount_krw: float) -> Ledger:
    """쓴 돈을 더한다. 날짜가 바뀌었으면 먼저 새 장부로 넘긴다.

    Args:
        ledger: 지금 장부.
        today: 오늘 날짜.
        amount_krw: 이번에 쓴 돈(원). **음수는 무시한다** — 환불 개념이 없다.

    Returns:
        더해진 장부.
    """
    rolled = rolled_over(ledger, today)
    if amount_krw <= 0:
        return rolled
    return replace(rolled, spent_krw=rolled.spent_krw + amount_krw)


def budget_left(ledger: Ledger, today: dt.date, cap_krw: float) -> float:
    """오늘 남은 예산(원). 0 밑으로는 안 내려간다."""
    rolled = rolled_over(ledger, today)
    return max(0.0, cap_krw - rolled.spent_krw)


@dataclass
class RateHistory:
    """한 곳(IP)이 «언제» 조사를 시작했는지.

    ★ dict를 통째로 들고 있으면 방문자가 늘수록 영원히 커진다.
      `prune()`으로 오래된 것을 «반드시» 치운다.
    """

    starts: dict[str, list[float]] = field(default_factory=dict)


def prune(history: RateHistory, now: float, window_sec: int) -> None:
    """시간 창을 벗어난 기록을 지운다. 비어 버린 곳은 통째로 뺀다.

    Args:
        history: 시작 기록.
        now: 지금 시각 (단조 증가 초). `time.monotonic()`을 넘긴다.
        window_sec: 셀 시간 창(초).

    ★ 「비어 버린 곳을 뺀다」가 핵심이다 — 안 빼면 방문한 IP 수만큼
      빈 목록이 영원히 쌓여 결국 같은 문제(메모리 증가)가 된다.
    """
    cutoff = now - window_sec
    for key in list(history.starts):
        kept = [t for t in history.starts[key] if t > cutoff]
        if kept:
            history.starts[key] = kept
        else:
            del history.starts[key]


def rate_ok(
    history: RateHistory, key: str, now: float, *, window_sec: int, max_runs: int
) -> bool:
    """이 곳이 지금 한 건 더 시작해도 되는가. **세기만 하고 기록하지 않는다.**

    Args:
        history: 시작 기록.
        key: 사람을 가르는 열쇠 (보통 IP).
        now: 지금 시각(초).
        window_sec: 셀 시간 창.
        max_runs: 그 창 안에 허용할 횟수.

    Returns:
        시작해도 되면 True.

    ★ 세는 것과 적는 것을 나눈 이유 — 예산·동시실행 검사에서 «떨어지면»
      그 요청은 시작되지 않은 것이므로 횟수에 넣으면 안 된다.
      합쳐 두면 「거절당했는데 횟수만 까이는」 억울한 일이 생긴다.
    """
    prune(history, now, window_sec)
    return len(history.starts.get(key, [])) < max_runs


def record_start(history: RateHistory, key: str, now: float) -> None:
    """실제로 시작한 건만 기록한다."""
    history.starts.setdefault(key, []).append(now)


def decide(
    *,
    ledger: Ledger,
    today: dt.date,
    cap_krw: float,
    costs_money: bool,
    history: RateHistory,
    key: str,
    now: float,
    window_sec: int,
    max_runs: int,
    running: int,
    max_concurrent: int,
) -> Verdict:
    """조사를 시작해도 되는지 한 번에 판단한다.

    Args:
        ledger: 오늘 장부.
        today: 오늘 날짜.
        cap_krw: 하루 상한(원).
        costs_money: 이번 조사가 «진짜»라서 돈이 드는가. 데모면 False.
        history: 시작 기록.
        key: 사람을 가르는 열쇠(IP).
        now: 지금 시각(초).
        window_sec: 횟수를 셀 시간 창.
        max_runs: 그 창 안에 허용할 횟수.
        running: 지금 돌고 있는 조사 수.
        max_concurrent: 동시에 돌 수 있는 수.

    Returns:
        `Verdict.OK`면 시작해도 된다.

    ★ 검사 순서에 뜻이 있다 — **횟수 → 동시실행 → 예산**.
      · 횟수가 가장 싸고 가장 흔한 방어라 먼저 본다.
      · 예산을 마지막에 보는 이유는, 데모(공짜)일 때 «아예 안 보기» 위해서다.
        데모까지 예산으로 막으면 돈도 안 드는데 화면이 멈춘다.
    """
    if not rate_ok(history, key, now, window_sec=window_sec, max_runs=max_runs):
        return Verdict.RATE
    if running >= max_concurrent:
        return Verdict.BUSY
    if costs_money and budget_left(ledger, today, cap_krw) <= 0:
        return Verdict.BUDGET
    return Verdict.OK
