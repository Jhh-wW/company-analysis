"""주석 번호 목록 때문에 실제 실적표가 사라지는 회귀를 검증한다."""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

import pytest

from src.features.audit_financials.logic import parse_audit_financials


def _statement(revenue: str, *, note_header: str = "주 석") -> str:
    return (
        "포괄손익계산서 "
        "2025년 1월 1일부터 2025년 12월 31일까지 "
        "2024년 1월 1일부터 2024년 12월 31일까지 "
        f"(단위: 원) 과목 {note_header} 당기 전기 "
        f"영업수익 {revenue} 영업손실 2000 3000 당기순손실 4000 5000"
    )


@pytest.mark.parametrize("notes", ["18,27", "1,2,3", "18, 27"])
def test_주석열의_명확한_번호목록_뒤_두_기간_금액을_보존한다(notes):
    result = parse_audit_financials(_statement(f"{notes} 925113 819678"))

    assert result.table is not None
    assert result.table.raw_rows == [
        ["2025", "925,113", "-2,000", "-4,000"],
        ["2024", "819,678", "-3,000", "-5,000"],
    ]


@pytest.mark.parametrize(
    "revenue,note_header",
    [
        ("18,27 925113 819678", ""),
        ("18,027 925113 819678", "주석"),
        ("18,27 925113", "주석"),
        ("18,27 925113 -", "주석"),
        ("18,27 18 925113 819678", "주석"),
        ("18,1000 925113 819678", "주석"),
        ("(주석 18,27) 18,27 925113 819678", "주석"),
    ],
)
def test_모호한_주석과_금액결측은_다른_수치로_채우지_않는다(revenue, note_header):
    assert parse_audit_financials(
        _statement(revenue, note_header=note_header)
    ).table is None


def test_천단위_금액은_주석으로_제거하지_않는다():
    result = parse_audit_financials(_statement("18,027 19,028"))
    assert result.table is not None
    assert [row[1] for row in result.table.raw_rows] == ["18,027", "19,028"]


def test_토스페이먼츠_실제_공시의_기간_범위_원값과_근거를_보존한다():
    source = (
        Path(__file__).parent / "fixtures" / "20260331000296_income.xml"
    ).read_text(encoding="utf-8")
    # 운영 read_filing_text와 동일하게 XML 태그와 연속 공백을 정리한다.
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(source)))
    result = parse_audit_financials(plain, cite="조각 1·감사보고서실적")

    assert result.table is not None
    assert result.table.rows == [
        ["2025", "9,251.1", "-13.1", "-159.5"],
        ["2024", "8,196.8", "-226.7", "-435.1"],
    ]
    assert result.table.raw_rows == [
        ["2025", "925,113,921,197", "-1,314,004,847", "-15,952,944,781"],
        ["2024", "819,678,045,176", "-22,671,903,802", "-43,506,460,890"],
    ]
    assert result.table.entity_scope == "separate"
    assert result.table.raw_unit == "원"
    assert result.evidence is not None
    assert result.evidence.excerpt in plain
    assert hashlib.sha256(result.evidence.excerpt.encode("utf-8")).hexdigest() == (
        result.evidence.text_hash
    )
