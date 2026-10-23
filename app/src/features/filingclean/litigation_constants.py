"""소송 문장의 앞 문맥과 같은 공시의 보고기간을 보존하는 규칙."""

import re

from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_LITIGATION, LEGACY_KIND_RELATED_PARTY, LEGACY_KIND_SG_AND_A,
)

PERIOD_FRAGMENT_KINDS = frozenset({LEGACY_KIND_LITIGATION, LEGACY_KIND_RELATED_PARTY, LEGACY_KIND_SG_AND_A})

LITIGATION_PREFIX_LOOKBACK = 160
LITIGATION_PREFIX_RE = re.compile(
    r"(?:\(\d+\)\s*)?(?:(?:당기말|전기말|보고기간말|기말)\s*)?현재\s*"
    r"(?:회사|당사)(?:가|는)\s*(?:(?:원고|피고)(?:로|인)\s*)?$"
)
RECEIPT_RE = re.compile(r"\d{14}")
CORP_CODE_RE = re.compile(r"\d{8}")
DATE_VALUE_RE = re.compile(r"\d{8}")
DATE_TEXT_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
XML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
XML_ENCODINGS = ("utf-8", "cp949", "euc-kr")
