"""보고서 링크가 살아 있는지 «판단»한다 (문제로그 P-93).

★ 여기에는 시계가 없다. **오늘 날짜를 인자로 받는** 순수 함수뿐이다 —
  그래야 「두 달 뒤」를 시험에서 실제로 돌려볼 수 있다.

★ 판정을 못 하는 경우(날짜가 비었거나 깨졌을 때)는 **살아 있는 것으로 본다.**
  이유 — 여기서 막으면 «멀쩡한 보고서가 안 열리는» 고장이 되고,
  사용자는 그것을 「이 도구는 안 된다」로 읽는다.
  날짜를 못 읽는 것은 우리 쪽 문제이지 사용자 잘못이 아니다.
  ⚠️ 이건 «보안을 느슨하게» 하는 선택이다. 그래도 되는 이유는
    내용이 공개된 공시·뉴스라 민감도가 낮기 때문이다 (sharing.py 참고).
"""

from __future__ import annotations

import datetime as dt
from typing import Optional


def parse_generated_date(generated_at: str) -> Optional[dt.date]:
    """보고서에 적힌 만든 날짜를 날짜로 바꾼다.

    Args:
        generated_at: `Report.generated_at` 문자열. `"2026-08-16"` 또는
            `"2026-08-16T09:30:00"` 같은 모양이 들어온다.

    Returns:
        읽어낸 날짜. 비었거나 못 읽으면 None.

    ★ 앞 10글자만 본다 — 뒤에 시각·시간대가 붙어 있어도 날짜만 필요하다.
    """
    head = (generated_at or "").strip()[:10]
    if not head:
        return None
    try:
        return dt.date.fromisoformat(head)
    except ValueError:
        return None


def is_expired(generated_at: str, today: dt.date, max_age_days: int) -> bool:
    """이 보고서 링크가 기간이 지났는가.

    Args:
        generated_at: 보고서를 만든 날짜 문자열.
        today: 오늘 날짜.
        max_age_days: 살려 둘 기간(일).

    Returns:
        기간이 지났으면 True.

    ★ 날짜를 못 읽으면 **False**(살아 있음)를 돌려준다 — 위 모듈 설명 참고.
    ★ 미래 날짜(시계가 틀린 컴퓨터 등)도 살아 있는 것으로 본다.
    """
    made = parse_generated_date(generated_at)
    if made is None:
        return False
    return (today - made).days > max_age_days


def days_left(generated_at: str, today: dt.date, max_age_days: int) -> Optional[int]:
    """며칠 더 열리는지. 못 세면 None.

    Args:
        generated_at: 보고서를 만든 날짜 문자열.
        today: 오늘 날짜.
        max_age_days: 살려 둘 기간(일).

    Returns:
        남은 날 수 (0 이상). 이미 지났으면 0. 날짜를 못 읽으면 None.

    ★ 화면에 「며칠 남았습니다」를 적기 위한 값이다 —
      갑자기 안 열리는 것보다 미리 알려주는 편이 낫다.
    """
    made = parse_generated_date(generated_at)
    if made is None:
        return None
    return max(0, max_age_days - (today - made).days)
