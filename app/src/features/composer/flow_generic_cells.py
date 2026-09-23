"""파서와 도식 검사가 함께 쓰는 일반어 칸 판정."""

import unicodedata

from src.features.composer.diagram_review_constants import (
    FLOW_GENERIC_CELL_PARTICLES, FLOW_GENERIC_CELL_TERMS,
)


def _key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


_GENERIC_KEYS = frozenset(_key(term) for term in FLOW_GENERIC_CELL_TERMS)


def is_generic_flow_cell(value: str) -> bool:
    """전체가 일반어인 칸만 판정한다. 수식어·제품명·빈 칸을 확대 해석하지 않는다."""
    key = _key(value)
    if not key:
        return False
    if key in _GENERIC_KEYS:
        return True
    return any(key.endswith(particle) and key[:-len(particle)] in _GENERIC_KEYS
               for particle in FLOW_GENERIC_CELL_PARTICLES)
