"""보완조사 진입·출고 진단의 닫힌 실행 어휘."""

from typing import Final

from src.features.pipeline.supplementary_research_release_constants import (
    SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY,
    SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO,
)

SUPPLEMENTARY_RESEARCH_CONTINUE_STEP: Final[str] = "6_수집_보완조사_계속"
SUPPLEMENTARY_RESEARCH_RELEASE_STEP: Final[str] = "8_보완조사_최종검사"
SUPPLEMENTARY_RESEARCH_INVALID_RESULT_CODES: Final[frozenset[str]] = frozenset({
    SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO,
    SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY,
})
