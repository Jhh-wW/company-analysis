"""소송 주석의 기준일을 본문·도식·공개 Fact에서 같은 의미로 유지한다."""

from datetime import date

from src.features.composer import litigation_timing_constants as c
from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_KIND_LITIGATION


def _snapshot(text: str) -> bool:
    return bool(c.LITIGATION_RE.search(text) and c.SNAPSHOT_RE.search(text))


def litigation_period_context(fragment) -> bool:
    return bool(fragment.reporting_period and _snapshot(fragment.text) and (
        fragment.kind == LEGACY_KIND_LITIGATION or c.CURRENT_PERIOD_RE.search(fragment.text)
    ))


def _explicit_date(text: str) -> str:
    dates = set()
    for match in c.AS_OF_RE.finditer(text):
        try:
            dates.add(date(
                int(match['year']),
                c.YEAR_END_MONTH if match['year_end'] else int(match['month'] or match['iso_month']),
                c.YEAR_END_DAY if match['year_end'] else int(match['day'] or match['iso_day']),
            ).isoformat())
        except ValueError:
            return ""
    return next(iter(dates)) if len(dates) == 1 else ""


def _source_date(text: str, reporting_period: str) -> str:
    if c.PRIOR_PERIOD_RE.search(text):
        return ""  # 당기의 기간에서 전기말을 만들어 내지 않는다.
    if c.CURRENT_PERIOD_RE.search(text):
        try:
            start, end = (date.fromisoformat(part) for part in reporting_period.split('/'))
        except ValueError:
            return ""
        return end.isoformat() if start <= end else ""
    return _explicit_date(text)


def litigation_snapshot_date(claim: str, sources) -> str:
    """같은 자기 원문의 명시 기준일과 주장 날짜가 일치한 경우만 돌려준다."""
    if not _snapshot(claim) or c.UNBOUND_CURRENT_RE.search(claim):
        return ""
    stated = _explicit_date(claim)
    if not stated:
        return ""
    dates = tuple(c.AS_OF_RE.finditer(claim))
    if len(dates) != 1:
        return ""
    dated = dates[0]
    leading = c.DATED_SNAPSHOT_LEAD_RE.match(claim, dated.end())
    trailing = (
        dated.group().rstrip().endswith(("기준", "현재"))
        and c.SNAPSHOT_BEFORE_DATE_RE.search(claim[:dated.start()])
        and c.DATED_SNAPSHOT_COUNT_RE.match(claim, dated.end())
    )
    if not leading and not trailing:
        # 날짜의 단순 존재가 아니라 바로 뒤 소송 현황을 한정하는 범위만 쓴다.
        # '이후에도', '기준일을 지나 현재'를 같은 시점 스냅샷으로 바꾸지 않는다.
        return ""
    relevant = [(text, period) for text, period in sources if _snapshot(text)]
    if relevant and all(_source_date(text, period) == stated for text, period in relevant):
        return stated
    return ""


def litigation_timing_problem(text: str, sources, fragments, *, section_id: str) -> str:
    """옛 소송 조각만으로 날짜 없는 현재 현황을 승인하지 않는다."""
    if section_id != "current_challenges" or not _snapshot(text):
        return ""
    relevant = []
    for source_id, original in sources.items():
        fragment = fragments.get(source_id)
        if (not _snapshot(original) or not (
                c.CURRENT_PERIOD_RE.search(original) or c.PRIOR_PERIOD_RE.search(original)
                or (fragment and (fragment.kind == LEGACY_KIND_LITIGATION
                    or fragment.location == LEGACY_KIND_LITIGATION)))):
            continue
        relevant.append((original, getattr(fragment, "reporting_period", "")))
    if not relevant:
        return ""
    return "" if litigation_snapshot_date(text, relevant) else c.LITIGATION_TIMING_UNSUPPORTED


def litigation_numeric_text(text: str, sources) -> str:
    """자기 원문의 정본 기간과 대조한 날짜만 수치 검사에서 분리한다.

    기간의 연도·월·일을 일반 숫자 풀에 넣지 않는다. 건수·금액 등 나머지
    숫자는 원문 대조를 그대로 통과해야 한다.
    """
    if not litigation_snapshot_date(text, sources):
        return text
    match = c.AS_OF_RE.search(text)
    return text[:match.start()] + text[match.end():] if match else text
