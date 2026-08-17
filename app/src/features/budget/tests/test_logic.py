"""돈·횟수 막기의 «판단»을 못 박는다 (문제로그 P-92).

★ 여기서 지키는 것은 하나다 — **인터넷에 올려도 돈이 무제한으로 새지 않는다.**
  `/run`에는 로그인도 횟수 제한도 예산 상한도 없었다. 진짜 조사 모드로 올리면
  아무나 회사 이름만 바꿔가며 건당 82~182원을 계속 쓰게 만들 수 있었다.

★ 이 시험들이 «시각»을 인자로 넣는 이유 — 하루가 바뀌는 것, 10분이 지나는 것을
  **실제로 돌려봐야** 한다. 코드가 안에서 시계를 부르면 그걸 못 본다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.features.budget.logic import (
    Ledger,
    RateHistory,
    Verdict,
    add_spend,
    budget_left,
    decide,
    prune,
    rate_ok,
    record_start,
    rolled_over,
)

_오늘 = dt.date(2026, 8, 16)
_내일 = dt.date(2026, 8, 17)
_상한 = 3000.0


def _판단(**바꿀것) -> Verdict:
    """기본값은 「다 통과」다. 시험마다 «한 가지»만 바꿔 넣는다."""
    기본 = dict(
        ledger=Ledger(day=_오늘, spent_krw=0.0),
        today=_오늘,
        cap_krw=_상한,
        costs_money=True,
        history=RateHistory(),
        key="1.2.3.4",
        now=1000.0,
        window_sec=600,
        max_runs=5,
        running=0,
        max_concurrent=2,
    )
    기본.update(바꿀것)
    return decide(**기본)


# ══════════════════════════════════════════════════════════
# ① 예산 — 다 쓰면 막는다
# ══════════════════════════════════════════════════════════


def test_예산이_남으면_통과한다():
    assert _판단(ledger=Ledger(day=_오늘, spent_krw=2000.0)) is Verdict.OK


def test_예산을_다_쓰면_막는다():
    """★ 이게 안 되면 배포하는 순간 돈이 무제한으로 샌다."""
    assert _판단(ledger=Ledger(day=_오늘, spent_krw=_상한)) is Verdict.BUDGET


def test_예산을_넘겨_썼어도_막는다():
    """동시 실행 때문에 조금 넘길 수 있다 — 넘긴 뒤에도 계속 막혀야 한다."""
    assert _판단(ledger=Ledger(day=_오늘, spent_krw=_상한 + 500)) is Verdict.BUDGET


def test_데모는_예산을_안_본다():
    """★ 반대 방향 — 데모는 0원인데 막으면 «돈도 안 드는 화면»이 멈춘다."""
    verdict = _판단(ledger=Ledger(day=_오늘, spent_krw=99999.0), costs_money=False)

    assert verdict is Verdict.OK


# ══════════════════════════════════════════════════════════
# ② 날짜가 바뀌면 «저절로» 풀린다
# ══════════════════════════════════════════════════════════


def test_날이_바뀌면_예산이_되살아난다():
    """★ 자정에 따로 비우는 장치를 두지 않았다 — 그게 안 돌면 영영 안 풀린다."""
    다_쓴_장부 = Ledger(day=_오늘, spent_krw=_상한)

    assert _판단(ledger=다_쓴_장부, today=_내일) is Verdict.OK


def test_날이_바뀌면_장부가_0원이_된다():
    assert rolled_over(Ledger(day=_오늘, spent_krw=500.0), _내일).spent_krw == 0.0


def test_같은_날이면_장부를_안_건드린다():
    장부 = Ledger(day=_오늘, spent_krw=500.0)

    assert rolled_over(장부, _오늘) is 장부


def test_날이_바뀐_뒤_더하면_새_장부에_쌓인다():
    쌓인 = add_spend(Ledger(day=_오늘, spent_krw=2900.0), _내일, 100.0)

    assert (쌓인.day, 쌓인.spent_krw) == (_내일, 100.0)


@pytest.mark.parametrize("금액", [0.0, -50.0])
def test_0원이나_음수는_안_더한다(금액: float):
    """환불 개념이 없다 — 음수를 받으면 «덜 쓴 것»이 되어 상한이 헐거워진다."""
    assert add_spend(Ledger(day=_오늘, spent_krw=100.0), _오늘, 금액).spent_krw == 100.0


def test_남은_예산은_0_밑으로_안_내려간다():
    assert budget_left(Ledger(day=_오늘, spent_krw=9999.0), _오늘, _상한) == 0.0


# ══════════════════════════════════════════════════════════
# ③ 횟수 — 몰아치면 잠깐 쉬게 한다
# ══════════════════════════════════════════════════════════


def test_창_안에_많이_시작했으면_막는다():
    history = RateHistory()
    for _ in range(5):
        record_start(history, "1.2.3.4", 1000.0)

    assert _판단(history=history) is Verdict.RATE


def test_시간이_지나면_다시_풀린다():
    """★ 영구 차단이 아니다 — 10분이 지나면 저절로 풀려야 한다."""
    history = RateHistory()
    for _ in range(5):
        record_start(history, "1.2.3.4", 1000.0)

    assert _판단(history=history, now=1000.0 + 601) is Verdict.OK


def test_다른_사람은_영향을_안_받는다():
    """★ 한 사람이 몰아쳤다고 «모두»가 막히면 그건 서비스 중단이다."""
    history = RateHistory()
    for _ in range(5):
        record_start(history, "1.2.3.4", 1000.0)

    assert _판단(history=history, key="5.6.7.8") is Verdict.OK


def test_거절당한_요청은_횟수에_안_들어간다():
    """★ 세기(`rate_ok`)와 적기(`record_start`)를 나눈 이유다.

    합쳐 두면 예산 때문에 거절당한 요청까지 횟수를 까먹어,
    「돈도 안 썼는데 차단」이 된다.
    """
    history = RateHistory()

    for _ in range(10):
        rate_ok(history, "1.2.3.4", 1000.0, window_sec=600, max_runs=5)

    assert history.starts.get("1.2.3.4", []) == []


# ══════════════════════════════════════════════════════════
# ④ 기록이 «영원히» 쌓이지 않는다
# ══════════════════════════════════════════════════════════


def test_오래된_기록은_치운다():
    history = RateHistory()
    record_start(history, "1.2.3.4", 1000.0)

    prune(history, 1000.0 + 601, 600)

    assert history.starts == {}


def test_빈_목록도_통째로_치운다():
    """★ 이걸 안 하면 방문한 IP 수만큼 «빈 목록»이 영원히 남는다 — 같은 문제다."""
    history = RateHistory()
    for i in range(100):
        record_start(history, f"10.0.0.{i}", 1000.0)

    prune(history, 1000.0 + 601, 600)

    assert len(history.starts) == 0


def test_아직_안_지난_기록은_남긴다():
    history = RateHistory()
    record_start(history, "1.2.3.4", 1000.0)

    prune(history, 1000.0 + 599, 600)

    assert history.starts["1.2.3.4"] == [1000.0]


# ══════════════════════════════════════════════════════════
# ⑤ 동시 실행 — 예산이 «넘치는 폭»을 묶는다
# ══════════════════════════════════════════════════════════


def test_동시_실행이_꽉_차면_막는다():
    assert _판단(running=2, max_concurrent=2) is Verdict.BUSY


def test_한_자리_남으면_통과한다():
    assert _판단(running=1, max_concurrent=2) is Verdict.OK


def test_데모여도_동시_실행은_막는다():
    """★ 데모는 공짜지만 «메모리»는 먹는다 — 그건 막아야 한다."""
    assert _판단(running=2, max_concurrent=2, costs_money=False) is Verdict.BUSY


# ══════════════════════════════════════════════════════════
# ⑥ 검사 순서 (싼 것부터, 데모를 안 막게)
# ══════════════════════════════════════════════════════════


def test_횟수가_예산보다_먼저다():
    """둘 다 걸릴 때 «횟수»라고 답해야 한다 — 더 싸고 더 흔한 이유다."""
    history = RateHistory()
    for _ in range(5):
        record_start(history, "1.2.3.4", 1000.0)

    verdict = _판단(history=history, ledger=Ledger(day=_오늘, spent_krw=_상한))

    assert verdict is Verdict.RATE
