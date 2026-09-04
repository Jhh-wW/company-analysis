"""공식 원문끼리만 비교하는 9장 수집·검증 경계."""

from .logic import (
    CandidateEvidence,
    ComparisonBlockedError,
    ComparisonBuildResult,
    ComparisonSourceConfigurationError,
    ComparisonSourceInternalError,
    ComparisonSourceTransientError,
    OfficialCompanyBundle,
    build_competitive_position,
    discover_candidates,
)

__all__ = [
    "CandidateEvidence",
    "ComparisonBlockedError",
    "ComparisonBuildResult",
    "ComparisonSourceConfigurationError",
    "ComparisonSourceInternalError",
    "ComparisonSourceTransientError",
    "OfficialCompanyBundle",
    "build_competitive_position",
    "discover_candidates",
]
