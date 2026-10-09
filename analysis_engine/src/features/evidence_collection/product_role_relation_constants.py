"""공식 제품 절의 현재 상품·제공 역할을 결속하는 좁은 문형."""

import re

SLOT_ID = 'portfolio:product_role'
REASON_CODE = 'direct_pattern:current_product_role_relation'
AUXILIARY_SCORE_MILLIS = 125
MAX_SENTENCE_CHARS = 1200
SENTENCE_RE = re.compile(r'(?<=[가-힣)])[.!?]\s*|[;\n]+')
OFFICIAL_HEADING_RE = re.compile(r'주요\s*(?:제품|상품)|(?:제품|상품)\s*및\s*서비스|사업의\s*(?:현황|개요)|사업\s*현황')
EXCLUDED_HEADING_RE = re.compile(r'미래|비전|계획|예정|가정|전망|경력|회계|업계|산업의\s*특성')
SUBJECT_RE = re.compile(
    r'^(?P<actor>당사의\s*[가-힣A-Za-z0-9 ·-]{1,40}(?:사업부|부문)|당사|회사|본사|연결회사|'
    r'[가-힣A-Za-z0-9&·._ -]{1,60}(?:㈜|주식회사|\(주\)|사업부|부문))'
    r'\s*(?:는|은|이|가)\s*'
)
EXCLUDED_RE = re.compile(
    r'타사|다른\s*회사|경쟁사|고객사(?:는|가|의)|업계|업체들|기업들|'
    r'소개|인용|주장|밝힙|밝힌|라고|다고|예를\s*들|예시|가정|가령|경우|예정|계획|향후|전망|예상|'
    r'중단|종료|단종|과거에는|[12][0-9]{3}년\s*당시|'
    r'(?:제조|생산|제공|공급)하지|하지\s*않|하지\s*못|'
    r'계정|회계|장부|재고자산|공정가치|자산화|수익인식'
)
MANUFACTURING_RE = re.compile(
    r'^(?P<objects>[^.!?;\n]{2,350}?)(?:등을|을|를)\s*'
    r'(?:생산|제조)하는\s+'
    r'(?P<role>[가-힣A-Za-z0-9 ·-]{2,70}(?:제품|상품|부품))\s*'
    r'전문\s*(?:제조|생산)회사(?:로서|입니다|이다)'
)
GENERIC_OBJECT_RE = re.compile(r'^(?:여러|다양한|각종|혁신적인|새로운)?\s*(?:제품|상품|서비스|솔루션)(?:\s*등)?$')
EMBEDDED_SUBJECT_RE = re.compile(
    r'(?:당사|회사|본사|고객(?:사)?|협력사|경쟁사|종속회사|'
    r'[^,\s]{1,60}(?:주식회사|㈜|\(주\)))(?:는|은|이|가)\s+'
)
SERVICE_ROLE_RE = re.compile(
    r'(?P<function>[가-힣A-Za-z0-9 ·-]{2,70})\s*서비스(?:를|을)\s*제공하는\s+'
    r'(?P<name>[가-힣A-Za-z0-9_-]{2,30})\s*사업부(?:를|을)\s*주력으로\s*하고\s*있(?:습니다|다)'
)
CURRENT_ITEM_END_RE = re.compile(r'^(?:해당|이|본|동)\s*(?:제품|상품|서비스)(?:은|는|이|가|의\s*제공은)\s*[^.!?;\n]{0,30}(?:단종|종료|중단)')
NAME_BOUNDARY_CHARS = '가-힣A-Za-z0-9_-'
LEGAL_NAME_RE = re.compile(r'\s+|주식회사|\(주\)|㈜')
SELF_ACTORS = frozenset({'당사', '회사', '본사', '연결회사'})
MAX_CONTEXT_EVENTS = 4096
EXAMPLE_MARKER_RE = re.compile(
    r'예(?:를\s*들어|를\s*들면|컨대)[\s\S]{0,300}?'
    r'(?:해\s*보겠습니다|가정(?:합니다|한다|해)|이렇습니다|요청(?:합니다|한다)|정의(?:합니다|한다))|'
    r'(?:프롬프트|요청|실습|프로젝트\s*규칙)의?\s*예시\s*[:：]'
)
HYPOTHETICAL_MARKER_RE = re.compile(r'가정|가상|가령|해\s*보겠습니다')
FICTIONAL_COMPANY_DECLARATION_RE = re.compile(
    r'^[ \t]*(?:다음은\s+)?가상의\s*(?:회사|기업)'
    r'(?:가[^\n.!?]{0,180}가정합니다|입니다|(?:을|를)\s*대상으로[^\n.!?]{0,180}실습입니다)'
    r'[.!?](?:\s|$)', re.MULTILINE,
)
ACTUAL_CASE_HEADING_RE = re.compile(
    r'^(?:실제\s*(?:회사|고객|도입|적용)\s*(?:적용\s*)?사례|고객\s*성공\s*사례)[\s:：]*$'
)
ACTUAL_PREFIX_RE = re.compile(r'^실제로?\s+')
ACTUAL_ACTION_RE = re.compile(
    r'[가-힣]+(?:했다|했(?:으며|고)|하였(?:다|습니다|으며|고)|했습니다|한다|합니다|하고\s*있(?:다|습니다)|되었(?:다|습니다))'
)
