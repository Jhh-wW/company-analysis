"""열쇠 링크의 «판단»을 못 박는다 (문제로그 P-94).

★ 이 시험이 지키는 것 — **링크마다 예산이 따로 센다.**
  한 회사 인사팀이 하루치를 다 써도 **다른 회사 링크는 멀쩡히 돈다.**
  그게 「전체 하나」가 아니라 「링크당」을 고른 이유다 (2026-08-16 사용자 결정).

⚠️ **전체 상한은 없다** (사용자 결정). 그러므로 최악의 하루 지출은
  `3,000원 × 살아 있는 링크 수`다. 이 시험은 그 사실을 «바꾸지» 않는다 —
  링크별로 정확히 나뉘는지만 확인한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.features.sharelink.constants import (
    PER_LINK_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
)
from src.features.sharelink.logic import (
    DailySpend,
    add_spend,
    budget_left,
    can_start_new_run,
    is_valid_key,
    rolled_over,
    spent_for,
    total_spent,
)

_오늘 = dt.date(2026, 8, 16)
_내일 = dt.date(2026, 8, 17)
_상한 = PER_LINK_DAILY_BUDGET_KRW
_카카오 = "a1b2c3d4e5f60718"
_네이버 = "0f1e2d3c4b5a6978"


# ══════════════════════════════════════════════════════════
# ① 링크마다 «따로» 센다 — 이 방식의 핵심
# ══════════════════════════════════════════════════════════


def test_한_링크가_다_써도_다른_링크는_돈다():
    """★ P-94의 핵심. 이게 안 되면 「링크당」을 고른 의미가 없다."""
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, _상한)

    assert not can_start_new_run(장부, _카카오, _오늘, _상한)
    assert can_start_new_run(장부, _네이버, _오늘, _상한)


def test_링크별로_쓴_돈이_안_섞인다():
    장부 = DailySpend(day=_오늘)
    장부 = add_spend(장부, _카카오, _오늘, 500.0)
    장부 = add_spend(장부, _네이버, _오늘, 200.0)

    assert spent_for(장부, _카카오, _오늘) == 500.0
    assert spent_for(장부, _네이버, _오늘) == 200.0


def test_열쇠_없는_손님도_같은_상한을_받는다():
    """★ 안 걸면 「열쇠 없이 들어오는 길」이 상한 없는 구멍이 된다."""
    장부 = add_spend(DailySpend(day=_오늘), PUBLIC_BUCKET, _오늘, _상한)

    assert not can_start_new_run(장부, PUBLIC_BUCKET, _오늘, _상한)


# ══════════════════════════════════════════════════════════
# ② 날짜가 바뀌면 «저절로» 풀린다
# ══════════════════════════════════════════════════════════


def test_날이_바뀌면_모든_링크가_되살아난다():
    장부 = DailySpend(day=_오늘)
    장부 = add_spend(장부, _카카오, _오늘, _상한)
    장부 = add_spend(장부, _네이버, _오늘, _상한)

    assert can_start_new_run(장부, _카카오, _내일, _상한)
    assert can_start_new_run(장부, _네이버, _내일, _상한)


def test_날이_바뀌면_장부가_통째로_비워진다():
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, 500.0)

    assert rolled_over(장부, _내일).by_key == {}


def test_같은_날이면_장부를_안_건드린다():
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, 500.0)

    assert rolled_over(장부, _오늘) is 장부


# ══════════════════════════════════════════════════════════
# ③ 예산 셈 (실수 방지)
# ══════════════════════════════════════════════════════════


def test_남은_예산을_센다():
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, 1000.0)

    assert budget_left(장부, _카카오, _오늘, _상한) == _상한 - 1000.0


def test_넘겨_썼어도_0_밑으로_안_내려간다():
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, _상한 + 500)

    assert budget_left(장부, _카카오, _오늘, _상한) == 0.0


@pytest.mark.parametrize("금액", [0.0, -100.0])
def test_0원이나_음수는_안_더한다(금액: float):
    """환불 개념이 없다 — 음수를 받으면 상한이 헐거워진다."""
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, 금액)

    assert 장부.by_key.get(_카카오, 0.0) == 0.0


def test_안_쓴_링크는_상한이_그대로다():
    assert budget_left(DailySpend(day=_오늘), "처음보는열쇠", _오늘, _상한) == _상한


# ══════════════════════════════════════════════════════════
# ④ 전체 합계는 «막지는 않아도 보여야» 한다
# ══════════════════════════════════════════════════════════


def test_오늘_전체_쓴_돈을_합칠_수_있다():
    """★ 상한이 없다는 것과 «안 보여도 된다»는 것은 다른 말이다.

    전체 상한을 두지 않기로 했으므로(사용자 결정), 최악의 지출은
    링크 수에 비례한다 — 그 숫자를 볼 수 없으면 얼마가 나갈지 모르는 채로 둔다.
    """
    장부 = DailySpend(day=_오늘)
    장부 = add_spend(장부, _카카오, _오늘, 1200.0)
    장부 = add_spend(장부, _네이버, _오늘, 800.0)

    assert total_spent(장부, _오늘) == 2000.0


def test_날이_바뀌면_전체_합계도_0이_된다():
    장부 = add_spend(DailySpend(day=_오늘), _카카오, _오늘, 1200.0)

    assert total_spent(장부, _내일) == 0.0


# ══════════════════════════════════════════════════════════
# ⑤ 열쇠 모양 — 아무 글자나 «새 통장»이 되면 안 된다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("열쇠", [_카카오, "0123456789abcdef", "abcdef01"])
def test_제대로_된_열쇠는_받는다(열쇠: str):
    assert is_valid_key(열쇠)


@pytest.mark.parametrize(
    "열쇠",
    [
        "",                       # 빈 값
        "  ",                     # 공백
        "abc",                    # 너무 짧다
        "z" * 16,                 # 16진수가 아니다
        "a" * 65,                 # 너무 길다
        "abc-123-def-4567",       # 기호 섞임
        "../../etc/passwd",       # 경로 장난
        "<script>alert(1)</script>",
    ],
)
def test_이상한_열쇠는_거절한다(열쇠: str):
    """★ 아무 글자나 받아주면 주소창에 타이핑해 «새 통장»을 무한히 만들 수 있다.

    그러면 링크당 상한이 아무 의미가 없어진다.
    """
    assert not is_valid_key(열쇠)


def test_대문자도_받아준다():
    """사람이 QR 대신 손으로 칠 수 있다 — 대소문자로 막으면 억울하다."""
    assert is_valid_key(_카카오.upper())
