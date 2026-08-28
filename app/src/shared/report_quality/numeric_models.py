"""수치 의미 결속의 중립 자료형."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.shared.report_quality.models import VerificationState


class NumericFormula(str, Enum):
    """프로그램이 재계산할 수 있는 닫힌 공식."""

    IDENTITY = "identity"
    DELTA = "delta"
    RATE = "rate"
    CAGR = "cagr"
    MARGIN = "margin"
    PERCENTAGE_POINT = "percentage_point"
    PEAK = "peak"


class NumericSign(str, Enum):
    """문자열의 기호를 잃지 않는 값 부호."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    ZERO = "zero"


class UnitDimension(str, Enum):
    """표시 단위 문자열과 별도로 보존하는 물리·회계 차원."""

    CURRENCY = "currency"
    COUNT = "count"
    PERCENT = "percent"
    PERCENTAGE_POINT = "percentage_point"
    MULTIPLE = "multiple"
    OTHER = "other"


class EntityScope(str, Enum):
    """수치가 어느 회계·법인 범위인지 나타내는 안정 식별자."""

    CONSOLIDATED = "consolidated"
    SEPARATE = "separate"
    ENTITY = "entity"
    SEGMENT = "segment"


@dataclass(frozen=True)
class NumericOperand:
    """공식의 원시 피연산자 한 건과 그 이름표."""

    role: str
    metric: str
    entity_scope: EntityScope
    period: str
    value: str
    sign: NumericSign
    unit: str
    unit_dimension: UnitDimension
    source_identity: str


@dataclass(frozen=True)
class NumericBinding:
    """공개 수치 하나를 원값·공식·표시값·검증 상태까지 결속한다."""

    version: str
    metric: str
    entity_scope: EntityScope
    period_start: str
    period_end: str
    sign: NumericSign
    unit: str
    unit_dimension: UnitDimension
    formula: NumericFormula
    operands: tuple[NumericOperand, ...]
    calculated_value: str
    display_value: str
    rounding_mode: str
    rounding_places: int
    tolerance: str
    source_identity: str
    verification_state: VerificationState
    period_count: str = ""
