"""DART 주요계정 원문과 공개 실적표를 정확히 결속하는 독립 검증기.

표 안의 숫자끼리 계산이 맞는지만 확인하면, 누군가 표와 ``raw_rows``를 함께
바꿔치기해도 그 숫자는 다시 ``VERIFIED``로 승격될 수 있다. 이 모듈은 수집기가
보존한 DART JSON을 별도로 해석해 지표·기간·범위·통화·원값·표시값이 모두 같은
경우에만 파생 계산의 원재료로 인정한다.
"""

from __future__ import annotations

import json
import logging
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


#: 지표 이름 → (계정 ID 우선순위, 계정 이름 우선순위).
#:
#: ★ 예전에는 ID·이름을 `frozenset` 으로 담았다. 집합은 «순서가
#:   없어서» 표를 만드는 쪽(`company_performance/logic.py::_ACCOUNT_IDS`)과
#:   «다른 행»을 고를 수 있었다. 실제로 그랬다:
#:     · 표를 만드는 쪽 — ID 를 «순서대로» 하나씩 보고, 먼저 맞는 ID 에서 멈춘다.
#:     · 대조기(옛 판) — 그 지표의 ID 를 «전부 한꺼번에» 모았다.
#:   영업이익은 ID 가 둘인데 DART 응답에 둘 다 들어 있으면, 대조기는 서로 다른
#:   행 2개를 얻어 「모호하다」며 포기했다(분기 9). 그래서 4장 누적 증감률
#:   claim 이 «실제 자료에서 한 번도» 만들어지지 않았다 —
#:   저장 보고서 38건 전부 구조화 사실 0개(실측).
#: ⚠️ 아래 순서는 `company_performance/logic.py::_ACCOUNT_IDS` 와 «같아야» 한다.
#:   두 쪽이 다른 행을 고르면 대조는 영원히 성립하지 않는다.
#:   시험이 두 표를 묶어 지킨다.
_ACCOUNT_IDENTITIES: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]], ...
] = (
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
#: 대조가 성립하려면 표에 «아는» 금액 지표가 최소 몇 개 있어야 하는가.
#:
#: ★ 예전에는 「매출액과 영업이익이 «둘 다» 있을 것」을 요구했다.
#:   그런데 은행 손익계산서에는 매출액에 해당하는 계정이 «아예 없다»
#:   (실측: 우리은행 — 표가 영업이익·당기순이익 두 열로 만들어진다).
#:   그래서 우리은행은 표가 생겨도 대조가 «영원히» 실패했다(실측: 분기 8).
#: ★ v2-107 이 표를 만드는 쪽에서 같은 가정을 걷어냈는데 대조기에는 남아 있었다.
#:   두 쪽 규칙이 다르면 「만들 수는 있지만 검증은 못 하는」 표가 생긴다.
#: ⚠️ `company_performance/logic.py::MIN_METRICS_FOR_TABLE` 과 «같아야» 한다.
#:   시험이 두 값을 묶어 지킨다.
MIN_METRICS_FOR_MATCH: int = 2

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


#: 「같은 행인가」를 가르는 필드.
#:
#: ★ 예전에는 «행 전체»를 JSON 으로 찍어 비교했다. 그래서 표시
#:   순서(`ord`)나 합계 보조값처럼 표와 «무관한» 필드가 하나만 달라도 서로 다른
#:   행으로 보고 「모호하다」며 대조를 포기했다(실측: 현대카드 분기 9).
#:   표를 만드는 쪽(`company_performance/logic.py::_row_signature`)은 처음부터
#:   아래 필드만 봤다 — 두 쪽 판정이 달라 대조가 성립할 수 없었다.
#: ⚠️ 느슨해진 것이 «아니다» — 금액·기간·통화·사업연도·보고서코드가 전부
#:   들어 있어, 값이 다른 행이 섞이면 여전히 「모호」로 잡힌다. 게다가 고른 뒤
#:   모든 연도·금액을 표와 글자 단위로 다시 대조한다.
#: ⚠️ 표를 만드는 쪽과 «같은 목록»이어야 한다. 시험이 두 목록을 묶어 지킨다.
_SIGNATURE_KEYS: tuple[str, ...] = (
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


def _row_signature(row: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(row.get(key) or "").strip() for key in _SIGNATURE_KEYS)


def _one_unambiguous_row(
    candidates: Sequence[dict[str, object]],
) -> dict[str, object] | None:
    """같은 후보군 안에서 서로 다른 행이 섞이면 고르지 않는다."""

    signatures = {_row_signature(row) for row in candidates}
    if not candidates or len(signatures) != 1:
        return None
    return candidates[0]


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

    # ★ ID·이름을 «순서대로 하나씩» 본다 — 표를 만드는 쪽과 «같은 행»을 골라야
    #   대조가 성립한다. 한꺼번에 모으면 ID 가 둘 이상인 지표(영업이익)에서
    #   서로 다른 행이 섞여 「모호」로 떨어진다(실측: 분기 9).
    # ⚠️ 느슨해진 것이 «아니다» — 고른 뒤의 검사는 그대로다:
    #   한 후보군 안에서 서로 다른 행이 섞이면 여전히 None 이고,
    #   그렇게 고른 행의 모든 연도·값은 아래에서 표와 글자 단위로 대조된다.
    for index, account_id in enumerate(account_ids):
        candidates = [
            row
            for row in statement_rows
            if str(row.get("account_id") or "").strip() == account_id
        ]
        if candidates:
            picked = _one_unambiguous_row(candidates)
            if picked is None:
                # ⚠️ 우리 상수(지표 이름)와 «개수»만 남긴다 — 회사 원문 금지.
                logger.warning(
                    "지표 행 고르기 실패: %s · 계정ID %d번째 · 후보 %d개(서로 다름)",
                    metric,
                    index,
                    len(candidates),
                )
            return picked

    for index, account_name in enumerate(account_names):
        normalized = _normalized_name(account_name)
        candidates = [
            row
            for row in statement_rows
            if _normalized_name(row.get("account_nm")) == normalized
        ]
        if candidates:
            picked = _one_unambiguous_row(candidates)
            if picked is None:
                logger.warning(
                    "지표 행 고르기 실패: %s · 계정이름 %d번째 · 후보 %d개(서로 다름)",
                    metric,
                    index,
                    len(candidates),
                )
            return picked

    logger.warning(
        "지표 행 고르기 실패: %s · 계정ID·이름 어느 것도 안 맞음 (손익 행 %d개)",
        metric,
        len(statement_rows),
    )
    return None


logger = logging.getLogger(__name__)


def _no_match(branch: int) -> bool:
    """표와 DART 원 payload 가 «어느 분기»에서 어긋났는지만 남긴다.

    ★ 이 대조가 실패하면 4장 누적 증감률 claim 이 통째로
      안 만들어지는데(저장 보고서 38건 전부 0개), 여러 갈래 중 어디서
      떨어졌는지 알 방법이 없었다.
    ⚠️ 회사 원문·금액·계정 이름은 남기지 않는다 — 분기 번호만.
    """

    logger.warning("DART 원 payload 대조 실패: 분기 %d", branch)
    return False


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
        return _no_match(1)
    if canonical_payload != evidence_payload or not isinstance(payload, dict):
        return _no_match(2)
    raw_payload_rows = payload.get("list")
    if payload.get("status") != "000" or not isinstance(raw_payload_rows, list):
        return _no_match(3)
    if not raw_payload_rows or any(not isinstance(row, dict) for row in raw_payload_rows):
        return _no_match(4)
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
        return _no_match(5)

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
        return _no_match(6)
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
        return _no_match(7)

    metrics = tuple(str(value).strip() for value in table.headers[1:])
    allowed_metrics = tuple(item[0] for item in _ACCOUNT_IDENTITIES)
    if (
        len(metrics) != len(set(metrics))
        or any(metric not in allowed_metrics for metric in metrics)
        or len(metrics) < MIN_METRICS_FOR_MATCH
        or metrics != tuple(metric for metric in allowed_metrics if metric in metrics)
    ):
        return _no_match(8)

    metric_rows: list[dict[str, object]] = []
    for metric in metrics:
        row = _metric_row(scoped_rows, metric)
        if row is None:
            return _no_match(9)
        currency = str(row.get("currency") or "").strip().upper()
        if currency not in _WON_CURRENCIES:
            return _no_match(10)
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
                return _no_match(11)
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
            return _no_match(12)
        business_year = str(row.get("bsns_year") or "").strip()
        if business_year != str(period_tuple[0]):
            return _no_match(13)
        if reference_periods is None:
            reference_periods = period_tuple
            expected_years = [str(year) for year in period_tuple]
        elif period_tuple != reference_periods:
            return _no_match(14)
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
