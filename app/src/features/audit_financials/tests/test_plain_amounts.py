"""공개 파서 경계에서 실제 0과 결측·주석·다른 행의 숫자를 구별한다."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from src.features.audit_financials.logic import parse_audit_financials


HEADER = (
    "손익계산서 2025.01.01부터 2025.12.31까지 "
    "2024.01.01부터 2024.12.31까지 (단위: 원) "
)
METRICS = ("매출액", "영업이익", "당기순이익")
VALUES = (("100,000", "90,000"), ("20,000", "10,000"), ("5,000", "4,000"))
FIXTURES = Path(__file__).parent / "fixtures"


def _plain(rows: tuple[str, ...], *, notes: bool = False) -> str:
    columns = "과목 주석 당기 전기 " if notes else ""
    return HEADER + columns + " ".join(
        f"{metric} {row}" for metric, row in zip(METRICS, rows)
    )


@pytest.mark.parametrize("metric_index", range(len(METRICS)))
@pytest.mark.parametrize("period_index", range(len(VALUES[0])))
@pytest.mark.parametrize("token", ("0", "(0)", "-0", "+0", "△0", "▲0", "−0"))
def test_zero_keeps_its_metric_and_period(
    metric_index: int, period_index: int, token: str
) -> None:
    values = [list(row) for row in VALUES]
    values[metric_index][period_index] = token

    result = parse_audit_financials(_plain(tuple(" ".join(row) for row in values)))

    assert result.table is not None
    values[metric_index][period_index] = "0"
    assert result.table.raw_rows == [
        [year, *(row[index] for row in values)]
        for index, year in enumerate(("2025", "2024"))
    ]


@pytest.mark.parametrize("notes", (False, True))
def test_all_zero_is_a_complete_table(notes: bool) -> None:
    rows = ("19 0 0", "20 0 0", "21 0 0") if notes else ("0 0",) * len(METRICS)

    result = parse_audit_financials(_plain(rows, notes=notes))

    assert result.table is not None
    assert result.table.raw_rows == [["2025", "0", "0", "0"], ["2024", "0", "0", "0"]]
    assert result.table.rows == result.table.raw_rows
    assert all(check.startswith("0|") for row in result.table.numeric_checks for check in row)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (("100 90", "20 10", "5 4"), [["2025", "100", "20", "5"], ["2024", "90", "10", "4"]]),
        (
            ("100,000 (90,000)", "△20,000 −10,000", "0 +4,000"),
            [["2025", "100,000", "-20,000", "0"], ["2024", "-90,000", "-10,000", "4,000"]],
        ),
        (
            ("2025 0", "0 2024", "2000 2099"),
            [["2025", "2,025", "0", "2,000"], ["2024", "0", "2,024", "2,099"]],
        ),
    ],
)
def test_positive_negative_and_mixed_values_keep_order(
    rows: tuple[str, ...], expected: list[list[str]]
) -> None:
    result = parse_audit_financials(_plain(rows))

    assert result.table is not None
    assert result.table.raw_rows == expected


@pytest.mark.parametrize("period_index", range(len(VALUES[0])))
@pytest.mark.parametrize("missing", ("", "-", "—", "–", "미공시"))
def test_missing_amount_never_borrows_from_the_next_account(
    period_index: int, missing: str
) -> None:
    revenue = list(VALUES[0])
    revenue[period_index] = missing

    result = parse_audit_financials(
        _plain((" ".join(revenue), "20,000 10,000", "5,000 4,000"))
    )

    assert result.table is None
    assert result.diagnostic_reason


def test_non_target_account_also_ends_the_amount_sequence() -> None:
    result = parse_audit_financials(
        _plain(("0 영업비용 30,000 25,000", "20,000 10,000", "5,000 4,000"))
    )

    assert result.table is None


def test_metric_name_inside_another_account_cannot_fill_a_missing_value() -> None:
    result = parse_audit_financials(
        _plain(("0 부문매출액 30,000 25,000", "20,000 10,000", "5,000 4,000"))
    )

    assert result.table is None


@pytest.mark.parametrize("row", ("19 0 90,000", "19 100,000 0", "0 0"))
def test_note_column_does_not_replace_zero(row: str) -> None:
    result = parse_audit_financials(_plain((row, "20,000 10,000", "5,000 4,000"), notes=True))

    assert result.table is not None
    expected = row.split()[-len(VALUES[0]):]
    assert [period[1] for period in result.table.raw_rows] == expected


@pytest.mark.parametrize(
    ("row", "notes"),
    (
        ("19 0", True),
        ("19 90,000", True),
        ("19 - 0", True),
        ("19 0 -", True),
        ("0 0 90,000", True),
        ("-19 0 90,000", True),
        ("1000 0 90,000", True),
        ("19 0 90,000", False),
        ("2025년 0 90,000", False),
        ("0 2024년", False),
        ("0 2024기말", False),
        ("0 12월", False),
        ("0 31일", False),
        ("주석 19 0", False),
    ),
)
def test_ambiguous_note_year_or_missing_column_produces_no_table(row: str, notes: bool) -> None:
    result = parse_audit_financials(_plain((row, "20,000 10,000", "5,000 4,000"), notes=notes))

    assert result.table is None


@pytest.mark.parametrize("missing", ("", "-"))
@pytest.mark.parametrize("original", ("4,274,313,429", "2,871,681,996"))
def test_xml_missing_period_does_not_shift_a_note_into_the_value(
    original: str, missing: str
) -> None:
    xml = (FIXTURES / "20260406001240_income.xml").read_text(encoding="utf-8")

    result = parse_audit_financials("", xml_text=xml.replace(original, missing))

    assert result.table is None


@pytest.mark.parametrize("include_plain_copy", (False, True))
def test_structured_blank_cannot_be_recovered_by_flattening_xml(include_plain_copy: bool) -> None:
    xml = (
        f"<TABLE><TR><TD>{HEADER}</TD></TR>"
        "<TR><TD>매출액</TD><TD>19</TD><TD></TD><TD>0</TD></TR>"
        "<TR><TD>영업이익</TD><TD>20,000</TD><TD>10,000</TD></TR>"
        "<TR><TD>당기순이익</TD><TD>5,000</TD><TD>4,000</TD></TR></TABLE>"
    )

    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)) if include_plain_copy else ""
    result = parse_audit_financials(plain, xml_text=xml)

    assert result.table is None


def test_xml_without_structured_metric_rows_keeps_plain_fallback() -> None:
    xml = f"<TABLE><TR><TD>{HEADER}</TD></TR></TABLE>" + "".join(
        f"<P>{metric} {' '.join(values)}</P>" for metric, values in zip(METRICS, VALUES)
    )

    result = parse_audit_financials("", xml_text=xml)

    assert result.table is not None
    assert result.table.raw_rows == [
        ["2025", "100,000", "20,000", "5,000"],
        ["2024", "90,000", "10,000", "4,000"],
    ]


@pytest.mark.parametrize(
    "fixture_name",
    ("20260406001240_income.xml", "20260410001926_income.xml", "20260320000802_income.xml"),
)
def test_existing_fixtures_keep_the_same_plain_and_xml_values(fixture_name: str) -> None:
    xml = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(xml)))

    structured = parse_audit_financials("", xml_text=xml)
    flattened = parse_audit_financials(plain)

    assert structured.table is not None
    assert flattened.table is not None
    assert flattened.table.raw_rows == structured.table.raw_rows
    assert flattened.table.rows == structured.table.rows


@pytest.mark.parametrize("use_xml", (False, True))
def test_three_period_fixture_keeps_zero_in_the_current_period(use_xml: bool) -> None:
    xml = (FIXTURES / "20260320000802_income.xml").read_text(encoding="utf-8")
    xml = xml.replace("2,649,870,246", "0")
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(xml)))

    result = parse_audit_financials("", xml_text=xml) if use_xml else parse_audit_financials(plain)

    assert result.table is not None
    assert result.table.raw_rows == [
        ["2025", "0", "49,318,276", "-254,385,318"],
        ["2024", "2,255,648,536", "184,045,287", "-3,431,847"],
    ]
