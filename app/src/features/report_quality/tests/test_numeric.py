from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.features.report_quality.constants import (
    NUMERIC_BINDING_VERSION,
    ROUNDING_MODE,
)
from src.features.report_quality.models import VerificationState
from src.features.report_quality.numeric import (
    NumericBinding,
    EntityScope,
    NumericFormula,
    NumericOperand,
    NumericSign,
    UnitDimension,
    claim_fact_from_binding,
    decode_numeric_check,
    encode_numeric_check,
    numeric_binding_problems,
    numeric_fact_problems,
)


_SOURCE = "document:dart.fss.or.kr:20250828000123"


def _rate_binding() -> NumericBinding:
    operands = (
        NumericOperand(
            role="start",
            metric="매출",
            entity_scope=EntityScope.CONSOLIDATED,
            period="2024",
            value="100",
            sign=NumericSign.POSITIVE,
            unit="억원",
            unit_dimension=UnitDimension.CURRENCY,
            source_identity=_SOURCE,
        ),
        NumericOperand(
            role="end",
            metric="매출",
            entity_scope=EntityScope.CONSOLIDATED,
            period="2025",
            value="125",
            sign=NumericSign.POSITIVE,
            unit="억원",
            unit_dimension=UnitDimension.CURRENCY,
            source_identity=_SOURCE,
        ),
    )
    return NumericBinding(
        version=NUMERIC_BINDING_VERSION,
        metric="매출",
        entity_scope=EntityScope.CONSOLIDATED,
        period_start="2024",
        period_end="2025",
        sign=NumericSign.POSITIVE,
        unit="%",
        unit_dimension=UnitDimension.PERCENT,
        formula=NumericFormula.RATE,
        operands=operands,
        calculated_value="25",
        display_value="25.0",
        rounding_mode=ROUNDING_MODE,
        rounding_places=1,
        tolerance="0",
        source_identity=_SOURCE,
        verification_state=VerificationState.VERIFIED,
    )


def test_versioned_numeric_checks가_모든_이름표를_왕복보존한다() -> None:
    binding = _rate_binding()

    restored = decode_numeric_check(encode_numeric_check(binding))

    assert restored == binding
    assert numeric_binding_problems(restored) == ()


def test_factrecord_투영값과_numericbinding을_함께_검산한다() -> None:
    fact = claim_fact_from_binding(
        fact_id="growth-01",
        section_owner="past_changes",
        source_id="filing-01",
        claim="연결 매출은 해당 기간 증가했다.",
        claim_slot="revenue-growth:2024-2025",
        binding=_rate_binding(),
    )

    assert numeric_fact_problems(fact) == ()


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda item: replace(item, sign=NumericSign.NEGATIVE), "부호"),
        (
            lambda item: replace(
                item,
                unit="명",
                unit_dimension=UnitDimension.COUNT,
            ),
            "단위",
        ),
        (lambda item: replace(item, metric="자산"), "지표"),
        (lambda item: replace(item, period_end="2024"), "기간"),
    ],
)
def test_부호_단위_지표_기간_변조는_통과하지_못한다(mutate, expected: str) -> None:
    broken = mutate(_rate_binding())

    problems = numeric_binding_problems(broken)

    assert any(expected in problem for problem in problems), problems


def test_누적_24_28퍼센트를_연평균_25퍼센트라고_쓸수없다() -> None:
    base = _rate_binding()
    cagr = replace(
        base,
        formula=NumericFormula.CAGR,
        operands=(
            replace(base.operands[0], period="2023", value="100"),
            replace(base.operands[1], period="2025", value="124.28"),
        ),
        period_start="2023",
        period_end="2025",
        period_count="2",
        calculated_value="25",
        display_value="25.00",
        rounding_places=2,
        tolerance="0.0001",
    )

    problems = numeric_binding_problems(cagr)

    assert any("공식 재계산" in problem for problem in problems), problems


def test_음수_기준이나_0교차를_일반_증감률로_검증하지_않는다() -> None:
    base = _rate_binding()
    sign_crossing = replace(
        base,
        operands=(
            replace(
                base.operands[0],
                value="-100",
                sign=NumericSign.NEGATIVE,
            ),
            replace(base.operands[1], value="100"),
        ),
        calculated_value="200",
        display_value="200.0",
    )

    problems = numeric_binding_problems(sign_crossing)

    assert any("시작값은 양수" in problem for problem in problems), problems


def test_큰_허용오차로_틀린_공식을_숨길수없다() -> None:
    broken = replace(
        _rate_binding(),
        calculated_value="999",
        display_value="999.0",
        tolerance="1000",
    )

    problems = numeric_binding_problems(broken)

    assert any("허용오차" in problem for problem in problems), problems


def test_기간을_거꾸로_결속하면_통과하지_못한다() -> None:
    base = _rate_binding()
    reversed_period = replace(
        base,
        period_start="2025",
        period_end="2024",
        operands=(
            replace(base.operands[0], period="2025"),
            replace(base.operands[1], period="2024"),
        ),
    )

    problems = numeric_binding_problems(reversed_period)

    assert any("앞서야" in problem for problem in problems), problems


def test_백분율차이를_그냥_퍼센트_delta로_표시할수없다() -> None:
    base = _rate_binding()
    wrong_delta = replace(
        base,
        formula=NumericFormula.DELTA,
        operands=(
            replace(
                base.operands[0],
                value="10",
                unit="%",
                unit_dimension=UnitDimension.PERCENT,
            ),
            replace(
                base.operands[1],
                value="15",
                unit="%",
                unit_dimension=UnitDimension.PERCENT,
            ),
        ),
        calculated_value="5",
        display_value="5.0",
    )

    problems = numeric_binding_problems(wrong_delta)

    assert any("percentage_point" in problem for problem in problems), problems


def test_반올림_자릿수와_표시문자열_자릿수가_같아야한다() -> None:
    broken = replace(_rate_binding(), display_value="25")

    problems = numeric_binding_problems(broken)

    assert any("소수 자릿수" in problem for problem in problems), problems


def test_numericbinding_JSON은_숫자필드를_문자열로_보존해야한다() -> None:
    encoded = encode_numeric_check(_rate_binding())
    prefix, payload_text = encoded.split(":", 1)
    payload = json.loads(payload_text)
    payload["calculated_value"] = 25
    broken = prefix + ":" + json.dumps(payload, ensure_ascii=False)

    with pytest.raises(ValueError, match="문자열 필드"):
        decode_numeric_check(broken)


def test_numeric_checks에_legacy와_versioned를_섞지_않는다() -> None:
    fact = claim_fact_from_binding(
        fact_id="growth-01",
        section_owner="past_changes",
        source_id="filing-01",
        claim="연결 매출은 해당 기간 증가했다.",
        claim_slot="revenue-growth:2024-2025",
        binding=_rate_binding(),
    )
    mixed = replace(fact, numeric_checks=(*fact.numeric_checks, "100|1|0|100"))

    problems = numeric_fact_problems(mixed)

    assert problems is not None
    assert any("정확히 한 건" in problem for problem in problems)


@pytest.mark.parametrize(
    "broken",
    [
        replace(_rate_binding(), operands=()),
        replace(_rate_binding(), formula=NumericFormula.PEAK, operands=()),
        replace(
            _rate_binding(),
            operands=(replace(_rate_binding().operands[0], role="wrong"),),
        ),
    ],
)
def test_피연산자_role이_깨져도_검사기가_예외로_죽지_않는다(
    broken: NumericBinding,
) -> None:
    problems = numeric_binding_problems(broken)

    assert problems
