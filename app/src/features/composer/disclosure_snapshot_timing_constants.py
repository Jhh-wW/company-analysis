"""결산일 현재의 보증·담보 현황과 자료 확인일을 구분하는 경계."""

import re

SNAPSHOT_TIMING_UNSUPPORTED = "disclosure_snapshot_date_unsupported"
FINANCIAL_STATUS_RE = re.compile(r"담보|(?:지급|연대)?보증")
RELATIVE_SNAPSHOT_RE = re.compile(r"(?:보고기간\s*(?:종료일|말)|당기말|기말)\s*현재")
PRIOR_SNAPSHOT_RE = re.compile(r"전기말\s*현재")
CURRENT_STATUS_RE = re.compile(r"현재|지금|있다|있으며|있는|있습니다|제공한다|제공받는다|유지한다|이어간다")
REAFFIRMED_RE = re.compile(r"현재|지금|이후에도|계속|여전히|기준일을\s*지나")
CLAUSE_SPLIT_RE = re.compile(r"(?<=[다요])[.!?]\s*|[;\n]")
DATED_PREFIX_RE = re.compile(
    r"^(?P<year>\d{4})(?:년\s*(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일|"
    r"-(?P<iso_month>\d{2})-(?P<iso_day>\d{2}))\s*(?:기준|현재)(?:\s*공시상)?\s*[,，]?\s*"
)
OFFICIAL_CURRENT_KINDS = frozenset({"official_identity_verified_web_page", "공식 홈페이지", "회사 공식 웹", "공식 웹"})
FILING_KINDS = frozenset({"공시", "dart_audit_report", "dart_business_report", "특수관계자"})
CURRENT_SUBJECT_RE = re.compile(r"현재|지금")
SOURCE_DATED_STATUS_RE = re.compile(r"\d{4}(?:년\s*\d{1,2}월\s*\d{1,2}일|-\d{2}-\d{2})\s*(?:기준|현재)")
WHITESPACE_RE = re.compile(r"\s+")
CURRENT_PROOF_UNCERTAIN_RE = re.compile(r"만약|경우|조건|가정|가능|아니|않|없(?:다|습니다)|할\s*수")
SNAPSHOT_DATE_LABEL = "공시 현황 기준일"
SNAPSHOT_DATE_GUIDE = "자료 발행일·수집일이 아니다. 결산 현황은 이 날짜 기준으로만 서술한다"
