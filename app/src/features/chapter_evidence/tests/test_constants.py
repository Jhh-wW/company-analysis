from __future__ import annotations

import pytest

from src.features.chapter_evidence.constants import (
    CompanyType,
    DART_SOURCE_KIND_PREFIX,
    OFFICIAL_SOURCE_KIND_PREFIX,
    expected_required_path_prefix,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


@pytest.mark.parametrize("company_type", list(CompanyType))
@pytest.mark.parametrize("section_id", REQUIRED_EVIDENCE_SECTION_IDS)
def test_아홉장_세유형_전부_기대경로가_정의돼있다(
    company_type: CompanyType, section_id: str
) -> None:
    prefix = expected_required_path_prefix(company_type, section_id)

    assert prefix in {DART_SOURCE_KIND_PREFIX, OFFICIAL_SOURCE_KIND_PREFIX}


def test_listed와_financial은_기대경로가_같다() -> None:
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        assert expected_required_path_prefix(
            CompanyType.LISTED, section_id
        ) == expected_required_path_prefix(CompanyType.FINANCIAL, section_id)


def test_audit_only는_최소_한_장에서_listed와_경로가_다르다() -> None:
    differs = any(
        expected_required_path_prefix(CompanyType.LISTED, section_id)
        != expected_required_path_prefix(CompanyType.AUDIT_ONLY, section_id)
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    )
    assert differs


def test_알수없는_회사유형은_거부한다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 회사 유형"):
        expected_required_path_prefix("unicorn", "identity")  # type: ignore[arg-type]


def test_알수없는_장_식별자는_거부한다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 근거 장 식별자"):
        expected_required_path_prefix(CompanyType.LISTED, "unknown-section")
