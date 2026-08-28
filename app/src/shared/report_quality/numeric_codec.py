"""NumericBinding과 versioned ``numeric_checks`` 문자열의 엄격한 왕복."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Final

from src.shared.report_quality.constants import NUMERIC_CHECK_PREFIX
from src.shared.report_quality.models import VerificationState
from src.shared.report_quality.numeric_models import (
    EntityScope,
    NumericBinding,
    NumericFormula,
    NumericOperand,
    NumericSign,
    UnitDimension,
)


_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "metric",
        "entity_scope",
        "period_start",
        "period_end",
        "sign",
        "unit",
        "unit_dimension",
        "formula",
        "operands",
        "calculated_value",
        "display_value",
        "rounding_mode",
        "rounding_places",
        "tolerance",
        "source_identity",
        "verification_state",
        "period_count",
    }
)
_OPERAND_KEYS: Final[frozenset[str]] = frozenset(
    {
        "role",
        "metric",
        "entity_scope",
        "period",
        "value",
        "sign",
        "unit",
        "unit_dimension",
        "source_identity",
    }
)
_BINDING_STRING_KEYS: Final[frozenset[str]] = _BINDING_KEYS - {
    "operands",
    "rounding_places",
}


def encode_numeric_check(binding: NumericBinding) -> str:
    """공백·키 순서 차이가 없는 저장용 numeric_checks 문자열."""

    payload = asdict(binding)
    return NUMERIC_CHECK_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def is_versioned_numeric_check(value: object) -> bool:
    """현재 versioned numeric check인지 접두사만 안전하게 확인한다."""

    return isinstance(value, str) and value.startswith(NUMERIC_CHECK_PREFIX)


def decode_numeric_check(value: str) -> NumericBinding:
    """저장 문자열을 엄격한 NumericBinding으로 복원한다."""

    if not is_versioned_numeric_check(value):
        raise ValueError("현재 수치 결속 버전의 numeric_checks 항목이 아닙니다")
    try:
        payload = json.loads(value[len(NUMERIC_CHECK_PREFIX) :])
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("수치 결속 JSON을 읽을 수 없습니다") from error
    if not isinstance(payload, dict) or frozenset(payload) != _BINDING_KEYS:
        raise ValueError("수치 결속의 필드가 계약과 정확히 일치하지 않습니다")
    if any(not isinstance(payload[key], str) for key in _BINDING_STRING_KEYS):
        raise ValueError("수치 결속의 문자열 필드 형식이 올바르지 않습니다")
    raw_operands = payload["operands"]
    if not isinstance(raw_operands, list):
        raise ValueError("수치 결속 operands는 목록이어야 합니다")
    operands: list[NumericOperand] = []
    try:
        for raw in raw_operands:
            if not isinstance(raw, dict) or frozenset(raw) != _OPERAND_KEYS:
                raise ValueError("피연산자 필드가 계약과 정확히 일치하지 않습니다")
            if any(not isinstance(raw[key], str) for key in _OPERAND_KEYS):
                raise ValueError("피연산자 필드는 모두 문자열이어야 합니다")
            operands.append(
                NumericOperand(
                    role=str(raw["role"]),
                    metric=str(raw["metric"]),
                    entity_scope=EntityScope(raw["entity_scope"]),
                    period=str(raw["period"]),
                    value=str(raw["value"]),
                    sign=NumericSign(raw["sign"]),
                    unit=str(raw["unit"]),
                    unit_dimension=UnitDimension(raw["unit_dimension"]),
                    source_identity=str(raw["source_identity"]),
                )
            )
        places = payload["rounding_places"]
        if isinstance(places, bool) or not isinstance(places, int):
            raise ValueError("rounding_places는 정수여야 합니다")
        return NumericBinding(
            version=str(payload["version"]),
            metric=str(payload["metric"]),
            entity_scope=EntityScope(payload["entity_scope"]),
            period_start=str(payload["period_start"]),
            period_end=str(payload["period_end"]),
            sign=NumericSign(payload["sign"]),
            unit=str(payload["unit"]),
            unit_dimension=UnitDimension(payload["unit_dimension"]),
            formula=NumericFormula(payload["formula"]),
            operands=tuple(operands),
            calculated_value=str(payload["calculated_value"]),
            display_value=str(payload["display_value"]),
            rounding_mode=str(payload["rounding_mode"]),
            rounding_places=places,
            tolerance=str(payload["tolerance"]),
            source_identity=str(payload["source_identity"]),
            verification_state=VerificationState(payload["verification_state"]),
            period_count=str(payload["period_count"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("수치 결속 값의 형식이 올바르지 않습니다") from error
