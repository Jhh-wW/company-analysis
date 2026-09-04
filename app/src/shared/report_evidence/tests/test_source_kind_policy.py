"""실제 공식 수집 생산자와 닫힌 source_kind 정본의 완전성 검사."""

from __future__ import annotations

import ast
from pathlib import Path

from src.features.homepage.constants import (
    WIDE_SOURCE_KIND_IR_PDF,
    WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
    WIDE_SOURCE_KIND_RECRUIT_PAGE,
    WIDE_SOURCE_KIND_WEB_PAGE,
)
from src.shared.report_evidence.constants import (
    FORMAL_ATTEMPT_SOURCE_KINDS,
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
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
)


_DART_SOURCE_CONSTANT_NAMES = {
    "SOURCE_KIND_BUSINESS_REPORT",
    "SOURCE_KIND_AUDIT_REPORT",
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


def test_신원검증웹은_완전한_DARTproof만_TIER1이_될수_있는_두조합이다() -> None:
    assert FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
    ] == frozenset(
        {
            (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED),
            (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL),
        }
    )
