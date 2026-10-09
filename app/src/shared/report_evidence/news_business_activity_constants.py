"""뉴스 사업 연결의 닫힌 원문·결속 계약."""

import re
from typing import Final

NEWS_BUSINESS_BINDING_VERSION: Final[str] = "news-business-anchor-v1"
NEWS_BUSINESS_BINDING_PREFIX: Final[str] = NEWS_BUSINESS_BINDING_VERSION + ":"
NEWS_BUSINESS_MAX_ANCHORS: Final[int] = 3
NEWS_BUSINESS_ID_PREFIX: Final[str] = "news-business-"
NEWS_BUSINESS_STATES: Final[frozenset[str]] = frozenset({"ongoing", "completed"})
NEWS_BUSINESS_KINDS: Final[frozenset[str]] = frozenset({"reported_fact", "company_statement"})
NEWS_BUSINESS_UNIT_RE = re.compile(r"(?<=[.!?。])\s+|[\n;]+")
NEWS_BUSINESS_YEAR_RE = re.compile(r"(?:19|20)[0-9]{2}년")
NEWS_BUSINESS_NESTED_ACTOR_RE = re.compile(
    r"(?:^|\s)(?:\S*(?:회사|기업|업체|고객사|계열사|자회사)|고객|협력사)(?:은|는|이|가)\s"
)
NEWS_BUSINESS_CESSATION_RE = re.compile(r"(?:취소|철회|중단|중지|매각|청산)")
NEWS_BUSINESS_REFERENCE_RE = re.compile(r"(?:그|해당|이)\s*사업")
NEWS_BUSINESS_INVALID_RE = re.compile(
    r"(?:예정|계획|추진|협상|조건부|검토|미완료|취소|철회|중단|중지|매각|청산|"
    r"하지\s*않|하지\s*못|않았다|아니다|아니며|소문|가능성|목표)"
)
NEWS_BUSINESS_NONITEM_RE = re.compile(
    r"(?:지분|주식|채권|펀드|금융상품|투자상품|기업집단|계열사|자회사|그룹)"
)
NEWS_BUSINESS_GENERIC_ITEMS: Final[frozenset[str]] = frozenset(
    {"사업", "부문", "제품", "서비스", "회사", "기업", "사업 부문"}
)
NEWS_BUSINESS_PROVIDING_RE = re.compile(
    r"(?P<item>[^.!?\n;,]{2,90}?)(?:을|를)\s*"
    r"(?:제조|생산|제공|판매|공급|영위)(?:한다|합니다|하고\s*있(?:다|습니다|으며))"
)
NEWS_BUSINESS_ACQUISITION_RE = re.compile(
    r"(?P<item>[^.!?\n;,]{2,90}?(?:사업(?:\s*부문)?|사업부문))"
    r"(?:을|를)\s*인수(?:해(?:서)?|하여|했다|하였습니다|하였으며|를\s*완료했다)"
)
NEWS_BUSINESS_BINDING_FIELDS: Final[frozenset[str]] = frozenset({
    "version", "company_id", "company_name", "company_names", "business_item",
    "business_role", "claim_kind", "temporal_status", "quote_sha256", "document_sha256",
    "document_id", "url", "published_on", "publisher", "title", "location",
    "span_start", "span_end",
})
