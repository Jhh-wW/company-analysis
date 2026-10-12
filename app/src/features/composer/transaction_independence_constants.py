"""계약의 추가·독립 관계를 자기 인용에 결속하는 표지."""

import re

TRANSACTION_INDEPENDENCE_UNBOUND = 'transaction_independence_unbound'
TRANSACTION_SPLIT_RE = re.compile(r'[.!?。\n;]+|(?:하였|했|하|되었|되)(?:고|으며|지만)\s*')
TRANSACTION_NOUN_RE = re.compile(r'계약|약정')
TRANSACTION_ACTION_RE = re.compile(r'체결|유지|맺')
INDEPENDENCE_MARKER = r'(?:별도로도|별도로|별도의|별개로|추가로|추가적인|추가의|별도|추가)'
INDEPENDENCE_MARKER_RE = re.compile(INDEPENDENCE_MARKER)
QUALIFIED_TRANSACTION_RE = re.compile(
    rf'(?P<before>{INDEPENDENCE_MARKER})\s*(?P<object>[가-힣A-Za-z0-9·/\s]{{0,60}}?)(?:계약|약정)'
    rf'|(?:계약|약정)(?:을|를|도|은|는)?\s*(?P<after>{INDEPENDENCE_MARKER})\s*(?:체결|유지|맺)'
)
ACCOUNTING_PREFIX_RE = re.compile(r'별도(?:의)?\s*(?:재무제표|재무|회계|기준)')
TRANSACTION_REPORTING_RE = re.compile(r'공시한|공개한|보고한|기재한|발표한')
TRANSACTION_ACTIVITY_TERM_LIMIT = 3
TRANSACTION_WORD_RE = re.compile(r'[가-힣A-Za-z0-9]{2,}')
TRANSACTION_GENERIC_WORDS = frozenset({
    '회사', '회사는', '당사', '당사는', '연결회사', '연결회사는', '본사', '본사는',
    '계약', '계약을', '계약은', '계약도', '약정', '약정을', '약정은',
    '체결', '체결하였다', '체결했다', '체결한다', '체결하였습니다', '체결하고',
    '유지', '유지한다', '유지하고', '유지하고있다', '있다', '있으며',
    '별도로도', '별도로', '별도의', '별개로', '추가로', '추가적인', '추가의',
    '별도', '추가', '다수', '다수의',
})
TRANSACTION_NONACTUAL_RE = re.compile(
    r'않|아니|없|미체결|(?:가정|추정)(?:하|했|한|되|된|임|이다|이며|입니다|으로)'
)
TRANSACTION_SUBJECT_RE = re.compile(r'(?P<actor>[가-힣A-Za-z0-9㈜()]+)(?:은|는|이|가)\s')
TRANSACTION_GENERIC_ACTORS = frozenset({'회사', '당사', '연결회사', '본사'})
TRANSACTION_FOREIGN_ACTORS = frozenset({'타사', '다른회사', '협력회사', '경쟁회사', '경쟁사'})
