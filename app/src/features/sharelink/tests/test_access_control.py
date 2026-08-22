"""공유 capability GET 보조 limiter의 비식별·고정 메모리 계약."""

from __future__ import annotations

import re

from src.features.sharelink import access_control


def test_requester_identifier_is_stable_irreversible_and_domain_separated() -> None:
    capability = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
    client_ip = "2001:0db8::1"

    identifier = access_control.requester_hash_of(capability, client_ip)

    assert identifier == access_control.requester_hash_of(
        capability.upper(), "2001:db8:0:0:0:0:0:1"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", identifier)
    assert identifier != access_control.requester_hash_of(
        "b1b2c3d4e5f60718a1b2c3d4e5f60718", client_ip
    )
    assert identifier != access_control.requester_hash_of(capability, "203.0.113.7")
    assert capability not in identifier
    assert client_ip not in identifier


def test_limiter_applies_requester_and_capability_caps_atomically() -> None:
    limiter = access_control.AccessLimiter(
        per_requester_limit=2,
        per_capability_limit=3,
        max_entries=8,
    )
    now_iso = "2026-08-23T10:00:00+09:00"
    capability = "capability-secret"

    assert limiter.allow(capability, "203.0.113.1", now_iso)
    assert limiter.allow(capability, "203.0.113.1", now_iso)
    assert not limiter.allow(capability, "203.0.113.1", now_iso)
    assert limiter.allow(capability, "203.0.113.2", now_iso)
    assert not limiter.allow(capability, "203.0.113.2", now_iso)


def test_limiter_memory_is_bounded_and_contains_no_raw_identifiers() -> None:
    limiter = access_control.AccessLimiter(
        per_requester_limit=2,
        per_capability_limit=3,
        max_entries=4,
    )
    raw_values: list[str] = []
    for index in range(20):
        capability = f"raw-capability-{index}"
        client_ip = f"198.51.100.{index + 1}"
        raw_values.extend((capability, client_ip))
        assert limiter.allow(
            capability,
            client_ip,
            f"2026-08-23T10:{index:02d}:00+09:00",
        )
        assert limiter.entry_count <= limiter.max_entries

    memory_projection = repr(limiter._entries)  # noqa: SLF001 — 비밀 비노출 계약
    assert all(raw not in memory_projection for raw in raw_values)
