from __future__ import annotations

from dataclasses import replace

from src.core.numeric_validation import validate_versioned_numeric_record
from src.features.pipeline.port import FactRecord
from src.features.report_quality.constants import (
    NUMERIC_BINDING_VERSION,
    ROUNDING_MODE,
)
from src.features.report_quality.models import VerificationState
from src.features.report_quality.numeric import (
    EntityScope,
    NumericBinding,
    NumericFormula,
    NumericOperand,
    NumericSign,
    UnitDimension,
    claim_fact_from_binding,
    encode_numeric_check,
)
from src.features.report_standard import publish as publish_module


_DOCUMENT_ID = "20250828000123"
_HOST = "dart.fss.or.kr"
_SOURCE_IDENTITY = f"document:{_HOST}:{_DOCUMENT_ID}"


def _binding() -> NumericBinding:
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
            source_identity=_SOURCE_IDENTITY,
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
            source_identity=_SOURCE_IDENTITY,
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
        source_identity=_SOURCE_IDENTITY,
        verification_state=VerificationState.VERIFIED,
    )


def _record(binding: NumericBinding | None = None) -> FactRecord:
    selected = binding or _binding()
    projected = claim_fact_from_binding(
        fact_id="growth-01",
        section_owner="past_changes",
        source_id="filing-01",
        claim="2024년부터 2025년까지 연결 매출은 25.0% 증가했다.",
        claim_slot="revenue-growth:2024-2025",
        binding=selected,
    )
    return FactRecord(
        fact_id=projected.fact_id,
        section_owner=projected.section_owner,
        source_id=projected.source_id,
        source_host=_HOST,
        source_document_id=_DOCUMENT_ID,
        verification_status=projected.verification_state,
        status=projected.verification_state,
        claim=projected.claim,
        subject_scope=projected.subject_scope,
        raw_value=projected.raw_value,
        calculation=projected.calculation,
        display_value=projected.display_value,
        rounding_rule=projected.rounding_rule,
        numeric_checks=list(projected.numeric_checks),
    )


def test_품질_assessor와_기존_출고가_같은_versioned검증기를_쓴다() -> None:
    record = _record()

    assert validate_versioned_numeric_record(record) == ()
    assert publish_module._numeric_problems(record) == []


def test_versioned_지표변조를_기존_출고도_통과시키지_않는다() -> None:
    record = _record()
    broken = replace(
        _binding(),
        metric="자산",
    )
    mutated = replace(record, numeric_checks=[encode_numeric_check(broken)])

    core_problems = validate_versioned_numeric_record(mutated)
    publish_problems = publish_module._numeric_problems(mutated)

    assert core_problems is not None
    assert any("지표" in problem for problem in core_problems)
    assert any("지표" in problem for problem in publish_problems)


def test_레거시_numeric_checks는_기존_검사로만_읽는다() -> None:
    record = replace(
        _record(),
        raw_value="100",
        calculation="원 단위",
        display_value="100",
        rounding_rule="ROUND_HALF_UP:0",
        numeric_checks=["100|1|0|100"],
    )

    assert validate_versioned_numeric_record(record) is None
