"""보고서 링크가 살아 있는지 «판단»한다 (문제로그 P-93).

★ 여기에는 시계가 없다. **오늘 날짜를 인자로 받는** 순수 함수뿐이다 —
  그래야 「두 달 뒤」를 시험에서 실제로 돌려볼 수 있다.

★ 판정을 못 하는 경우(날짜가 비었거나 깨졌거나 미래일 때)는 **닫힌 것으로 본다.**
  공유 주소는 로그인 없이 열리므로, 저장값이 손상됐다는 이유로 만료가 무기한
  풀려서는 안 된다. 저장값을 바로잡은 뒤 다시 열 수 있다.
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

    ★ 날짜만 있거나 완전한 ISO 시각이 붙은 두 형식을 받는다. 앞 10글자만 잘라
      읽으면 ``2026-08-16-깨짐``도 정상 날짜가 되므로 전체 문자열을 검증한다.
    """
    if not isinstance(generated_at, str):
        return None
    raw = generated_at.strip()
    if len(raw) < 10 or raw[4:5] != "-" or raw[7:8] != "-":
        return None
    try:
        if len(raw) == 10:
            return dt.date.fromisoformat(raw)
        if raw[10:11] not in {"T", " "}:
            return None
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return dt.datetime.fromisoformat(normalized).date()
    except (OverflowError, ValueError):
        return None


def is_expired(generated_at: str, today: dt.date, max_age_days: int) -> bool:
    """이 보고서 링크가 기간이 지났는가.

    Args:
        generated_at: 보고서를 만든 날짜 문자열.
        today: 오늘 날짜.
        max_age_days: 살려 둘 기간(일).

    Returns:
        기간이 지났으면 True.

    ★ 날짜를 못 읽거나 미래이면 **True**(닫힘)를 돌려준다 — 위 모듈 설명 참고.
    ★ 발급일로부터 정확히 ``max_age_days``일째 되는 날부터 닫힌다.
    """
    made = parse_generated_date(generated_at)
    if (
        made is None
        or made > today
        or not isinstance(max_age_days, int)
        or isinstance(max_age_days, bool)
        or max_age_days <= 0
    ):
        return True
    return (today - made).days >= max_age_days


def days_left(generated_at: str, today: dt.date, max_age_days: int) -> int:
    """며칠 더 열리는지. 못 세면 None.

    Args:
        generated_at: 보고서를 만든 날짜 문자열.
        today: 오늘 날짜.
        max_age_days: 살려 둘 기간(일).

    Returns:
        남은 날 수 (0 이상). 이미 지났거나 안전하게 닫힌 값이면 0.

    ★ 화면에 「며칠 남았습니다」를 적기 위한 값이다 —
      갑자기 안 열리는 것보다 미리 알려주는 편이 낫다.
    """
    made = parse_generated_date(generated_at)
    if (
        made is None
        or made > today
        or not isinstance(max_age_days, int)
        or isinstance(max_age_days, bool)
        or max_age_days <= 0
    ):
        return 0
    return max(0, max_age_days - (today - made).days)
