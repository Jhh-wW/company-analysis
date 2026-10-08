"""작성자가 명시한 공시 문서 이름과 실제 인용 종류를 대조하는 표지."""

import re

from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_BUSINESS_REPORT,
)

SOURCE_ATTRIBUTION_MISMATCH = "source_document_attribution_mismatch"
BUSINESS_REPORT_ATTRIBUTION_RE = re.compile(
    r"사업\s*보고서(?:는|가|에(?:서는|서|는)?|상(?:에는|에|으로)?)\s*"
)
BUSINESS_REPORT_NAME_RE = re.compile(r"사업\s*보고서|annual\s+report", re.IGNORECASE)
BUSINESS_REPORT_SOURCE_KINDS = frozenset({SOURCE_KIND_DART_BUSINESS_REPORT})
