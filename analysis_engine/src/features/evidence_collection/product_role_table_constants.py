"""자기 사업부문 선언과 제품 현황표를 연결하는 좁은 정형 계약."""

import re
from features.evidence_collection.source_context_constants import SELF_SECTION_ORIGIN

MAX_TABLE_CHARS = 12000
MAX_DECLARATIONS = 32
MAX_SCOPE_PREFIX_CHARS = 200000
TABLE_AUXILIARY_INDEX_MULTIPLIER = 2
ITEM_HEADER = '품목'
USE_HEADER = '구체적용도'
TYPE_HEADER = '매출유형'
ACTOR_HEADERS = frozenset({'회사명', '업체명', '법인명', '사업부문'})
OFFER_TYPES = frozenset({'제품', '상품', '서비스', '용역'})
EMPTY_VALUES = frozenset({'', '-', '기타', '합계', '계', '해당없음', '없음'})
PRODUCT_HEADING_RE = re.compile(r'주요\s*(?:제품|상품)[^\n]{0,60}현황')
EXCLUDED_RE = re.compile(r'회계|재고|원가|가정|가상|예시|업계|타사|미래|예정|계획|폐지|중단|단종')
OVERVIEW_RE = re.compile(r'(?m)^[ \t]*(?:I|Ⅰ)[.．]\s*회사(?:의)?\s*개요\s*$')
MAJOR_RE = re.compile(r'(?m)^[ \t]*(?:[IVXⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[.．]\s*[^\n]+')
DECLARATION_TITLE_RE = re.compile(r'(?:^|\n)[^\n]{0,8}주요\s*사업의\s*내용\s*\n\s*')
DECLARATION_SENTENCE_RE = re.compile(r'당사는\s+[^\n.!?]{1,1800}[.]')
LOCAL_COMPANY_RE = re.compile(r'(?:^|[\n.!?])\s*([가-힣A-Za-z0-9&·._ -]{1,60}(?:주식회사|㈜|\(주\)))(?:는|은|이|가|의)\s*')
COMPANY_LABEL_RE = re.compile(r'(?m)^[ \t]*([가-힣A-Za-z0-9&·._ -]{1,60}(?:주식회사|㈜|\(주\)))[ \t]*$')
RELATED_ACTOR_HEADING_RE = re.compile(r'(?m)^[^\n]{0,12}(?:종속회사|관계법인|연결대상\s*회사)[^\n]{0,30}(?:현황|개황|목록)[^\n]*$')
SELF_NAME_RE = re.compile(r'당사의\s*(?:명칭|상호|회사명)은\s*')
SELF_SWITCH_RE = re.compile(r'(?:^|[\n.!?])\s*(?:당사|회사|본사)(?:는|가)\s*')
LEGAL_NAME_RE = re.compile(r'\s+|주식회사|\(주\)|㈜')
TABLE_ATTRIBUTION_EXCLUDED_RE = re.compile(
    r'(?:협력회사|타사|다른\s*회사|경쟁사)의\s*제품[^\n]{0,80}(?:인용|자료|현황)'
    r'|산업의\s*(?:일반적인|일반)\s*제품\s*현황'
    r'|(?:향후|미래)\s*비전[^\n]{0,100}(?:다음|아래)\s*제품[^\n]{0,80}(?:목표|계획)'
)
FICTIONAL_TABLE_DECLARATION_RE = re.compile(
    r'(?m)^[ \t]*(?:다음은\s+)?가상의\s*(?:회사|기업)(?:가|를|을)[^\n.!?]{0,180}'
    r'가정한\s*(?:실습\s*)?예시입니다[.]'
)
