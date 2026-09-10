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
    # 2026-09-11 자리수 규칙 갱신 — 가장 작은 값 21.66억이 100억 미만이라
    # 소수 한 자리를 쓴다. 정수로 찍으면 -23.8과 -24.0이 똑같이 «-24»가 된다.
    assert table.scale_places == 1
    assert table.rows == [
        ["2025", "42.7", "-23.8", "-21.7"],
        ["2024", "28.7", "-24.0", "-33.1"],
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
        "4,274,313,429|100000000|1|42.7",
        "-2,381,829,906|100000000|1|-23.8",
        "-2,166,028,141|100000000|1|-21.7",
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
    # 2026-09-11 자리수 규칙 갱신 — 2024 당기순이익 -34.3억이 100억 미만이다.
    assert table.scale_places == 1
    assert table.rows[0] == ["2025", "26,498.7", "493.2", "-2,543.9"]
    assert table.rows[1] == ["2024", "22,556.5", "1,840.5", "-34.3"]
    assert table.raw_rows[0] == [
        "2025",
        "2,649,870,246",
        "49,318,276",
        "-254,385,318",
    ]
    assert table.raw_unit == "천원"
    assert table.scale_divisor == "100000"
    assert table.numeric_checks[0] == [
        "2,649,870,246|100000|1|26,498.7",
        "49,318,276|100000|1|493.2",
        "-254,385,318|100000|1|-2,543.9",
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


#: 2026-09-11 소규모 회사 실측 손익계산서. 당기순이익 82,552,618원은 0.83억이라
#: 자리수를 0으로 못 박아 두면 표에 1이 찍히고, 전기 366,016,342원(3.66억)은 4가
#: 찍혀 독자가 -75%로 읽는다(실제 -77.45%).
_SMALL_COMPANY_INCOME_XML = """
<TABLE><TR><TD>손 익 계 산 서</TD></TR>
<TR><TD>2025.01.01부터 2025.12.31까지</TD></TR>
<TR><TD>2024.01.01부터 2024.12.31까지</TD></TR>
<TR><TD>(단위: 원)</TD></TR></TABLE>
<TABLE>
<TR><TD>매출액</TD><TD>27,351,053,389</TD><TD>25,811,194,484</TD></TR>
<TR><TD>영업이익</TD><TD>436,660,956</TD><TD>583,314,634</TD></TR>
<TR><TD>당기순이익</TD><TD>82,552,618</TD><TD>366,016,342</TD></TR>
</TABLE>
<TABLE><TR><TD>현 금 흐 름 표</TD></TR></TABLE>
"""

#: 표시값으로 읽은 변동과 원값으로 계산한 변동의 허용 차이(%p).
#: ★ 리터럴로 둔다 — 생산 상수를 import해 기대값을 만들면 자리수 상한이
#:   낮아지는 회귀를 못 잡는 순환 검증이 된다.
#: ★ 이 문턱이 걸러 낸 실측 두 건: 옛 정수 표시(1 / 4)는 2.45%p, 유효숫자
#:   방식이 남긴 「1.0 / 1.0」 구간은 8.65%p 어긋났다.
_MAX_DISPLAY_CHANGE_GAP_POINTS = 1.0


def test_작은_값이_섞이면_소수_자리를_늘려_0으로_지우지_않는다() -> None:
    result = parse_audit_financials("", xml_text=_SMALL_COMPANY_INCOME_XML)

    table = result.performance_table
    assert table is not None
    assert table.scale_places == 2
    assert table.rows == [
        ["2025", "273.51", "4.37", "0.83"],
        ["2024", "258.11", "5.83", "3.66"],
    ]
    assert table.raw_rows[0] == ["2025", "27,351,053,389", "436,660,956", "82,552,618"]
    # 하류 재검산 계약(`composer.public_manifest`)이 읽는 자리수도 함께 바뀐다.
    assert table.numeric_checks[0][2] == "82,552,618|100000000|2|0.83"


def test_증감률은_표시값이_아니라_원값으로_계산한다() -> None:
    """표에서 읽은 변동과 원값 변동이 어긋나지 않는지 함께 잰다.

    누적 증감률 claim(`composer.structured_claims`)은 ``raw_rows``만 쓰므로
    표시 자리수가 그 값을 바꾸지 않는다. 그래도 독자는 «표를 보고» 변동을
    읽으므로, 두 값이 벌어지면 화면이 거짓말을 한다.
    """

    result = parse_audit_financials("", xml_text=_SMALL_COMPANY_INCOME_XML)
    table = result.performance_table
    assert table is not None

    def _number(value: str) -> float:
        return float(value.replace(",", ""))

    # 당기순이익 열(마지막)의 전기 → 당기 변동.
    raw_change = (
        (_number(table.raw_rows[0][3]) - _number(table.raw_rows[1][3]))
        / _number(table.raw_rows[1][3])
        * 100
    )
    shown_change = (
        (_number(table.rows[0][3]) - _number(table.rows[1][3]))
        / _number(table.rows[1][3])
        * 100
    )

    assert round(raw_change, 2) == -77.45
    assert abs(shown_change - raw_change) <= _MAX_DISPLAY_CHANGE_GAP_POINTS


def test_값이_모두_크면_기존_정수_표시를_그대로_쓴다() -> None:
    """바이트 불변 회귀 — 큰 회사 표는 자리수 규칙이 들어와도 안 바뀐다."""

    result = parse_audit_financials(
        "",
        xml_text=_fixture("20260410001926_income.xml"),
        cite="우아한형제들 연결감사보고서",
    )

    table = result.performance_table
    assert table is not None
    assert table.scale_places == 0
    assert table.rows[0] == ["2025", "52,830", "5,929", "4,406"]
    assert (
        table.numeric_checks[0][0] == "5,282,986,749,183|100000000|0|52,830"
    )


def test_최소값이_1억_언저리여도_표시로_변동을_읽을_수_있다() -> None:
    """★ 독립 검토 반례 — 1.04억과 0.95억이 둘 다 「1.0」으로 찍히면 안 된다.

    앞선 판은 반올림한 표시값의 «유효숫자 개수»로 자리수를 정했다. 십진수는
    반올림하며 생긴 뒤따르는 0도 자릿수로 세므로 1.0을 두 자리로 봤고, 자리수가
    1에서 멈춰 두 해가 같은 글자로 찍혔다. 독자가 읽는 변동 0%, 실제 -8.65%다.
    """

    xml = """
    <TABLE><TR><TD>손 익 계 산 서</TD></TR>
    <TR><TD>2025.01.01부터 2025.12.31까지</TD></TR>
    <TR><TD>2024.01.01부터 2024.12.31까지</TD></TR>
    <TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE>
    <TR><TD>매출액</TD><TD>27,351,053,389</TD><TD>25,811,194,484</TD></TR>
    <TR><TD>영업이익</TD><TD>436,660,956</TD><TD>583,314,634</TD></TR>
    <TR><TD>당기순이익</TD><TD>95,000,000</TD><TD>104,000,000</TD></TR>
    </TABLE>
    <TABLE><TR><TD>현 금 흐 름 표</TD></TR></TABLE>
    """

    result = parse_audit_financials("", xml_text=xml)
    table = result.performance_table
    assert table is not None
    assert table.scale_places == 2
    assert [row[3] for row in table.rows] == ["0.95", "1.04"]

    def _number(value: str) -> float:
        return float(value.replace(",", ""))

    raw_change = (95_000_000 - 104_000_000) / 104_000_000 * 100
    shown_change = (
        (_number(table.rows[0][3]) - _number(table.rows[1][3]))
        / _number(table.rows[1][3])
        * 100
    )

    assert round(raw_change, 2) == -8.65
    assert abs(shown_change - raw_change) <= _MAX_DISPLAY_CHANGE_GAP_POINTS


def test_괄호_삼각형_마이너스와_HALF_UP을_처리한다() -> None:
    xml = """
    <TABLE><TR><TD>손 익 계 산 서</TD></TR>
    <TR><TD>2025.01.01부터 2025.12.31까지</TD></TR>
    <TR><TD>2024.01.01부터 2024.12.31까지</TD></TR>
    <TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE>
    <TR><TD>매출액</TD><TD>124,500,000</TD><TD>(124,500,000)</TD></TR>
    <TR><TD>영업이익(손실)</TD><TD>△124,500,000</TD><TD>-124,500,000</TD></TR>
    <TR><TD>당기순이익(손실)</TD><TD>500,000</TD><TD>(500,000)</TD></TR>
    </TABLE>
    <TABLE><TR><TD>현 금 흐 름 표</TD></TR></TABLE>
    """

    result = parse_audit_financials("", xml_text=xml)

    table = result.performance_table
    assert table is not None
    # 2026-09-11 갱신 — 자리수 규칙이 들어와 기대 문자열이 바뀌었고, 값도
    # «반올림 방식이 실제로 갈리는» 것으로 바꿨다. 소수 둘째 자리에서
    # HALF_UP과 HALF_EVEN이 갈리려면 셋째 자리가 정확히 5여야 한다:
    #   1.245억 → HALF_UP 1.25 / HALF_EVEN 1.24
    #   0.005억 → HALF_UP 0.01 / HALF_EVEN 0.00
    # 옛 값(0.49999999억)은 어떤 방식으로도 0.50이라 HALF_UP을 못 가렸다.
    # 괄호·△ 음수 읽기를 재는 이 시험의 뜻은 그대로다.
    assert table.scale_places == 2
    assert table.rows == [
        ["2025", "1.25", "-1.25", "0.01"],
        ["2024", "-1.25", "-1.25", "-0.01"],
    ]
    assert table.raw_rows[1][1:] == ["-124,500,000", "-124,500,000", "-500,000"]
