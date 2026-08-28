"""DART 주요계정 원문과 공개 실적표를 정확히 결속하는 독립 검증기.

표 안의 숫자끼리 계산이 맞는지만 확인하면, 누군가 표와 ``raw_rows``를 함께
바꿔치기해도 그 숫자는 다시 ``VERIFIED``로 승격될 수 있다. 이 모듈은 수집기가
보존한 DART JSON을 별도로 해석해 지표·기간·범위·통화·원값·표시값이 모두 같은
경우에만 파생 계산의 원재료로 인정한다.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Protocol, Sequence


class FinancialTableInput(Protocol):
    headers: Sequence[str]
    rows: Sequence[Sequence[str]]
    raw_rows: Sequence[Sequence[str]]
    unit: str
    scale_divisor: str
    scale_places: int
    entity_scope: str
    raw_unit: str
    unit_dimension: str


_ACCOUNT_IDENTITIES: tuple[
    tuple[str, frozenset[str], frozenset[str]], ...
] = (
    (
        "매출액",
        frozenset({"ifrs-full_Revenue"}),
        frozenset({"매출액", "영업수익", "수익(매출액)", "수익"}),
    ),
    (
        "영업이익",
        frozenset(
            {
                "dart_OperatingIncomeLoss",
                "ifrs-full_ProfitLossFromOperatingActivities",
            }
        ),
        frozenset({"영업이익", "영업이익(손실)"}),
    ),
    (
        "당기순이익",
        frozenset({"ifrs-full_ProfitLoss"}),
        frozenset({"당기순이익", "당기순이익(손실)", "연결당기순이익"}),
    ),
)
_PERIOD_FIELDS = (
    ("thstrm_amount", "thstrm_dt"),
    ("frmtrm_amount", "frmtrm_dt"),
    ("bfefrmtrm_amount", "bfefrmtrm_dt"),
)
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})(?!\d)"
)
_AMOUNT_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$")
_WON_CURRENCIES = frozenset({"KRW", "원", "WON"})
_INCOME_STATEMENTS = frozenset({"", "IS", "CIS"})


def _normalized_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _amount(value: object) -> tuple[str, str] | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if not _AMOUNT_RE.fullmatch(raw):
        return None
    try:
        parsed = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    if parsed == 0:
        parsed = Decimal(0)
    shown = (parsed / Decimal(100_000_000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return f"{parsed:,.0f}", f"{shown:,.0f}"


def _period_year(value: object) -> int | None:
    matches = list(_DATE_RE.finditer(str(value or "")))
    if len(matches) != 2:
        return None
    try:
        start, end = (
            date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
            for match in matches
        )
    except ValueError:
        return None
    days = (end - start).days + 1
    if start > end or not 350 <= days <= 380:
        return None
    return end.year


def _row_signature(row: dict[str, object]) -> str:
    try:
        return json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""


def _metric_row(
    rows: Sequence[dict[str, object]], metric: str
) -> dict[str, object] | None:
    identity = next(
        (item for item in _ACCOUNT_IDENTITIES if item[0] == metric),
        None,
    )
    if identity is None:
        return None
    _label, account_ids, account_names = identity
    statement_rows = [
        row
        for row in rows
        if str(row.get("sj_div") or "").strip().upper()
        in _INCOME_STATEMENTS
    ]
    candidates = [
        row
        for row in statement_rows
        if str(row.get("account_id") or "").strip() in account_ids
    ]
    if not candidates:
        normalized_names = {_normalized_name(name) for name in account_names}
        candidates = [
            row
            for row in statement_rows
            if _normalized_name(row.get("account_nm")) in normalized_names
        ]
    signatures = {_row_signature(row) for row in candidates}
    if not candidates or "" in signatures or len(signatures) != 1:
        return None
    return candidates[0]


def dart_payload_matches_table(
    table: FinancialTableInput,
    evidence_payload: str,
) -> bool:
    """원 DART JSON이 표의 모든 공개·검산 셀을 정확히 만들 수 있는가."""

    try:
        payload = json.loads(str(evidence_payload or ""))
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if canonical_payload != evidence_payload or not isinstance(payload, dict):
        return False
    raw_payload_rows = payload.get("list")
    if payload.get("status") != "000" or not isinstance(raw_payload_rows, list):
        return False
    if not raw_payload_rows or any(not isinstance(row, dict) for row in raw_payload_rows):
        return False
    payload_rows: list[dict[str, object]] = raw_payload_rows

    report_codes = {
        str(value).strip()
        for value in (
            payload.get("reprt_code"),
            *(row.get("reprt_code") for row in payload_rows),
        )
        if value is not None and str(value).strip()
    }
    if report_codes and report_codes != {"11011"}:
        return False

    consolidated = [
        row
        for row in payload_rows
        if str(row.get("fs_div") or "").strip().upper() == "CFS"
    ]
    if consolidated:
        scoped_rows = consolidated
        expected_scope = "consolidated"
    else:
        scoped_rows = [
            row
            for row in payload_rows
            if str(row.get("fs_div") or "").strip().upper() == "OFS"
        ]
        expected_scope = "separate"
    if not scoped_rows or table.entity_scope != expected_scope:
        return False
    if (
        tuple(table.headers[:1]) != ("사업연도",)
        or table.scale_divisor != "100000000"
        or isinstance(table.scale_places, bool)
        or table.scale_places != 0
        or table.unit != "억원"
        or table.raw_unit != "원"
        or table.unit_dimension != "currency"
        or len(table.headers) < 3
        or len(table.rows) != 3
        or len(table.raw_rows) != 3
    ):
        return False

    metrics = tuple(str(value).strip() for value in table.headers[1:])
    allowed_metrics = tuple(item[0] for item in _ACCOUNT_IDENTITIES)
    if (
        len(metrics) != len(set(metrics))
        or any(metric not in allowed_metrics for metric in metrics)
        or not {"매출액", "영업이익"}.issubset(metrics)
        or metrics != tuple(metric for metric in allowed_metrics if metric in metrics)
    ):
        return False

    metric_rows: list[dict[str, object]] = []
    for metric in metrics:
        row = _metric_row(scoped_rows, metric)
        if row is None:
            return False
        currency = str(row.get("currency") or "").strip().upper()
        if currency not in _WON_CURRENCIES:
            return False
        metric_rows.append(row)

    expected_years: list[str] = []
    expected_raw_columns: list[list[str]] = []
    expected_display_columns: list[list[str]] = []
    reference_periods: tuple[int, ...] | None = None
    for row in metric_rows:
        periods: list[int] = []
        raw_values: list[str] = []
        display_values: list[str] = []
        for amount_key, date_key in _PERIOD_FIELDS:
            year = _period_year(row.get(date_key))
            amount = _amount(row.get(amount_key))
            if year is None or amount is None:
                return False
            periods.append(year)
            raw_values.append(amount[0])
            display_values.append(amount[1])
        period_tuple = tuple(periods)
        if (
            len(set(period_tuple)) != 3
            or period_tuple != tuple(sorted(period_tuple, reverse=True))
            or any(
                newer != older + 1
                for newer, older in zip(period_tuple, period_tuple[1:])
            )
        ):
            return False
        business_year = str(row.get("bsns_year") or "").strip()
        if business_year != str(period_tuple[0]):
            return False
        if reference_periods is None:
            reference_periods = period_tuple
            expected_years = [str(year) for year in period_tuple]
        elif period_tuple != reference_periods:
            return False
        expected_raw_columns.append(raw_values)
        expected_display_columns.append(display_values)

    expected_raw_rows = tuple(
        tuple(
            [expected_years[index]]
            + [column[index] for column in expected_raw_columns]
        )
        for index in range(3)
    )
    expected_display_rows = tuple(
        tuple(
            [expected_years[index]]
            + [column[index] for column in expected_display_columns]
        )
        for index in range(3)
    )
    return (
        tuple(tuple(str(cell) for cell in row) for row in table.raw_rows)
        == expected_raw_rows
        and tuple(tuple(str(cell) for cell in row) for row in table.rows)
        == expected_display_rows
    )
