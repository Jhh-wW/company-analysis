"""과제는 확인했지만 대응을 확인하지 못한 행의 공개를 막는다."""

import unicodedata
from collections.abc import Sequence

from src.features.composer.challenge_constants import (
    CHALLENGE_EMPTY_RESPONSES,
    CHALLENGE_RESPONSE_CELL_COUNT,
    CHALLENGE_RESPONSE_CELL_INDEX,
    CHALLENGE_RESPONSE_MISSING,
)


def challenge_response_problem(cells: Sequence[str]) -> str:
    """대응 칸 전체의 빈 값만 검사한다. 실제 대응의 의미 검수는 기존 경로가 맡는다."""
    if isinstance(cells, str) or len(cells) != CHALLENGE_RESPONSE_CELL_COUNT:
        return ""
    response = cells[CHALLENGE_RESPONSE_CELL_INDEX]
    normalized = "".join(unicodedata.normalize("NFKC", response).casefold().split())
    return CHALLENGE_RESPONSE_MISSING if normalized in CHALLENGE_EMPTY_RESPONSES else ""
