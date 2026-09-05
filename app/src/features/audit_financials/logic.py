"""DART 감사보고서의 손익계산서를 추정 없이 2개년 실적표로 옮긴다."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from src.features.audit_financials.constants import (
    DIAGNOSTIC_AMOUNT_NOT_FOUND,
    DIAGNOSTIC_METRIC_NOT_FOUND,
    DIAGNOSTIC_STATEMENT_NOT_FOUND,
    DIAGNOSTIC_UNIT_NOT_FOUND,
    DIAGNOSTIC_YEAR_NOT_FOUND,
    DISPLAY_PLACES,
    DISPLAY_UNIT,
    EVIDENCE_MAX_CHARS,
    KOREAN_MONTHS,
    LOSS_ONLY_ALIASES,
    METRIC_ALIASES,
    OUTPUT_YEAR_COUNT,
    PLAIN_METRIC_WINDOW_CHARS,
    UNIT_DIVISORS,
)


_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_SPACE_RE = re.compile(r"\s+")
_TABLE_RE = re.compile(r"<TABLE\b[^>]*>.*?</TABLE\s*>", re.IGNORECASE | re.DOTALL)
_ROW_RE = re.compile(r"<TR\b[^>]*>(.*?)</TR\s*>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(
    r"<(?:TD|TH|TE)\b[^>]*>(.*?)</(?:TD|TH|TE)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_STATEMENT_TITLE_RE = re.compile(
    r"(?P<scope>연\s*결\s*)?(?:(?:포\s*괄\s*)?손\s*익\s*계\s*산\s*서)"
)
_NEXT_STATEMENT_RE = re.compile(
    r"(?:연\s*결\s*)?(?:재\s*무\s*상\s*태\s*표|자\s*본\s*변\s*동\s*표|현\s*금\s*흐\s*름\s*표)"
)
_FULL_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s*(?:년|[./-])\s*"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*"
    r"(?P<day>\d{1,2})\s*(?:일)?(?!\d)"
)
_UNIT_RE = re.compile(
    r"단\s*위\s*[:：]\s*(?P<unit>백\s*만\s*원|천\s*원|원)"
)
_AMOUNT_TOKEN_RE = re.compile(
    r"(?<![\d,])(?:"
    r"\(\s*(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?\s*\)"
    r"|[△▲+\-−]\s*(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?"
    r"|(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?"
    r")(?![\d,])"
)
_ENUMERATION_PREFIX_RE = re.compile(
    r"^[\s\u3000]*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+|\d+)[.)．]?\s*",
    re.IGNORECASE,
)

_ALIAS_TO_METRIC = {
    alias: metric for metric, aliases in METRIC_ALIASES for alias in aliases
}
_SORTED_ALIASES = tuple(
    sorted(_ALIAS_TO_METRIC, key=lambda value: (-len(value), value))
)


@dataclass(frozen=True)
class AuditEvidence:
    """선택한 원문 구간의 위치와 SHA-256 지문."""

    source_kind: str
    start: int
    end: int
    text_hash: str
    excerpt: str = field(repr=False, compare=False)

    @property
    def location(self) -> str:
        kind = "XML" if self.source_kind == "xml" else "평문"
        return f"{kind} 문자 {self.start}-{self.end}"


@dataclass(frozen=True)
class AuditPerformanceTable:
    """``ReportTable`` 생성자에 그대로 전달할 수 있는 2개년 표 payload."""

    caption: str
    headers: list[str]
    rows: list[list[str]]
    cite: str
    numeric: bool
    raw_rows: list[list[str]]
    scale_divisor: str
    scale_places: int
    display_unit: str
    evidence_rows: list[str] = field(repr=False, compare=False)
    presentation: str = "trend"
    entity_scope: str = ""
    raw_unit: str = ""
    unit_dimension: str = "currency"

    @property
    def numeric_checks(self) -> list[list[str]]:
        """행마다 ``원수치|나눗수|자릿수|표시값``을 재현한다."""

        return [
            [
                f"{raw}|{self.scale_divisor}|{self.scale_places}|{shown}"
                for raw, shown in zip(raw_row[1:], shown_row[1:])
            ]
            for raw_row, shown_row in zip(self.raw_rows, self.rows)
        ]

    def to_report_table_payload(self) -> dict[str, Any]:
        """파이프라인 경계에서 ``ReportTable(**payload)``로 바꿀 공개 계약."""

        return {
            "caption": self.caption,
            "headers": [*self.headers],
            "rows": [list(row) for row in self.rows],
            "cite": self.cite,
            "numeric": self.numeric,
            "raw_rows": [list(row) for row in self.raw_rows],
            "scale_divisor": self.scale_divisor,
            "scale_places": self.scale_places,
            "display_unit": self.display_unit,
            "evidence_rows": [*self.evidence_rows],
            "presentation": self.presentation,
            "entity_scope": self.entity_scope,
            "raw_unit": self.raw_unit,
            "unit_dimension": self.unit_dimension,
        }


@dataclass(frozen=True)
class AuditFinancialsResult:
    """파싱 성공 표 또는 빈 결과와 결정론적 진단."""

    performance_table: AuditPerformanceTable | None
    evidence: AuditEvidence | None = None
    diagnostic_reason: str = ""

    @property
    def table(self) -> AuditPerformanceTable | None:
        """호출부에서 읽기 쉬운 짧은 별칭."""

        return self.performance_table

    @property
    def is_found(self) -> bool:
        return self.performance_table is not None


@dataclass(frozen=True)
class _ParsedTable:
    start: int
    end: int
    text: str
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class _FiscalPeriod:
    start: date
    end: date

    @property
    def fiscal_year(self) -> int:
        return self.end.year


@dataclass(frozen=True)
class _Amount:
    raw: str
    value: Decimal
    had_grouping: bool


@dataclass(frozen=True)
class _StatementCandidate:
    source_kind: str
    scope: str
    start: int
    end: int
    search_text: str
    evidence_text: str
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class _Attempt:
    result: AuditFinancialsResult | None
    reason: str
    progress: int


def _plain_text(raw: str) -> str:
    decoded = html.unescape(str(raw or "")).replace("\u3000", " ")
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", decoded)).strip()


def _normalized_label(value: str) -> str:
    text = html.unescape(value).replace("（", "(").replace("）", ")")
    text = _ENUMERATION_PREFIX_RE.sub("", text)
    return re.sub(r"[\s\u3000]", "", text).strip(".:·")


def _metric_key(value: str) -> tuple[str, str] | None:
    normalized = _normalized_label(value)
    metric = _ALIAS_TO_METRIC.get(normalized)
    return (metric, normalized) if metric is not None else None


def _scope_from_title(value: str) -> str | None:
    normalized = re.sub(r"[\s\u3000]", "", html.unescape(value))
    if "연결포괄손익계산서" in normalized or "연결손익계산서" in normalized:
        return "연결"
    if "포괄손익계산서" in normalized or "손익계산서" in normalized:
        return "별도"
    return None


def _has_next_statement_title(value: str) -> bool:
    normalized = re.sub(r"[\s\u3000]", "", html.unescape(value))
    return any(
        title in normalized
        for title in ("재무상태표", "자본변동표", "현금흐름표")
    )


def _extract_tables(raw: str) -> list[_ParsedTable]:
    tables: list[_ParsedTable] = []
    for match in _TABLE_RE.finditer(raw):
        block = match.group(0)
        rows: list[tuple[str, ...]] = []
        for row_match in _ROW_RE.finditer(block):
            cells = tuple(
                _plain_text(cell.group(1))
                for cell in _CELL_RE.finditer(row_match.group(1))
            )
            if cells:
                rows.append(cells)
        tables.append(
            _ParsedTable(
                start=match.start(),
                end=match.end(),
                text=_plain_text(block),
                rows=tuple(rows),
            )
        )
    return tables


def _xml_candidates(raw: str) -> list[_StatementCandidate]:
    tables = _extract_tables(raw)
    candidates: list[_StatementCandidate] = []
    for index, table in enumerate(tables):
        scope = _scope_from_title(table.text)
        if scope is None:
            continue
        stop = len(tables)
        for later in range(index + 1, len(tables)):
            later_table = tables[later]
            if (
                _scope_from_title(later_table.text) is not None
                or _has_next_statement_title(later_table.text)
            ):
                stop = later
                break
        selected = tables[index:stop]
        if not selected:
            continue
        end = min(selected[-1].end, table.start + EVIDENCE_MAX_CHARS)
        evidence_text = raw[table.start:end]
        candidates.append(
            _StatementCandidate(
                source_kind="xml",
                scope=scope,
                start=table.start,
                end=end,
                search_text=" ".join(item.text for item in selected),
                evidence_text=evidence_text,
                rows=tuple(row for item in selected for row in item.rows),
            )
        )
    return candidates


def _plain_candidates(raw: str, *, source_kind: str = "plain") -> list[_StatementCandidate]:
    plain = _plain_text(raw)
    matches = list(_STATEMENT_TITLE_RE.finditer(plain))
    candidates: list[_StatementCandidate] = []
    for index, match in enumerate(matches):
        next_income_start = matches[index + 1].start() if index + 1 < len(matches) else len(plain)
        next_statement = _NEXT_STATEMENT_RE.search(plain, match.end())
        end = min(
            next_income_start,
            next_statement.start() if next_statement is not None else len(plain),
            match.start() + EVIDENCE_MAX_CHARS,
        )
        excerpt = plain[match.start():end].strip()
        if not excerpt:
            continue
        candidates.append(
            _StatementCandidate(
                source_kind=source_kind,
                scope="연결" if match.group("scope") else "별도",
                start=match.start(),
                end=match.start() + len(excerpt),
                search_text=excerpt,
                evidence_text=excerpt,
            )
        )
    return candidates


def _extract_periods(header: str) -> tuple[_FiscalPeriod, ...]:
    dates: list[date] = []
    for match in _FULL_DATE_RE.finditer(header):
        try:
            dates.append(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            )
        except ValueError:
            continue
    periods: list[_FiscalPeriod] = []
    for index in range(0, len(dates) - 1, 2):
        start, end = dates[index], dates[index + 1]
        days = (end - start).days + 1
        if start <= end and 350 <= days <= 380:
            period = _FiscalPeriod(start=start, end=end)
            if period not in periods:
                periods.append(period)
    periods.sort(key=lambda period: period.end, reverse=True)
    if len(periods) < OUTPUT_YEAR_COUNT:
        return ()
    for newer, older in zip(periods, periods[1:]):
        if (
            newer.fiscal_year != older.fiscal_year + 1
            or newer.end.month != older.end.month
        ):
            return ()
    return tuple(periods)


def _extract_unit(header: str) -> str:
    matches = list(_UNIT_RE.finditer(header))
    if not matches:
        return ""
    return re.sub(r"\s", "", matches[-1].group("unit"))


def _parse_amount(token: str, *, force_negative: bool) -> _Amount | None:
    text = html.unescape(token).strip().replace("−", "-")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text[:1] in {"△", "▲", "-"}:
        negative = True
        text = text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()
    compact = text.replace(",", "")
    try:
        value = Decimal(compact)
    except InvalidOperation:
        return None
    if not value.is_finite() or value != value.to_integral_value():
        return None
    if negative or (force_negative and value > 0):
        value = -abs(value)
    if value == 0:
        value = Decimal(0)
    return _Amount(
        raw=f"{value:,.0f}",
        value=value,
        had_grouping="," in text,
    )


def _row_amounts(
    cells: tuple[str, ...], *, period_count: int, force_negative: bool
) -> tuple[_Amount, ...]:
    amounts: list[_Amount] = []
    for cell in cells:
        if _AMOUNT_TOKEN_RE.fullmatch(cell.strip()) is None:
            continue
        parsed = _parse_amount(cell, force_negative=force_negative)
        if parsed is not None:
            amounts.append(parsed)
    if len(amounts) < period_count:
        return ()
    # 주석 번호가 숫자인 표가 많으므로 실제 기간 값은 행의 오른쪽 끝에서 고른다.
    return tuple(amounts[-period_count:])


def _structured_observations(
    rows: tuple[tuple[str, ...], ...], *, period_count: int
) -> dict[str, tuple[_Amount, ...]]:
    observations: dict[str, tuple[_Amount, ...]] = {}
    for row in rows:
        for cell_index, cell in enumerate(row):
            identified = _metric_key(cell)
            if identified is None:
                continue
            metric, alias = identified
            amounts = _row_amounts(
                row[cell_index + 1 :],
                period_count=period_count,
                force_negative=alias in LOSS_ONLY_ALIASES,
            )
            if amounts and metric not in observations:
                observations[metric] = amounts
            break
    return observations


def _loose_alias_pattern(alias: str) -> str:
    return r"\s*".join(re.escape(character) for character in alias)


def _plain_observation(
    text: str, *, aliases: tuple[str, ...], period_count: int
) -> tuple[_Amount, ...]:
    for alias in sorted(aliases, key=lambda value: (-len(value), value)):
        pattern = re.compile(_loose_alias_pattern(alias) + r"(?![가-힣A-Za-z])")
        for match in pattern.finditer(text):
            window = text[match.end() : match.end() + PLAIN_METRIC_WINDOW_CHARS]
            parsed: list[_Amount] = []
            for token_match in _AMOUNT_TOKEN_RE.finditer(window):
                amount = _parse_amount(
                    token_match.group(0), force_negative=alias in LOSS_ONLY_ALIASES
                )
                if amount is not None:
                    parsed.append(amount)
            grouped = [amount for amount in parsed if amount.had_grouping]
            if len(grouped) >= period_count:
                return tuple(grouped[:period_count])
            if len(parsed) == period_count:
                return tuple(parsed)
            if len(parsed) == period_count + 1 and abs(parsed[0].value) <= 999:
                return tuple(parsed[1:])
    return ()


def _plain_observations(
    text: str, *, period_count: int
) -> dict[str, tuple[_Amount, ...]]:
    return {
        metric: amounts
        for metric, aliases in METRIC_ALIASES
        if (
            amounts := _plain_observation(
                text, aliases=aliases, period_count=period_count
            )
        )
    }


def _metric_anchor(text: str) -> int:
    indexes: list[int] = []
    for alias in _SORTED_ALIASES:
        match = re.search(_loose_alias_pattern(alias), text)
        if match is not None:
            indexes.append(match.start())
    return min(indexes) if indexes else len(text)


def _display_value(value: Decimal, divisor: Decimal) -> str:
    shown = (value / divisor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if shown == 0:
        shown = Decimal(0)
    return f"{shown:,.0f}"


def _evidence(candidate: _StatementCandidate) -> AuditEvidence:
    payload = candidate.evidence_text
    return AuditEvidence(
        source_kind=candidate.source_kind,
        start=candidate.start,
        end=candidate.end,
        text_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        excerpt=payload,
    )


def _attempt_candidate(candidate: _StatementCandidate, *, cite: str) -> _Attempt:
    header = candidate.search_text[: _metric_anchor(candidate.search_text)]
    unit = _extract_unit(header)
    if not unit:
        return _Attempt(None, DIAGNOSTIC_UNIT_NOT_FOUND, 1)
    periods = _extract_periods(header)
    if not periods:
        return _Attempt(None, DIAGNOSTIC_YEAR_NOT_FOUND, 2)
    period_count = len(periods)
    observations = (
        _structured_observations(candidate.rows, period_count=period_count)
        if candidate.rows
        else _plain_observations(candidate.search_text, period_count=period_count)
    )
    metrics = [metric for metric, _aliases in METRIC_ALIASES]
    if not any(metric in observations for metric in metrics):
        return _Attempt(None, DIAGNOSTIC_METRIC_NOT_FOUND, 3)
    if any(metric not in observations for metric in metrics):
        return _Attempt(None, DIAGNOSTIC_AMOUNT_NOT_FOUND, 4)

    divisor = UNIT_DIVISORS[unit]
    selected_periods = periods[:OUTPUT_YEAR_COUNT]
    years = [str(period.fiscal_year) for period in selected_periods]
    evidence = _evidence(candidate)
    rows: list[list[str]] = []
    raw_rows: list[list[str]] = []
    checks: list[list[str]] = []
    for period_index, year in enumerate(years):
        raw_values = [observations[metric][period_index].raw for metric in metrics]
        shown_values = [
            _display_value(observations[metric][period_index].value, divisor)
            for metric in metrics
        ]
        rows.append([year, *shown_values])
        raw_rows.append([year, *raw_values])
        checks.append(
            [
                f"{raw}|{divisor:.0f}|{DISPLAY_PLACES}|{shown}"
                for raw, shown in zip(raw_values, shown_values)
            ]
        )

    scope_value = "consolidated" if candidate.scope == "연결" else "separate"
    closing_month = KOREAN_MONTHS[selected_periods[0].end.month]
    table = AuditPerformanceTable(
        caption=(
            f"전자공시 최근 두 사업연도 {candidate.scope} 주요 실적 "
            f"(결산월: {closing_month}, 단위: {DISPLAY_UNIT})"
        ),
        headers=["사업연도", *metrics],
        rows=rows,
        cite=cite,
        numeric=True,
        raw_rows=raw_rows,
        scale_divisor=f"{divisor:.0f}",
        scale_places=DISPLAY_PLACES,
        display_unit=DISPLAY_UNIT,
        evidence_rows=[evidence.excerpt for _ in rows],
        entity_scope=scope_value,
        raw_unit=unit,
    )
    # 계산 경로가 달라지면 표를 만들지 않도록 내부 계약도 즉시 대조한다.
    if table.numeric_checks != checks:
        return _Attempt(None, DIAGNOSTIC_AMOUNT_NOT_FOUND, 4)
    return _Attempt(
        AuditFinancialsResult(performance_table=table, evidence=evidence),
        "",
        5,
    )


def _parse_candidates(
    candidates: list[_StatementCandidate], *, cite: str
) -> tuple[AuditFinancialsResult | None, _Attempt | None]:
    if not candidates:
        return None, None
    # 연결 제목이 하나라도 있으면 별도 표로 후퇴하지 않아 두 범위를 섞지 않는다.
    selected_scope = "연결" if any(item.scope == "연결" for item in candidates) else "별도"
    selected = [item for item in candidates if item.scope == selected_scope]
    best: _Attempt | None = None
    for candidate in selected:
        attempt = _attempt_candidate(candidate, cite=cite)
        if attempt.result is not None:
            return attempt.result, attempt
        if best is None or attempt.progress > best.progress:
            best = attempt
    return None, best


def parse_audit_financials(
    text: str, *, cite: str = "", xml_text: str | None = None
) -> AuditFinancialsResult:
    """감사보고서 평문과 선택 XML에서 최근 2개년 3개 지표를 읽는다.

    XML 표 구조를 함께 받으면 이를 먼저 쓰고, 평문은 운영 수집기가 태그를 지운
    문자열 그대로 받을 수 있다. 연결 손익계산서가 존재하면 별도 표로 후퇴하지
    않으며, 단위·연도·세 계정 중 하나라도 확정할 수 없으면 빈 결과를 돌려준다.
    """

    sources: list[tuple[str, str]] = []
    if isinstance(xml_text, str) and xml_text.strip():
        sources.append(("xml", xml_text))
    if isinstance(text, str) and text.strip():
        kind = "xml" if "<TABLE" in text.upper() else "plain"
        if not sources or text != sources[0][1]:
            sources.append((kind, text))
    if not sources:
        return AuditFinancialsResult(
            performance_table=None,
            diagnostic_reason=DIAGNOSTIC_STATEMENT_NOT_FOUND,
        )

    best: _Attempt | None = None
    found_statement = False
    for kind, source in sources:
        candidate_groups: list[list[_StatementCandidate]] = []
        if kind == "xml":
            candidate_groups.append(_xml_candidates(source))
        candidate_groups.append(_plain_candidates(source, source_kind=kind))
        for candidates in candidate_groups:
            found_statement = found_statement or bool(candidates)
            result, attempt = _parse_candidates(candidates, cite=cite)
            if result is not None:
                return result
            if attempt is not None and (best is None or attempt.progress > best.progress):
                best = attempt

    reason = (
        best.reason
        if best is not None
        else (
            DIAGNOSTIC_METRIC_NOT_FOUND
            if found_statement
            else DIAGNOSTIC_STATEMENT_NOT_FOUND
        )
    )
    return AuditFinancialsResult(
        performance_table=None,
        diagnostic_reason=reason,
    )


def parse_audit_financials_text(
    text: str, *, cite: str = "", xml_text: str | None = None
) -> AuditFinancialsResult:
    """기존 호출부가 의미를 바로 읽을 수 있는 공개 함수 별칭."""

    return parse_audit_financials(text, cite=cite, xml_text=xml_text)
