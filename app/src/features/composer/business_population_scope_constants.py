"""명시된 매출 모집단과 투자 계획 부문을 비교하는 좁은 문법이다."""
import re

REVENUE_OWNER_RE = re.compile(r'(?P<owner>[가-힣A-Za-z0-9&]+(?:부문|사업))매출(?:액)?')
REVENUE_CONTRAST_RE = re.compile(
    r'매출(?:액)?(?:이|은|는)?[^.。;\n]{0,120}?감소[^.。;\n]{0,60}?(?:으나|지만|반면),?'
    r'(?P<owner>[가-힣A-Za-z0-9&]+)매출(?:액)?(?:의경우|은|는|이)?[^.。;\n]{0,180}?증가'
)
CLAIM_REVENUE_CONTRAST_RE = re.compile(
    r'매출(?:액)?[^.。;\n]{0,120}?감소[^.。;\n]{0,60}?(?:으나|지만|반면),?(?P<tail>[^.。;\n]+)'
)
ASSERTED_INCREASE_RE = re.compile(r'증가(?:하였|했다|하였다|했|하고|한다|한것|할것|하였다|했습니다)')
POPULATION_SOURCE_WINDOW = 350
INVESTMENT_PLAN_RE = re.compile(r'투자[^.。;\n]{0,80}?(?:예정|계획|추진할)')
SOURCE_INVESTMENT_PLAN_RE = re.compile(r'(?:계획[^.。;\n]{0,80}?투자|투자[^.。;\n]{0,80}?(?:예정|계획))')
WHOLE_COMPANY_RE = re.compile(r'(?:연결회사|회사|당사)(?:의)?(?:전체|전사)|전사(?:적|차원|투자)')
LOCAL_SECTION_RE = re.compile(r'^\[[^\]\n]*(?:부문|사업부)[^\]\n]*\]$')
SECTION_PART_RE = re.compile(r'\s*[-–—·/]\s*')
LOCAL_OWNER_SUFFIX_RE = re.compile(r'(?:사업부문|사업부|부문|사업|분야)$')
CLAUSE_BOUNDARY_RE = re.compile(r'(?<=[다요])\.|[。;\n]|(?<!\d)\.(?!\d)')
EXPLICIT_SUBJECT_RE = re.compile(r'(?:회사|당사|연결회사|전사)(?:전체)?(?:는|은|가|의)')
OTHER_SECTION_SUBJECT_RE = re.compile(r'[가-힣A-Za-z0-9]+(?:사업부문|사업부|부문|사업|분야)(?:은|는|이|가|의|에서)')
REVENUE_METRIC_RE = re.compile(r'매출(?:액)?')
QUANTITY_SUBJECT_RE = re.compile(r'수주량(?:은|는|이|가|도)')
INVESTMENT_WORD_RE = re.compile(r'투자')
PURPOSE_END_RE = re.compile(r'(?:을|를)?위(?:해|하여)')
PLAN_SUBJECT_END_RE = re.compile(r'(?:계획은|예정은|는|은)')
INVESTMENT_PURPOSE_PREFIX_RE = re.compile(r'^(?:이며|이고)?(?:향후|앞으로)?(?:투자계획으로)?')
INVESTMENT_PURPOSE_LIST_SEPARATOR_RE = re.compile(r'및|(?<!\d),|,(?!\d)')
INVESTMENT_PURPOSE_LIST_MIN_ITEMS = 2
INVESTMENT_PLAN_LABEL_END_RE = re.compile(r'^(?:으로|은)')

