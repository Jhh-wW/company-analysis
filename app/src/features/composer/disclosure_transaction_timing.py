"""검수 원문은 보존하고 공시 거래의 표시 주장과 Fact 시점만 같은 근거에 잠근다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol, Sequence

from src.features.composer import disclosure_transaction_timing_constants as c


class DisclosureSource(Protocol):
    kind: object
    host: str
    publisher: str
    title: str
    reporting_period: str


@dataclass(frozen=True)
class DisclosureTransactionTiming:
    public_claim: str
    as_of: str


def _company(value: str) -> str:
    return c.COMPANY_NAME_REMOVE_RE.sub("", value)


def _own_filing(source: DisclosureSource, company_name: str) -> bool:
    return (
        getattr(source.kind, "value", source.kind) == c.FILING_KIND
        and source.host == c.DART_HOST
        and bool(_company(company_name))
        and _company(source.publisher) == _company(company_name)
    )


def _match_date(match) -> date | None:
    try:
        return date(*(int(match[key]) for key in ("year", "month", "day")))
    except ValueError:
        return None


def _annual_end(text: str, source: DisclosureSource) -> date | None:
    ends = {_match_date(match) for match in c.ANNUAL_END_RE.finditer(text)}
    ends.discard(None)
    period = source.reporting_period.strip()
    full = c.FULL_PERIOD_RE.fullmatch(period)
    if full:
        try:
            start, end = (date.fromisoformat(value) for value in full.groups())
            # 월·분기나 임의의 일수 범위를 회계연도로 승격하지 않는다.
            anniversary = start.replace(year=start.year + 1)
        except ValueError:
            return None
        if anniversary - timedelta(days=1) != end or ends != {end}:
            return None
        return end
    if period and not c.YEAR_PERIOD_RE.fullmatch(period):
        return None
    if len(ends) != 1:
        return None
    end = next(iter(ends))
    if period:
        return end if str(end.year) == period else None
    # legacy 조각은 보고기간 필드가 없을 수 있다. 같은 조각의 명시 회계연도
    # 종료일과 실제 공시 제목이 일치할 때만 종료일을 운반한다.
    title = c.AUDIT_TITLE_RE.fullmatch(source.title.strip())
    if title and (int(title[1]), int(title[2])) == (end.year, end.month):
        return end
    return None


def _merger_date(text: str, company_name: str) -> set[date]:
    result = set()
    if len(c.MERGER_COMPLETE_RE.findall(text)) != 1:
        return result
    for clause in c.MERGER_CLAUSE_RE.finditer(text):
        value = clause.group()
        if c.QUOTE_RE.search(value) or c.CURRENT_OR_FUTURE_RE.search(value):
            continue
        dates = list(c.KOREAN_DATE_RE.finditer(value))
        # 완료일은 '회사는 YYYY년 M월 D일에 … 합병을 완료'의 사건일이다.
        # 'YYYY년 M월 D일을 간주취득일'은 사건일 후보에 포함하지 않는다.
        for match in dates:
            before = value[:match.start()].strip()
            if before.startswith("이후 "):
                before = before[len("이후 "):].strip()
            own = before in ("회사는", "회사는 ") or before in (
                company_name + "는", company_name + "은",
            )
            if own and c.MERGER_EVENT_DATE_SUFFIX_RE.match(value[match.end():]):
                day = _match_date(match)
                if day:
                    result.add(day)
    return result


def disclosure_transaction_timing(
    claim: str, evidence: Sequence[tuple[str, DisclosureSource]], *,
    section_id: str, company_name: str, as_of_date: str,
) -> DisclosureTransactionTiming | None:
    """단일 명시 기간/완료일만 운반하며 전기의 실제 연도는 계산하지 않는다."""
    try:
        baseline = date.fromisoformat(as_of_date)
    except ValueError:
        return None
    if not evidence or c.QUOTE_RE.search(claim) or c.CURRENT_OR_FUTURE_RE.search(claim):
        return None
    if section_id == c.MERGER_SECTION:
        days = _merger_date(claim, company_name)
        supported = set()
        for text, source in evidence:
            if _own_filing(source, company_name):
                supported.update(_merger_date(text, company_name))
        if len(days) == 1 and days <= supported and next(iter(days)) < baseline:
            return DisclosureTransactionTiming(claim, next(iter(days)).isoformat())
        return None
    declared = c.PUBLIC_PERIOD_PREFIX_RE.match(claim)
    body = claim[declared.end():] if declared else claim
    if section_id != c.TRANSACTION_SECTION or c.NON_ANNUAL_CLAIM_RE.search(body):
        return None
    if not (c.RELATIVE_PERIOD_RE.search(body) and c.FINANCING_RE.search(body)
            and c.PAST_TRANSACTION_RE.search(body)):
        return None
    relative = list(c.RELATIVE_PERIOD_RE.finditer(body))
    if body[:relative[0].start()].strip():
        return None
    if any(not c.CLOSED_PAST_TRANSACTION_END_RE.search(
        body[match.start():relative[index + 1].start() if index + 1 < len(relative) else len(body)],
    ) for index, match in enumerate(relative)):
        return None
    periods = set()
    for text, source in evidence:
        if not (c.FINANCING_RE.search(text) and c.RELATIVE_PERIOD_RE.search(text)):
            continue
        if not _own_filing(source, company_name):
            return None
        end = _annual_end(text, source)
        if end is None:
            return None
        periods.add(end)
    if len(periods) != 1:
        return None
    end = next(iter(periods))
    if end >= baseline:
        return None
    if declared and _match_date(declared) != end:
        return None
    prefix = c.PUBLIC_PERIOD_PREFIX.format(year=end.year, month=end.month, day=end.day)
    return DisclosureTransactionTiming(
        prefix + body, end.isoformat(),
    )
