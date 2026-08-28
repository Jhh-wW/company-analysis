"""레거시 출고와 새 품질 계약이 공유하는 versioned 수치 검증 경계.

기존 ``raw|divisor|places|display`` 항목은 발급된 canonical 보고서를 읽기 위한
레거시 계약으로 남는다. 새 ``numeric-binding-v1``을 또 다른 계산기로 복제하지
않고, ``report_quality.numeric.numeric_fact_problems`` 한 구현으로만 검증한다.
"""

from __future__ import annotations

from typing import Protocol

from src.shared.report_quality.dto import ClaimFact
from src.shared.report_quality.numeric import numeric_fact_problems
from src.shared.report_quality.source_identity import document_identity_from_parts


class VersionedNumericRecord(Protocol):
    """FactRecord를 직접 import하지 않는 versioned 수치 투영 계약."""

    fact_id: str
    section_owner: str
    source_id: str
    source_host: str
    source_url: str
    source_document_id: str
    verification_status: str
    status: str
    claim: str
    subject_scope: str
    raw_value: str
    calculation: str
    display_value: str
    rounding_rule: str
    numeric_checks: list[str]
    metric: str
    period_start: str
    period_end: str
    sign: str
    unit: str
    unit_dimension: str
    formula: str


def validate_versioned_numeric_claim(
    fact: ClaimFact,
) -> tuple[str, ...] | None:
    """중립 ClaimFact를 단일 versioned 의미 검증기로 보낸다."""

    return numeric_fact_problems(fact)


def validate_versioned_numeric_record(
    record: VersionedNumericRecord,
) -> tuple[str, ...] | None:
    """기존 FactRecord를 손실 없이 ClaimFact로 투영해 같은 검증기를 쓴다.

    versioned 항목이 없으면 ``None``이다. 호출자는 이 경우에만 발급 당시의
    레거시 numeric_checks 정책을 적용한다.
    """

    source_identity = document_identity_from_parts(
        document_id=str(record.source_document_id or ""),
        host=str(record.source_host or ""),
        url=str(record.source_url or ""),
    )
    verification_state = str(
        record.verification_status or record.status or ""
    ).strip()
    return validate_versioned_numeric_claim(
        ClaimFact(
            fact_id=str(record.fact_id or ""),
            section_owner=str(record.section_owner or ""),
            source_id=str(record.source_id or ""),
            source_identity=source_identity,
            verification_state=verification_state,
            claim=str(record.claim or ""),
            subject_scope=str(record.subject_scope or ""),
            raw_value=str(record.raw_value or ""),
            calculation=str(record.calculation or ""),
            display_value=str(record.display_value or ""),
            rounding_rule=str(record.rounding_rule or ""),
            numeric_checks=tuple(str(value) for value in record.numeric_checks),
            metric=str(getattr(record, "metric", "") or ""),
            period_start=str(getattr(record, "period_start", "") or ""),
            period_end=str(getattr(record, "period_end", "") or ""),
            sign=str(getattr(record, "sign", "") or ""),
            unit=str(getattr(record, "unit", "") or ""),
            unit_dimension=str(getattr(record, "unit_dimension", "") or ""),
            formula=str(getattr(record, "formula", "") or ""),
        )
    )
