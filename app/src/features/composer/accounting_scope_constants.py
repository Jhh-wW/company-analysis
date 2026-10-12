"""회계 범위 단정과 자기 인용 결속의 좁은 문법."""
import re

ACCOUNTING_SCOPE_UNBOUND = 'accounting_scope_unbound'
ACCOUNTING_TABLE_SCOPE_LABELS = {'consolidated': '연결 재무제표', 'separate': '별도 재무제표'}
SCOPE_KIND = {'연결': 'consolidated', '별도': 'separate', '개별': 'separate'}
FINANCIAL_TERM = r'재무제표|매출(?:액)?|영업이익|당기순이익|순이익|손익|실적|재무정보'
SCOPE_PREFIX_RE = re.compile(
    rf'(?P<basis>연결|별도|개별)\s*(?:의\s*)?(?:재무제표\s*(?:기준)?|기준|{FINANCIAL_TERM})'
)
SCOPE_SUFFIX_RE = re.compile(
    rf'(?:{FINANCIAL_TERM})\s*[\[(]\s*(?P<basis>연결|별도|개별)\s*(?:기준)?\s*[\])]'
)
CONSOLIDATED_ACTOR_RE = re.compile(r'연결\s*회사(?:는|가|의|에서|\s|$)')
CLAUSE_RE = re.compile(r'[;\n]+|(?<=[.!?])\s+')
WORD_RE = re.compile(r'[가-힣A-Za-z][가-힣A-Za-z0-9_\-]*')
PARTICLE_RE = re.compile(r'(?:으로는|으로|에서는|에서|에게|에는|은|는|이|가|을|를|의|와|과|로)$')
BASIS_WORD_RE = re.compile(r'연결|별도|개별|재무제표|기준(?:으로)?')
SCOPE_HEADING_RE = re.compile(
    r'\s*(?:[\[(]\s*)?(?:연결|별도|개별)\s*(?:재무제표\s*(?:기준)?|기준|손익계산서)?\s*(?:[\])])?\s*'
)
CONTENT_STOPWORDS = frozenset({'회사', '당사', '본사', '기업', '기준', '재무제표', '표', '구성표', '한다', '있다', '이다'})
MIN_CONTENT_TOKEN_CHARS = 2
PRODUCT_HEADER_RE = re.compile(r'^(?:품목|제품|상품|제품명|상품명|제품[·/]서비스|제품및서비스)$')
USE_HEADER_RE = re.compile(r'^(?:구체적용도|용도|적용용도|매출액|매출유형)$')
FINANCIAL_METRIC_RE = re.compile(r'^(?:매출액?|영업이익|당기순이익|순이익|매출원가|자산총계|부채총계)$')
TABLE_ROW_RE = re.compile(r'[;\n]+')
NUMERIC_CELL_RE = re.compile(r'\d')
