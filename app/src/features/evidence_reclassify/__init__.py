"""빈 근거 칸을 정확 인용으로 재판정하는 순수 기능 경계."""

from src.features.evidence_reclassify.logic import (
    apply_removals,
    build_reclassify_request,
    parse_and_verify,
    to_typed_fragments,
)
from src.features.evidence_reclassify.models import (
    ReclassifyAssignment,
    ReclassifyDiagnostics,
    ReclassifyRejectedItem,
    ReclassifyRemoval,
    ReclassifyRequest,
    ReclassifyResult,
)

__all__ = (
    "ReclassifyAssignment",
    "ReclassifyDiagnostics",
    "ReclassifyRejectedItem",
    "ReclassifyRemoval",
    "ReclassifyRequest",
    "ReclassifyResult",
    "apply_removals",
    "build_reclassify_request",
    "parse_and_verify",
    "to_typed_fragments",
)
