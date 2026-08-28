"""공유 capability GET 보조 limiter의 개인정보 미수집·고정 메모리 계약."""

from __future__ import annotations

from src.features.sharelink import access_control


def test_limiter_applies_capability_cap_atomically_without_requester_input() -> None:
    limiter = access_control.AccessLimiter(
        per_capability_limit=3,
        max_entries=8,
    )
    now_iso = "2026-08-23T10:00:00+09:00"
    capability = "capability-secret"

    assert limiter.allow(capability, now_iso)
    assert limiter.allow(capability, now_iso)
    assert limiter.allow(capability, now_iso)
    assert not limiter.allow(capability, now_iso)

    # 다음 시간 구간과 다른 링크는 독립 통장이다.
    assert limiter.allow(capability, "2026-08-23T10:01:00+09:00")
    assert limiter.allow("other-capability", now_iso)


def test_limiter_memory_is_bounded_and_contains_no_raw_identifiers() -> None:
    limiter = access_control.AccessLimiter(
        per_capability_limit=3,
        max_entries=4,
    )
    raw_values: list[str] = []
    for index in range(20):
        capability = f"raw-capability-{index}"
        raw_values.append(capability)
        assert limiter.allow(capability, f"2026-08-23T10:{index:02d}:00+09:00")
        assert limiter.entry_count <= limiter.max_entries

    memory_projection = repr(limiter._entries)  # noqa: SLF001 — 비밀 비노출 계약
    assert all(raw not in memory_projection for raw in raw_values)
