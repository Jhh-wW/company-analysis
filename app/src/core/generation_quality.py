"""이전 core 경로의 호환 facade. 정본은 shared에 있다."""

from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    SHADOW_ASSESSMENT_MODE,
    UnboundGenerationSection,
    observe_unbound_generation,
    observe_generation,
)

__all__ = [
    "GenerationQualityObservation",
    "SHADOW_ASSESSMENT_MODE",
    "UnboundGenerationSection",
    "observe_unbound_generation",
    "observe_generation",
]
