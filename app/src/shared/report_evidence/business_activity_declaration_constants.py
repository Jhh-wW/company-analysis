"""회사 자신의 현재 사업 구성 선언에 한정된 문법."""

import re

DECLARATION_UNIT_RE = re.compile(r"[;\n]|(?<=[.!?。])(?:\s+|(?=[가-힣(]))")
DECLARATION_RE = re.compile(
    r"^(?:당사|우리\s*회사|본\s*회사|회사)의\s*"
    r"(?:(?:주요|주된|핵심)\s*)?사업은\s*"
    r"(?P<items>[^.;\n]{2,240}?)\s*(?:으로|로)\s*"
    r"구성되어\s*(?:있습니다|있다|있음)\s*[.。]?$"
)
DECLARATION_PREFIX_RE = re.compile(r"^(?:크게|주로)\s*")
DECLARATION_QUALIFIER_RE = re.compile(
    r"^[^.;\n]{2,120}?(?:전제로\s*한|기반으로\s*한)\s*"
)
DECLARATION_ITEM_RE = re.compile(
    r"(?P<item>[가-힣A-Za-z0-9][가-힣A-Za-z0-9 ()·/&_-]{1,69}?사업)"
    r"(?=$|과(?:\s|$)|\s*(?:및|그리고|[,，]))"
)
DECLARATION_EXCLUDED_RE = re.compile(
    r"사업\s*목적|정관|예정|계획|향후|추진|검토|과거|이전에는|종전|"
    r"[0-9]{4}\s*년\s*당시|"
    r"자회사|종속회사|종속기업(?:의|는|은|이|가)|계열사|경쟁사|타사|"
    r"(?:고객사|거래처|협력사|공급사|다른\s*회사)(?:의|는|은|가)|"
    r"경우|할\s*때|조건부|하면|되면|한다면|된다면|"
    r"(?:인수|합병|승인|허가|인가|설립|계약)\s*(?:완료\s*)?(?:를|을)?\s*전제로|"
    r"회계\s*(?:정책|관리)|수익\s*인식|재무\s*관리|법무\s*(?:업무|관리)|공시\s*관리|"
    r"(?:내부|사내|임직원)\s*(?:관리|업무|교육)|인사\s*관리|복리후생|준법\s*관리|"
    r"사업이란|산업이란|산업\s*전반|업계\s*전반"
)
DECLARATION_ACCOUNTING_PROCESS_RE = re.compile(r"회계\s*처리")
DECLARATION_EXTERNAL_ACCOUNTING_SERVICE_RE = re.compile(
    r"(?<![가-힣A-Za-z0-9])(?:고객|거래처|기업|공공기관)\s*대상\s*회계\s*처리\s*사업"
    r"(?=과(?:\s|$)|\s*(?:및|그리고|[,，]|으로|로))"
)
DECLARATION_GENERIC_RE = re.compile(
    r"^(?:기타|부대|지원|관리|일반)(?:\s|사업)|"
    r"^(?:제품|상품|서비스|콘텐츠|플랫폼)\s*사업$"
)
