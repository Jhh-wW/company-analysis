"""이전 공개 경로를 위한 호환 facade. 정본은 shared에 있다."""

from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)

__all__ = [
    "ClaimFact",
    "ReportCandidate",
    "ReportSectionCandidate",
    "SourceDocument",
]
