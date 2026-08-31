from __future__ import annotations

import pytest

from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_MISSING_IDENTITY,
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
    FINAL_GATE_REASON_MISSING_REVENUE,
    FINAL_GATE_REASON_OTHER_GATE,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    QUALITY_FLOOR_PROBLEM_CODES,
    SAFE_FINAL_GATE_REASONS,
    classify_v2_validation_final_gate_reason,
)


def test_최종게이트_사유는_원문없는_안전코드로_닫혀있다() -> None:
    assert SAFE_FINAL_GATE_REASONS == {
        FINAL_GATE_REASON_COMPARISON_BLOCKED,
        FINAL_GATE_REASON_MISSING_IDENTITY,
        FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
        FINAL_GATE_REASON_MISSING_REVENUE,
        FINAL_GATE_REASON_PUBLISH_BLOCKED,
        FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
        FINAL_GATE_REASON_OTHER_GATE,
    }
    assert SAFE_FINAL_GATE_REASONS == {
        "comparison_blocked",
        "publish_missing_identity",
        "publish_missing_identity_revenue",
        "publish_missing_revenue",
        "publish_blocked",
        "publish_blocked_quality_floor",
        "other_gate",
    }


# ══════════════════════════════════════════════════════════
# classify_v2_validation_final_gate_reason — task 022. pipeline.real과
# (앞으로 붙을) shadow 진단 하네스가 공유하는 유일한 권위. 순수 함수라
# 여기서 problem_codes 목록만 넣고 바로 확인할 수 있다.
# ══════════════════════════════════════════════════════════


def test_품질하한_코드집합은_정확히_세_개다() -> None:
    assert QUALITY_FLOOR_PROBLEM_CODES == frozenset(
        {
            "too_few_substantive_claims",
            "too_few_document_sources",
            "low_verified_ratio",
        }
    )


@pytest.mark.parametrize(
    "quality_floor_code",
    ["too_few_substantive_claims", "too_few_document_sources", "low_verified_ratio"],
)
def test_품질하한_세_코드_각각은_새_사유를_받는다(quality_floor_code: str) -> None:
    assert (
        classify_v2_validation_final_gate_reason((quality_floor_code,))
        == FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    )


@pytest.mark.parametrize(
    "structural_code",
    [
        "too_many_notice_only_sections",
        "one_claim_sections",
        "low_semantic_coverage",
        "low_public_sentence_coverage",
    ],
)
def test_구조_코드_네_개_각각은_기존_publish_blocked를_유지한다(
    structural_code: str,
) -> None:
    assert (
        classify_v2_validation_final_gate_reason((structural_code,))
        == FINAL_GATE_REASON_PUBLISH_BLOCKED
    )


def test_빈_problem_codes는_publish_blocked다() -> None:
    """problem_codes 없이 던져진 기존 호출자(구조·안전 결속 오류 등)."""
    assert (
        classify_v2_validation_final_gate_reason(())
        == FINAL_GATE_REASON_PUBLISH_BLOCKED
    )


def test_혼합_코드는_품질하한코드가_하나라도_있으면_품질하한사유다() -> None:
    assert (
        classify_v2_validation_final_gate_reason(
            ("one_claim_sections", "low_verified_ratio")
        )
        == FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    )
