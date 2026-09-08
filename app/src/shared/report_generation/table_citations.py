"""공개 표·도식의 행 순서에 결속된 인용 형식 검사."""

from __future__ import annotations

import re
from collections.abc import Sequence


_CITE_PATTERN = re.compile(r"\[([1-9][0-9]*)\]")


def validated_row_cites(rows: Sequence, row_cites: object) -> tuple[tuple[str, ...], ...]:
    """빈 필드는 옛 표이며, 새 필드는 각 행의 실제 인용을 빠짐없이 갖는다."""
    if not isinstance(row_cites, (list, tuple)):
        raise ValueError("행별 인용은 배열이어야 합니다")
    if not row_cites:
        return ()
    if len(row_cites) != len(rows):
        raise ValueError("행별 인용 개수가 공개 행과 다릅니다")
    result = []
    for row in row_cites:
        if not isinstance(row, (list, tuple)) or not row:
            raise ValueError("공개 행의 인용이 비었거나 배열이 아닙니다")
        if any(type(cite) is not str or not _CITE_PATTERN.fullmatch(cite) for cite in row):
            raise ValueError("행별 인용 번호가 정본 형식이 아닙니다")
        if list(row) != sorted(set(row), key=lambda cite: int(cite[1:-1])):
            raise ValueError("행별 인용은 중복 없이 번호순이어야 합니다")
        result.append(tuple(row))
    return tuple(result)
