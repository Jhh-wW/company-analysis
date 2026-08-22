"""원문·프롬프트 없이 span-selection 실패를 설명하는 공통 진단 계약."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Iterable


SAFE_PROVIDER_STOP_REASONS: Final[frozenset[str]] = frozenset(
    {
        "end_turn",
        "max_tokens",
        "pause_turn",
        "refusal",
        "stop_sequence",
        "tool_use",
    }
)
UNKNOWN_PROVIDER_STOP_REASON: Final[str] = "unknown"
MAJORITY_REASON_KEPT: Final[str] = "majority_kept"
MAJORITY_REASON_OUTPUT_LIMIT: Final[str] = "output_limit_suspected"
MAJORITY_REASON_PARSE_FAILURE: Final[str] = "provider_parse_failure"
MAJORITY_REASON_PROVIDER_EMPTY: Final[str] = "all_provider_rounds_empty"
MAJORITY_REASON_ALL_REJECTED: Final[str] = "all_candidates_rejected"
MAJORITY_REASON_NO_CONSENSUS: Final[str] = "no_majority_consensus"
ROUND_REASON_KEPT: Final[str] = "validated_items_kept"
ROUND_REASON_OUTPUT_LIMIT: Final[str] = "output_limit_empty"
ROUND_REASON_PARSE_FAILURE: Final[str] = "provider_parse_failure"
ROUND_REASON_PROVIDER_EMPTY: Final[str] = "provider_empty_items"
ROUND_REASON_ALL_REJECTED: Final[str] = "all_candidates_rejected"


@dataclass(frozen=True)
class SpanSelectionRoundDiagnostic:
    """한 provider 호출과 로컬 검증 결과의 비민감 집계."""

    round_number: int
    requested_max_tokens: int
    output_tokens: int
    provider_stop_reason: str
    output_limit_reached: bool
    parse_failed: bool
    provider_selected: int
    validation_kept: int
    validation_rejected: int
    empty_reason: str


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_stop_reason(value: object) -> str:
    clean = str(value or "").strip()
    return (
        clean
        if clean in SAFE_PROVIDER_STOP_REASONS
        else UNKNOWN_PROVIDER_STOP_REASON
    )


def attach_round_result(
    step: dict[str, Any],
    *,
    requested_max_tokens: int,
    provider_selected: int,
    validation_kept: int,
    validation_rejected: int,
) -> None:
    """단계 행에 원문 없는 라운드 진단을 붙인다."""

    usage = step.get("usage")
    safe_usage = usage if isinstance(usage, dict) else {}
    output_tokens = _nonnegative_int(safe_usage.get("out"))
    token_limit = _nonnegative_int(
        safe_usage.get("requested_max_tokens", requested_max_tokens)
    )
    stop_reason = _safe_stop_reason(safe_usage.get("stop_reason"))
    output_limit_reached = bool(
        safe_usage.get("output_limit_reached") is True
        or stop_reason == "max_tokens"
        or (token_limit > 0 and output_tokens >= token_limit)
    )
    parse_failed = bool(
        safe_usage.get("parse_failed") is True
        or safe_usage.get("error") == "파싱실패"
    )
    selected = _nonnegative_int(provider_selected)
    kept = _nonnegative_int(validation_kept)
    rejected = _nonnegative_int(validation_rejected)
    if kept > 0:
        empty_reason = ROUND_REASON_KEPT
    elif output_limit_reached:
        empty_reason = ROUND_REASON_OUTPUT_LIMIT
    elif parse_failed:
        empty_reason = ROUND_REASON_PARSE_FAILURE
    elif selected == 0:
        empty_reason = ROUND_REASON_PROVIDER_EMPTY
    else:
        empty_reason = ROUND_REASON_ALL_REJECTED
    step["span_selection_diagnostic"] = {
        "requested_max_tokens": token_limit,
        "output_tokens": output_tokens,
        "provider_stop_reason": stop_reason,
        "output_limit_reached": output_limit_reached,
        "parse_failed": parse_failed,
        "provider_selected": selected,
        "validation_kept": kept,
        "validation_rejected": rejected,
        "empty_reason": empty_reason,
    }


def round_diagnostic_from_steps(
    steps: Iterable[dict[str, Any]], *, round_number: int
) -> SpanSelectionRoundDiagnostic:
    """현재 라운드 단계 묶음에서 닫힌 진단 한 건을 복원한다."""

    raw: dict[str, Any] = {}
    for step in steps:
        candidate = step.get("span_selection_diagnostic")
        if isinstance(candidate, dict):
            raw = candidate
            break
    return SpanSelectionRoundDiagnostic(
        round_number=max(1, int(round_number)),
        requested_max_tokens=_nonnegative_int(raw.get("requested_max_tokens")),
        output_tokens=_nonnegative_int(raw.get("output_tokens")),
        provider_stop_reason=_safe_stop_reason(raw.get("provider_stop_reason")),
        output_limit_reached=raw.get("output_limit_reached") is True,
        parse_failed=raw.get("parse_failed") is True,
        provider_selected=_nonnegative_int(raw.get("provider_selected")),
        validation_kept=_nonnegative_int(raw.get("validation_kept")),
        validation_rejected=_nonnegative_int(raw.get("validation_rejected")),
        empty_reason=str(raw.get("empty_reason") or ROUND_REASON_PROVIDER_EMPTY),
    )


def majority_result_reason(
    rounds: Iterable[SpanSelectionRoundDiagnostic], *, majority_kept: int
) -> str:
    """라운드 집계만으로 다수결 결과가 빈 이유를 닫힌 코드로 분류한다."""

    if _nonnegative_int(majority_kept) > 0:
        return MAJORITY_REASON_KEPT
    values = tuple(rounds)
    if values and all(item.validation_kept == 0 for item in values):
        if any(item.output_limit_reached for item in values):
            return MAJORITY_REASON_OUTPUT_LIMIT
        if any(item.parse_failed for item in values):
            return MAJORITY_REASON_PARSE_FAILURE
        if all(item.provider_selected == 0 for item in values):
            return MAJORITY_REASON_PROVIDER_EMPTY
        return MAJORITY_REASON_ALL_REJECTED
    return MAJORITY_REASON_NO_CONSENSUS
