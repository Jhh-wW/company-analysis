"""최종 게이트 사유와 사용자 안내의 닫힌 번역 계약."""

from __future__ import annotations

from src.features.final_gate_diagnostic.presentation import (
    StoppedGuidanceState,
    guidance_for_final_gate_reason,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    SAFE_FINAL_GATE_REASONS,
)


def test_여덟_사용자_상태를_서로_다르게_번역한다() -> None:
    cases = {
        FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT: (
            StoppedGuidanceState.INTERNAL_EVIDENCE_ERROR
        ),
        FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED: (
            StoppedGuidanceState.EVIDENCE_CLASSIFICATION_UNDETERMINED
        ),
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT: (
            StoppedGuidanceState.TRANSIENT_COLLECTION_ISSUE
        ),
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION: (
            StoppedGuidanceState.OFFICIAL_SOURCE_CONFIGURATION
        ),
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT: (
            StoppedGuidanceState.EVIDENCE_INSUFFICIENT
        ),
        FINAL_GATE_REASON_COMPARISON_BLOCKED: (
            StoppedGuidanceState.COMPARISON_EVIDENCE_INSUFFICIENT
        ),
        FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR: (
            StoppedGuidanceState.GENERATION_QUALITY_SHORTFALL
        ),
        FINAL_GATE_REASON_PUBLISH_BLOCKED: StoppedGuidanceState.OTHER,
    }

    assert {
        guidance_for_final_gate_reason(reason).state for reason in cases
    } == set(StoppedGuidanceState)
    for reason, expected in cases.items():
        assert guidance_for_final_gate_reason(reason).state is expected


def test_닫힌_안전_코드_전체가_빈_안내_없이_번역된다() -> None:
    for reason in SAFE_FINAL_GATE_REASONS:
        guidance = guidance_for_final_gate_reason(reason)
        assert guidance.title
        assert guidance.summary
        assert guidance.meaning
        assert guidance.actions
        assert guidance.primary_button_label


def test_미등록_원문은_화면에_싣지_않고_기타로_닫는다() -> None:
    unsafe = "provider exception: secret-url"

    guidance = guidance_for_final_gate_reason(unsafe)

    assert guidance.state is StoppedGuidanceState.OTHER
    assert unsafe not in " ".join(
        (
            guidance.title,
            guidance.summary,
            guidance.meaning,
            *guidance.actions,
            guidance.primary_button_label,
        )
    )


def test_공식자료_설정오류는_사용자_재시도를_권하지_않는다() -> None:
    guidance = guidance_for_final_gate_reason(
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
    )

    assert guidance.state is StoppedGuidanceState.OFFICIAL_SOURCE_CONFIGURATION
    rendered = " ".join((guidance.title, guidance.summary, guidance.meaning, *guidance.actions))
    assert "운영자" in rendered
    assert "회사명이나 주소를 바꾸지" in rendered
    assert "잠시 뒤" not in rendered
