"""추상적인 노력만인 문장을 구체적인 사업 대응으로 세지 않는다."""

import unicodedata

from src.features.composer.challenge_generic_response_constants import (
    GENERIC_RESPONSE_ONLY,
    GENERIC_RESPONSE_ONLY_RE,
    GENERIC_RESPONSE_SENTENCE_RE,
)


def generic_response_problem(text: str) -> str:
    """문장 전체가 일반 목표·노력뿐일 때만 제외한다. 혼합 활동은 보존한다."""
    compact = "".join(unicodedata.normalize("NFKC", text).split())
    units = tuple(unit for unit in GENERIC_RESPONSE_SENTENCE_RE.split(compact) if unit)
    return (GENERIC_RESPONSE_ONLY if units and all(
        GENERIC_RESPONSE_ONLY_RE.fullmatch(unit) for unit in units
    ) else "")
