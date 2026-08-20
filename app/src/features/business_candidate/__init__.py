"""회사 통칭을 정식 법인 후보로 바꾸기 위한 안전한 사전 확인 단계."""

from .logic import (
    BusinessCandidate,
    CandidateResolution,
    RawBusinessCandidate,
    ResolutionStatus,
    resolve_candidates,
)

__all__ = [
    "BusinessCandidate",
    "CandidateResolution",
    "RawBusinessCandidate",
    "ResolutionStatus",
    "resolve_candidates",
]
