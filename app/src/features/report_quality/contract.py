"""이전 공개 경로를 위한 호환 facade. 정본은 shared에 있다."""

from src.shared.report_quality.contract import (
    contract_for_generation,
    contract_for_stored_assessment,
    resolve_contract,
)

__all__ = [
    "contract_for_generation",
    "contract_for_stored_assessment",
    "resolve_contract",
]
