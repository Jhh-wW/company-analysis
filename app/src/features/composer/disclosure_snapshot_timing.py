"""결산 현황을 현재 지속으로 읽지 않으며 명시된 사실 기준일만 운반한다."""

from datetime import date

from src.features.composer import disclosure_snapshot_timing_constants as c
from src.features.composer.disclosure_transaction_timing import _annual_end, _company


def _kind(source):
    kind = getattr(source, "formal_source_kind", "") or getattr(source, "kind", "")
    value = getattr(kind, "value", kind)
    return getattr(source, "source_type", "") if value == "기타" else value


def _source_end(text, source):
    period = getattr(source, "reporting_period", "")
    if period and "/" in period:
        try:
            start, end = (date.fromisoformat(value) for value in period.split("/"))
        except (ValueError, TypeError):
            return ""
        return end.isoformat() if start <= end else ""
    # 옛 조각은 같은 원문의 명시 회계연도 종료일과 감사보고서 제목을 함께 대조한다.
    from types import SimpleNamespace
    view = SimpleNamespace(reporting_period=period, title=getattr(source, "title", "") or
                           getattr(source, "document_title", ""))
    end = _annual_end(text, view)
    return end.isoformat() if end else ""


def _snapshot_sources(text, sources, fragments):
    if not c.FINANCIAL_STATUS_RE.search(text):
        return []
    result = []
    for source_id, original in sources.items():
        source = fragments.get(source_id)
        if source is None or _kind(source) not in c.FILING_KINDS:
            continue
        for clause in c.CLAUSE_SPLIT_RE.split(original):
            if (c.FINANCIAL_STATUS_RE.search(clause) and
                    (c.RELATIVE_SNAPSHOT_RE.search(clause) or c.PRIOR_SNAPSHOT_RE.search(clause))):
                end = "" if c.PRIOR_SNAPSHOT_RE.search(clause) else _source_end(original, source)
                result.append((source_id, clause, end))
    return result


def _dated_claim(text):
    match = c.DATED_PREFIX_RE.match(text)
    if not match or c.REAFFIRMED_RE.search(text[match.end():]):
        return ""
    try:
        return date(int(match["year"]), int(match["month"] or match["iso_month"]),
                    int(match["day"] or match["iso_day"])).isoformat()
    except ValueError:
        return ""


def _same_current_claim(text, sources, fragments, baseline_date):
    claim = c.WHITESPACE_RE.sub("", text).rstrip(".")
    publishers = {
        _company(getattr(fragments.get(source_id), "publisher", "") or
                 getattr(fragments.get(source_id), "source_publisher", ""))
        for source_id in sources if _kind(fragments.get(source_id)) in c.FILING_KINDS
    }
    if len(publishers) != 1 or "" in publishers:
        return False
    for source_id, original in sources.items():
        source = fragments.get(source_id)
        if source is None or _kind(source) not in c.OFFICIAL_CURRENT_KINDS:
            continue
        collected = getattr(source, "source_collected_on", "") or getattr(source, "collected_at", "")
        if not baseline_date or collected != baseline_date:
            continue
        publisher = _company(getattr(source, "publisher", "") or getattr(source, "source_publisher", ""))
        if publisher not in publishers:
            continue
        # 수집일만 현재라고 과거 공시를 현황 증명으로 바꾸지 않는다. 같은 주체·행동의
        # 현재 공식 원문이 후보 전체를 직접 담은 경우만 보완 근거로 인정한다.
        for clause in c.CLAUSE_SPLIT_RE.split(original):
            if (c.CURRENT_SUBJECT_RE.search(clause) and not c.RELATIVE_SNAPSHOT_RE.search(clause)
                    and not c.PRIOR_SNAPSHOT_RE.search(clause) and not c.SOURCE_DATED_STATUS_RE.search(clause)
                    and not c.CURRENT_PROOF_UNCERTAIN_RE.search(clause)
                    and claim == c.WHITESPACE_RE.sub("", clause).rstrip(".")):
                return True
    return False


def disclosure_snapshot_date(text, sources, fragments, *, baseline_date=""):
    """주장이 직접 밝힌 날짜와 자기 공시의 보고기간말이 모두 같을 때만 반환한다."""
    relevant = _snapshot_sources(text, sources, fragments)
    stated = _dated_claim(text)
    if baseline_date and stated and stated > baseline_date:
        return ""
    return stated if relevant and stated and all(end == stated for _, _, end in relevant) else ""


def disclosure_snapshot_context_date(fragment):
    """작가·검수에게 같은 자기 조각의 확정 기준일만 보여 준다."""
    relevant = _snapshot_sources(fragment.text, {fragment.fragment_id: fragment.text}, {fragment.fragment_id: fragment})
    ends = {end for _, _, end in relevant}
    return next(iter(ends)) if len(ends) == 1 and "" not in ends else ""


def disclosure_snapshot_timing_problem(text, sources, fragments, *, baseline_date=None):
    """특정 장·등급에 관계없이 공시의 결산 현황 현재화를 차단한다."""
    relevant = _snapshot_sources(text, sources, fragments)
    if not relevant or not (c.CURRENT_STATUS_RE.search(text) or c.DATED_PREFIX_RE.match(text)):
        return ""
    if disclosure_snapshot_date(text, sources, fragments, baseline_date=baseline_date or ""):
        return ""
    if c.DATED_PREFIX_RE.match(text):
        return c.SNAPSHOT_TIMING_UNSUPPORTED
    if baseline_date and all(end == baseline_date for _, _, end in relevant):
        return ""
    if _same_current_claim(text, sources, fragments, baseline_date):
        return ""
    return c.SNAPSHOT_TIMING_UNSUPPORTED
