from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import pytest

from src.features.audit_financials.constants import (
    DIAGNOSTIC_AMOUNT_NOT_FOUND,
    DIAGNOSTIC_STATEMENT_NOT_FOUND,
    DIAGNOSTIC_UNIT_NOT_FOUND,
    DIAGNOSTIC_YEAR_NOT_FOUND,
)
from src.features.audit_financials.logic import parse_audit_financials
from src.features.pipeline.port import ReportTable


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _filing_plain_text(raw: str) -> str:
    """운영 ``read_filing_text``와 같은 태그 제거·공백 축약."""

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw)))


def test_인이지_평문에서_당기와_전기_세_계정을_읽는다() -> None:
    text = _filing_plain_text(_fixture("20260406001240_income.xml"))

    result = parse_audit_financials(text, cite="인이지 감사보고서")

    assert result.is_found
    table = result.performance_table
    assert table is not None
    assert table.headers == ["사업연도", "매출액", "영업이익", "당기순이익"]
    assert table.rows == [
        ["2025", "43", "-24", "-22"],
        ["2024", "29", "-24", "-33"],
    ]
    assert table.raw_rows[0] == [
        "2025",
        "4,274,313,429",
        "-2,381,829,906",
        "-2,166,028,141",
    ]
    assert table.entity_scope == "separate"
    assert table.raw_unit == "원"
    assert table.scale_divisor == "100000000"
    assert table.numeric_checks[0] == [
        "4,274,313,429|100000000|0|43",
        "-2,381,829,906|100000000|0|-24",
        "-2,166,028,141|100000000|0|-22",
    ]


def test_우아한형제들_XML은_연결_2개년과_근거_지문을_보존한다() -> None:
    xml = _fixture("20260410001926_income.xml")

    result = parse_audit_financials("", xml_text=xml, cite="우아한형제들 연결감사보고서")

    table = result.performance_table
    assert table is not None
    assert table.rows == [
        ["2025", "52,830", "5,929", "4,406"],
        ["2024", "43,226", "6,408", "4,593"],
    ]
    assert table.entity_scope == "consolidated"
    assert len(table.rows) == len(table.raw_rows) == len(table.evidence_rows) == 2
    assert result.evidence is not None
    assert result.evidence.source_kind == "xml"
    assert result.evidence.location.startswith("XML 문자 ")
    assert result.evidence.text_hash == hashlib.sha256(
        result.evidence.excerpt.encode("utf-8")
    ).hexdigest()
    row_evidence = json.loads(table.evidence_rows[0])
    assert row_evidence["source"] == "audit_report_statement"
    assert row_evidence["source_excerpt"] == result.evidence.excerpt
    assert row_evidence["source_sha256"] == result.evidence.text_hash
    assert row_evidence["row"] == dict(zip(table.headers, table.raw_rows[0]))


def test_하이브_천원_원문은_API_대조값과_같고_최근_두_해만_낸다() -> None:
    result = parse_audit_financials(
        "",
        xml_text=_fixture("20260320000802_income.xml"),
        cite="하이브 사업보고서",
    )

    table = result.performance_table
    assert table is not None
    assert table.rows[0] == ["2025", "26,499", "493", "-2,544"]
    assert table.rows[1] == ["2024", "22,556", "1,840", "-34"]
    assert table.raw_rows[0] == [
        "2025",
        "2,649,870,246",
        "49,318,276",
        "-254,385,318",
    ]
    assert table.raw_unit == "천원"
    assert table.scale_divisor == "100000"
    assert table.numeric_checks[0] == [
        "2,649,870,246|100000|0|26,499",
        "49,318,276|100000|0|493",
        "-254,385,318|100000|0|-2,544",
    ]


def test_ReportTable과_같은_payload로_바로_검증된다() -> None:
    result = parse_audit_financials(
        "", xml_text=_fixture("20260406001240_income.xml"), cite="감사보고서"
    )
    table = result.performance_table
    assert table is not None

    report_table = ReportTable(**table.to_report_table_payload())

    assert report_table.is_valid
    assert report_table.rows == table.rows
    assert report_table.raw_rows == table.raw_rows


def test_연결표가_있으면_별도표의_값으로_메우지_않는다() -> None:
    incomplete_consolidated = _fixture("20260410001926_income.xml").replace(
        "<TR><TD>Ⅲ.영업이익</TD><TD><BR/></TD><TD>592,870,374,648</TD><TD>640,789,904,672</TD></TR>",
        "",
    )
    separate = _fixture("20260406001240_income.xml")

    result = parse_audit_financials("", xml_text=incomplete_consolidated + separate)

    assert result.performance_table is None
    assert result.diagnostic_reason == DIAGNOSTIC_AMOUNT_NOT_FOUND


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("재무제표가 없는 감사보고서 조각", DIAGNOSTIC_STATEMENT_NOT_FOUND),
        pytest.param(
            "손익계산서 2025.01.01부터 2025.12.31까지 "
            "2024.01.01부터 2024.12.31까지 매출액 100 90",
            DIAGNOSTIC_UNIT_NOT_FOUND,
        ),
        pytest.param(
            "손익계산서 (단위: 원) 매출액 100 90 영업이익 20 10 "
            "당기순이익 5 4",
            DIAGNOSTIC_YEAR_NOT_FOUND,
        ),
    ],
)
def test_못_찾으면_빈_결과와_구체적_진단을_돌려준다(
    text: str, reason: str
) -> None:
    result = parse_audit_financials(text)

    assert result.performance_table is None
    assert result.evidence is None
    assert result.diagnostic_reason == reason


def test_괄호_삼각형_마이너스와_HALF_UP을_처리한다() -> None:
    xml = """
    <TABLE><TR><TD>손 익 계 산 서</TD></TR>
    <TR><TD>2025.01.01부터 2025.12.31까지</TD></TR>
    <TR><TD>2024.01.01부터 2024.12.31까지</TD></TR>
    <TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE>
    <TR><TD>매출액</TD><TD>50,000,000</TD><TD>(50,000,000)</TD></TR>
    <TR><TD>영업이익(손실)</TD><TD>△50,000,000</TD><TD>-50,000,000</TD></TR>
    <TR><TD>당기순이익(손실)</TD><TD>49,999,999</TD><TD>(49,999,999)</TD></TR>
    </TABLE>
    <TABLE><TR><TD>현 금 흐 름 표</TD></TR></TABLE>
    """

    result = parse_audit_financials("", xml_text=xml)

    table = result.performance_table
    assert table is not None
    assert table.rows == [
        ["2025", "1", "-1", "0"],
        ["2024", "-1", "-1", "0"],
    ]
    assert table.raw_rows[1][1:] == ["-50,000,000", "-50,000,000", "-49,999,999"]
