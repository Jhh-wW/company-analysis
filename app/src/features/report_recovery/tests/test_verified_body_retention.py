"""검증 본문 보존은 완성 승인이나 안전·영수증 결속의 면제가 아니다."""
from dataclasses import replace

import pytest

from src.features.report_recovery.tests.test_logic import (
    _assessment, _primary, _recoverable, _sha256, _supplement,
)
from src.shared.report_recovery import RecoveryAction, decide_post_validation


_RETENTION_REASON = "verified_slots_retained_after_supplement"


def _same_public_result(assessment, *, recorded=True):
    primary = _primary(_recoverable("identity"))
    authorization = decide_post_validation(primary).supplement_authorization
    supplement = _supplement(
        primary, authorization, assessment,
        candidate_sha256=primary.candidate_sha256,
        section_sha256s=primary.section_sha256s,
        section_block_sha256s=primary.section_block_sha256s,
        unchanged_sections=(("identity", _RETENTION_REASON),) if recorded else (),
    )
    return primary, authorization, supplement


@pytest.mark.parametrize("safety_blocked,quality_failed", ((False, True), (True, True), (False, False)))
def test_불변후보는_실제품질과_안전을_구분하면서_계속_완성을_거절한다(safety_blocked, quality_failed):
    original = _recoverable("identity")
    assessment = _assessment(
        problem_codes=original.quality.problem_codes if quality_failed else (),
        underfilled=("identity",) if quality_failed else (),
        safety_blocked=safety_blocked,
    )
    primary, authorization, supplement = _same_public_result(assessment)
    decision = decide_post_validation(
        primary, supplement_authorization=authorization, supplement_receipt=supplement,
    )
    assert decision.action is RecoveryAction.STOP_NO_CHARGE
    assert not decision.publish_allowed and not decision.charge_allowed
    assert decision.authorized_additional_ai_calls == 0
    if quality_failed and not safety_blocked:
        assert decision.reason_code == "post_supplement_quality_failed"
        assert decision.quality_problem_codes == tuple(code.value for code in assessment.quality.problem_codes)
    else:
        assert decision.reason_code == "supplement_candidate_unchanged"
        assert decision.quality_problem_codes == ()


@pytest.mark.parametrize("damage", ("missing_observation", "unapproved_display", "changed_packet", "unapproved_block"))
def test_본문보존관측은_무변경증명과_비대상장_근거꾸러미_봉인검사를_대신하지_않는다(damage):
    primary, authorization, supplement = _same_public_result(
        _recoverable("identity"), recorded=damage != "missing_observation",
    )
    if damage != "missing_observation":
        field, target = {
            "unapproved_display": ("section_sha256s", "culture"),
            "changed_packet": ("evidence_packet_sha256s", "identity"),
            "unapproved_block": ("section_block_sha256s", "culture"),
        }[damage]
        changed = tuple((key, _sha256("변조") if key == target else value)
                        for key, value in getattr(supplement, field))
        supplement = replace(supplement, **{field: changed})
    with pytest.raises(ValueError):
        decide_post_validation(
            primary, supplement_authorization=authorization, supplement_receipt=supplement,
        )
