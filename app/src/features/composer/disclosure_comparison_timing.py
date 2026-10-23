"""닫힌 과거 비용비교만 자기 공시의 명시 보고기간 말일로 결속한다."""

from datetime import date

from src.features.composer import disclosure_comparison_timing_constants as c
from src.features.composer.disclosure_snapshot_timing import _kind
from src.features.composer.disclosure_snapshot_timing_constants import FILING_KINDS
from src.features.composer.disclosure_transaction_timing_constants import AUDIT_TITLE_RE


def comparison_period_required(claim):
    return bool(c.RELATIVE_COMPARISON_RE.search(claim) and c.PAST_COMPARISON_RE.search(claim)
                and c.FINANCIAL_METRIC_RE.search(claim))


def disclosure_comparison_date(claim, sources, fragments, *, company_name="", baseline_date=""):
    """자료 제목·공시일로 연도를 만들지 않고 전송된 원공시 기간만 소비한다."""
    if not comparison_period_required(claim) or c.CURRENT_OR_FUTURE_RE.search(claim):
        return ""
    company = c.OWN_COMPANY_RE.sub("", company_name)
    periods = set()
    document_ids = set()
    if not sources:
        return ""
    for source_id, text in sources.items():
        source = fragments.get(source_id)
        if source is None or _kind(source) not in (*FILING_KINDS, "판관비"):
            return ""
        if company:
            publisher = c.OWN_COMPANY_RE.sub("", getattr(source, "publisher", "") or getattr(source, "source_publisher", ""))
            if publisher != company:
                return ""
        period = getattr(source, "reporting_period", "")
        match = c.PERIOD_RE.fullmatch(period)
        if (not match or not c.CURRENT_HEADER_RE.search(text) or not c.PRIOR_HEADER_RE.search(text)
                or not c.FINANCIAL_METRIC_RE.search(text)):
            return ""
        try:
            start, end = (date.fromisoformat(value) for value in match.groups())
            baseline = date.fromisoformat(baseline_date) if baseline_date else date.max
        except ValueError:
            return ""
        if start > end or end > baseline:
            return ""
        title = AUDIT_TITLE_RE.fullmatch(getattr(source, "title", "") or getattr(source, "document_title", ""))
        if title and (int(title[1]), int(title[2])) != (end.year, end.month):
            return ""
        periods.add((start, end))
        document_id = getattr(source, "document_id", "") or getattr(source, "source_document_id", "")
        if not document_id:
            return ""
        document_ids.add(document_id)
    if len(periods) != 1 or len(document_ids) != 1:
        return ""
    return next(iter(periods))[1].isoformat()


def disclosure_comparison_timing_problem(claim, sources, fragments, *, company_name="", baseline_date=""):
    if not comparison_period_required(claim):
        return ""
    # 보도·홈페이지만 인용한 보통 비교는 기존 의미 검수의 소유다.
    if not any(_kind(fragments.get(source_id)) in (*FILING_KINDS, "판관비") for source_id in sources):
        return ""
    return "" if disclosure_comparison_date(claim, sources, fragments, company_name=company_name,
        baseline_date=baseline_date) else c.COMPARISON_PERIOD_UNBOUND
