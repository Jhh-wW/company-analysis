"""공식 원문에서 실제 사업 제공물만 선택하는 보수적 경계."""

from __future__ import annotations

import re
from typing import Final

BUSINESS_ACTIVITY_NAME_LABELS: Final[frozenset[str]] = frozenset({
    "제품", "브랜드", "사업부문", "대표 IP",
})
BUSINESS_ACTIVITY_SLOT_IDS: Final[frozenset[str]] = frozenset({
    "portfolio:product_role", "portfolio:revenue_link", "portfolio:portfolio_priority",
    "portfolio:customer_fit", "portfolio:lifecycle_stage",
    "business_model:revenue_model", "business_model:value_exchange",
    "identity:business_definition", "identity:corporate_identity",
})
BUSINESS_ACTIVITY_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"[;\n]|(?<=[.!?。])(?:\s+|(?=[가-힣(]))"
)
BUSINESS_ACTIVITY_EXCLUDED_RE: Final[re.Pattern[str]] = re.compile(
    r"수익\s*인식|회계\s*정책|공정\s*가치|손상\s*평가|감사인|감사의견|"
    r"정관|사업\s*목적|영업\s*목적|종속회사|자회사|계열사|경쟁사|타사|"
    r"전\s*세계|세계적으로|산업\s*전반|업계\s*전반|"
    r"예정|계획|추진|검토|준비|향후|목적으로|영위하기\s*위해|"
    r"경우|할\s*때|할\s*수|하게\s*될|중단|폐지|매각|종료"
)
BUSINESS_ACTIVITY_SELF_SUBJECT: Final[str] = (
    r"(?:당사|우리\s*회사|본\s*회사|회사)(?:는|은|가|의\s*(?:주요|주된|핵심)\s*사업은)\s*"
)
BUSINESS_ACTIVITY_OVERVIEW_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:[0-9]+\.|\([0-9]+\))\s*)?"
    r"(?:(?:회사의\s*개요|공시대상\s*사업부문의\s*구분)\s*)?"
)
BUSINESS_ACTIVITY_OVERVIEW_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"설립되어\s*|설립되었으며\s*|두고\s*있으며\s*|소재하고\s*있으며\s*"
)
BUSINESS_ACTIVITY_ITEM_PATTERN: Final[str] = r"(?P<item>[가-힣A-Za-z0-9][가-힣A-Za-z0-9 ()·/&_-]{1,69}?)"
BUSINESS_ACTIVITY_PRESENT_END: Final[str] = (
    r"(?:합니다|한다|하고\s*(?:있습니다|있다|있으며))"
)
BUSINESS_ACTIVITY_PRESENT_MODIFIER: Final[str] = (
    r"(?:" + BUSINESS_ACTIVITY_PRESENT_END + r"|하는\s*(?:기업|회사|법인)(?:입니다|이다))"
)
BUSINESS_ACTIVITY_DELIVERY_RE: Final[re.Pattern[str]] = re.compile(
    BUSINESS_ACTIVITY_ITEM_PATTERN
    + r"(?:을|를)\s*(?:제조|생산|판매|공급|운영|제공)"
    + r"(?:(?:·|\s*및\s*)\s*(?:제조|생산|판매|공급|운영|제공))?"
    + BUSINESS_ACTIVITY_PRESENT_MODIFIER
)
BUSINESS_ACTIVITY_MANUFACTURING_RE: Final[re.Pattern[str]] = re.compile(
    BUSINESS_ACTIVITY_ITEM_PATTERN
    + r"(?:의)?\s+(?:제조|생산|판매)(?:\s*및\s*(?:제조|생산|판매))?"
    + r"(?:(?:\s*사업)?(?:을|를)?\s*영위" + BUSINESS_ACTIVITY_PRESENT_MODIFIER
    + r"|(?:\s*사업)?입니다)"
)
BUSINESS_ACTIVITY_BUSINESS_RE: Final[re.Pattern[str]] = re.compile(
    BUSINESS_ACTIVITY_ITEM_PATTERN + r"\s*사업(?:을|를)\s*영위"
    + BUSINESS_ACTIVITY_PRESENT_MODIFIER
)
BUSINESS_ACTIVITY_PRIMARY_BUSINESS_RE: Final[re.Pattern[str]] = re.compile(
    BUSINESS_ACTIVITY_ITEM_PATTERN
    + r"(?:을|를)\s*(?:주요|주된|핵심)\s*사업(?:으로|로)\s*"
    + r"영위" + BUSINESS_ACTIVITY_PRESENT_MODIFIER
)
BUSINESS_ACTIVITY_SINGLE_MANUFACTURING_RE: Final[re.Pattern[str]] = re.compile(
    BUSINESS_ACTIVITY_ITEM_PATTERN
    + r"(?:의)?\s*제조\s*및\s*판매(?:을|를)\s*"
    + r"(?:단일|주요|주된|핵심)\s*사업(?:으로|로)\s*영위\s*"
    + BUSINESS_ACTIVITY_PRESENT_MODIFIER
)
BUSINESS_ACTIVITY_SEGMENT_COMPOSITION_RE: Final[re.Pattern[str]] = re.compile(
    BUSINESS_ACTIVITY_ITEM_PATTERN
    + r"(?:의)?\s*(?:제조|생산)\s*/\s*판매\s*(?:등을\s*)?하는\s*"
    + r"[^.;\n]{2,160}(?:사업)?부문으로\s*구성되어\s*"
    + r"(?:있는\s*[^.;\n]{0,30}(?:기업|회사)(?:입니다|이다)|있습니다|있다)"
)
BUSINESS_ACTIVITY_RECIPIENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:고객|사용자|소비자|거래처|기업\s*고객)(?:에게|에)\s*"
)
BUSINESS_ACTIVITY_GENERIC_ITEMS: Final[frozenset[str]] = frozenset({
    "제품", "상품", "서비스", "사업", "사업부문", "플랫폼", "콘텐츠",
    "고객", "가치", "주요 사업", "핵심 사업",
    "주요 제품", "주요 상품", "주요 서비스", "기타 제품", "기타 상품", "기타 서비스", "기타",
})
BUSINESS_ACTIVITY_NONITEM_RE: Final[re.Pattern[str]] = re.compile(
    r"에게|에서는|으로|위하여|하기\s*위해|및\s*기타|다양한|각종"
)
BUSINESS_ACTIVITY_LEGAL_COMPANY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\(주\)|㈜|주식회사)\s*[가-힣A-Za-z0-9·&_\-]+|"
    r"[가-힣A-Za-z0-9·&_\-]+\s*(?:\(주\)|㈜)"
)
BUSINESS_ACTIVITY_COMPANY_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<company>[가-힣A-Za-z0-9·&_\-]+)(?:의\s*사업\s*개요|"
    r"\s*\(이하\s*['\"‘’“”]?회사['\"‘’“”]?\))"
)
