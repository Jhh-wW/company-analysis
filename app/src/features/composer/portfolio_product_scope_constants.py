"""자기 품목 표와 3장 공급 대상의 이름을 결속하는 문법."""
import re

PRODUCT_TABLE_OWNER_HEADERS = frozenset({"사업부문", "사업부", "부문"})
PRODUCT_TABLE_DESCRIPTION_HEADERS = frozenset({"구체적용도", "용도", "제품설명", "서비스설명"})
PRODUCT_TABLE_TYPE_HEADERS = frozenset({"매출유형", "품목유형"})
PRODUCT_TABLE_TYPES = frozenset({"제품", "상품", "서비스", "용역"})
PRODUCT_TABLE_BOUNDARY_RE = re.compile(r"\r?\n|;")
PRODUCT_TABLE_HEADING_RE = re.compile(r"^\s*\[(?P<owner>[^\]\n]+)\]")
PRODUCT_OWNER_SUFFIX_RE = re.compile(r"(?:사업부문|사업부|부문|사업)$")
PRODUCT_OWNER_PART_RE = re.compile(r"\s*[-:]\s*")
PRODUCT_SELF_ACTORS = frozenset({"당사", "회사", "연결회사", "본회사", "우리회사"})
PRODUCT_TABLE_OWNER_RE = re.compile(
    r"(?P<owner>당사|회사|연결회사|타사|다른\s*회사|다른\s*기업|고객사|자회사|종속기업|관계기업)"
    r"(?:의|는|은|이|가)[^.。;\n|]*?(?:제품|상품|서비스|품목)"
)
PRODUCT_CLAUSE_RE = re.compile(r"[;。]|(?<=[다요])\.\s*|(?:하고|하며)\s*,?\s*(?=[^,;。]+?(?:은|는)\s)")
PRODUCT_ACTOR_RE = re.compile(r"(?:^|,\s*)(?P<actor>[가-힣A-Za-z0-9&()_\- ]+?)(?:은|는)\s+")
PRODUCT_ACTION_RE = re.compile(r"(?:공급|판매|제공|생산|제조|제작)(?:하|한|된|중|$)")
PRODUCT_OBJECT_RE = re.compile(r"(?P<items>.+)(?:을|를)\s+(?P<tail>[^,;。]*)$")
PRODUCT_LIST_RE = re.compile(r"\s*(?:[,，·]|\s+및\s+|\s+와\s+|\s+과\s+)\s*")
PRODUCT_NAME_PREFIX_RE = re.compile(r"^.*(?:을|를)\s*위한\s+|^.*\S+인\s+")
PRODUCT_NAME_SUFFIX_RE = re.compile(r"\s*(?:등|등의\s*제품|등의\s*서비스)$")
PRODUCT_MODIFIER_RE = re.compile(r"관련|(?:을|를)?위한")
PRODUCT_GENERIC_ITEMS = frozenset({"제품", "상품", "서비스", "용역", "기타", "사업", "역할"})
PRODUCT_DENIAL_RE = re.compile(r"하지\s*않|하지\s*못|아니|없|확인되지|미확인")
PRODUCT_ALIGNMENT_RE = re.compile(r":?-+:?")
