"""보고서 링크가 «두 달 뒤 저절로 닫히는지» 못 박는다 (문제로그 P-93).

★ 왜 기간을 두나 — 링크를 공유 가능하게 열어 둔 대신, 「언제까지」를 정해
  위험이 무한히 쌓이지 않게 한다. 그리고 조사 내용은 시간이 지나면 낡는다.
  반년 전 재무를 「지금 이 회사」처럼 보여주는 것이 더 나쁘다.

★ 이 시험이 «오늘»을 인자로 넣는 이유 — 두 달 뒤를 실제로 돌려봐야 하기 때문이다.
  코드가 안에서 시계를 부르면 그걸 못 본다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.features.budget.expiry import days_left, is_expired, parse_generated_date
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS

_기간 = REPORT_LINK_MAX_AGE_DAYS
_오늘 = dt.date(2026, 8, 16)


def _만든날(일수_전: int) -> str:
    return (_오늘 - dt.timedelta(days=일수_전)).isoformat()


# ══════════════════════════════════════════════════════════
# ① 기간이 지나면 닫힌다
# ══════════════════════════════════════════════════════════


def test_두_달이_지나면_닫힌다():
    """★ P-93 그 자체."""
    assert is_expired(_만든날(_기간 + 1), _오늘, _기간)


def test_딱_두_달째부터_닫힌다():
    assert is_expired(_만든날(_기간), _오늘, _기간)


def test_두_달_하루_전까지는_열린다():
    assert not is_expired(_만든날(_기간 - 1), _오늘, _기간)


def test_오늘_만든_것은_열린다():
    assert not is_expired(_만든날(0), _오늘, _기간)


def test_아주_오래된_것도_닫힌다():
    assert is_expired(_만든날(9999), _오늘, _기간)


# ══════════════════════════════════════════════════════════
# ② 판정을 못 하면 닫는다 (무기한 공개 방지)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "날짜",
    ["", "   ", "날짜아님", "2026-13-45", "20260816", None, 20260816],
)
def test_날짜를_못_읽으면_닫는다(날짜):
    assert is_expired(날짜 or "", _오늘, _기간)


def test_미래_날짜는_닫는다():
    미래 = (_오늘 + dt.timedelta(days=10)).isoformat()

    assert is_expired(미래, _오늘, _기간)


@pytest.mark.parametrize("max_age_days", [0, -1, True])
def test_올바르지_않은_수명은_닫는다(max_age_days):
    assert is_expired(_만든날(0), _오늘, max_age_days)


# ══════════════════════════════════════════════════════════
# ③ 날짜 읽기 — 시각이 붙어 있어도 읽는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "문자열",
    ["2026-08-16", "2026-08-16T09:30:00", "2026-08-16 09:30:00", "2026-08-16T00:00:00+09:00"],
)
def test_시각이_붙어_있어도_날짜를_읽는다(문자열: str):
    assert parse_generated_date(문자열) == dt.date(2026, 8, 16)


def test_못_읽으면_None():
    assert parse_generated_date("어제") is None


def test_UTC_보고서시각은_KST_발급일로_읽는다():
    assert parse_generated_date("2026-08-16T15:30:00Z") == dt.date(
        2026, 8, 17
    )


@pytest.mark.parametrize(
    "문자열", ["20260816", "2026-08-16-깨진뒤꼬리", "2026-08-16T09:30:00깨짐"]
)
def test_앞날짜만_맞고_전체가_깨진_값도_None(문자열):
    assert parse_generated_date(문자열) is None


# ══════════════════════════════════════════════════════════
# ④ 남은 날 — 갑자기 닫히지 않게 미리 알려줄 수 있어야 한다
# ══════════════════════════════════════════════════════════


def test_남은_날을_센다():
    assert days_left(_만든날(10), _오늘, _기간) == _기간 - 10


def test_이미_지났으면_0일():
    assert days_left(_만든날(_기간 + 5), _오늘, _기간) == 0


def test_날짜를_못_읽으면_닫힌_것과_같이_0일():
    assert days_left("", _오늘, _기간) == 0


def test_미래_날짜도_닫힌_것과_같이_0일():
    미래 = (_오늘 + dt.timedelta(days=1)).isoformat()
    assert days_left(미래, _오늘, _기간) == 0


@pytest.mark.parametrize("max_age_days", [0, -1, True])
def test_올바르지_않은_수명은_남은_날도_0일(max_age_days):
    assert days_left(_만든날(0), _오늘, max_age_days) == 0


# ══════════════════════════════════════════════════════════
# ⑤ 기간 값 자체
# ══════════════════════════════════════════════════════════


def test_기간이_두_달쯤이다():
    """★ 값을 바꿀 때 이 시험이 «의도한 변경인지» 되묻는 자리가 된다."""
    assert _기간 == 60
