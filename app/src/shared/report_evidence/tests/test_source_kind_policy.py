"""실제 공식 수집 생산자와 닫힌 source_kind 정본의 완전성 검사."""

from __future__ import annotations

import ast
from pathlib import Path

from src.features.chapter_evidence.constants import (
    REQUIRED_SOURCE_KINDS_BY_COMPANY_TYPE,
)
from src.features.homepage.constants import (
    WIDE_SOURCE_KIND_IR_PDF,
    WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
    WIDE_SOURCE_KIND_RECRUIT_PAGE,
    WIDE_SOURCE_KIND_WEB_PAGE,
)
from src.shared.official_ir import (
    IR_METADATA_VERIFICATION_VALUE,
    IR_METADATA_VERIFICATION_VALUE_COVER,
)
from src.shared.report_evidence.constants import (
    FORMAL_ATTEMPT_SOURCE_KINDS,
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_ROBOTS_TXT,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.source_kind_policy import (
    FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND,
    FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND,
    FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND,
    FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND,
    formal_source_writer_ineligibility_reason,
)


_DART_SOURCE_CONSTANT_NAMES = {
    "SOURCE_KIND_BUSINESS_REPORT",
    "SOURCE_KIND_AUDIT_REPORT",
    "SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT",
    "SOURCE_KIND_SEMIANNUAL_REPORT",
    "SOURCE_KIND_QUARTERLY_REPORT",
}


def _engine_constants_path() -> Path:
    return (
        Path(__file__).resolve().parents[5]
        / "analysis_engine"
        / "src"
        / "features"
        / "evidence_collection"
        / "constants.py"
    )


def _engine_dart_source_kinds(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id not in _DART_SOURCE_CONSTANT_NAMES:
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            raise AssertionError(f"{node.target.id}는 문자열 리터럴이어야 합니다")
        values[node.target.id] = node.value.value
    assert set(values) == _DART_SOURCE_CONSTANT_NAMES
    return frozenset(values.values())


def test_analysis_engine_DART종류가_앱정본과_정확히같다() -> None:
    expected = frozenset(
        {
            SOURCE_KIND_DART_BUSINESS_REPORT,
            SOURCE_KIND_DART_AUDIT_REPORT,
            SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
            SOURCE_KIND_DART_SEMIANNUAL_REPORT,
            SOURCE_KIND_DART_QUARTERLY_REPORT,
        }
    )

    assert _engine_dart_source_kinds(_engine_constants_path()) == expected


def test_공식웹생산종류가_앱정본과_정확히같다() -> None:
    assert frozenset(
        {
            WIDE_SOURCE_KIND_WEB_PAGE,
            WIDE_SOURCE_KIND_RECRUIT_PAGE,
            WIDE_SOURCE_KIND_IR_PDF,
            WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
        }
    ) == OFFICIAL_WEB_SOURCE_KINDS


def test_닫힌종류목록과_슬롯소유권표가_빠짐없이_같다() -> None:
    assert frozenset(FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND) == (
        FORMAL_DOCUMENT_SOURCE_KINDS
    )
    assert frozenset(FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND) == (
        FORMAL_ATTEMPT_SOURCE_KINDS
    )
    assert frozenset(FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND) == (
        FORMAL_DOCUMENT_SOURCE_KINDS
    )
    assert frozenset(FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND) == (
        FORMAL_DOCUMENT_SOURCE_KINDS
    )
    assert FORMAL_ATTEMPT_SOURCE_KINDS - FORMAL_DOCUMENT_SOURCE_KINDS == {
        SOURCE_KIND_ROBOTS_TXT
    }


def test_연결감사보고서는_감사보고서와_같은_슬롯과_등급의_OPTIONAL문서다() -> None:
    assert FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND[
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    ] == FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND[SOURCE_KIND_DART_AUDIT_REPORT]
    assert FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    ] == frozenset(
        {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)}
    )
    assert FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    ] == (SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)


def test_연결감사보고서는_회사유형별_REQUIRED종류에_들어가지_않는다() -> None:
    assert all(
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT not in source_kinds
        for by_section in REQUIRED_SOURCE_KINDS_BY_COMPANY_TYPE.values()
        for source_kinds in by_section.values()
    )


def test_신원검증웹은_완전한_DARTproof만_TIER1이_될수_있는_두조합이다() -> None:
    assert FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
    ] == frozenset(
        {
            (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED),
            (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL),
        }
    )


def _official_ir_writer_reason(
    *, published_on: str, reporting_period: str, verification: str
) -> str:
    pdf_url = "https://company.example/ir/report.pdf"
    return formal_source_writer_ineligibility_reason(
        source_kind=WIDE_SOURCE_KIND_IR_PDF,
        source_tier=SourceTier.TIER_1_OFFICIAL,
        requirement=SourceRequirement.REQUIRED,
        canonical_url=pdf_url,
        publisher="company.example",
        published_on=published_on,
        collected_at="2026-09-05",
        identity_binding="DART 기업개황과 같은 공식 host",
        reporting_period=reporting_period,
        attachment_url=pdf_url,
        ir_metadata_verification=verification,
    )


def test_IR_writer관문은_옛anchor와_새표지_표식을_모두_허용한다() -> None:
    for verification in (
        IR_METADATA_VERIFICATION_VALUE,
        IR_METADATA_VERIFICATION_VALUE_COVER,
    ):
        assert not _official_ir_writer_reason(
            published_on="2026-03-12",
            reporting_period="2025-Q4",
            verification=verification,
        )


def test_IR_writer관문은_표지메타도_기존시간상한으로_거른다() -> None:
    invalid_dates = (
        ("2025-12-30", "2025-FY"),
        ("2026-07-10", "2025-FY"),
        ("2025-07-31", "2025-Q2"),
    )
    for published_on, reporting_period in invalid_dates:
        assert _official_ir_writer_reason(
            published_on=published_on,
            reporting_period=reporting_period,
            verification=IR_METADATA_VERIFICATION_VALUE_COVER,
        ) == "official_ir_writer_metadata_incomplete"
