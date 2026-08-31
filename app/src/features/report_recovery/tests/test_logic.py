from __future__ import annotations

import pytest

from src.features.report_recovery.constants import (
    MAX_TOTAL_AI_CALLS,
    PRIMARY_AI_CALLS,
)
from src.features.report_recovery.logic import (
    decide_post_validation,
    decide_preflight,
)
from src.features.report_recovery.models import RecoveryAction
from src.shared.report_evidence.constants import (
    GenerationGateStatus,
    ReportExecutionOutcome,
)
from src.shared.report_evidence.models import GenerationGateDecision
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


def _gate(status: GenerationGateStatus) -> GenerationGateDecision:
    required = REQUIRED_EVIDENCE_SECTION_IDS
    if status is GenerationGateStatus.READY_FOR_GENERATION:
        ready, insufficient, unknown = required, (), ()
        outcome = None
    elif status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE:
        ready, insufficient, unknown = required[:-1], required[-1:], ()
        outcome = ReportExecutionOutcome.INSUFFICIENT_EVIDENCE
    else:
        ready, insufficient, unknown = required[:-1], (), required[-1:]
        outcome = ReportExecutionOutcome.TRANSIENT_FAILURE
    return GenerationGateDecision(
        company_id="corp-1",
        status=status,
        outcome=outcome,
        required_section_ids=required,
        ready_section_ids=ready,
        insufficient_section_ids=insufficient,
        unknown_section_ids=unknown,
        reason_codes=(),
    )


@pytest.mark.parametrize(
    "status",
    (
        GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE,
        GenerationGateStatus.STOP_TRANSIENT_FAILURE,
    ),
)
def test_자료부족이나_조회장애는_AI전에_무차감중단한다(
    status: GenerationGateStatus,
) -> None:
    decision = decide_preflight(_gate(status))

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.projected_total_ai_calls == 0
    assert not decision.publish_allowed
    assert not decision.charge_allowed


def test_아홉장이_ready일때만_기본10호출을_허용한다() -> None:
    decision = decide_preflight(_gate(GenerationGateStatus.READY_FOR_GENERATION))

    assert decision.action is RecoveryAction.RUN_PRIMARY
    assert decision.projected_total_ai_calls == PRIMARY_AI_CALLS == 10
    assert not decision.charge_allowed


def test_일부장만_ready인_축소게이트로_유료호출을_열수없다() -> None:
    shortened = GenerationGateDecision(
        company_id="corp-1",
        status=GenerationGateStatus.READY_FOR_GENERATION,
        outcome=None,
        required_section_ids=("identity",),
        ready_section_ids=("identity",),
        insufficient_section_ids=(),
        unknown_section_ids=(),
        reason_codes=(),
    )

    with pytest.raises(ValueError, match="필수 아홉 장"):
        decide_preflight(shortened)


@pytest.mark.parametrize(
    ("sections", "expected_calls"),
    [
        (("identity",), 12),
        (("identity", "culture"), 13),
    ],
)
def test_얇은장이_두개이하면_장마다한번과_묶음검수한번만_허용한다(
    sections: tuple[str, ...], expected_calls: int
) -> None:
    decision = decide_post_validation(
        underfilled_section_ids=sections,
        other_quality_shortfalls=False,
        safety_problems=False,
    )

    assert decision.action is RecoveryAction.RUN_SUPPLEMENTS
    assert decision.supplement_section_ids == sections
    assert decision.projected_total_ai_calls == expected_calls
    assert decision.projected_total_ai_calls <= MAX_TOTAL_AI_CALLS == 13
    assert not decision.charge_allowed


def test_얇은장이_세개면_비싼보충을_시작하지않는다() -> None:
    decision = decide_post_validation(
        underfilled_section_ids=("identity", "culture", "portfolio"),
        other_quality_shortfalls=False,
        safety_problems=False,
    )

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.projected_total_ai_calls == 10


def test_안전실패나_출처수부족은_다시써도_고쳐지지않으므로_즉시끝낸다() -> None:
    safety = decide_post_validation(
        underfilled_section_ids=("identity",),
        other_quality_shortfalls=False,
        safety_problems=True,
    )
    quality = decide_post_validation(
        underfilled_section_ids=("identity",),
        other_quality_shortfalls=True,
        safety_problems=False,
    )

    assert safety.action is RecoveryAction.STOP_NO_CHARGE
    assert quality.action is RecoveryAction.STOP_NO_CHARGE
    assert safety.projected_total_ai_calls == quality.projected_total_ai_calls == 10


def test_보충뒤에도_얇으면_두번째보충없이_무차감끝낸다() -> None:
    decision = decide_post_validation(
        underfilled_section_ids=("identity",),
        other_quality_shortfalls=False,
        safety_problems=False,
        supplemented_section_ids=("identity", "culture"),
    )

    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.projected_total_ai_calls == 13
    assert not decision.charge_allowed


def test_검사를_모두통과한_완성본만_공개와_정상차감을_함께허용한다() -> None:
    decision = decide_post_validation(
        underfilled_section_ids=(),
        other_quality_shortfalls=False,
        safety_problems=False,
        supplemented_section_ids=("identity",),
    )

    assert decision.action is RecoveryAction.RELEASE_COMPLETE
    assert decision.projected_total_ai_calls == 12
    assert decision.publish_allowed
    assert decision.charge_allowed


def test_정책밖장이나_중복보충은_결정으로_숨기지않는다() -> None:
    with pytest.raises(ValueError, match="정책 밖"):
        decide_post_validation(
            underfilled_section_ids=("made_up",),
            other_quality_shortfalls=False,
            safety_problems=False,
        )
    with pytest.raises(ValueError, match="같은 장"):
        decide_post_validation(
            underfilled_section_ids=("identity", "identity"),
            other_quality_shortfalls=False,
            safety_problems=False,
        )
    with pytest.raises(ValueError, match="보충 횟수 상한"):
        decide_post_validation(
            underfilled_section_ids=("identity",),
            other_quality_shortfalls=False,
            safety_problems=False,
            supplemented_section_ids=("identity", "culture", "portfolio"),
        )
