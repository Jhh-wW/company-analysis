"""이전 core 경로의 호환 facade. 정본은 shared에 있다."""

from src.shared.report_quality.numeric_validation import (
    VersionedNumericRecord,
    validate_versioned_numeric_claim,
    validate_versioned_numeric_record,
)

__all__ = [
    "VersionedNumericRecord",
    "validate_versioned_numeric_claim",
    "validate_versioned_numeric_record",
]
