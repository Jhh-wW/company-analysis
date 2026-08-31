from __future__ import annotations

import pytest

from src.features.report_quality.constants import (
    LEGACY_UNVERSIONED_CONTRACT,
    QUALITY_CONTRACT_VERSION,
    REQUIRED_QUALITY_SECTION_IDS,
    STRICT_QUALITY_CONTRACT_VERSION,
    STRICT_REQUIRED_QUALITY_SECTION_IDS,
)
from src.features.report_quality.contract import (
    contract_for_generation,
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
    assert (
        strict.min_claims_per_covered_section
        == legacy_default.min_claims_per_covered_section
    )
    assert strict.min_substantive_claims == legacy_default.min_substantive_claims
    assert strict.min_verified_ratio == legacy_default.min_verified_ratio
    assert strict.min_document_sources == legacy_default.min_document_sources


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
