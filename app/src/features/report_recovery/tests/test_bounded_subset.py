"""회복 대상을 제한 선택해도 전체 공개 하한과 한 회차 결속을 유지한다."""
from dataclasses import replace

import pytest

from src.features.report_recovery.tests.test_logic import (
    _assessment, _primary, _recoverable, _sha256, _supplement,
)
from src.shared.report_quality.models import QualityProblemCode
from src.shared.report_recovery import (
    MAX_SUPPLEMENT_SECTIONS, MAX_TOTAL_AI_CALLS, RecoveryAction,
    decide_post_validation,
)


def test_three_targets_prioritize_required_semantic_slots_within_two_calls():
    primary = _primary(_assessment(
        problem_codes=(QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS,
                       QualityProblemCode.LOW_PUBLIC_SENTENCE_COVERAGE),
        underfilled=('current_challenges',),
        semantic_underfilled=('future_strategy', 'business_model'),
    ))
    decision = decide_post_validation(primary)
    assert decision.action is RecoveryAction.RUN_SUPPLEMENTS
    assert decision.supplement_section_ids == ('business_model', 'future_strategy')
    assert decision.authorized_additional_ai_calls == 3
    assert decision.projected_total_ai_calls <= MAX_TOTAL_AI_CALLS
    assert not decision.publish_allowed and not decision.charge_allowed
    assert primary.assessment.quality.underfilled_sections == ('current_challenges',)


def test_more_than_two_semantic_targets_keep_policy_order_and_bound():
    decision = decide_post_validation(_primary(_assessment(
        problem_codes=(QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS,),
        semantic_underfilled=('future_strategy', 'portfolio', 'business_model'),
    )))
    assert decision.supplement_section_ids == ('business_model', 'portfolio')
    assert len(decision.supplement_section_ids) == MAX_SUPPLEMENT_SECTIONS == 2


def test_quantity_only_targets_keep_policy_order_within_bound():
    decision = decide_post_validation(_primary(_recoverable('culture', 'portfolio', 'identity')))
    assert decision.supplement_section_ids == ('identity', 'portfolio')


def test_existing_two_target_order_is_unchanged():
    decision = decide_post_validation(_primary(_assessment(
        problem_codes=(QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS,),
        underfilled=('identity',), semantic_underfilled=('future_strategy',),
    )))
    assert decision.supplement_section_ids == ('identity', 'future_strategy')


def test_semantic_priority_selection_returns_writer_policy_order():
    decision = decide_post_validation(_primary(_assessment(
        problem_codes=(QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS,),
        underfilled=('identity', 'portfolio'), semantic_underfilled=('future_strategy',),
    )))
    assert decision.supplement_section_ids == ('identity', 'future_strategy')


@pytest.mark.parametrize('unsafe', (False, True))
def test_nonrecoverable_or_unsafe_assessment_never_authorizes_subset(unsafe):
    assessment = _assessment(
        problem_codes=(QualityProblemCode.TOO_FEW_DOCUMENT_SOURCES,),
        underfilled=('identity', 'portfolio', 'culture'), safety_blocked=unsafe,
    )
    decision = decide_post_validation(_primary(assessment))
    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert decision.authorized_additional_ai_calls == 0
    assert decision.supplement_authorization is None


def test_remaining_unselected_problem_blocks_full_and_third_round():
    primary = _primary(_assessment(
        problem_codes=(QualityProblemCode.MISSING_REQUIRED_PUBLIC_CLAIM_SLOTS,),
        underfilled=('current_challenges',),
        semantic_underfilled=('business_model', 'future_strategy'),
    ))
    first = decide_post_validation(primary)
    followup = _supplement(primary, first.supplement_authorization, _recoverable('current_challenges'))
    final = decide_post_validation(primary,
        supplement_authorization=first.supplement_authorization, supplement_receipt=followup)
    assert final.action is RecoveryAction.STOP_NO_CHARGE
    assert final.reason_code == 'post_supplement_quality_failed'
    assert not final.publish_allowed and not final.charge_allowed
    assert final.authorized_additional_ai_calls == 0


def test_unselected_section_change_still_invalidates_supplement_receipt():
    primary = _primary(_recoverable('identity', 'portfolio', 'culture'))
    first = decide_post_validation(primary)
    followup = _supplement(primary, first.supplement_authorization, _assessment())
    wrong = replace(followup, section_sha256s=tuple(
        (section, _sha256('unapproved') if section == 'culture' else sha)
        for section, sha in followup.section_sha256s))
    with pytest.raises(ValueError, match='승인하지 않은 장'):
        decide_post_validation(primary,
            supplement_authorization=first.supplement_authorization, supplement_receipt=wrong)
