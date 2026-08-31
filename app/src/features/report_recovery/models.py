"""호환 facade: 회복 계약의 정본은 ``src.shared.report_recovery``다."""

from src.shared.report_recovery import (
    GenerationValidationReceipt,
    RecoveryAction,
    RecoveryDecision,
    SupplementAuthorization,
    ValidationRound,
)

__all__ = [
    "GenerationValidationReceipt",
    "RecoveryAction",
    "RecoveryDecision",
    "SupplementAuthorization",
    "ValidationRound",
]
