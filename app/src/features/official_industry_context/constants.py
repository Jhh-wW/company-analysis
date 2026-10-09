"""공식 산업 자료의 선택·단회 검수와 닫힌 응답 계약."""

import re

PROMPT_VERSION = "official-industry-context-v4"
MAX_CANDIDATES = 6
MAX_ANCHORS = 3
MAX_FRAGMENT_CHARS = 6000
MAX_INPUT_CHARS = 12000
MAX_PROMPT_CHARS = 32000
MAX_SENTENCES = 100
MAX_ASSESSMENT_SENTENCES = 4
MAX_ASSESSMENT_CHARS = 900
MAX_OUTPUT_ITEMS = 2
MAX_OUTPUT_TOKENS = 3000
ID_DIGEST_CHARS = 20
SENTENCE_ID_PATTERN = rf"^sentence-[0-9a-f]{{{ID_DIGEST_CHARS}}}$"
MIN_BUSINESS_CORE_CHARS = 2
BUSINESS_ACTIVITY_SUFFIX_RE = re.compile(r"(?:사업|서비스|공사|제조|생산|개발|유통|판매|공급)$")
INDUSTRY_SCOPE_RE = re.compile(r"산업|시장|업계")
STATUSES = ("proposed", "no_current_problem", "different_business", "uncertain")
BOOL_FIELDS = ("current_problem", "same_business", "geography_supported", "period_supported")
COMMON_BOOL_FIELDS = tuple(name for name in BOOL_FIELDS if name != "geography_supported")
QUOTE_FIELDS = ("industry", "problem", "geography_detail", "geography_evidence", "applicability_quote")
GEOGRAPHY_QUOTE_FIELDS = ("geography_detail", "geography_evidence")
COMMON_QUOTE_FIELDS = tuple(name for name in QUOTE_FIELDS if name not in GEOGRAPHY_QUOTE_FIELDS)
BASE_FIELDS = ("fragment_id", "anchor_id", "status")
PROPOSED_FIELDS = (*BASE_FIELDS, "sentence_ids", *BOOL_FIELDS, *QUOTE_FIELDS, "geography", "observation_period")
LOCATION_RE = re.compile(r"^(?:chars:|raw_xml_chars:)?([0-9]{1,10})-([0-9]{1,10})$")
WEB_INDEX_RE = re.compile(r"^[0-9]{1,10}$")
WEB_LIST_LOCATION_RE = re.compile(r"#[0-9]+$| · 목록 ")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。])(?=[ \t\r\n]|$)|\n+")
ASSESSMENT_CLAUSE_RE = re.compile(r"[.!?。\n,;]+|하지만|그러나|반면|한편")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YEAR_RE = re.compile(r"(?:19|20)[0-9]{2}")
EXACT_PERIOD_OPTION_RE = re.compile(
    r"(?<![0-9])(?:19|20)[0-9]{2}"
    r"(?:년(?:\s*(?:현재|상반기|하반기|[1-4]분기))?|[.\-/](?:0?[1-9]|1[0-2])"
    r"(?:[.\-/](?:0?[1-9]|[12][0-9]|3[01]))?)?"
    r"(?![0-9])"
)
PERIOD_RE = re.compile(r"(?:19|20)[0-9]{2}(?:년|[.\-/][0-9]{1,2})?|당기|현재|최근|상반기|하반기|[1-4]분기")
DOMESTIC_MARKERS = ("국내", "한국", "대한민국", "south korea", "republic of korea")
FOREIGN_SCOPE_MARKERS = ("해외", "외국")
WORLD_MARKERS = ("세계", "글로벌", "global", "worldwide")
EXPLICIT_WORLD_MARKERS = ("전세계", "전 세계", "세계 전역", "세계 각국", "세계적으로", "worldwide")
GLOBAL_ACTOR_RE = re.compile(r"(?:글로벌|global)\s*(?:기업|회사|업체|매체|플랫폼|고객)", re.I)
# 지명이 제품·기업 이름에 들어갔다는 이유로 지역 범위를 단정하지 않는다.
# 명시된 지역 시장을 미확인 경로로 우회하는 경우만 닫는 보조 검사다.
NAMED_REGION_PATTERN = (
    r"(?:일본|중국|미국|캐나다|멕시코|브라질|칠레|아르헨티나|영국|독일|프랑스|이탈리아|스페인|"
    r"네덜란드|벨기에|스위스|오스트리아|폴란드|스웨덴|노르웨이|핀란드|덴마크|러시아|우크라이나|"
    r"인도|베트남|태국|인도네시아|말레이시아|싱가포르|필리핀|대만|홍콩|호주|뉴질랜드|"
    r"튀르키예|터키|이스라엘|사우디아라비아|아랍에미리트|남아프리카공화국|이집트|"
    r"유럽|아시아|북미|남미|중남미|동남아시아|동남아|중동|아프리카|오세아니아|"
    r"japan|china|united states|usa|europe|asia)"
)
REGION_ACTOR_RE = re.compile(
    r"(?:국내|한국|해외|외국|세계|글로벌|global|" + NAMED_REGION_PATTERN + r")"
    r"\s*(?:기업|회사|업체|매체|플랫폼|고객)", re.I,
)
NAMED_REGION_SCOPE_RE = re.compile(
    NAMED_REGION_PATTERN +
    r"(?=\s|의|내|지역|시장|산업|업계)(?:\s*(?:내|지역)?(?:의)?\s*)"
    r"[^.!?。\n,;]{0,40}(?:시장|산업|업계)",
    re.I,
)
GENERIC_PROBLEMS = frozenset({"시장", "경쟁", "강세", "독주", "사업", "산업", "시장 현황", "정의", "문제"})
GENERIC_GEOGRAPHIES = frozenset({"시장", "사업", "산업", "회사", "기업", "고객", "지역"})
# 아래 표지는 닫힌 모순을 거절하며 임의 의미 합격을 입증하지 않는다.
FUTURE_RE = re.compile(r"예상|전망|가능성|(?:할|될|겪을|발생할)\s*수\s*있|계획|예정")
PAST_OR_RESOLVED_RE = re.compile(r"과거|당시|지난해|작년|해소(?:되|됐|하)|해결(?:되|됐|하)|종료(?:되|됐)|현재[^.!?。]*없")
DEFINITION_RE = re.compile(r"(?:이란|란)\s|정의(?:한다|합니다|된다|입니다)|의미(?:한다|합니다|하는)")
DOMINANCE_RE = re.compile(r"강세|독주|우세|강자|선도(?:기업|업체)")
ACTUAL_HARM_RE = re.compile(r"감소|하락|둔화|위축|침체|부족|차질|지연|상승|급등|부담|피해|손실|악화|규제|경쟁\s*심화")
ACCOUNTING_RE = re.compile(r"회계\s*정책|공정\s*가치|상각\s*후\s*원가|수익[^.!?。]{0,30}인식|감사\s*절차|회계\s*기준")
POLICY_END_RE = re.compile(r"측정(?:합니다|한다)|평가(?:합니다|한다)|인식(?:합니다|한다)|적용(?:합니다|한다)")
CUSTOMER_USE_RE = re.compile(r"(?:광고주|고객|이용자|사용자|운송업체)[^.!?。]{0,45}(?:광고비|사용|운행|주차|이용료)|광고를\s*구매하는\s*(?:기업|회사|업체)[^.!?。]{0,45}(?:집행\s*비용|광고비)")
SUPPLIER_IMPACT_RE = re.compile(r"(?:판매|제공|공급|생산|제조|매출|수익)[^.!?。]{0,40}(?:감소|하락|차질|부담|피해|악화|둔화)")
GUIDE = (
    "observation_period는 해당 자료 exact_period_options의 문자열을 그대로 선택하세요. "
    "applicability_quote는 같은 조각의 applicability_quote_options에서 해당 anchor_id를 지원하며 "
    "선택한 sentence_ids의 연속 평가 범위 안에 있는 text를 그대로 선택하세요. "
    "적용 선택값의 좌표는 첫 출현 위치이며 sentence_ids는 같은 문구가 있는 모든 문장입니다. "
    "앵커는 사업 비교 근거이며 적용 인용을 앵커 문구로 대신할 수 없습니다. "
    "기간 또는 적용 인용 선택값이 없거나 의미 연결이 불확실하면 비제안 상태를 반환하세요. "
    "선택값은 원문 탐색 후보일 뿐 같은 사업·문제·시점의 의미 승인을 대신하지 않습니다. "
    "공식 자료와 회사의 공식 사업 앵커를 연결하는 산업 검수입니다. 회사 직접 피해나 대응을 만들지 마세요. "
    "각 fragment_id×anchor_id를 한 번씩 판정하세요. 문제 없거나 불확실하면 비제안 상태를 반환하세요. "
    "proposed는 같은 사업 활동의 명시한 보고기간에 관찰된 산업 문제와 명확한 자료 기간이 지원될 때만 사용합니다. "
    "국내·세계·해외 범위는 그 평가 원문이 직접 지원할 때만 선택하고 geography_supported=true로 표시하세요. "
    "공식 원문에 실제 산업 문제와 시점은 있지만 지역 범위를 확인하지 못하면 geography=unspecified, "
    "geography_supported=false, geography_detail와 geography_evidence는 모두 빈 문자열로 반환하세요. "
    "이는 지역 미확인 공식 관찰로만 표시되며 국내·세계 산업 근거를 대신하지 않습니다. "
    "회사의 소재지나 원문의 언어로 국내를 추정하거나 전체 시장을 세계로 바꾸지 마세요. "
    "보고기간의 관찰을 오늘 현재 회사 피해로 단정하지 말고 기간을 정확히 표시하세요. "
    "정의·시장 규모·강자의 존재만으로 문제를 확정하지 마세요. 미래 예상·목표·이미 해소된 문제·다른 시기의 일회 과거 사건·회계정책은 제외하세요. "
    "광고를 사용하는 고객의 비용과 광고를 판매하는 사업, 제품 제조업과 이를 사용하는 사업을 구분하세요. "
    "같은 제품명이 나와도 문제를 겪는 주체의 사업 활동을 앵커와 대조하고, 그 사업에 미치는 영향이 원문에 명시된 범위만 판정하세요. "
    "국내 시장에 진입한 글로벌 기업은 세계 전체 문제의 지역 근거가 아닙니다. "
    "sentence_ids는 해당 조각의 연속된 문장만 순서대로 고르세요. 다른 문단·조각·앵커를 빌리지 마세요. "
    "산업·문제·지역·적용 인용은 그 연속 평가 범위 안의 정확 문자열이어야 합니다. "
    "industry는 선택한 문장 안의 산업 명칭을 그대로 복사하세요. 공식 사업 앵커의 이름으로 대체하거나 명칭을 재작성하지 마세요. "
    "적용 인용에는 공식 사업 항목의 제공물 명칭이 있어야 하며, 표현 차이로 정확히 연결되지 않으면 uncertain을 반환하세요. "
    "observation_period는 제목 또는 평가 범위의 명시 연도가 포함된 정확 시점 문자열입니다. 문제 소재절의 연도·지역과 충돌하는 다른 절의 시점을 빌리지 마세요. 보고기간과 공표일을 구분하고 공표일만으로 현재성을 만들지 마세요. "
    "자료 하나당 proposed는 최대1개, 전체 최대2개입니다. 비제안 객체는 fragment_id/anchor_id/status만 반환하세요. "
    "불리언은 실제 bool입니다. 요청 밖 ID·필드·문장 합성·원문 재작성은 허용하지 않습니다."
)
