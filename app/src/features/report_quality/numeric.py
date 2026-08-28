"""이전 공개 경로를 위한 호환 facade. 정본은 shared에 있다."""

from src.shared.report_quality.numeric import (
    claim_fact_from_binding,
    numeric_binding_problems,
    numeric_fact_problems,
)
from src.shared.report_quality.numeric_codec import (
    decode_numeric_check,
    encode_numeric_check,
    is_versioned_numeric_check,
)
from src.shared.report_quality.numeric_models import (
    EntityScope,
    NumericBinding,
    NumericFormula,
    NumericOperand,
    NumericSign,
    UnitDimension,
)

__all__ = [
    "claim_fact_from_binding",
    "numeric_binding_problems",
    "numeric_fact_problems",
    "decode_numeric_check",
    "encode_numeric_check",
    "is_versioned_numeric_check",
    "EntityScope",
    "NumericBinding",
    "NumericFormula",
    "NumericOperand",
    "NumericSign",
    "UnitDimension",
]
