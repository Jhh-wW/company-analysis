"""전자공시 주요계정 원값으로 최근 완료 3개 사업연도 표를 만든다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from src.features.pipeline.port import ReportTable


_ACCOUNT_IDS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "매출액",
        ("ifrs-full_Revenue",),
        ("매출액", "영업수익", "수익(매출액)", "수익"),
    ),
    (
        "영업이익",
        (
            "dart_OperatingIncomeLoss",
            "ifrs-full_ProfitLossFromOperatingActivities",
        ),
        ("영업이익", "영업이익(손실)"),
    ),
    (
        "당기순이익",
        ("ifrs-full_ProfitLoss",),
        ("당기순이익", "당기순이익(손실)", "연결당기순이익"),
    ),
)
_PERIOD_FIELDS: tuple[tuple[str, str], ...] = (
    ("thstrm_amount", "thstrm_dt"),
    ("frmtrm_amount", "frmtrm_dt"),
    ("bfefrmtrm_amount", "bfefrmtrm_dt"),
)
_REQUIRED_METRICS = frozenset({"매출액", "영업이익"})
_ANNUAL_REPORT_CODE = "11011"
_INCOME_STATEMENT_CODES = frozenset({"IS", "CIS"})
_WON_CURRENCIES = frozenset({"KRW", "원", "WON"})
_AMOUNT_RE = re.compile(
    r"^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$"
)
_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})(?!\d)"
)
_YEAR_RE = re.compile(r"20\d{2}")
_KOREAN_MONTHS = (
    "",
    "일월",
    "이월",
    "삼월",
    "사월",
    "오월",
    "유월",
    "칠월",
    "팔월",
    "구월",
    "시월",
    "십일월",
    "십이월",
)


@dataclass(frozen=True)
class _FiscalPeriod:
    """전자공시에 명시된 한 완료 사업연도의 실제 시작일과 종료일."""

    start: date
    end: date

    @property
    def fiscal_year(self) -> int:
        # 비12월 결산 법인도 종료연도가 FY 표기의 기준이다. 시작연도를 쓰면
        # 2024.04.01~2025.03.31을 FY2024로 잘못 낮추게 된다.
        return self.end.year


@dataclass(frozen=True)
class _MetricObservation:
    """동일 계정 한 행에서 얻은 3개년 기간·원값·공개 표시값."""

    label: str
    periods: tuple[_FiscalPeriod, _FiscalPeriod, _FiscalPeriod]
    raw_values: tuple[str, str, str]
    display_values: tuple[str, str, str]
    business_year: int


def _amount(raw: Any) -> tuple[str, str]:
    """원 단위 정수를 보존하고 억원 정수 표시는 ``ROUND_HALF_UP``한다."""

    if raw is None or isinstance(raw, bool):
        return "", ""
    text = str(raw).strip()
    if not _AMOUNT_RE.fullmatch(text):
        return "", ""
    value = Decimal(text.replace(",", ""))
    # ``-0``은 수치상 0과 같지만 표시·해시가 불필요하게 갈라지므로 정규화한다.
    if value == 0:
        value = Decimal(0)
    shown = (value / Decimal(100_000_000)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return f"{value:,.0f}", f"{shown:,.0f}"


def _period(raw: Any) -> _FiscalPeriod | None:
    """DART 기간 문자열을 읽되 날짜가 없으면 사업연도를 추정하지 않는다."""

    matches = list(_DATE_RE.finditer(str(raw or "")))
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
    if start > end:
        return None
    # 52·53주 회계를 포함하되 분기·반기와 전환기의 단기 사업연도는 배제한다.
    days = (end - start).days + 1
    if not 350 <= days <= 380:
        return None
    return _FiscalPeriod(start=start, end=end)


def _periods(
    row: dict[str, Any],
) -> tuple[_FiscalPeriod, _FiscalPeriod, _FiscalPeriod] | None:
    parsed = tuple(
        _period(row.get(date_key)) for _amount_key, date_key in _PERIOD_FIELDS
    )
    if any(period is None for period in parsed):
        return None
    current, previous, before = parsed
    assert current is not None and previous is not None and before is not None
    periods = (current, previous, before)

    # 최신→과거 순서, 연속된 실제 기간, 동일 결산월을 모두 만족해야 한다.
    # 날짜가 비었을 때 ``bsns_year - n``으로 채우는 것은 금지한다.
    for newer, older in zip(periods, periods[1:]):
        if newer.start != older.end + timedelta(days=1):
            return None
        if newer.end.year != older.end.year + 1:
            return None
        if newer.end.month != older.end.month:
            return None
    return periods


def _business_year(row: dict[str, Any]) -> int | None:
    text = str(row.get("bsns_year") or "").strip()
    return int(text) if _YEAR_RE.fullmatch(text) else None


def _is_annual_payload(
    financials: dict[str, Any], rows: list[dict[str, Any]]
) -> bool:
    """명시된 보고서 코드가 있다면 사업보고서(11011)만 허용한다."""

    codes = [
        str(value).strip()
        for value in (
            financials.get("reprt_code"),
            *(row.get("reprt_code") for row in rows),
        )
        if value is not None and str(value).strip()
    ]
    return not codes or all(code == _ANNUAL_REPORT_CODE for code in codes)


def _scope_rows(
    rows: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]] | None:
    """연결을 우선하며 연결 행이 하나라도 있으면 별도 행과 섞지 않는다."""

    consolidated = [
        row
        for row in rows
        if str(row.get("fs_div") or "").strip().upper() == "CFS"
    ]
    if consolidated:
        return "연결", consolidated
    separate = [
        row
        for row in rows
        if str(row.get("fs_div") or "").strip().upper() == "OFS"
    ]
    return ("별도", separate) if separate else None


def _row_signature(row: dict[str, Any]) -> tuple[str, ...]:
    """동일 계정 ID가 서로 다른 값·범위로 중복되면 모호성을 감지한다."""

    keys = (
        "account_id",
        "account_nm",
        "account_detail",
        "sj_div",
        "currency",
        "bsns_year",
        "reprt_code",
        *(
            key
            for amount_key, date_key in _PERIOD_FIELDS
            for key in (amount_key, date_key)
        ),
    )
    return tuple(str(row.get(key) or "").strip() for key in keys)


def _one_unambiguous(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not rows:
        return None
    signatures = {_row_signature(row) for row in rows}
    return rows[0] if len(signatures) == 1 else None


def _metric_row(
    rows: list[dict[str, Any]], ids: tuple[str, ...], names: tuple[str, ...]
) -> Optional[dict[str, Any]]:
    statement_rows = [
        row
        for row in rows
        if not str(row.get("sj_div") or "").strip()
        or str(row.get("sj_div") or "").strip().upper()
        in _INCOME_STATEMENT_CODES
    ]
    for account_id in ids:
        candidates = [
            row
            for row in statement_rows
            if str(row.get("account_id") or "").strip() == account_id
        ]
        if candidates:
            return _one_unambiguous(candidates)

    for name in names:
        normalized_name = name.replace(" ", "")
        candidates = [
            row
            for row in statement_rows
            if str(row.get("account_nm") or "").replace(" ", "")
            == normalized_name
        ]
        if candidates:
            return _one_unambiguous(candidates)
    return None


def _metric_observation(
    label: str, row: dict[str, Any]
) -> _MetricObservation | None:
    periods = _periods(row)
    business_year = _business_year(row)
    if periods is None or business_year is None:
        return None
    if periods[0].fiscal_year != business_year:
        return None

    amounts = tuple(
        _amount(row.get(amount_key)) for amount_key, _date_key in _PERIOD_FIELDS
    )
    if not all(raw and shown for raw, shown in amounts):
        return None
    return _MetricObservation(
        label=label,
        periods=periods,
        raw_values=tuple(raw for raw, _shown in amounts),
        display_values=tuple(shown for _raw, shown in amounts),
        business_year=business_year,
    )


def _currency_is_won(rows: list[dict[str, Any]]) -> bool:
    """억원 변환이므로 명시된 비원화 행은 공개 표로 승격하지 않는다."""

    currencies = [
        str(row.get("currency") or "").strip().upper() for row in rows
    ]
    return bool(currencies) and all(
        currency and currency in _WON_CURRENCIES for currency in currencies
    )


def _collected_payload(financials: dict[str, Any]) -> str:
    """실제 API 응답을 손대지 않은 결정론적 JSON 원문으로 잠근다."""

    try:
        return json.dumps(
            financials,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""


def build_three_year_table(
    financials: Optional[dict[str, Any]], *, cite: str
) -> ReportTable | None:
    """행=사업연도·열=지표인 공식 3개년 실적표를 fail-closed로 만든다.

    연결(CFS)을 우선하고, 연결 행이 전혀 없을 때만 별도(OFS)를 쓴다. 매출액과
    영업이익의 원 단위 값·연속된 연간 기간·동일 결산월이 모두 있어야 한다.
    공개 셀에는 억원 표시값만 두고 원값과 변환 규칙은 ``raw_rows`` 및 scale
    필드에 남겨 downstream ``FactRecord.numeric_checks``가 재현할 수 있게 한다.
    """

    if not isinstance(financials, dict):
        return None
    if financials.get("status") != "000":
        return None
    raw_rows = financials.get("list")
    if not isinstance(raw_rows, list) or not raw_rows:
        return None
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if len(rows) != len(raw_rows) or not _is_annual_payload(financials, rows):
        return None
    collected_payload = _collected_payload(financials)
    if not collected_payload:
        return None

    selected = _scope_rows(rows)
    if selected is None:
        return None
    scope, scoped_rows = selected

    observations: dict[str, _MetricObservation] = {}
    observation_rows: dict[str, dict[str, Any]] = {}
    for label, ids, names in _ACCOUNT_IDS:
        row = _metric_row(scoped_rows, ids, names)
        if row is None:
            continue
        observation = _metric_observation(label, row)
        if observation is not None:
            observations[label] = observation
            observation_rows[label] = row

    if not _REQUIRED_METRICS.issubset(observations):
        return None

    reference_periods = observations["매출액"].periods
    reference_year = observations["매출액"].business_year
    if (
        observations["영업이익"].periods != reference_periods
        or observations["영업이익"].business_year != reference_year
    ):
        return None

    # 선택 지표도 기간·범위가 다르면 비교표에서 제외한다. 필수 두 지표의 계약은
    # 위에서 별도로 실패 처리했으므로 선택 지표 누락이 표 전체를 가장하지 않는다.
    metric_labels = [
        label
        for label, _ids, _names in _ACCOUNT_IDS
        if label in observations
        and observations[label].periods == reference_periods
        and observations[label].business_year == reference_year
    ]
    if not _currency_is_won([observation_rows[label] for label in metric_labels]):
        return None
    years = [str(period.fiscal_year) for period in reference_periods]
    closing_month = _KOREAN_MONTHS[reference_periods[0].end.month]

    table = ReportTable(
        # 숫자로 쓴 "3개년"·"3월"은 표 FactRecord의 수치 장부 밖 숫자가 된다.
        # 한글 수사로 기간·결산월을 보존해 공개 claim과 numeric_checks를 일치시킨다.
        caption=(
            f"전자공시 최근 세 사업연도 {scope} 주요 실적 "
            f"(결산월: {closing_month}, 단위: 억원)"
        ),
        headers=["사업연도", *metric_labels],
        rows=[
            [
                years[index],
                *(observations[label].display_values[index] for label in metric_labels),
            ]
            for index in range(3)
        ],
        cite=cite,
        numeric=True,
        raw_rows=[
            [
                years[index],
                *(observations[label].raw_values[index] for label in metric_labels),
            ]
            for index in range(3)
        ],
        scale_divisor="100000000",
        scale_places=0,
        display_unit="억원",
        presentation="trend",
        entity_scope=("consolidated" if scope == "연결" else "separate"),
        raw_unit="원",
        unit_dimension="currency",
        # 세 공개 행은 같은 실제 API 응답에서 구조화됐다. 공개 행을 이어 붙인
        # 문장을 원문이라고 자가 등록하지 않고 원 응답 자체를 보존한다.
        evidence_rows=[collected_payload, collected_payload, collected_payload],
    )
    return table if table.is_valid else None
