"""비용 사업일은 host 시간대와 tzdata 설치 여부에 기대지 않는다."""

from __future__ import annotations

import datetime as dt

from src.core import clock


def test_kst는_utc_host와_무관하게_9시간_앞선다():
    utc = dt.datetime(2026, 8, 17, 15, 0, tzinfo=dt.timezone.utc)

    local = utc.astimezone(clock.KST)

    assert local.isoformat() == "2026-08-18T00:00:00+09:00"
    assert clock.business_day_label(local.date()) == "2026-08-18 (한국시간)"


def test_tzdata가_없어도_고정_kst_fallback을_쓴다(monkeypatch):
    def missing(_name: str):
        raise clock.ZoneInfoNotFoundError("시험용 tzdata 없음")

    monkeypatch.setattr(clock, "ZoneInfo", missing)

    fallback = clock._load_kst()
    aware = dt.datetime(2026, 8, 18, 12, 34, 56, tzinfo=fallback)

    assert str(fallback) == "Asia/Seoul"
    assert aware.utcoffset() == dt.timedelta(hours=9)
    assert aware.isoformat().endswith("+09:00")


def test_iso_now_kst는_offset을_포함한다(monkeypatch):
    fixed = dt.datetime(2026, 8, 18, 23, 59, 59, tzinfo=clock.KST)
    monkeypatch.setattr(clock, "now_kst", lambda: fixed)

    assert clock.today_kst() == dt.date(2026, 8, 18)
    assert clock.iso_now_kst() == "2026-08-18T23:59:59+09:00"


def test_aware_instant와_legacy_naive를_같은_kst_사업일로_해석한다():
    assert clock.business_date_from_iso("2026-08-17T15:00:00+00:00") == dt.date(
        2026, 8, 18
    )
    assert clock.business_date_from_iso("2026-08-18T00:00:00") == dt.date(
        2026, 8, 18
    )
    assert clock.business_date_from_iso("2026-08-18T14:59:59Z") == dt.date(
        2026, 8, 18
    )
