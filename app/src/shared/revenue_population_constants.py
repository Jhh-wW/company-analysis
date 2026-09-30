"""매출표의 집계 범위와 기간을 표시하는 원문 문법."""
import re

POPULATION_LOOKBACK_CHARS = 2048
POPULATION_CLAIM_LOOKBACK_CHARS = 64
POPULATION_SCOPE_RE = re.compile(r"\[[^\]\n]{1,80}(?:부문|사업)[^\]\n]{0,40}\]")
PARENT_SECTION_RE = re.compile(r"(?<!\S)(?:[IVX]+\.|[0-9]+\.)\s+(?=[가-힣A-Za-z])")
REPORTING_BASIS_RE = re.compile(r"(?<![가-힣])(?:연결(?:기준|재무제표)?|별도(?:기준|재무제표)?|개별(?:기준|재무제표)?)(?![가-힣])")
REPORTING_PERIOD_RE = re.compile(r"누적|(?:20\d{2}년\s*)?(?:[1-4]분기|상반기|하반기)|20\d{2}년")
POPULATION_CAPTION_SEPARATOR = " · 집계 범위: "
POPULATION_UNKNOWN_LABEL = "원문 표 합계 기준"
PERIOD_UNKNOWN_LABEL = "기간 미확인"
WHOLE_REVENUE_CLAIM_RE = re.compile(r"(?:회사|기업|그룹|연결)?(?:의)?전체매출|전사매출|회사전체|전체사업매출|총매출(?:의|에서)")
REVENUE_ROW_RE = re.compile(r"매출\s*(?:액|수익|비중)|비\s*율|비\s*중")
REVENUE_ITEM_HEADER_RE = re.compile(r"품\s*목|제품(?:명)?|상품(?:명)?|서비스(?:명)?")
REVENUE_SOURCE_HEADER_END_RE = re.compile(r"품목\s*:|(?:비\s*중|비\s*율)(?!\s*:)")
WHOLE_BUSINESS_COMPARISON_RE = re.compile(
    r"(?:전체(?:사업)?(?:포트폴리오|사업)|(?:회사|기업|전사|그룹)(?:의)?(?:전체|사업|포트폴리오|에서))"
    r"[^.!?|]{0,48}(?:비중|대부분|압도|지배적|최대|가장|[0-9]+위|주력|핵심|중심)"
)
PORTFOLIO_ROLE_COMPARISON_RE = re.compile(
    r"포트폴리오(?:에서|의)[^.!?|]{0,32}(?:비중|대부분|압도|지배적|최대|가장|[0-9]+위|주력|핵심|중심)"
)
PRIMARY_BUSINESS_ROLE_RE = re.compile(r"(?:주력|핵심)사업")
WHOLE_BUSINESS_POPULATION_RE = re.compile(r"(?:회사|기업|전사|그룹)(?:의)?전체(?:사업|포트폴리오)?|전체사업포트폴리오")
EXPLICIT_COMPANY_PRIMARY_BUSINESS_RE = re.compile(
    r"(?:회사|당사|기업|그룹)(?:의|는|은)[^.!?|]{0,64}(?:주력|핵심)사업"
)
COMPARATIVE_MAGNITUDE_RE = re.compile(r"비중|대부분|압도|지배적|최대|가장|[0-9]+위")
EXPLICIT_COMPANY_CLAIM_RE = re.compile(r"^(?:회사|기업|전사|그룹)")
LOCAL_COMPARISON_SUBJECT_PATTERN = r"(?:[가-힣A-Za-z0-9&()]{1,24}(?:은|는|이|가))?"
