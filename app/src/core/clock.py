"""애플리케이션의 한국 표준시 기준 시계.

운영체제의 로컬 시간대에 기대면 배포 환경에 따라 일일 예산의 경계가 달라진다.
비용 일자는 항상 Asia/Seoul에서 계산하고, 영속 시각은 offset을 포함해 남긴다.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_kst() -> dt.tzinfo:
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        # Windows/Python installations may not ship an IANA tzdata database.
        # South Korea has used a fixed UTC+09:00 offset since 1988 and has no
        # DST, so this preserves the application's current business-day contract
        # without making tzdata an import-time dependency.
        return dt.timezone(dt.timedelta(hours=9), name="Asia/Seoul")


KST = _load_kst()


def now_kst() -> dt.datetime:
    """현재 시각을 Asia/Seoul aware datetime으로 돌려준다."""
    return dt.datetime.now(KST)


def today_kst() -> dt.date:
    """일일 예산에 쓰는 현재 한국 날짜."""
    return now_kst().date()


def iso_now_kst() -> str:
    """감사·비용 원장에 넣을 offset 포함 한국 시각."""
    return now_kst().isoformat(timespec="seconds")


def business_day_label(day: dt.date) -> str:
    """관리 화면에 시간대까지 숨김없이 표시하는 일자 라벨."""
    return f"{day.isoformat()} (한국시간)"


def business_date_from_iso(value: str) -> dt.date:
    """ISO 시각을 KST 사업일로 바꾼다.

    이전 버전의 offset 없는 관측값은 당시 제품이 의도한 한국 wall-clock으로
    해석한다. 새 값과 offset이 있는 외부 값은 instant를 KST로 변환한다.
    """
    parsed = dt.datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST).date()
