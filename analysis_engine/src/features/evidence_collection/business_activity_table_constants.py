"""현재 자기 영업표를 검색 방향으로만 사용하는 닫힌 구조 경계."""

import re

MAX_ACTIVITY_TABLE_WINDOW_CHARS = 4096
MAX_ACTIVITY_TABLE_ITEM_CHARS = 70
ACTIVITY_TABLE_SLOT_ID = "identity:business_definition"
ACTIVITY_TABLE_REASON = "direct_pattern:current_company_activity_table"
ACTIVITY_MAJOR_HEADING_RE = re.compile(r"(?m)^\s*[0-9]{1,3}[.．](?:\s+|(?=[가-힣A-Za-z]))")
ACTIVITY_OWNER_RE = re.compile(
    r"(?:당기(?:와\s*전기)?(?:말)?\s*(?:현재|중))\s*"
    r"(?:당사|우리\s*회사|본\s*회사)가\s*(?:직접\s*)?"
    r"(?:시공한|수행한|제공한|판매한|생산한)(?![가-힣A-Za-z0-9])"
)
ACTIVITY_EXCLUDED_RE = re.compile(
    r"과거|당시|설립|목적|분할|예정|계획|전제|경우|중단|종료|완료(?:된|한)?\s*(?:공사|계약|사업|이력)|"
    r"종속기업|종속회사|자회사|타회사|고객사|거래처의|회계\s*정책|수익\s*인식\s*정책|"
    r"내부\s*(?:관리|업무)|임직원|회계\s*관리"
)
ACTIVITY_CURRENT_RE = re.compile(r"(?<![가-힣])당\s*기(?![가-힣])")
ACTIVITY_PRIOR_RE = re.compile(r"(?<![가-힣])전\s*기(?![가-힣])")
ACTIVITY_ITEM_HEADERS = frozenset({"구분", "사업부문", "품목", "제품명", "상품명", "서비스종류", "공사종류", "공종"})
ACTIVITY_REVENUE_HEADERS = frozenset({"누적공사수익", "당기매출액", "매출액", "당기수익"})
ACTIVITY_FOREIGN_HEADERS = frozenset({"공사명", "프로젝트명", "발주처", "고객명", "회사명", "업체명"})
ACTIVITY_STATUS_HEADERS = frozenset({"상태", "진행상황", "비고"})
ACTIVITY_PERIOD_HEADERS = frozenset({"기간", "대상기간", "귀속기간", "실적기간", "사업기간"})
ACTIVITY_ROW_PERIOD_EXCLUDED_RE = re.compile(r"전\s*기|전년도|전년|과거|예정|계획")
ACTIVITY_INACTIVE_RE = re.compile(r"완료|종료|중단|예정|미착공|미실행|취소")
ACTIVITY_GENERIC_ITEMS = frozenset({"구분", "합계", "총계", "계", "기타", "제품", "상품", "서비스", "주요계약"})
ACTIVITY_HEADER_FOOTNOTE_RE = re.compile(r"\([^)]*\)")
ACTIVITY_ITEM_RE = re.compile(
    rf"[가-힣A-Za-z0-9][가-힣A-Za-z0-9 ·/&_-]{{1,{MAX_ACTIVITY_TABLE_ITEM_CHARS - 1}}}"
)
ACTIVITY_SITE_SUFFIX_RE = re.compile(r"(?P<item>.+공사)\s+현장$")
ACTIVITY_POSITIVE_AMOUNT_RE = re.compile(r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?")
ACTIVITY_COMPANY_HEADING_RE = re.compile(
    r"(?P<company>(?:\(주\)|㈜)?[가-힣A-Za-z0-9·&._-]+(?:\s+[가-힣A-Za-z0-9·&._-]+){0,3})의\s*"
    r"(?:사업\s*개요|영업\s*개황|(?:당기|현재)\s*(?:공사|매출|사업|실적))"
)
ACTIVITY_COMPANY_KEY_RE = re.compile(r"\s+|주식회사|\(주\)|㈜")
ACTIVITY_HEADING_LEAD_RE = re.compile(r"^(?:다음은|아래는)\s+")
ACTIVITY_DIRECT_ACTOR_RE = re.compile(
    r"(?:당기(?:와\s*전기)?(?:말)?\s*(?:현재|중))\s*"
    r"(?P<actor>[가-힣A-Za-z0-9·&._() -]+?)(?:가|이|는|은)\s*"
    r"(?:직접\s*)?(?:시공한|수행한|제공한|판매한|생산한)(?![가-힣A-Za-z0-9])"
)
ACTIVITY_SELF_ACTORS = frozenset({"당사", "우리회사", "본회사"})
ACTIVITY_NEGATED_OWNER_RE = re.compile(r"(?:공사|사업|실적|것)(?:는|가|이)?\s*(?:없다|없습니다|아니다|아닙니다)")
