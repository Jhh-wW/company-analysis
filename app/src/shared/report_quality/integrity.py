"""완성 판정 객체가 자기 숫자와 계약을 실제로 지키는지 재검산한다.

생성기는 품질 판정 객체를 만들지만, 출고 경계가 그 객체의 ``grade`` 문자열만
믿으면 내부 배선 실수 하나로 빈 보고서도 ``완성``이라고 꾸밀 수 있다. 이 모듈은
원문을 다시 평가하지 않고도 완성 판정 안의 개수·비율·아홉 장·안전 목록이 선택된
계약과 서로 일치하는지 확인한다.
"""

from __future__ import annotations

from decimal import Decimal

from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
    SafetyAssessment,
)


class AssessmentIntegrityError(ValueError):
    """완성 판정의 닫힌 필드들이 서로 모순된다."""


def _require_nonnegative_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssessmentIntegrityError(f"{label}는 0 이상의 정수여야 합니다")
    return value


def _ordered_counts(
    values: tuple[tuple[str, int], ...],
    *,
    required_section_ids: tuple[str, ...],
    label: str,
) -> tuple[int, ...]:
    if type(values) is not tuple or any(
        type(item) is not tuple
        or len(item) != 2
        or type(item[0]) is not str
        or item[0] != item[0].strip()
        for item in values
    ):
        raise AssessmentIntegrityError(f"{label}의 저장 형식이 손상됐습니다")
    section_ids = tuple(section_id for section_id, _count in values)
    if section_ids != required_section_ids:
        raise AssessmentIntegrityError(
            f"{label}에는 계약 순서의 필수 장이 정확히 한 번씩 필요합니다"
        )
    return tuple(
        _require_nonnegative_int(count, label=f"{label} {section_id}")
        for section_id, count in values
    )


def assert_complete_generation_assessment(
    assessment: GenerationAssessment,
) -> None:
    """공개·차감 후보인 ``완성`` 판정을 계약 수치로 다시 확인한다.

    부분·실패 판정을 완성으로 승격시키는 함수가 아니다. 오직 실제 출고 후보가
    완성이라고 주장할 때만 호출하며, 모순이 하나라도 있으면 예외로 막는다.
    """

    if type(assessment) is not GenerationAssessment:
        raise TypeError("완성 재검산에는 GenerationAssessment가 필요합니다")
    quality = assessment.quality
    safety = assessment.safety
    if type(quality) is not QualityAssessment or type(safety) is not SafetyAssessment:
        raise TypeError("완성 재검산에는 정확한 품질·안전 판정이 필요합니다")
    if any(
        type(value) is not str or value != value.strip() or not value
        for value in (
            assessment.contract_version,
            quality.contract_version,
            safety.contract_version,
        )
    ):
        raise AssessmentIntegrityError("품질·안전 계약 버전 형식이 손상됐습니다")
    contract = contract_for_generation(assessment.contract_version)
    versions = {
        assessment.contract_version,
        quality.contract_version,
        safety.contract_version,
    }
    if versions != {contract.version}:
        raise AssessmentIntegrityError("품질·안전·출고 계약 버전이 서로 다릅니다")
    if (
        quality.grade is not QualityGrade.COMPLETE
        or assessment.publication_grade is not QualityGrade.COMPLETE
        or safety.decision is not ReleaseDecision.RELEASE_ALLOWED
    ):
        raise AssessmentIntegrityError("완성 공개 판정이 아닌 결과는 출고할 수 없습니다")
    if (
        quality.shortfall_reasons
        or quality.problem_codes
        or quality.notice_only_sections
        or quality.one_claim_sections
        or quality.underfilled_sections
        or quality.semantic_underfilled_sections
    ):
        raise AssessmentIntegrityError("완성 판정에 품질 부족 장이나 사유가 남아 있습니다")
    tuple_text_fields = (
        (quality.shortfall_reasons, "품질 부족 사유"),
        (quality.notice_only_sections, "안내문 장"),
        (quality.one_claim_sections, "한 claim 장"),
        (quality.underfilled_sections, "공개 문장 부족 장"),
        (quality.semantic_underfilled_sections, "의미 부족 장"),
        (safety.verified_fact_ids, "검증 fact"),
        (safety.unverified_fact_ids, "미검증 fact"),
        (safety.rejected_fact_ids, "거절 fact"),
        (safety.problems, "안전 문제"),
    )
    for values, label in tuple_text_fields:
        if type(values) is not tuple or any(
            type(value) is not str or value != value.strip() or not value
            for value in values
        ):
            raise AssessmentIntegrityError(f"{label} 저장 형식이 손상됐습니다")
    if type(quality.problem_codes) is not tuple or any(
        type(code) is not QualityProblemCode for code in quality.problem_codes
    ):
        raise AssessmentIntegrityError("품질 문제 코드는 닫힌 enum 목록이어야 합니다")
    if safety.problems or safety.unverified_fact_ids or safety.rejected_fact_ids:
        raise AssessmentIntegrityError("완성 판정에 미검증·거절·안전 문제가 남아 있습니다")

    substantive = _require_nonnegative_int(
        quality.substantive_claims,
        label="실질 claim 수",
    )
    verified = _require_nonnegative_int(
        quality.verified_claims,
        label="검증 claim 수",
    )
    sources = _require_nonnegative_int(
        quality.document_sources,
        label="독립 문서 수",
    )
    if substantive < contract.min_substantive_claims:
        raise AssessmentIntegrityError("완성 판정의 실질 claim 수가 계약 하한보다 적습니다")
    if verified != substantive:
        raise AssessmentIntegrityError("공개 가능 완성본의 모든 claim이 검증되지 않았습니다")
    verified_ids = safety.verified_fact_ids
    if (
        any(not value for value in verified_ids)
        or len(verified_ids) != len(set(verified_ids))
        or len(verified_ids) != verified
    ):
        raise AssessmentIntegrityError("검증 claim 수와 안전 판정의 fact 목록이 다릅니다")
    expected_ratio = Decimal(verified) / Decimal(substantive)
    if (
        type(quality.verified_ratio) is not Decimal
        or not quality.verified_ratio.is_finite()
        or quality.verified_ratio != expected_ratio
    ):
        raise AssessmentIntegrityError("검증 claim 비율이 실제 개수와 다릅니다")
    if quality.verified_ratio < contract.min_verified_ratio:
        raise AssessmentIntegrityError("완성 판정의 검증 비율이 계약 하한보다 낮습니다")
    if sources < contract.min_document_sources:
        raise AssessmentIntegrityError("완성 판정의 독립 문서 수가 계약 하한보다 적습니다")

    semantic_counts = _ordered_counts(
        quality.section_claim_counts,
        required_section_ids=contract.required_section_ids,
        label="장별 의미 claim 수",
    )
    public_counts = _ordered_counts(
        quality.section_public_sentence_counts,
        required_section_ids=contract.required_section_ids,
        label="장별 공개 문장 수",
    )
    if any(
        count < contract.min_claims_per_covered_section
        for count in (*semantic_counts, *public_counts)
    ):
        raise AssessmentIntegrityError("완성 판정에 장별 최소 claim 수 미달이 있습니다")


__all__ = [
    "AssessmentIntegrityError",
    "assert_complete_generation_assessment",
]
