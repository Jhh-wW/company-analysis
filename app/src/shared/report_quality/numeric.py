"""뜻이 결속된 versioned ``numeric_checks`` 계약과 결정론적 검산.

기존 ``FactRecord.numeric_checks``는 문자열 목록이므로 저장 스키마를 깨지 않고
새 항목 하나를 ``numeric-binding-v1:{JSON}``으로 보존한다. 이 모듈은 기존
FactRecord를 import하지 않으며, 호출 feature가 ``ClaimFact`` DTO로 투영한다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import (
    Decimal,
    DecimalException,
    InvalidOperation,
    ROUND_HALF_UP,
    localcontext,
)
from typing import Final

from src.shared.report_quality.constants import (
    MAX_FORMULA_TOLERANCE,
    NUMERIC_BINDING_VERSION,
    ROUNDING_MODE,
)
from src.shared.report_quality.dto import ClaimFact
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
    "EntityScope",
    "NumericBinding",
    "NumericFormula",
    "NumericOperand",
    "NumericSign",
    "UnitDimension",
    "claim_fact_from_binding",
    "decode_numeric_check",
    "encode_numeric_check",
    "is_versioned_numeric_check",
    "numeric_binding_problems",
    "numeric_fact_problems",
]


_CANONICAL_DECIMAL: Final[re.Pattern[str]] = re.compile(
    r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?$"
)
_YEAR_PERIOD: Final[re.Pattern[str]] = re.compile(r"^(20\d{2})$")
_QUARTER_PERIOD: Final[re.Pattern[str]] = re.compile(r"^(20\d{2})-Q([1-4])$")
_HALF_PERIOD: Final[re.Pattern[str]] = re.compile(r"^(20\d{2})-H([1-2])$")


def _decimal(value: object) -> Decimal | None:
    raw = str(value)
    if len(raw) > 128 or _CANONICAL_DECIMAL.fullmatch(raw) is None:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _sign(value: Decimal) -> NumericSign:
    if value > 0:
        return NumericSign.POSITIVE
    if value < 0:
        return NumericSign.NEGATIVE
    return NumericSign.ZERO


def _roles(binding: NumericBinding) -> dict[str, NumericOperand] | None:
    roles = {operand.role: operand for operand in binding.operands}
    return roles if len(roles) == len(binding.operands) else None


def _period_key(value: str) -> tuple[int, int, int] | None:
    raw = str(value or "").strip()
    if match := _YEAR_PERIOD.fullmatch(raw):
        return int(match.group(1)), 0, 0
    if match := _QUARTER_PERIOD.fullmatch(raw):
        return int(match.group(1)), int(match.group(2)) * 3, 0
    if match := _HALF_PERIOD.fullmatch(raw):
        return int(match.group(1)), int(match.group(2)) * 6, 0
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.year, parsed.month, parsed.day


def _formula_value(binding: NumericBinding) -> tuple[Decimal | None, list[str]]:
    problems: list[str] = []
    roles = _roles(binding)
    if roles is None:
        return None, ["피연산자 role이 중복됐습니다"]
    values = {
        role: _decimal(operand.value)
        for role, operand in roles.items()
    }
    if any(value is None for value in values.values()):
        return None, ["피연산자 값을 Decimal로 계산할 수 없습니다"]

    formula = binding.formula
    required_roles: set[str]
    if formula is NumericFormula.IDENTITY:
        required_roles = {"value"}
    elif formula in {
        NumericFormula.DELTA,
        NumericFormula.RATE,
        NumericFormula.CAGR,
        NumericFormula.PERCENTAGE_POINT,
    }:
        required_roles = {"start", "end"}
    elif formula is NumericFormula.MARGIN:
        required_roles = {"numerator", "denominator"}
    else:
        required_roles = set(roles)
        if not required_roles:
            return None, ["peak 공식에는 피연산자가 하나 이상 필요합니다"]
    if set(roles) != required_roles:
        problems.append(
            f"{formula.value} 공식의 피연산자 role이 정확하지 않습니다"
        )
        return None, problems

    try:
        with localcontext() as context:
            context.prec = 160
            if formula is NumericFormula.IDENTITY:
                return values["value"], problems
            if formula is NumericFormula.DELTA:
                return values["end"] - values["start"], problems
            if formula is NumericFormula.PERCENTAGE_POINT:
                return values["end"] - values["start"], problems
            if formula is NumericFormula.RATE:
                # 손익처럼 음수 기준값이나 0을 가로지르는 값에는 일반적인
                # 「증감률」의 경제적 뜻이 하나로 정해지지 않는다. 예를 들어
                # -100→+100을 200% 성장이라고 쓰면 계산식은 맞아 보여도 독자를
                # 오도한다. 이런 경우는 delta 등 별도 의미 계약이 생기기 전까지
                # VERIFIED rate로 출고하지 않는다.
                if values["start"] <= 0 or values["end"] < 0:
                    return None, [
                        "증감률의 시작값은 양수이고 종료값은 0 이상이어야 합니다"
                    ]
                return (
                    (values["end"] - values["start"])
                    / abs(values["start"])
                    * Decimal(100),
                    problems,
                )
            if formula is NumericFormula.CAGR:
                count = _decimal(binding.period_count)
                if (
                    count is None
                    or count <= 0
                    or values["start"] <= 0
                    or values["end"] < 0
                ):
                    return None, ["CAGR의 기간 수와 시작·종료값이 계산 가능하지 않습니다"]
                result = (
                    (values["end"] / values["start"])
                    ** (Decimal(1) / count)
                    - Decimal(1)
                ) * Decimal(100)
                return result, problems
            if formula is NumericFormula.MARGIN:
                if values["denominator"] == 0:
                    return None, ["마진의 분모가 0이라 계산할 수 없습니다"]
                return (
                    values["numerator"]
                    / values["denominator"]
                    * Decimal(100),
                    problems,
                )
            return max(value for value in values.values() if value is not None), problems
    except (DecimalException, OverflowError, ValueError):
        return None, [f"{formula.value} 공식을 Decimal로 계산할 수 없습니다"]


def _period_problems(binding: NumericBinding, roles: dict[str, NumericOperand]) -> list[str]:
    formula = binding.formula
    if formula is NumericFormula.IDENTITY:
        if "value" not in roles:
            return ["identity 공식의 value 피연산자가 없습니다"]
        valid = (
            binding.period_start
            == binding.period_end
            == roles["value"].period
        )
    elif formula in {
        NumericFormula.DELTA,
        NumericFormula.RATE,
        NumericFormula.CAGR,
        NumericFormula.PERCENTAGE_POINT,
    }:
        if "start" not in roles or "end" not in roles:
            return [f"{formula.value} 공식의 start/end 피연산자가 없습니다"]
        valid = (
            bool(binding.period_start)
            and bool(binding.period_end)
            and binding.period_start != binding.period_end
            and roles["start"].period == binding.period_start
            and roles["end"].period == binding.period_end
        )
    elif formula is NumericFormula.MARGIN:
        if "numerator" not in roles or "denominator" not in roles:
            return ["margin 공식의 numerator/denominator 피연산자가 없습니다"]
        valid = (
            binding.period_start
            == binding.period_end
            == roles["numerator"].period
            == roles["denominator"].period
        )
    else:
        periods = {operand.period for operand in binding.operands}
        valid = (
            bool(periods)
            and binding.period_start in periods
            and binding.period_end in periods
        )
    problems = [] if valid else ["기간과 공식 피연산자의 위치가 일치하지 않습니다"]
    period_values = [binding.period_start, binding.period_end]
    period_values.extend(operand.period for operand in binding.operands)
    if any(_period_key(value) is None for value in period_values):
        problems.append("기간은 YYYY·YYYY-Qn·YYYY-Hn·ISO 날짜 중 하나여야 합니다")
    start_key = _period_key(binding.period_start)
    end_key = _period_key(binding.period_end)
    if (
        formula
        in {
            NumericFormula.DELTA,
            NumericFormula.RATE,
            NumericFormula.CAGR,
            NumericFormula.PERCENTAGE_POINT,
        }
        and start_key is not None
        and end_key is not None
        and start_key >= end_key
    ):
        problems.append("시작 기간은 종료 기간보다 앞서야 합니다")
    return problems


def _unit_metric_problems(binding: NumericBinding) -> list[str]:
    problems: list[str] = []
    operands = binding.operands
    common_formulas = {
        NumericFormula.IDENTITY,
        NumericFormula.DELTA,
        NumericFormula.RATE,
        NumericFormula.CAGR,
        NumericFormula.PERCENTAGE_POINT,
        NumericFormula.PEAK,
    }
    if binding.formula in common_formulas and any(
        operand.metric != binding.metric for operand in operands
    ):
        problems.append("결과 지표와 피연산자 지표가 일치하지 않습니다")
    if any(operand.entity_scope != binding.entity_scope for operand in operands):
        problems.append("법인 범위가 피연산자와 일치하지 않습니다")
    if any(operand.source_identity != binding.source_identity for operand in operands):
        problems.append("원문 문서 신원이 피연산자와 일치하지 않습니다")

    if binding.formula in {
        NumericFormula.IDENTITY,
        NumericFormula.DELTA,
        NumericFormula.PEAK,
    }:
        if any(
            operand.unit != binding.unit
            or operand.unit_dimension is not binding.unit_dimension
            for operand in operands
        ):
            problems.append("결과 단위와 피연산자 단위 차원이 일치하지 않습니다")
        if (
            binding.formula is NumericFormula.DELTA
            and any(
                operand.unit_dimension is UnitDimension.PERCENT
                for operand in operands
            )
        ):
            problems.append("백분율 차이는 delta가 아니라 percentage_point 공식이어야 합니다")
    elif binding.formula in {NumericFormula.RATE, NumericFormula.CAGR}:
        source_units = {(operand.unit, operand.unit_dimension) for operand in operands}
        if (
            len(source_units) != 1
            or binding.unit != "%"
            or binding.unit_dimension is not UnitDimension.PERCENT
        ):
            problems.append("증감률/CAGR의 원값 단위 또는 결과 % 단위가 맞지 않습니다")
    elif binding.formula is NumericFormula.PERCENTAGE_POINT:
        if (
            any(
                operand.unit != "%"
                or operand.unit_dimension is not UnitDimension.PERCENT
                for operand in operands
            )
            or binding.unit != "%p"
            or binding.unit_dimension is not UnitDimension.PERCENTAGE_POINT
        ):
            problems.append("%와 %p 단위가 정확히 결속되지 않았습니다")
    else:
        source_units = {(operand.unit, operand.unit_dimension) for operand in operands}
        if (
            len(source_units) != 1
            or binding.unit != "%"
            or binding.unit_dimension is not UnitDimension.PERCENT
        ):
            problems.append("마진의 분자·분모 단위 또는 결과 % 단위가 맞지 않습니다")
    return problems


def numeric_binding_problems(binding: NumericBinding) -> tuple[str, ...]:
    """부호·단위·지표·기간·공식·반올림을 모두 재계산한다."""

    problems: list[str] = []
    if binding.version != NUMERIC_BINDING_VERSION:
        problems.append("지원하지 않는 수치 결속 버전입니다")
    required_text = (
        binding.metric,
        binding.entity_scope,
        binding.period_start,
        binding.period_end,
        binding.unit,
        binding.calculated_value,
        binding.display_value,
        binding.tolerance,
        binding.source_identity,
    )
    if not all(str(value).strip() for value in required_text):
        problems.append("수치 결속의 필수 이름표가 비었습니다")
    if binding.rounding_mode != ROUNDING_MODE:
        problems.append("반올림 방식은 ROUND_HALF_UP이어야 합니다")
    if not 0 <= binding.rounding_places <= 12:
        problems.append("반올림 자릿수는 0~12여야 합니다")

    calculated = _decimal(binding.calculated_value)
    display = _decimal(binding.display_value)
    tolerance = _decimal(binding.tolerance)
    if calculated is None or display is None or tolerance is None or tolerance < 0:
        problems.append("계산값·표시값·허용오차를 Decimal로 읽을 수 없습니다")
    elif tolerance > MAX_FORMULA_TOLERANCE:
        problems.append("허용오차가 공식 계약의 최대값을 넘었습니다")
    if binding.formula is NumericFormula.CAGR:
        if not binding.period_count.strip():
            problems.append("CAGR에는 기간 수가 필요합니다")
    elif binding.period_count.strip():
        problems.append("CAGR가 아닌 공식에는 period_count를 둘 수 없습니다")
    if not binding.source_identity.startswith(("document:", "url:")):
        problems.append("원문 문서 identity 형식이 올바르지 않습니다")

    for operand in binding.operands:
        value = _decimal(operand.value)
        if value is None:
            problems.append(f"{operand.role or '<빈 role>'} 피연산자 값이 유효하지 않습니다")
        elif _sign(value) is not operand.sign:
            problems.append(f"{operand.role} 피연산자의 부호 이름표가 값과 다릅니다")
        if not all(
            str(item).strip()
            for item in (
                operand.role,
                operand.metric,
                operand.entity_scope,
                operand.period,
                operand.unit,
                operand.source_identity,
            )
        ):
            problems.append("피연산자의 필수 이름표가 비었습니다")

    roles = _roles(binding)
    if roles is None:
        problems.append("피연산자 role이 중복됐습니다")
    else:
        problems.extend(_period_problems(binding, roles))
    problems.extend(_unit_metric_problems(binding))

    expected, formula_problems = _formula_value(binding)
    problems.extend(formula_problems)
    if calculated is not None and _sign(calculated) is not binding.sign:
        problems.append("결과 부호 이름표가 계산값과 다릅니다")
    if (
        expected is not None
        and calculated is not None
        and tolerance is not None
        and abs(expected - calculated) > tolerance
    ):
        problems.append("저장 계산값이 원시 피연산자와 공식 재계산 결과에 맞지 않습니다")
    if calculated is not None and display is not None and 0 <= binding.rounding_places <= 12:
        try:
            with localcontext() as context:
                context.prec = 160
                quantum = Decimal(1).scaleb(-binding.rounding_places)
                rounded = calculated.quantize(quantum, rounding=ROUND_HALF_UP)
        except DecimalException:
            problems.append("표시값을 ROUND_HALF_UP으로 계산할 수 없습니다")
        else:
            if display != rounded:
                problems.append("표시값이 ROUND_HALF_UP 재계산 결과와 다릅니다")
        decimal_places = (
            len(binding.display_value.split(".", 1)[1])
            if "." in binding.display_value
            else 0
        )
        if decimal_places != binding.rounding_places:
            problems.append("표시값의 소수 자릿수가 반올림 계약과 다릅니다")
    return tuple(dict.fromkeys(problems))


def numeric_fact_problems(fact: ClaimFact) -> tuple[str, ...] | None:
    """ClaimFact의 versioned numeric_checks를 외부 필드와 함께 검증한다.

    versioned 항목이 없으면 ``None``을 돌려 호출자가 legacy 정책을 적용하게 한다.
    새 생성 계약에서는 숫자가 있는 claim에 ``None``이 나오면 안전 차단한다.
    """

    versioned = [
        value for value in fact.numeric_checks if is_versioned_numeric_check(value)
    ]
    if not versioned:
        return None
    problems: list[str] = []
    if len(versioned) != 1 or len(fact.numeric_checks) != 1:
        return ("versioned numeric_checks는 원자 claim마다 정확히 한 건이어야 합니다",)
    try:
        binding = decode_numeric_check(versioned[0])
    except ValueError as error:
        return (str(error),)
    problems.extend(numeric_binding_problems(binding))
    if fact.subject_scope.strip() != binding.entity_scope.value:
        problems.append("FactRecord 법인 범위가 수치 결속과 다릅니다")
    if fact.source_identity.strip() != binding.source_identity.strip():
        problems.append("FactRecord 원문 문서 신원이 수치 결속과 다릅니다")
    if fact.verification_state.strip() != binding.verification_state.value:
        problems.append("FactRecord 검증 상태가 수치 결속과 다릅니다")
    expected_raw = " | ".join(
        f"{operand.role}={operand.value}" for operand in binding.operands
    )
    if fact.raw_value.strip() != expected_raw:
        problems.append("FactRecord 원시값 장부가 수치 결속 피연산자와 다릅니다")
    if fact.calculation.strip() != binding.formula.value:
        problems.append("FactRecord 계산식 이름이 수치 결속 공식과 다릅니다")
    if fact.display_value.strip() != binding.display_value.strip():
        problems.append("FactRecord 표시값이 수치 결속 표시값과 다릅니다")
    expected_rounding = f"{binding.rounding_mode}:{binding.rounding_places}"
    if fact.rounding_rule.strip() != expected_rounding:
        problems.append("FactRecord 반올림 규칙이 수치 결속과 다릅니다")
    structured_values = {
        "metric": binding.metric,
        "period_start": binding.period_start,
        "period_end": binding.period_end,
        "sign": binding.sign.value,
        "unit": binding.unit,
        "unit_dimension": binding.unit_dimension.value,
        "formula": binding.formula.value,
    }
    actual_structured = {
        name: getattr(fact, name).strip() for name in structured_values
    }
    # 이미 발급된 versioned fact에는 이 additive 필드들이 없다. 전부 빈 옛
    # 레코드는 기존 결속만 검증하고, 하나라도 새 필드가 있으면 부분 누락까지
    # 엄격히 잡는다. 새 생성 assessor는 별도로 전 필드 존재를 요구한다.
    if any(actual_structured.values()):
        for name, expected in structured_values.items():
            if actual_structured[name] != expected:
                problems.append(f"FactRecord {name} 이름표가 수치 결속과 다릅니다")
    return tuple(dict.fromkeys(problems))


def claim_fact_from_binding(
    *,
    fact_id: str,
    section_owner: str,
    source_id: str,
    claim: str,
    claim_slot: str,
    binding: NumericBinding,
) -> ClaimFact:
    """호출 feature가 FactRecord를 만들기 전 사용할 손실 없는 DTO 조립기."""

    return ClaimFact(
        fact_id=fact_id,
        section_owner=section_owner,
        source_id=source_id,
        source_identity=binding.source_identity,
        verification_state=binding.verification_state.value,
        claim_slot=claim_slot,
        claim=claim,
        subject_scope=binding.entity_scope.value,
        raw_value=" | ".join(
            f"{operand.role}={operand.value}" for operand in binding.operands
        ),
        calculation=binding.formula.value,
        display_value=binding.display_value,
        rounding_rule=f"{binding.rounding_mode}:{binding.rounding_places}",
        numeric_checks=(encode_numeric_check(binding),),
        metric=binding.metric,
        period_start=binding.period_start,
        period_end=binding.period_end,
        sign=binding.sign.value,
        unit=binding.unit,
        unit_dimension=binding.unit_dimension.value,
        formula=binding.formula.value,
    )