# 계약 금액·기간 행은 매출 구성이나 수익 우선순위를 직접 증명하지 않는다.
REVENUE_PRIORITY_RE = re.compile(
    r'(?:주요|주된|최대|핵심|가장큰)(?:수익원|매출원|매출발생원)|'
    r'매출(?:액)?(?:의|에서)?(?:대부분|과반|가장큰비중)|'
    r'매출(?:액)?비중(?:은|이|을|으로|의)|매출(?:액)?의?\d+(?:\.\d+)?%'
)
CONTRACT_PERIOD_RE = re.compile(
    r'\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:~|∼|～|–|—|-)(?:\d{4}[./-])?\d{1,2}[./-]\d{1,2}'
)
CONTRACT_AMOUNT_RE = re.compile(r'[+-]?\d[\d,]*(?:\.\d+)?')
CONTRACT_ROW_MIN_COLUMNS = 4
CONTRACT_PERIOD_COLUMN = 2
CONTRACT_AMOUNT_COLUMN = 3
EXPLICIT_REVENUE_PRIORITY_RE = re.compile(
    r'(?:주요|주된|최대|핵심|가장큰)(?:수익원|매출원|매출발생원)|'
    r'매출(?:액)?(?:의|에서)?(?:대부분|과반|가장큰비중)'
)
REVENUE_COMPOSITION_RE = re.compile(r'매출(?:액|수익|비중|구성)?')
REVENUE_PRIORITY_TARGET_END_RE = re.compile(r'으로|로서|이다|입니다|이며|이고|였다|이었다|[,.。;]')
REVENUE_PRIORITY_LEADING_SUBJECT_RE = re.compile(r'^(?:회사|당사|연결회사)(?:는|은|의)')
REVENUE_PRIORITY_FOREIGN_OWNER_RE = re.compile(r'(?:타사|다른회사|다른기업|고객사|자회사|종속기업|관계기업)(?:의|는|은|이|가)')
REVENUE_SHARE_RE = re.compile(r'\d+(?:\.\d+)?%')
REVENUE_PRIORITY_POSTPARTICLE_RE = re.compile(r'^(?:은|는|이|가)')
REVENUE_PRIORITY_PREPARTICLE_RE = re.compile(r'(?:의|이|가|은|는)$')
REVENUE_ITEM_COLUMN_RE = re.compile(r'(?:품목|제품|상품|사업|사업부문)')
REVENUE_AMOUNT_COLUMN_RE = re.compile(r'매출(?:액|수익)')
REVENUE_SHARE_COLUMN_RE = re.compile(r'(?:비중|비율|구성비)')
REVENUE_TABLE_OWNER_RE = re.compile(
    r'(?<![가-힣A-Za-z0-9])'
    r'(?P<owner>당사|회사|연결회사|타사|다른\s*회사|다른\s*기업|고객\s*회사|고객사|자회사|종속기업|관계기업)'
    r'(?:의|는|은|이|가)[^.。;\n|]*?매출(?:액|수익|비중|구성)?'
)
REVENUE_TABLE_OWNERS = frozenset({'당사', '회사', '연결회사'})

# 명시 부문 제품표의 품목을 같은 부문 밖으로 옮긴 주장만 비교한다.
PRODUCT_SECTION_EXCLUSION_RE = re.compile(
    r'(?P<owner>[가-힣A-Za-z0-9&]+(?:사업부문|사업부|부문))'
    r'(?:외에도|외의|밖에서|이아닌|을제외한|에속하지않는)'
)
PRODUCT_SECTION_HEADING_RE = re.compile(r'^\[(?P<owner>[^\]\n]+(?:부문|사업부))\]$')
PRODUCT_ITEM_HEADER_RE = re.compile(r'^(?:품목|제품|상품)$')
PRODUCT_ITEM_SPLIT_RE = re.compile(r'[\s·,/]+')
PRODUCT_ITEM_IGNORED_WORDS = frozenset({'등', '기타', '제품', '상품'})
PRODUCT_ITEM_MIN_CHARS = 2
PRODUCT_ASSERTION_BOUNDARY_RE = re.compile(r'[,;。\n]|(?<!\d)\.(?!\d)|(?:하며|이며|이고|지만)\s+')
PRODUCT_ASSERTION_DENIAL_RE = re.compile(r'판매하지|공급하지|아니(?:다|며|고)|않(?:는다|습니다)|없(?:다|습니다)')
