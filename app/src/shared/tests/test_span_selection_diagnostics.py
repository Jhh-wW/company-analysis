from __future__ import annotations

from src.shared.span_selection_diagnostics import (
    SAFE_VALIDATION_REJECTION_REASONS,
    attach_round_result,
    majority_result_reason,
    round_diagnostic_from_steps,
)


def _diagnostic_step(*, output: int, limit: int, parse_failed: bool) -> dict:
    step = {
        "usage": {
            "out": output,
            "requested_max_tokens": limit,
            "stop_reason": "max_tokens" if output >= limit else "end_turn",
            "parse_failed": parse_failed,
        }
    }
    attach_round_result(
        step,
        requested_max_tokens=limit,
        provider_selected=0,
        validation_kept=0,
        validation_rejected=0,
    )
    return step


def test_3000토큰_파싱실패는_원문없이_절단의심으로_남긴다() -> None:
    step = _diagnostic_step(output=3000, limit=3000, parse_failed=True)
    diagnostic = round_diagnostic_from_steps([step], round_number=2)

    assert diagnostic.round_number == 2
    assert diagnostic.output_tokens == 3000
    assert diagnostic.requested_max_tokens == 3000
    assert diagnostic.provider_stop_reason == "max_tokens"
    assert diagnostic.output_limit_reached is True
    assert diagnostic.parse_failed is True
    assert diagnostic.empty_reason == "output_limit_empty"
    assert diagnostic.validation_rejection_reason_counts == ()
    assert "response" not in step["span_selection_diagnostic"]
    assert "prompt" not in step["span_selection_diagnostic"]


def test_세_라운드가_상한에서_모두_비면_다수결_원인을_구분한다() -> None:
    rounds = tuple(
        round_diagnostic_from_steps(
            [_diagnostic_step(output=3000, limit=3000, parse_failed=True)],
            round_number=number,
        )
        for number in (1, 2, 3)
    )

    assert majority_result_reason(rounds, majority_kept=0) == "output_limit_suspected"


def test_각_라운드에_통과항목이_있어도_겹치지_않으면_합의없음이다() -> None:
    rounds = []
    for number in (1, 2, 3):
        step = {"usage": {"out": 200, "stop_reason": "end_turn"}}
        attach_round_result(
            step,
            requested_max_tokens=3000,
            provider_selected=1,
            validation_kept=1,
            validation_rejected=0,
        )
        rounds.append(round_diagnostic_from_steps([step], round_number=number))

    assert majority_result_reason(rounds, majority_kept=0) == "no_majority_consensus"


def test_임의_provider_종료문구는_unknown으로_줄인다() -> None:
    step = {"usage": {"out": 10, "stop_reason": "민감할 수 있는 임의 원문"}}
    attach_round_result(
        step,
        requested_max_tokens=3000,
        provider_selected=0,
        validation_kept=0,
        validation_rejected=0,
    )

    diagnostic = round_diagnostic_from_steps([step], round_number=1)
    assert diagnostic.provider_stop_reason == "unknown"


def test_거절사유는_닫힌코드별_합계만_남긴다() -> None:
    step = {"usage": {"out": 100, "stop_reason": "end_turn"}}
    attach_round_result(
        step,
        requested_max_tokens=6000,
        provider_selected=4,
        validation_kept=0,
        validation_rejected=4,
        validation_rejection_reason_counts={
            "subject_label_not_in_source": 3,
            "저장하면 안 되는 회사 원문": 1,
        },
    )

    diagnostic = round_diagnostic_from_steps([step], round_number=1)

    assert diagnostic.validation_rejection_reason_counts == (
        ("other_validation_failure", 1),
        ("subject_label_not_in_source", 3),
    )
    assert all(
        reason in SAFE_VALIDATION_REJECTION_REASONS
        for reason, _count in diagnostic.validation_rejection_reason_counts
    )
    assert "저장하면 안 되는 회사 원문" not in repr(
        step["span_selection_diagnostic"]
    )


def test_거절사유_합계가_다르면_진단집계불일치로_닫는다() -> None:
    step = {"usage": {"out": 100, "stop_reason": "end_turn"}}
    attach_round_result(
        step,
        requested_max_tokens=6000,
        provider_selected=3,
        validation_kept=0,
        validation_rejected=3,
        validation_rejection_reason_counts={
            "subject_label_not_in_source": 2,
        },
    )

    diagnostic = round_diagnostic_from_steps([step], round_number=1)

    assert diagnostic.validation_rejection_reason_counts == (
        ("diagnostic_accounting_mismatch", 3),
    )
