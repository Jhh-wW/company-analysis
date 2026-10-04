"""같은 기사 안의 닫힌 법인 명칭 선언만 읽는 경계."""

import re

ARTICLE_ALIAS_MIN_CHARS = 3
ARTICLE_ALIAS_MAX_CHARS = 80
ARTICLE_ALIAS_NAME_RE = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9 &.\-]*")
ARTICLE_ALIAS_ROLE_RE = re.compile(
    r"대표|회장|사장|직원|자회사|계열사|경쟁사|협력|공동|파트너|별도|다른\s*법인|소속|브랜드|제품"
)
ARTICLE_ALIAS_DECLARATION_SUFFIX = (
    r"\(\s*(?:이하\s+)?['\"‘’“”]?(?P<alias>[가-힣A-Za-z0-9][가-힣A-Za-z0-9 &.\-]*)['\"‘’“”]?\s*\)"
    r"(?=\s*(?:은|는|이|가|,|，|:|：))"
)
ARTICLE_ALIAS_DECLARATION_RE = re.compile(
    r"(?P<full>[가-힣A-Za-z0-9][가-힣A-Za-z0-9 &.\-]*)\s*" + ARTICLE_ALIAS_DECLARATION_SUFFIX
)
ARTICLE_ALIAS_FOREIGN_ROLE_RE = r"(?:별도\s*법인|다른\s*법인|경쟁사|자회사|계열사|공동사업자|협력사)\s*['\"‘’“”]?{name}(?=$|[\s,，.。]|은|는|이|가)"
ARTICLE_ALIAS_REVERSE_FOREIGN_ROLE_RE = (
    r"(?<!\w){name}['\"‘’“”]?\s*(?:은|는|이|가)\s*[^.!?。\n]{{0,80}}"
    r"(?:별도\s*법인|다른\s*법인|자회사|계열사|경쟁사|공동사업자|협력사)"
    r"(?:다|이다|입니다|였다)(?=$|[\s.!?。])"
)
