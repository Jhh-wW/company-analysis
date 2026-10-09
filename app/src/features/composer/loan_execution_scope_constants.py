"""대출 약정 표의 한도와 실제 실행을 구분하는 닫힌 문법."""
import re

LOAN_EXECUTION_PROBLEM = 'scope_condition_unbound'
LOAN_SOURCE_ROW_RE = re.compile(r'[;\n]')
LOAN_CLAUSE_RE = re.compile(r'(?<=[다요])[.!?]\s*|[;\n]|[|]')
LOAN_LIMIT_HEADER_RE = re.compile(r'(?:한도금액|약정한도|대출한도|한도액)')
LOAN_EXECUTION_HEADER_RE = re.compile(r'(?:실행금액|실행액|대출실행액)')
LOAN_TABLE_COLUMNS = 4
LOAN_INSTITUTION_COLUMN = 0
LOAN_PURPOSE_COLUMN = 1
LOAN_FAMILIES = (
    ('working', re.compile(r'(?:운영|운전)자금대출')),
    ('facility', re.compile(r'시설자금대출')),
    ('general', re.compile(r'일반자금대출')),
    ('receivable', re.compile(r'매출채권담보대출')),
)
LOAN_ACTUAL_ACTION_RE = re.compile(
    r'(?:제공)?받고있(?:다|으며|어)|받고(?=[가-힣A-Za-z0-9]+(?:은행|조합))|'
    r'받았(?:다|으며|고|습니다)|받는다|'
    r'차입(?:하고있|했다|하였|중)|실행(?:했다|하였|받았다|중)'
)
LOAN_PRESENT_ACTION_RE = re.compile(
    r'(?:제공)?받고있(?:다|으며|어)|받고(?=[가-힣A-Za-z0-9]+(?:은행|조합))|'
    r'받는다|차입(?:하고있|중)|실행중'
)
LOAN_COORDINATION_RE = re.compile(r'(?:(?:을|를|은|는|이|가|및|과|와|도|,|·|ㆍ|현재|실제|실제로))*')
LOAN_NON_ACTUAL_RE = re.compile(r'않|못|아니|예정|계획|가능|할수|경우|가정|조건')
LOAN_NON_ACTUAL_AFTER_RE = re.compile(r'^(?:지|을|다고|라고|이라고|인)?(?:않|못|아니|예정|계획|가능|경우|가정|조건)')
LOAN_AMOUNT_RE = re.compile(r'[+-]?\d[\d,]*(?:\.\d+)?')
LOAN_EXECUTED_AMOUNT_RE = re.compile(
    r'^(?:의)?실행(?:금액|액)(?:은|이)?(?P<amount>[+-]?\d[\d,]*(?:\.\d+)?)'
)
LOAN_AMOUNT_UNIT_RE = re.compile(r'\d[\d,]*(?:\.\d+)?(?:억원|백만원|천원|만원|원)')
LOAN_INSTITUTION_RE = re.compile(r'[가-힣A-Za-z0-9]+?(?:은행|조합)')
LOAN_INSTITUTION_PREFIX_RE = re.compile(r'^(?:한편)?(?:(?:회사|당사|연결회사)(?:는|은|가|이)|및|과|와)*$')
LOAN_INSTITUTION_GENERIC_RE = re.compile(r'^(?:은행|조합)$')
LOAN_DIRECT_SUBJECT_RE = re.compile(
    r'(?P<own>회사|당사|연결회사)(?:는|은|가|이)|'
    r'(?P<foreign>고객사|고객|차주|자회사|종속기업|관계기업|타사|다른회사)(?:는|은|이|가)'
)
LOAN_EMPTY_EXECUTION_RE = re.compile(r'[-–—]')
