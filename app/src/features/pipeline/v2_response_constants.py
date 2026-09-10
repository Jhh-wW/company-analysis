"""v2 성공 응답 진단에 허용하는 비민감 값만 정의한다."""

from typing import Final

V2_RESPONSE_STEP: Final[str] = "v2_provider_response"
V2_RESPONSE_UNKNOWN: Final[str] = "unknown"
V2_RESPONSE_STAGES: Final[frozenset[str]] = frozenset({
    "v2_compose", "v2_review", "v2_diagram",
})
V2_RESPONSE_STOP_REASONS: Final[frozenset[str]] = frozenset({
    "end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal",
})
