"""결산 금융관계 표를 현재 지속으로 넓히지 않는 닫힌 문형."""

import re

FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED = 'financial_snapshot_current_relation_unsupported'
FINANCIAL_RELATION_SECTION = 'operations_partners'
FINANCIAL_FILING_KINDS = frozenset({'dart_audit_report', 'dart_periodic_report'})
FINANCIAL_CURRENT_OFFICIAL_KINDS = frozenset({'official_identity_verified_web_page'})
FINANCIAL_GUARANTEE_ROLE_RE = re.compile(r'(?:지급|연대)?보증\s*(?:제공|관계|보험|을\s*(?:받|제공))')
FINANCIAL_INVESTMENT_ROLE_RE = re.compile(r'금융\s*자산\s*투자\s*대상|투자\s*대상')
FINANCIAL_RELATION_RE = re.compile(
    r'(?:지급|연대)?보증\s*(?:제공|관계|보험|을\s*(?:받|제공))'
    r'|금융\s*자산\s*투자\s*대상|투자\s*대상'
)
FINANCIAL_CURRENT_RELATION_RE = re.compile(
    r'(?:유지하|이어가|지속하|지속되)고\s*있(?:다|습니다|음|는)'
    r'|기능(?:하고\s*있(?:다|습니다|음|는)|한다|합니다)'
    r'|보증(?:을|도)?\s*(?:제공받|받|제공하)고\s*있(?:다|습니다|음|는)'
)
FINANCIAL_SNAPSHOT_TABLE_RE = re.compile(r'보증\s*금액|보증\s*내역|당기말|전기말')
FINANCIAL_HISTORICAL_PREFIX_RE = re.compile(
    r'^\s*(?:(?:20\d{2}\s*년\s*(?:말|기말|\d{1,2}\s*월\s*\d{1,2}\s*일)'
    r'|20\d{2}[-.]\d{1,2}[-.]\d{1,2}|당기말|전기말)\s*(?:기준|당시|현재)(?:으로)?\s*'
    r'|당시\s+)'
)
FINANCIAL_REAFFIRMED_CURRENT_RE = re.compile(r'현재|지금|오늘|이후|이래|까지')
FINANCIAL_CURRENT_SOURCE_RE = re.compile(r'현재|지금')
FINANCIAL_SOURCE_UNCERTAIN_RE = re.compile(r'예정|계획|조건|경우|만약|않|아니|종료|중단|있었다')
FINANCIAL_SOURCE_CLAUSE_RE = re.compile(r'[.!?;\n]')
FINANCIAL_PROVIDER_COLUMN_RE = re.compile(r'(?:^|;)\s*([^;|]+)\|')
FINANCIAL_PROVIDER_FOOTNOTE_RE = re.compile(r'\([^)]*\)|[*]')
FINANCIAL_PROVIDER_LABEL_RE = re.compile(r'단위|구\s*분|제공자|보증\s*금액|보증\s*내역|합계|기타')
FINANCIAL_PROVIDER_MIN_CHARS = 2
FINANCIAL_PROVIDER_MENTION_SUFFIX = r'(?=$|[\s,.;:]|(?:은|는|이|가|과|와|의|로부터|에서|에|를|을)(?=$|[\s,.;:]))'
FINANCIAL_PROVIDER_OTHER_ACTOR_RE = re.compile(r'의\s*(?:자회사|계열사|관계사|협력사)')
