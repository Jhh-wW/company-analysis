"""provider 응답에서 원문 없는 구조화 진단만 만든다."""

from __future__ import annotations

from typing import Any

from features.provider_diagnostics.constants import (
    SAFE_STOP_REASONS,
    UNKNOWN_STOP_REASON,
)


def safe_stop_reason(value: object) -> str:
    """알려진 provider 종료 코드만 돌려준다."""

    clean = str(value or "").strip()
    return clean if clean in SAFE_STOP_REASONS else UNKNOWN_STOP_REASON


def build_usage_diagnostic(
    *,
    stop_reason: object,
    output_tokens: int,
    requested_max_tokens: int,
    parse_failed: bool = False,
) -> dict[str, Any]:
    """응답 본문 없이 출력 상한·파싱 상태를 설명하는 필드만 만든다."""

    clean_output = max(0, int(output_tokens))
    clean_limit = max(0, int(requested_max_tokens))
    clean_reason = safe_stop_reason(stop_reason)
    output_limit_reached = bool(
        clean_reason == "max_tokens"
        or (clean_limit > 0 and clean_output >= clean_limit)
    )
    return {
        "stop_reason": clean_reason,
        "requested_max_tokens": clean_limit,
        "output_limit_reached": output_limit_reached,
        "truncation_suspected": bool(output_limit_reached and parse_failed),
        "parse_failed": bool(parse_failed),
    }

