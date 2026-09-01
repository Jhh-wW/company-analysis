"""FULL 보고서의 한 번짜리 보충 승인과 검증 후 결정을 공유한다.

``composer``와 옛 ``features.report_recovery`` facade가 함께 쓰는 순수 계약이다.
기능 간 직접 import를 만들지 않기 위해 정본을 shared에 두며, provider나 렌더러를
알지 않고 결속된 평가 영수증만으로 다음 행동을 결정한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
    canonical_sha256,
    require_sha256,
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


# FULL 근거 패킷: 장별 작가 9회 + 장 경계가 묶인 검수 1회.
PRIMARY_WRITER_CALLS: Final[int] = 9
PRIMARY_REVIEW_CALLS: Final[int] = 1

# 얇은 장 하나마다 한 번만 보충하고, 보충 결과를 한 번에 다시 검수한다.
MAX_SUPPLEMENT_SECTIONS: Final[int] = 2
SUPPLEMENT_CALLS_PER_SECTION: Final[int] = 1
SUPPLEMENT_REVIEW_CALLS: Final[int] = 1

PRIMARY_AI_CALLS: Final[int] = PRIMARY_WRITER_CALLS + PRIMARY_REVIEW_CALLS
MAX_TOTAL_AI_CALLS: Final[int] = (
    PRIMARY_AI_CALLS
    + MAX_SUPPLEMENT_SECTIONS * SUPPLEMENT_CALLS_PER_SECTION
    + SUPPLEMENT_REVIEW_CALLS
)


class RecoveryAction(str, Enum):
    """오케스트레이터가 다음에 할 수 있는 닫힌 행동."""

    STOP_NO_CHARGE = "STOP_NO_CHARGE"
    RUN_PRIMARY = "RUN_PRIMARY"
    RUN_SUPPLEMENTS = "RUN_SUPPLEMENTS"
    RELEASE_COMPLETE = "RELEASE_COMPLETE"


@dataclass(frozen=True)
class SupplementAuthorization:
    """기본 평가가 허용한 정확한 장과 후보에만 유효한 한 번짜리 승인."""

    company_id: str
    base_candidate_sha256: str
    base_receipt_sha256: str
    section_ids: tuple[str, ...]
    authorization_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        company_id = str(self.company_id).strip()
        if not company_id:
            raise ValueError("보충 승인에는 회사 식별자가 필요합니다")
        base_candidate_sha256 = require_sha256(
            self.base_candidate_sha256,
            label="기본 후보 지문",
        )
        base_receipt_sha256 = require_sha256(
            self.base_receipt_sha256,
            label="기본 검증 영수증 지문",
        )
        section_ids = tuple(str(item).strip() for item in self.section_ids)
        if not 1 <= len(section_ids) <= MAX_SUPPLEMENT_SECTIONS:
            raise ValueError("보충 승인은 장 1~2개만 담을 수 있습니다")
        if any(not item for item in section_ids):
            raise ValueError("보충 승인 장 식별자는 비어 있을 수 없습니다")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("같은 장을 두 번 승인할 수 없습니다")
        unknown = set(section_ids) - set(REQUIRED_EVIDENCE_SECTION_IDS)
        if unknown:
            raise ValueError("보충 승인에 정책 밖 장을 넣을 수 없습니다")
        authorization_sha256 = canonical_sha256(
            {
                "version": 1,
                "company_id": company_id,
                "base_candidate_sha256": base_candidate_sha256,
                "base_receipt_sha256": base_receipt_sha256,
                "section_ids": list(section_ids),
            }
        )
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "base_candidate_sha256", base_candidate_sha256)
        object.__setattr__(self, "base_receipt_sha256", base_receipt_sha256)
        object.__setattr__(self, "section_ids", section_ids)
        object.__setattr__(self, "authorization_sha256", authorization_sha256)


@dataclass(frozen=True)
class RecoveryDecision:
    """자료·품질 상태에서 파생된 공개·차감·호출 결정."""

    action: RecoveryAction
    reason_code: str
    observed_total_ai_calls: int = 0
    authorized_additional_ai_calls: int = 0
    supplement_authorization: SupplementAuthorization | None = None
    publish_allowed: bool = False
    charge_allowed: bool = False
    # 품질 때문에 닫은 중단만 이 코드를 싣는다. 최종 게이트가 «품질 하한
    # 미달»을 다른 검증 실패와 구분해 사용자에게 말하려면 이 값이 필요하다.
    quality_problem_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("회복 결정에는 기계 사유 코드가 필요합니다")
        if (
            self.quality_problem_codes
            and self.reason_code not in QUALITY_DERIVED_STOP_REASON_CODES
        ):
            raise ValueError(
                "품질에서 비롯되지 않은 회복 사유에 품질 코드를 실을 수 없습니다"
            )
        if (
            self.observed_total_ai_calls < 0
            or self.authorized_additional_ai_calls < 0
        ):
            raise ValueError("AI 호출 수는 음수가 될 수 없습니다")
        if self.projected_total_ai_calls > MAX_TOTAL_AI_CALLS:
            raise ValueError("보고서 AI 호출 상한을 넘는 결정을 만들 수 없습니다")
        if self.publish_allowed != (
            self.action is RecoveryAction.RELEASE_COMPLETE
        ):
            raise ValueError("완성 공개 행동만 공개를 허용할 수 있습니다")
        if self.charge_allowed != self.publish_allowed:
            raise ValueError("완성 공개가 아닌 결과는 정상 차감을 허용할 수 없습니다")
        if self.action is RecoveryAction.RUN_SUPPLEMENTS:
            if self.supplement_authorization is None:
                raise ValueError("보충 행동에는 결속된 승인이 필요합니다")
            if self.observed_total_ai_calls != PRIMARY_AI_CALLS:
                raise ValueError("보충 승인은 기본 9회 작성·1회 검수 뒤에만 가능합니다")
            expected = (
                len(self.supplement_authorization.section_ids)
                * SUPPLEMENT_CALLS_PER_SECTION
                + SUPPLEMENT_REVIEW_CALLS
            )
            if self.authorized_additional_ai_calls != expected:
                raise ValueError("보충 승인과 추가 AI 호출 수가 다릅니다")
        elif self.supplement_authorization is not None:
            raise ValueError("보충 행동이 아닌 결정에는 보충 승인을 넣을 수 없습니다")
        if self.action is RecoveryAction.RUN_PRIMARY:
            if self.observed_total_ai_calls != 0:
                raise ValueError("기본 생성 전에는 관측된 AI 호출이 없어야 합니다")
            if self.authorized_additional_ai_calls != PRIMARY_AI_CALLS:
                raise ValueError("기본 생성은 9회 작성·1회 검수만 승인할 수 있습니다")
        elif self.action is not RecoveryAction.RUN_SUPPLEMENTS:
            if self.authorized_additional_ai_calls:
                raise ValueError("실행 행동이 아니면 추가 AI 호출을 승인할 수 없습니다")

    @property
    def supplement_section_ids(self) -> tuple[str, ...]:
        if self.supplement_authorization is None:
            return ()
        return self.supplement_authorization.section_ids

    @property
    def projected_total_ai_calls(self) -> int:
        return self.observed_total_ai_calls + self.authorized_additional_ai_calls


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

#: 회복 정책이 «품질» 때문에 닫은 중단 사유. 구조 결속·생산 증거·공개 안전
#: 실패는 여기에 «없다» — 그쪽까지 품질 미달로 뭉뚱그리면 사용자에게 틀린
#: 이유가 나가고, 재시도해야 할 일과 포기해야 할 일이 뒤바뀐다.
QUALITY_DERIVED_STOP_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "too_many_underfilled_sections",
        "post_validation_nonrecoverable_quality",
        "post_supplement_quality_failed",
    }
)


def _quality_codes_of(assessment: GenerationAssessment) -> tuple[str, ...]:
    """평가가 남긴 품질 코드를 문자열 값으로 정규화한다.

    ``QualityProblemCode``는 ``str`` Enum이지만 ``str(member)``는 값이 아니라
    ``"QualityProblemCode.X"``를 준다. 최종 게이트 분류기는 ``.value`` 문자열
    집합과 대조하므로 여기서 값으로 고정해 넘긴다.
    """

    return tuple(code.value for code in assessment.quality.problem_codes)


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
            quality_problem_codes=_quality_codes_of(assessment),
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
    if supplement_receipt.writer_calls != (
        len(authorization.section_ids) * SUPPLEMENT_CALLS_PER_SECTION
    ):
        raise ValueError("보충 작성 호출 수가 승인한 장 수와 다릅니다")
    if supplement_receipt.reviewer_calls != SUPPLEMENT_REVIEW_CALLS:
        raise ValueError("보충 묶음의 실제 검수 1회가 없습니다")
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
    supplement_safety_blocked = _is_safety_blocked(supplement_receipt.assessment)
    return RecoveryDecision(
        action=RecoveryAction.STOP_NO_CHARGE,
        reason_code=(
            "post_supplement_safety_blocked"
            if supplement_safety_blocked
            else "post_supplement_quality_failed"
        ),
        observed_total_ai_calls=observed,
        quality_problem_codes=(
            ()
            if supplement_safety_blocked
            else _quality_codes_of(supplement_receipt.assessment)
        ),
    )


def decide_post_validation(
    primary_receipt: GenerationValidationReceipt,
    *,
    supplement_authorization: SupplementAuthorization | None = None,
    supplement_receipt: GenerationValidationReceipt | None = None,
) -> RecoveryDecision:
    """실제 평가 영수증에서만 공개·한 번 보충·무차감을 결정한다."""

    if type(primary_receipt) is not GenerationValidationReceipt:
        raise TypeError("첫 검증의 결속된 영수증이 필요합니다")
    if primary_receipt.round is not ValidationRound.PRIMARY:
        raise ValueError("첫 영수증은 기본 생성 회차여야 합니다")
    if (
        primary_receipt.writer_calls != PRIMARY_WRITER_CALLS
        or primary_receipt.reviewer_calls != PRIMARY_REVIEW_CALLS
    ):
        raise ValueError("첫 영수증에는 실제 9회 작성·1회 검수가 필요합니다")
    if (supplement_authorization is None) != (supplement_receipt is None):
        raise ValueError("보충 승인과 실제 보충 영수증은 함께 필요합니다")
    if supplement_authorization is not None and (
        type(supplement_authorization) is not SupplementAuthorization
        or type(supplement_receipt) is not GenerationValidationReceipt
    ):
        raise TypeError("보충에는 정확한 승인과 검증 영수증이 필요합니다")
    if supplement_authorization is None or supplement_receipt is None:
        return _decide_first_validation(primary_receipt)
    return _decide_second_validation(
        primary_receipt=primary_receipt,
        authorization=supplement_authorization,
        supplement_receipt=supplement_receipt,
    )


__all__ = [
    "GenerationValidationReceipt",
    "MAX_SUPPLEMENT_SECTIONS",
    "QUALITY_DERIVED_STOP_REASON_CODES",
    "MAX_TOTAL_AI_CALLS",
    "PRIMARY_AI_CALLS",
    "PRIMARY_REVIEW_CALLS",
    "PRIMARY_WRITER_CALLS",
    "RecoveryAction",
    "RecoveryDecision",
    "SUPPLEMENT_CALLS_PER_SECTION",
    "SUPPLEMENT_REVIEW_CALLS",
    "SupplementAuthorization",
    "ValidationRound",
    "decide_post_validation",
    "decide_preflight",
]
