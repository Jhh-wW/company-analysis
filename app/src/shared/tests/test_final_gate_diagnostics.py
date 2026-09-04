from __future__ import annotations

import pytest

from src.shared.final_gate_diagnostics import (
    EVIDENCE_CLASSIFICATION_UNDETERMINED_PROBLEM_CODES,
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
    FINAL_GATE_REASON_MISSING_IDENTITY,
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
    FINAL_GATE_REASON_MISSING_REVENUE,
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_REASON_OTHER_GATE,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    INTERNAL_EVIDENCE_CONTRACT_PROBLEM_CODES,
    OFFICIAL_EVIDENCE_INSUFFICIENT_PROBLEM_CODES,
    OFFICIAL_EVIDENCE_CONFIGURATION_PROBLEM_CODES,
    OFFICIAL_EVIDENCE_TRANSIENT_PROBLEM_CODES,
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
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
        FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
        FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
        FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
        FINAL_GATE_REASON_OTHER_GATE,
    }
    assert SAFE_FINAL_GATE_REASONS == {
        "comparison_blocked",
        "publish_missing_identity",
        "publish_missing_identity_revenue",
        "publish_missing_revenue",
        "publish_blocked",
        "publish_blocked_quality_floor",
        "official_evidence_insufficient",
        "official_evidence_transient",
        "official_evidence_configuration",
        "internal_evidence_contract",
        "evidence_classification_undetermined",
        "other_gate",
    }


# ══════════════════════════════════════════════════════════
# classify_v2_validation_final_gate_reason — task 022. pipeline.real과
# (앞으로 붙을) shadow 진단 하네스가 공유하는 유일한 권위. 순수 함수라
# 여기서 problem_codes 목록만 넣고 바로 확인할 수 있다.
# ══════════════════════════════════════════════════════════


def test_품질하한_코드집합은_필수의미칸과_해석폭주도_포함한다() -> None:
    assert QUALITY_FLOOR_PROBLEM_CODES == frozenset(
        {
            "too_few_substantive_claims",
            "too_few_document_sources",
            "low_verified_ratio",
            "too_many_interpretation_claims_per_section",
            "excessive_interpretation_claims",
            "missing_required_public_claim_slots",
        }
    )


def test_공식자료와_내부근거_상세코드는_닫힌_집합이다() -> None:
    assert OFFICIAL_EVIDENCE_INSUFFICIENT_PROBLEM_CODES == frozenset(
        {
            "preflight_document_sources_insufficient",
            "preflight_official_evidence_insufficient",
        }
    )
    assert OFFICIAL_EVIDENCE_TRANSIENT_PROBLEM_CODES == frozenset(
        {"preflight_official_evidence_transient"}
    )
    assert OFFICIAL_EVIDENCE_CONFIGURATION_PROBLEM_CODES == frozenset(
        {"preflight_official_evidence_configuration"}
    )
    assert EVIDENCE_CLASSIFICATION_UNDETERMINED_PROBLEM_CODES == frozenset(
        {"preflight_classifier_coverage_gap"}
    )
    assert INTERNAL_EVIDENCE_CONTRACT_PROBLEM_CODES == frozenset(
        {
            "preflight_packet_invalid",
            "preflight_table_cite_invalid",
            "preflight_table_evidence_invalid",
            "preflight_unregistered_fragment_kind",
            "evidence_transport_invalid",
            "evidence_manifest_binding_invalid",
            "public_manifest_binding_invalid",
        }
    )


@pytest.mark.parametrize(
    "detail_code",
    sorted(OFFICIAL_EVIDENCE_INSUFFICIENT_PROBLEM_CODES),
)
@pytest.mark.parametrize("namespaced", [False, True])
def test_공식자료_자체부족만_insufficient로_분류한다(
    detail_code: str, namespaced: bool
) -> None:
    value = f"report_recovery:{detail_code}" if namespaced else detail_code

    assert (
        classify_v2_validation_final_gate_reason((value,))
        == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
    )


@pytest.mark.parametrize("namespaced", [False, True])
def test_공식자료_일시확인실패는_transient로_분류한다(namespaced: bool) -> None:
    detail_code = "preflight_official_evidence_transient"
    value = f"report_recovery:{detail_code}" if namespaced else detail_code

    assert (
        classify_v2_validation_final_gate_reason((value,))
        == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
    )


@pytest.mark.parametrize("namespaced", [False, True])
def test_공식자료_접근설정실패는_configuration으로_분류한다(
    namespaced: bool,
) -> None:
    detail_code = "preflight_official_evidence_configuration"
    value = f"report_recovery:{detail_code}" if namespaced else detail_code

    assert (
        classify_v2_validation_final_gate_reason((value,))
        == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
    )


@pytest.mark.parametrize("namespaced", [False, True])
def test_분류기가_뜻을_확인하지못한_자료는_어느쪽으로도_단정하지않는다(
    namespaced: bool,
) -> None:
    detail_code = "preflight_classifier_coverage_gap"
    value = f"report_recovery:{detail_code}" if namespaced else detail_code

    assert (
        classify_v2_validation_final_gate_reason((value,))
        == FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED
    )


@pytest.mark.parametrize(
    "detail_code",
    sorted(INTERNAL_EVIDENCE_CONTRACT_PROBLEM_CODES),
)
@pytest.mark.parametrize("namespaced", [False, True])
def test_근거운반과_manifest_계약오류는_내부결함으로_분류한다(
    detail_code: str, namespaced: bool
) -> None:
    value = f"report_recovery:{detail_code}" if namespaced else detail_code

    assert (
        classify_v2_validation_final_gate_reason((value,))
        == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    )


@pytest.mark.parametrize(
    "quality_floor_code",
    [
        "too_few_substantive_claims",
        "too_few_document_sources",
        "low_verified_ratio",
        "too_many_interpretation_claims_per_section",
        "excessive_interpretation_claims",
        "missing_required_public_claim_slots",
    ],
)
def test_품질하한_각_코드는_새_사유를_받는다(quality_floor_code: str) -> None:
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


def test_내부결함이_자료부족과_품질하한에_섞이면_내부결함을_우선한다() -> None:
    assert (
        classify_v2_validation_final_gate_reason(
            (
                "too_few_document_sources",
                "report_recovery:preflight_document_sources_insufficient",
                "report_recovery:evidence_transport_invalid",
            )
        )
        == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    )


def test_설정오류는_일시장애와_자료부족보다_우선한다() -> None:
    assert (
        classify_v2_validation_final_gate_reason(
            (
                "preflight_official_evidence_insufficient",
                "preflight_official_evidence_transient",
                "preflight_official_evidence_configuration",
            )
        )
        == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
    )


def test_미등록_예외문과_원문은_저장가능한_사유코드로_새지않는다() -> None:
    raw_detail = (
        "APIStatusError: https://official.example/private?corp=123 응답 원문 "
        "evidence_transport_invalid"
    )

    classified = classify_v2_validation_final_gate_reason((raw_detail,))

    assert classified == FINAL_GATE_REASON_PUBLISH_BLOCKED
    assert raw_detail not in SAFE_FINAL_GATE_REASONS


def test_report_recovery_접두사를_두번붙인_문자열은_승인코드가_아니다() -> None:
    assert (
        classify_v2_validation_final_gate_reason(
            ("report_recovery:report_recovery:preflight_packet_invalid",)
        )
        == FINAL_GATE_REASON_PUBLISH_BLOCKED
    )
