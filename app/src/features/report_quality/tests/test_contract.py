from __future__ import annotations

from decimal import Decimal

import pytest

from src.features.report_quality.constants import (
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    LEGACY_UNVERSIONED_CONTRACT,
    QUALITY_CONTRACT_VERSION,
    REQUIRED_QUALITY_SECTION_IDS,
    STRICT_QUALITY_CONTRACT_VERSION,
    STRICT_REQUIRED_QUALITY_SECTION_IDS,
)
from src.shared.report_evidence.policy import EVIDENCE_SLOT_POLICY_VERSION
from src.features.report_quality.contract import (
    contract_for_generation,
    contract_for_stored_assessment,
    resolve_contract,
)
from src.features.report_quality.models import ContractUse


def test_새_보고서는_현재_생성계약으로_명시평가한다() -> None:
    contract = contract_for_generation()
    resolution = resolve_contract(use=ContractUse.GENERATION)

    assert contract.version == QUALITY_CONTRACT_VERSION
    assert resolution.resolved_version == QUALITY_CONTRACT_VERSION
    assert resolution.assess_now is True
    assert resolution.preserve_issued is False


def test_알수없는_생성계약을_최신버전으로_몰래_바꾸지_않는다() -> None:
    with pytest.raises(ValueError, match="알 수 없는"):
        contract_for_generation("report-quality-v999")


def test_full계약은_v1을_바꾸지_않고_9장과_안내문0개를_요구한다() -> None:
    legacy_default = contract_for_generation()
    strict = contract_for_generation(STRICT_QUALITY_CONTRACT_VERSION)

    assert legacy_default.version == QUALITY_CONTRACT_VERSION
    assert legacy_default.required_section_ids == REQUIRED_QUALITY_SECTION_IDS
    assert "competitive_position" not in legacy_default.required_section_ids
    assert strict.version == STRICT_QUALITY_CONTRACT_VERSION
    assert strict.required_section_ids == STRICT_REQUIRED_QUALITY_SECTION_IDS
    assert strict.required_section_ids[-1] == "competitive_position"
    assert strict.max_notice_only_sections == 0
    assert legacy_default.min_claims_per_covered_section == 2
    assert strict.min_claims_per_covered_section == 0
    assert strict.min_public_sentences_per_section == 3
    assert (
        strict.required_public_claim_slot_policy_version
        == EVIDENCE_SLOT_POLICY_VERSION
    )
    assert strict.min_substantive_claims == legacy_default.min_substantive_claims
    assert strict.min_verified_ratio == legacy_default.min_verified_ratio
    assert strict.min_document_sources == legacy_default.min_document_sources
    assert strict.max_interpreted_claims_per_section == 2
    assert strict.max_interpreted_claims == 10
    assert strict.max_interpreted_ratio == Decimal("0.25")


def test_v2와_v3를_평가할수있되_FULL발급선택은_라우터가_소유한다() -> None:
    current = contract_for_generation(STRICT_QUALITY_CONTRACT_VERSION)
    enforce_v2 = contract_for_generation(
        LEGACY_STRICT_QUALITY_CONTRACT_VERSION
    )
    stored_v2 = contract_for_stored_assessment(
        LEGACY_STRICT_QUALITY_CONTRACT_VERSION
    )

    assert current.version == "report-quality-v3-full"
    assert current.max_interpreted_claims_per_section == 2
    assert enforce_v2.version == "report-quality-v2-full"
    assert enforce_v2.required_section_ids == STRICT_REQUIRED_QUALITY_SECTION_IDS
    assert enforce_v2.min_substantive_claims == current.min_substantive_claims
    assert enforce_v2.min_verified_ratio == current.min_verified_ratio
    assert enforce_v2.min_document_sources == current.min_document_sources
    assert enforce_v2.max_interpreted_claims_per_section is None
    assert stored_v2.version == "report-quality-v2-full"
    assert stored_v2.max_interpreted_claims_per_section is None


@pytest.mark.parametrize(
    "version",
    ("report-quality-v999-full", "report-quality-v4-full"),
)
def test_저장평가도_미지원_미래버전을_현재계약으로_바꾸지않는다(
    version: str,
) -> None:
    with pytest.raises(ValueError, match="알 수 없는 저장"):
        contract_for_stored_assessment(version)


def test_full계약도_과거조회에서는_소급평가하지_않는다() -> None:
    resolution = resolve_contract(
        STRICT_QUALITY_CONTRACT_VERSION,
        use=ContractUse.HISTORICAL_READ,
    )

    assert resolution.assess_now is False
    assert resolution.preserve_issued is True
    assert resolution.resolved_version == STRICT_QUALITY_CONTRACT_VERSION


@pytest.mark.parametrize("stored", ["", "report-quality-v0", "외부-옛계약"])
def test_과거링크는_현재계약으로_소급평가하지_않는다(stored: str) -> None:
    resolution = resolve_contract(stored, use=ContractUse.HISTORICAL_READ)

    assert resolution.assess_now is False
    assert resolution.preserve_issued is True
    assert resolution.resolved_version == (stored or LEGACY_UNVERSIONED_CONTRACT)
