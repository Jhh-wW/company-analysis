"""자료 부족은 AI 전에, 얇은 결과는 결속된 한 번 뒤에 끝내는 정책."""

from __future__ import annotations

from collections.abc import Iterable

from src.features.report_recovery.constants import (
    MAX_SUPPLEMENT_SECTIONS,
    PRIMARY_AI_CALLS,
    SUPPLEMENT_CALLS_PER_SECTION,
    SUPPLEMENT_REVIEW_CALLS,
)
from src.features.report_recovery.models import (
    GenerationValidationReceipt,
    RecoveryAction,
    RecoveryDecision,
    SupplementAuthorization,
    ValidationRound,
)
from src.shared.report_evidence.constants import GenerationGateStatus
from src.shared.report_evidence.models import GenerationGateDecision
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
)


_RECOVERABLE_QUALITY_CODES = frozenset(
    {
        QualityProblemCode.TOO_MANY_NOTICE_ONLY_SECTIONS,
        QualityProblemCode.ONE_CLAIM_SECTIONS,
        QualityProblemCode.LOW_SEMANTIC_COVERAGE,
        QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE,
        QualityProblemCode.TOO_FEW_SUBSTANTIVE_CLAIMS,
    }
)
_NONRECOVERABLE_QUALITY_CODES = frozenset(
    {
        QualityProblemCode.LOW_VERIFIED_RATIO,
        QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES,
    }
)


def _unique_sections(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    result = tuple(str(value).strip() for value in values)
    if any(not value for value in result):
        raise ValueError(f"{label}에는 빈 장 식별자를 넣을 수 없습니다")
    if len(result) != len(set(result)):
        raise ValueError(f"{label}에는 같은 장을 두 번 넣을 수 없습니다")
    unknown = sorted(set(result) - set(REQUIRED_EVIDENCE_SECTION_IDS))
    if unknown:
        raise ValueError(f"{label}에 정책 밖 장이 있습니다: {', '.join(unknown)}")
    return result


def _problem_sections(assessment: GenerationAssessment) -> tuple[str, ...]:
    quality = assessment.quality
    groups = (
        _unique_sections(quality.notice_only_sections, label="안내문 장"),
        _unique_sections(quality.one_claim_sections, label="한 claim 장"),
        _unique_sections(quality.underfilled_sections, label="공개 문장 부족 장"),
        _unique_sections(
            quality.semantic_underfilled_sections,
            label="의미 범주 부족 장",
        ),
    )
    unordered = {section_id for group in groups for section_id in group}
    return tuple(
        section_id
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        if section_id in unordered
    )


def _is_complete(assessment: GenerationAssessment) -> bool:
    quality = assessment.quality
    safety = assessment.safety
    return (
        quality.grade is QualityGrade.COMPLETE
        and assessment.publication_grade is QualityGrade.COMPLETE
        and safety.decision is ReleaseDecision.RELEASE_ALLOWED
        and not safety.problems
        and not quality.problem_codes
        and not _problem_sections(assessment)
    )


def _is_safety_blocked(assessment: GenerationAssessment) -> bool:
    return (
        assessment.safety.decision is not ReleaseDecision.RELEASE_ALLOWED
        or bool(assessment.safety.problems)
    )


def _supplement_targets(
    assessment: GenerationAssessment,
) -> tuple[str, ...] | None:
    codes = frozenset(assessment.quality.problem_codes)
    if codes & _NONRECOVERABLE_QUALITY_CODES:
        return None
    if not codes or not codes.issubset(_RECOVERABLE_QUALITY_CODES):
        return None
    targets = _problem_sections(assessment)
    if not targets or len(targets) > MAX_SUPPLEMENT_SECTIONS:
        return None
    return targets


def _authorization_for(
    receipt: GenerationValidationReceipt,
    section_ids: tuple[str, ...],
) -> SupplementAuthorization:
    return SupplementAuthorization(
        company_id=receipt.company_id,
        base_candidate_sha256=receipt.candidate_sha256,
        base_receipt_sha256=receipt.receipt_sha256,
        section_ids=section_ids,
    )


def decide_preflight(gate: GenerationGateDecision) -> RecoveryDecision:
    """9장 근거가 모두 READY일 때만 첫 유료 묶음을 허용한다."""

    if gate.required_section_ids != REQUIRED_EVIDENCE_SECTION_IDS:
        raise ValueError("사전검사에는 정책 순서의 필수 아홉 장이 모두 필요합니다")
    if gate.status is GenerationGateStatus.STOP_TRANSIENT_FAILURE:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="preflight_evidence_unknown",
        )
    if gate.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="preflight_evidence_insufficient",
        )
    if gate.status is not GenerationGateStatus.READY_FOR_GENERATION:
        raise ValueError("알 수 없는 생성 게이트 상태입니다")
    if not gate.can_call_ai:
        raise ValueError("AI 허용 표식과 생성 게이트 상태가 다릅니다")
    return RecoveryDecision(
        action=RecoveryAction.RUN_PRIMARY,
        reason_code="preflight_all_sections_ready",
        authorized_additional_ai_calls=PRIMARY_AI_CALLS,
    )


