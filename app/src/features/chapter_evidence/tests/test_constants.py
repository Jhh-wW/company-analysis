from __future__ import annotations

import pytest

from src.features.chapter_evidence.constants import (
    CompanyType,
    DART_SOURCE_KIND_PREFIX,
    OFFICIAL_SOURCE_KIND_PREFIX,
    expected_required_path_prefix,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


_TYPES_WITH_EXPECTED_PATH = tuple(
    company_type for company_type in CompanyType if company_type is not CompanyType.UNDECIDED
)


@pytest.mark.parametrize("company_type", _TYPES_WITH_EXPECTED_PATH)
@pytest.mark.parametrize("section_id", REQUIRED_EVIDENCE_SECTION_IDS)
def test_아홉장_세유형_전부_기대경로가_정의돼있다(
    company_type: CompanyType, section_id: str
) -> None:
    prefix = expected_required_path_prefix(company_type, section_id)

    assert prefix in {DART_SOURCE_KIND_PREFIX, OFFICIAL_SOURCE_KIND_PREFIX}


def test_undecided_회사유형은_기대경로_함수가_지원하지_않는다() -> None:
    # diagnose.py는 undecided에서 이 함수를 아예 호출하지 않는다(기대할
    # 접두어 자체가 없다는 뜻이라). 혹시라도 잘못 호출되면 조용히 틀린
    # 접두어를 주는 대신 명확히 거부해야 한다.
    with pytest.raises(ValueError, match="알 수 없는 회사 유형"):
        expected_required_path_prefix(CompanyType.UNDECIDED, "identity")


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
