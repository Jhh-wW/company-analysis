"""수집기가 원공시에서 읽은 명시 날짜 범위만 공시 기간 필드로 허용한다."""

from datetime import date
import re

from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_AUDIT_REPORT, SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT, SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
)

DART_PERIOD_SOURCE_KINDS = frozenset({SOURCE_KIND_DART_AUDIT_REPORT, SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT, SOURCE_KIND_DART_QUARTERLY_REPORT, SOURCE_KIND_DART_SEMIANNUAL_REPORT})
FILING_PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})")


def filing_period_is_valid(value):
    if not isinstance(value, str) or not (match := FILING_PERIOD_RE.fullmatch(value)):
        return False
    try:
        start, end = (date.fromisoformat(part) for part in match.groups())
    except ValueError:
        return False
    return start <= end
