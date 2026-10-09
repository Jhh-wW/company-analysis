"""명시 법인 이름에 붙인 소속 관계를 자기 인용으로 확인하는 문법."""

import re

RELATION_KINDS = {
    "종속회사": "subsidiary", "종속기업": "subsidiary", "자회사": "subsidiary",
    "계열사": "affiliate", "계열회사": "affiliate",
    "관계회사": "associate", "관계기업": "associate",
}
RELATION_PATTERN = "|".join(RELATION_KINDS)
LEGAL_FORM_RE = re.compile(r"주식회사|\(주\)|㈜", re.IGNORECASE)
NAMED_RELATION_RE = re.compile(
    rf"(?P<relation>{RELATION_PATTERN})"
    r"(?:\s*(?:인|중(?:의)?|가운데|에\s*해당하는)\s*|\s+)"
    r"(?P<actor>[^\n;|,]+?)(?:은|는|이|가|의|을|를|에)(?=\s|[,.;]|$)"
)
# 이 검사는 명시 법인 수식만 다룬다. 일반적인 ‘자회사 관리’는 법인 이름이 아니다.
FORMAL_NAME_RE = re.compile(r"주식회사|\(주\)|㈜|\b[A-Z][A-Za-z0-9]*\b")
RELATION_NEGATION_RE = re.compile(
    rf"^(?:은|는|이|가)?(?:당사의|회사의|본사의)?(?:{RELATION_PATTERN})"
    r"(?:이|가|에)?(?:아니|해당하지않|관계가없)"
)
FOREIGN_OWNER_RE = re.compile(r"(?:타사|다른\s*회사|고객사|경쟁사|거래처)의\s*$")
NAMED_OWNER_RE = re.compile(r"(?P<owner>[^\s;|,]+)의\s*$")
SELF_OWNER_KEYS = frozenset({"당사", "회사", "본사", "연결회사", "연결기업"})
TABLE_RELATION_HEADERS = frozenset({"관계", "회사와의관계", "당사와의관계", "지배기업과의관계", "구분"})
TABLE_ENTITY_HEADERS = frozenset({"회사명", "기업명", "법인명", "성명(법인명)", "대상회사", "제공받은기업"})
