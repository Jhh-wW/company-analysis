"""자료 부족은 AI 전에, 얇은 결과는 제한된 한 번 뒤에 끝내는 정책."""

from __future__ import annotations

from collections.abc import Iterable

from src.features.report_recovery.constants import (
    MAX_SUPPLEMENT_SECTIONS,
    PRIMARY_AI_CALLS,
    SUPPLEMENT_CALLS_PER_SECTION,
    SUPPLEMENT_REVIEW_CALLS,
)
from src.features.report_recovery.models import RecoveryAction, RecoveryDecision
from src.shared.report_evidence.constants import GenerationGateStatus
from src.shared.report_evidence.models import GenerationGateDecision
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


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
        projected_total_ai_calls=PRIMARY_AI_CALLS,
    )


def decide_post_validation(
    *,
    underfilled_section_ids: Iterable[str],
    other_quality_shortfalls: bool,
    safety_problems: bool,
    supplemented_section_ids: Iterable[str] = (),
) -> RecoveryDecision:
    """검증 뒤 공개·한 번 보충·무차감 중 하나만 선택한다.

    안전 문제나 출처 수 부족처럼 다시 글을 써도 해결되지 않는 문제는 더 돈을
    쓰지 않는다. 처음 결과에서 얇은 장이 1~2개일 때만 각 장 한 번과 묶음 검수
    한 번을 허용한다. 보충 뒤에는 어떤 실패든 끝내고 정상 차감하지 않는다.
    """

    underfilled = _unique_sections(underfilled_section_ids, label="얇은 장")
    supplemented = _unique_sections(
        supplemented_section_ids,
        label="이미 보충한 장",
    )
    if len(supplemented) > MAX_SUPPLEMENT_SECTIONS:
        raise ValueError("승인된 장별 보충 횟수 상한을 넘었습니다")
    calls_spent = PRIMARY_AI_CALLS
    if supplemented:
        calls_spent += (
            len(supplemented) * SUPPLEMENT_CALLS_PER_SECTION
            + SUPPLEMENT_REVIEW_CALLS
        )

    if safety_problems:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="post_validation_safety_blocked",
            projected_total_ai_calls=calls_spent,
        )
    if other_quality_shortfalls:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="post_validation_nonrecoverable_quality",
            projected_total_ai_calls=calls_spent,
        )
    if not underfilled:
        return RecoveryDecision(
            action=RecoveryAction.RELEASE_COMPLETE,
            reason_code="post_validation_complete",
            projected_total_ai_calls=calls_spent,
            publish_allowed=True,
            charge_allowed=True,
        )
    if supplemented:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="post_supplement_still_underfilled",
            projected_total_ai_calls=calls_spent,
        )
    if len(underfilled) > MAX_SUPPLEMENT_SECTIONS:
        return RecoveryDecision(
            action=RecoveryAction.STOP_NO_CHARGE,
            reason_code="too_many_underfilled_sections",
            projected_total_ai_calls=calls_spent,
        )

    projected = (
        calls_spent
        + len(underfilled) * SUPPLEMENT_CALLS_PER_SECTION
        + SUPPLEMENT_REVIEW_CALLS
    )
    return RecoveryDecision(
        action=RecoveryAction.RUN_SUPPLEMENTS,
        reason_code="one_recovery_round_allowed",
        supplement_section_ids=underfilled,
        projected_total_ai_calls=projected,
    )
