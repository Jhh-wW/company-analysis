"""공식 원문에서 뉴스 전용 법인 약칭을 읽는 닫힌 문법과 상한."""

from typing import Final


MAX_OFFICIAL_NEWS_ALIAS_ADDITIONS: Final[int] = 1
ALIAS_ATOM_PATTERN: Final[str] = r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9&.-]{1,39}"
ALIAS_DEFINITION_PATTERN: Final[str] = (
    r"\(이하\s+(?:"
    rf"(?P<plain>{ALIAS_ATOM_PATTERN})|"
    rf"'(?P<single>{ALIAS_ATOM_PATTERN})'|"
    rf'"(?P<double>{ALIAS_ATOM_PATTERN})"|'
    rf"‘(?P<curly_single>{ALIAS_ATOM_PATTERN})’|"
    rf"“(?P<curly_double>{ALIAS_ATOM_PATTERN})”"
    r")(?:,\s*대표\s+[가-힣A-Za-z][가-힣A-Za-z .-]{0,39})?\)"
)
# 왼쪽 이름 전체를 비교한다. 수식어가 붙은 무인용 문장은 추측해 잘라내지 않는다.
LEGAL_NAME_BOUNDARY_PATTERN: Final[str] = r"[\n.!?;:‘’“”\"']"
SENTENCE_BOUNDARY_PATTERN: Final[str] = r"[\n.!?;]"
UNSAFE_DEFINITION_CONTEXT_PATTERN: Final[str] = (
    r"아니|아닌|아님|아닙|아닐|않|없|못하|하지|말라|금지|거부|부인|부정|"
    r"잘못|오류|예시|가정|가칭|오인|오기|정정|철회|"
    r"다른\s*(?:회사|법인)|동명|자회사|계열사|종속(?:회사|기업)|관계회사|합작회사|"
    r"제품명|브랜드|서비스명|플랫폼명|상표|과거|옛\s*이름"
)
GENERIC_COMPANY_REFERENCES: Final[frozenset[str]] = frozenset({
    "회사", "당사", "본사", "본회사", "동사", "동법인", "당법인", "법인", "기업",
    "그룹", "지배회사", "지배기업", "연결회사", "연결기업", "종속기업", "서비스",
    "제품", "플랫폼", "브랜드", "사업", "thecompany", "company", "group",
})
DART_FRAGMENT_LOCATION_PATTERN: Final[str] = r"([0-9]{1,10})-([0-9]{1,10})"
WEB_FRAGMENT_INDEX_PATTERN: Final[str] = r"[0-9]{1,10}"
DART_VERIFIED_IDENTITY_CHECK: Final[str] = "verified_match"