def _decide_first_validation(
    receipt: GenerationValidationReceipt,
) -> RecoveryDecision:
    observed = receipt.observed_ai_calls
    assessment = receipt.assessment
    if _is_complete(assessment):
        return RecoveryDecision(
            action=RecoveryAction.RELEASE_COMPLETE,
            reason_code="post_validation_complete",
            observed_total_ai_calls=observed,
            publish_allowed=True,
            charge_allowed=True,
        )
    if _is_safety_blocked(assessment):
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="post_validation_safety_blocked",
            observed_total_ai_calls=observed,
        )

    targets = _supplement_targets(assessment)
    if targets is None:
        reason_code = (
            "too_many_underfilled_sections"
            if len(_problem_sections(assessment)) > MAX_SUPPLEMENT_SECTIONS
            else "post_validation_nonrecoverable_quality"
        )
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code=reason_code,
            observed_total_ai_calls=observed,
        )

    authorization = _authorization_for(receipt, targets)
    additional_calls = (
        len(targets) * SUPPLEMENT_CALLS_PER_SECTION
        + SUPPLEMENT_REVIEW_CALLS
    )
    return RecoveryDecision(
        action=RecoveryAction.RUN_SUPPLEMENTS,
        reason_code="one_recovery_round_allowed",
        observed_total_ai_calls=observed,
        authorized_additional_ai_calls=additional_calls,
        supplement_authorization=authorization,
    )


def _validate_supplement_binding(
    *,
    primary_receipt: GenerationValidationReceipt,
    authorization: SupplementAuthorization,
    supplement_receipt: GenerationValidationReceipt,
) -> None:
    expected_targets = _supplement_targets(primary_receipt.assessment)
    if expected_targets is None:
        raise ValueError("기본 평가가 허용하지 않은 보충 영수증입니다")
    expected_authorization = _authorization_for(
        primary_receipt,
        expected_targets,
    )
    if authorization != expected_authorization:
        raise ValueError("보충 승인이 기본 후보·평가·대상 장과 다릅니다")
    if supplement_receipt.round is not ValidationRound.SUPPLEMENT:
        raise ValueError("두 번째 영수증은 보충 회차여야 합니다")
    if supplement_receipt.company_id != primary_receipt.company_id:
        raise ValueError("보충 결과의 회사가 기본 결과와 다릅니다")
    if supplement_receipt.base_receipt_sha256 != primary_receipt.receipt_sha256:
        raise ValueError("보충 결과가 정확한 기본 평가 영수증에 결속되지 않았습니다")
    if supplement_receipt.supplemented_section_ids != authorization.section_ids:
        raise ValueError("실제 보충한 장이 승인한 장과 다릅니다")
    if (
        supplement_receipt.evidence_packet_sha256s
        != primary_receipt.evidence_packet_sha256s
    ):
        raise ValueError("보충 중 근거 꾸러미가 바뀌었습니다")
    base_sections = dict(primary_receipt.section_sha256s)
    result_sections = dict(supplement_receipt.section_sha256s)
    approved = set(authorization.section_ids)
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        changed = base_sections[section_id] != result_sections[section_id]
        if section_id in approved and not changed:
            raise ValueError("승인된 보충 장의 내용 지문이 바뀌지 않았습니다")
        if section_id not in approved and changed:
            raise ValueError("승인하지 않은 장이 보충 중 바뀌었습니다")
    if (
        supplement_receipt.assessment.contract_version
        != primary_receipt.assessment.contract_version
    ):
        raise ValueError("보충 전후 품질 계약 버전이 다릅니다")


def _decide_second_validation(
    *,
    primary_receipt: GenerationValidationReceipt,
    authorization: SupplementAuthorization,
    supplement_receipt: GenerationValidationReceipt,
) -> RecoveryDecision:
    _validate_supplement_binding(
        primary_receipt=primary_receipt,
        authorization=authorization,
        supplement_receipt=supplement_receipt,
    )
    observed = (
        primary_receipt.observed_ai_calls
        + supplement_receipt.observed_ai_calls
    )
    if supplement_receipt.candidate_sha256 == primary_receipt.candidate_sha256:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="supplement_candidate_unchanged",
            observed_total_ai_calls=observed,
        )
    if _is_complete(supplement_receipt.assessment):
        return RecoveryDecision(
            action=RecoveryAction.RELEASE_COMPLETE,
            reason_code="post_supplement_complete",
            observed_total_ai_calls=observed,
            publish_allowed=True,
            charge_allowed=True,
        )
    return RecoveryDecision(
        action=RecoveryAction.STOP_NO_CHARGE,
        reason_code=(
            "post_supplement_safety_blocked"
            if _is_safety_blocked(supplement_receipt.assessment)
            else "post_supplement_quality_failed"
        ),
        observed_total_ai_calls=observed,
    )


def decide_post_validation(
    primary_receipt: GenerationValidationReceipt,
    *,
    supplement_authorization: SupplementAuthorization | None = None,
    supplement_receipt: GenerationValidationReceipt | None = None,
) -> RecoveryDecision:
    """실제 평가 영수증에서만 공개·한 번 보충·무차감을 결정한다.

    호출자가 안전 여부나 보충 완료 여부를 불리언으로 주장할 통로는 없다.
    보충 결과를 낼 때는 첫 결정이 발급한 정확한 승인과 그 승인에 결속된 실제
    호출 영수증을 함께 내야 하며, 두 번째 실패 뒤에는 재보충하지 않는다.
    """

    if not isinstance(primary_receipt, GenerationValidationReceipt):
        raise TypeError("첫 검증의 결속된 영수증이 필요합니다")
    if primary_receipt.round is not ValidationRound.PRIMARY:
        raise ValueError("첫 영수증은 기본 생성 회차여야 합니다")
    if (supplement_authorization is None) != (supplement_receipt is None):
        raise ValueError("보충 승인과 실제 보충 영수증은 함께 필요합니다")
    if supplement_authorization is None or supplement_receipt is None:
        return _decide_first_validation(primary_receipt)
    return _decide_second_validation(
        primary_receipt=primary_receipt,
        authorization=supplement_authorization,
        supplement_receipt=supplement_receipt,
    )
