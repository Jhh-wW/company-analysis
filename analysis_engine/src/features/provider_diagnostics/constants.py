"""provider 구조화 출력 진단에 쓰는 닫힌 상수.

응답 본문이나 provider 오류 문구는 진단에 넣지 않는다. 종료 사유도 아래
허용 목록에 있는 기계 코드만 보존해 파일럿 로그에 민감한 원문이 섞이지 않게 한다.
"""

from __future__ import annotations

from typing import Final


SAFE_STOP_REASONS: Final[frozenset[str]] = frozenset(
    {
        "end_turn",
        "max_tokens",
        "pause_turn",
        "refusal",
        "stop_sequence",
        "tool_use",
    }
)
UNKNOWN_STOP_REASON: Final[str] = "unknown"

