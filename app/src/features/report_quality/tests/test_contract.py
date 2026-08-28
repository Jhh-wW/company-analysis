from __future__ import annotations

import pytest

from src.features.report_quality.constants import (
    LEGACY_UNVERSIONED_CONTRACT,
    QUALITY_CONTRACT_VERSION,
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


@pytest.mark.parametrize("stored", ["", "report-quality-v0", "외부-옛계약"])
def test_과거링크는_현재계약으로_소급평가하지_않는다(stored: str) -> None:
    resolution = resolve_contract(stored, use=ContractUse.HISTORICAL_READ)

    assert resolution.assess_now is False
    assert resolution.preserve_issued is True
    assert resolution.resolved_version == (stored or LEGACY_UNVERSIONED_CONTRACT)
