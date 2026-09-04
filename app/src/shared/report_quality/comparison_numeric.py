"""공식 양사 비교 FactRecord의 다중 문서 수치 검산기.

일반 ``NumericBinding``은 피연산자 전체가 한 ``source_identity``를 공유한다.
양사 비교를 그 모양에 억지로 넣으면 비교사 원값의 문서 신원이 사라진다. 이
검산기는 비교 생산기의 닫힌 두 공식만 별도로 재계산하고, 두 Source 결속은
공통 안전 평가기가 그대로 검사하게 둔다.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from src.shared.report_quality.dto import ClaimFact
from src.shared.report_quality.constants import COMPETITIVE_COMPARISON_CLAIM_TYPE
from src.shared.report_quality.comparison_claims import (
    comparison_profitability_claim,
    comparison_scale_claim,
)
from src.shared.report_quality.comparison_evidence import (
    comparison_evidence_amounts,
)


_INTEGER = re.compile(r"^-?[0-9]+$")


def _integers(raw: str, expected: int) -> tuple[int, ...] | None:
    parts = tuple(part.strip() for part in raw.split(";"))
    if len(parts) != expected or any(_INTEGER.fullmatch(part) is None for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _common_problems(fact: ClaimFact) -> list[str]:
    problems: list[str] = []
    if fact.section_owner != "competitive_position":
        problems.append("공식 비교 수치의 소유 장이 9장이 아닙니다")
    if not all(
        (
            fact.comparison_target.strip(),
            fact.comparison_metric.strip(),
            fact.comparison_definition.strip(),
            fact.comparison_basis.strip(),
            fact.comparison_period.strip(),
            fact.comparison_scope.strip(),
            fact.comparison_judgment.strip(),
            fact.comparator_source_id.strip(),
            fact.comparator_state_evidence.strip(),
        )
    ):
        problems.append("공식 비교 수치의 양사 조건·원문 필드가 비었습니다")
    if tuple(fact.supporting_source_ids) != (
        fact.source_id,
        fact.comparator_source_id,
    ):
        problems.append("공식 비교 수치가 자사·비교사 Source를 정확한 순서로 갖지 않습니다")
    try:
        period_start, period_end = fact.comparison_period.split("~", 1)
    except ValueError:
        problems.append("공식 비교 수치의 기간을 시작·종료일로 나눌 수 없습니다")
    else:
        if (fact.period_start, fact.period_end) != (period_start, period_end):
            problems.append("공식 비교 수치의 구조화 기간이 비교 기간과 다릅니다")
    if fact.sign != "positive":
        problems.append("공개 비교 차이·배수의 표시값 부호가 양수가 아닙니다")
    return problems


def _evidence_operand_problems(
    fact: ClaimFact,
    values: tuple[int, ...] | None,
) -> list[str]:
    """raw 원값을 양사 DART의 정확한 계정 행에서 다시 읽어 대조한다."""

    if values is None:
        return []
    self_amounts = comparison_evidence_amounts(
        evidence=fact.state_evidence,
        period=fact.comparison_period,
        definition=fact.comparison_definition,
        scope=fact.comparison_scope,
    )
    comparator_amounts = comparison_evidence_amounts(
        evidence=fact.comparator_state_evidence,
        period=fact.comparison_period,
        definition=fact.comparison_definition,
        scope=fact.comparison_scope,
    )
    if self_amounts is None or comparator_amounts is None:
        return ["공식 비교 원값을 양사 DART의 정확한 계정 행에서 읽을 수 없습니다"]
    if values != (*self_amounts, *comparator_amounts):
        return ["공식 비교 raw 원값이 양사 DART 계정 행의 당기금액과 다릅니다"]
    return []


def _profitability_problems(fact: ClaimFact) -> list[str]:
    values = _integers(fact.raw_value, 4)
    if values is None:
        return ["영업이익률 비교 원값 네 건을 정수로 읽을 수 없습니다"]
    self_revenue, self_operating, other_revenue, other_operating = values
    if self_revenue <= 0 or other_revenue <= 0:
        return ["영업이익률 비교의 양사 매출액이 양수가 아닙니다"]
    try:
        self_margin = (
            Decimal(self_operating) * Decimal(100) / Decimal(self_revenue)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        other_margin = (
            Decimal(other_operating) * Decimal(100) / Decimal(other_revenue)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        signed = self_margin - other_margin
        difference = abs(signed).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ZeroDivisionError):
        return ["영업이익률 비교를 Decimal로 재계산할 수 없습니다"]
    problems: list[str] = _evidence_operand_problems(fact, values)
    if signed == 0:
        problems.append("차이가 없는 영업이익률을 우열 문장으로 공개했습니다")
    direction = "높았다" if signed > 0 else "낮았다"
    expected_claim = comparison_profitability_claim(
        comparison_target=fact.comparison_target,
        difference=f"{difference:.1f}",
        direction=direction,
    )
    expected_display = (
        f"자사 {self_margin:.1f}%; 비교사 {other_margin:.1f}%; "
        f"차이 {difference:.1f}%p"
    )
    expected_checks = (
        f"{self_revenue}|1|0|{self_revenue}",
        f"{self_operating}|1|0|{self_operating}",
        f"{other_revenue}|1|0|{other_revenue}",
        f"{other_operating}|1|0|{other_operating}",
        f"{self_operating}|{Decimal(self_revenue) / Decimal(100)}|1|{self_margin:.1f}",
        f"{other_operating}|{Decimal(other_revenue) / Decimal(100)}|1|{other_margin:.1f}",
        f"{difference:.1f}|1|1|{difference:.1f}",
    )
    expected = {
        "claim": expected_claim,
        "display_value": expected_display,
        "calculation": (
            "양사 영업이익÷매출액×100 후 자사 영업이익률에서 "
            "비교사 영업이익률을 차감"
        ),
        "rounding_rule": "각 영업이익률과 차이를 %·%p 소수 첫째 자리 ROUND_HALF_UP",
        "metric": "영업이익률 차이",
        "unit": "%p",
        "unit_dimension": "percentage_point",
        "formula": "comparison_operating_margin_difference_v1",
        "comparison_scope": "연결재무제표(CFS)",
        "comparison_judgment": (
            "competitive_advantage"
            if signed > 0
            else "operating_characteristic"
        ),
    }
    for field, value in expected.items():
        if getattr(fact, field) != value:
            problems.append(f"영업이익률 비교의 {field}가 재계산 결과와 다릅니다")
    if tuple(fact.numeric_checks) != expected_checks:
        problems.append("영업이익률 비교의 원값 검산 장부가 재계산 결과와 다릅니다")
    return problems


def _scale_problems(fact: ClaimFact) -> list[str]:
    values = _integers(fact.raw_value, 2)
    if values is None:
        return ["매출 규모 비교 원값 두 건을 정수로 읽을 수 없습니다"]
    self_value, other_value = values
    if other_value == 0:
        return ["매출 규모 비교의 비교사 매출액이 0입니다"]
    try:
        ratio = (Decimal(self_value) / Decimal(other_value)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ZeroDivisionError):
        return ["매출 규모 배수를 Decimal로 재계산할 수 없습니다"]
    problems: list[str] = _evidence_operand_problems(fact, values)
    if ratio <= 0:
        problems.append("공개 매출 규모 배수가 양수가 아닙니다")
    ratio_text = f"{ratio:.1f}배"
    expected_claim = comparison_scale_claim(
        comparison_scope=fact.comparison_scope,
        comparison_target=fact.comparison_target,
        ratio_text=ratio_text,
    )
    expected_checks = (
        f"{self_value}|1|0|{self_value}",
        f"{other_value}|1|0|{other_value}",
        f"{self_value}|{other_value}|1|{ratio:.1f}",
    )
    expected = {
        "claim": expected_claim,
        "display_value": ratio_text,
        "calculation": "자사 매출액÷비교사 매출액 = " + ratio_text,
        "rounding_rule": "자사 매출액÷비교사 매출액, 소수 첫째 자리 ROUND_HALF_UP",
        "metric": "매출 규모 배수",
        "unit": "배",
        "unit_dimension": "multiple",
        "formula": "comparison_revenue_ratio_v1",
        "comparison_judgment": "operating_characteristic",
    }
    for field, value in expected.items():
        if getattr(fact, field) != value:
            problems.append(f"매출 규모 비교의 {field}가 재계산 결과와 다릅니다")
    if tuple(fact.numeric_checks) != expected_checks:
        problems.append("매출 규모 비교의 원값 검산 장부가 재계산 결과와 다릅니다")
    return problems


def comparison_numeric_problems(fact: ClaimFact) -> tuple[str, ...] | None:
    """공식 비교기가 만든 수치 claim이면 재검산하고, 아니면 ``None``."""

    if fact.claim_type != COMPETITIVE_COMPARISON_CLAIM_TYPE:
        return None
    problems = _common_problems(fact)
    if fact.comparison_metric == "영업이익률":
        problems.extend(_profitability_problems(fact))
    elif fact.comparison_metric == "매출 규모":
        problems.extend(_scale_problems(fact))
    else:
        problems.append("공식 비교기가 허용하지 않은 비교 수치 축입니다")
    return tuple(dict.fromkeys(problems))


__all__ = ["comparison_numeric_problems"]
