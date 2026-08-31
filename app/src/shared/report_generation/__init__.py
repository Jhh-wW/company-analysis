"""보고서 생성 생산부와 저장·release 소비부가 공유하는 중립 계약."""

from src.shared.report_generation.models import (
    GenerationCallLedger,
    GenerationCallRecord,
    GenerationProducerEvidence,
    GenerationRunMetrics,
    assert_canonical_producer_evidence,
)
from src.shared.report_generation.canonical import (
    assert_report_matches_generation_evidence,
    public_content_projection,
)

__all__ = [
    "GenerationCallLedger",
    "GenerationCallRecord",
    "GenerationProducerEvidence",
    "GenerationRunMetrics",
    "assert_canonical_producer_evidence",
    "assert_report_matches_generation_evidence",
    "public_content_projection",
]
