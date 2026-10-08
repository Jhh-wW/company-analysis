"""산업 과제의 탐색·검수 한도와 닫힌 응답 계약."""

import re

INDUSTRY_PROMPT_VERSION = "industry-context-v5"
INDUSTRY_ASSESSMENT_MAX_ANCHORS = 3
INDUSTRY_ASSESSMENT_FIELD = "industry_assessments"
INDUSTRY_ASSESSMENT_STATUSES = (
    "proposed", "no_current_problem", "different_business", "geography_unbound",
    "insufficient_quote", "uncertain",
)
INDUSTRY_PRIORITY_GUIDE = (
    "이번 묶음의 주과제는 각 기사와 공식 사업 앵커별 산업문제 판정입니다. "
    "먼저 같은 사업 활동의 현재 문제와 실제 적용 지역을 자기 원문에서 확인하고, "
    "확인되면 industry_assessments의 해당 판정 안에 검증 가능한 인용을 선택하세요. "
    "same_company/material=false도 이 판정을 생략하는 이유가 아닙니다. "
    "각 기사의 industry_assessments에는 모든 공식 anchor_id를 한 번씩 판정하세요: "
    "proposed=인용 제안, no_current_problem=현재 문제 없음, different_business=다른 사업, "
    "geography_unbound=지역 근거 부족, insufficient_quote=인용 근거 부족, uncertain=불확실. "
    "proposed는 status와 모든 산업 근거 필드를 같은 객체에 반환하며 빈 근거는 허용하지 않습니다. "
    "비제안 상태는 anchor_id와 status만 반환합니다. 기사당 proposed는 최대 1개이며 "
    "나머지 앵커는 비제안 상태로 판정하세요. 별도 산업 제안 배열은 출력하지 마세요. "
    "상태는 사실이나 근거 승인으로 쓰지 않으며 불확실하면 산업 제안 0개도 허용합니다. "
    "회사 직접 사건도 기존 기준으로 독립 검수해 반환하고, 회사 인용 실패를 산업으로 자동 전환하지 마세요. "
)
INDUSTRY_TOPIC_PREFIX = "industry_"
INDUSTRY_QUERY_COUNT = 4
INDUSTRY_COMPANY_QUERY_COUNT = 2
INDUSTRY_BODY_DIVISOR = 6
# 산업·업계의 문제와 동향을 탐색하며 검색어가 실제 문제를 증명하지는 않는다.
# 첫 질의는 지역을 지정하지 않는다. 국내 탐색 몫의 후보도 본문 지역 검수가 필요하다.
INDUSTRY_QUERY_EXPRESSIONS = (
    ("", "산업 과제"),
    ("세계", "산업 동향"),
    ("국내", "업계 문제"),
    ("글로벌", "업계 위기"),
)
INDUSTRY_QUERY_THEMES = tuple(theme for _, theme in INDUSTRY_QUERY_EXPRESSIONS)
INDUSTRY_BUSINESS_ACTIVITY_GUIDE = (
    "같은 제품명이 나와도 제조·공급업과 이를 사용하는 운송·시공·운영업을 구분하세요. "
    "문제를 겪는 주체의 사업 활동을 공식 앵커와 대조하세요. "
    "고객의 사용상 문제나 개별 사고를 제품 산업 전체의 문제로 확대하지 마세요. "
    "공식 앵커와 같은 사업 활동에 미치는 영향이 기사에 명시되면 그 범위에서 판정하세요. "
)
# 원문 문자를 삭제하거나 업종 이름을 번역하지 않고 활동 접미어의 경계만 나눈다.
INDUSTRY_SEARCH_ACTIVITY_RE = re.compile(
    r"^(?P<object>[가-힣A-Za-z0-9]{2,})(?P<activity>공사|서비스|제조|판매|유통)$"
)
# 명시 정보성 제목은 삭제하지 않는다. 제목에 문제 사건이 있으면 이 제한을 적용하지 않는다.
INDUSTRY_SEARCH_INFORMATION_TITLE_RE = re.compile(
    r"^\s*(?:\[\s*who\s+is\s*\?\s*\]|\[?관련주\]?\s*(?:목록|모음|정리|총정리))|"
    r"관련주[^.!?\n]{0,30}(?:목록|모음|총정리)\s*$",
    re.I,
)
INDUSTRY_BUSINESS_TOKEN_END = r"(?=$|[^가-힣A-Za-z0-9]|의|은|는|이|가|업계|산업|시장|제조|생산|수요|가격|공급)"
INDUSTRY_SEARCH_CLAUSE_RE = re.compile(r"[.!?。…\n]+")
INDUSTRY_QUERY_REGIONS = (("국내", "domestic"), ("세계", "global"))
# 검색 메타데이터는 본문 조사 순위에만 쓰며 문제의 사실 여부를 증명하지 않는다.
INDUSTRY_SEARCH_PROBLEM_RE = re.compile(
    r"공급난|공급\s*(?:부족|지연|차질)|수요\s*(?:감소|둔화|위축)|생산\s*(?:중단|차질|감소)|"
    r"원가\s*(?:상승|부담)|(?:가격|유가|운임)\s*(?:상승|급등)|"
    r"관세\s*(?:부담|인상|부과|파고)|복병|인력\s*부족|납기\s*지연|"
    r"불량|리콜|규제|과잉\s*(?:공급|생산)|경쟁\s*심화|침체|부족|차질|위기|"
    r"shortage|disruption|recall|overcapacity|declin|regulat|rising\s+cost",
    re.I,
)
# 공식 앵커 질의의 예비 후보를 읽는 순위에만 쓰는 일반 논점 표지다.
# 이 표지는 같은 사업·현재 문제·지역 또는 회사의 피해를 증명하지 않는다.
INDUSTRY_SEARCH_QUESTION_RE = re.compile(r"문제|쟁점|숙제")
INDUSTRY_MAX_PROBLEMS_PER_ARTICLE = 1
INDUSTRY_QUOTE_MAX_CHARS = 900
INDUSTRY_DOMESTIC_MARKERS = ("국내", "한국", "대한민국", "south korea", "republic of korea")
INDUSTRY_PROBLEM_CLAUSE_RE = re.compile(r"[.!?。\n]+")
INDUSTRY_GLOBAL_MARKERS = ("세계", "글로벌", "global", "worldwide")
INDUSTRY_POSSIBILITY_RE = re.compile(
    r"가능성|(?:할|될|겪을|발생할)\s*수\s*있|"
    r"예상(?:된|되는)|전망(?:된|되는)|발생할\s*(?:것|전망)"
)
INDUSTRY_GLOBAL_COMPANY_MODIFIER_RE = re.compile(
    r"(?:글로벌|global)\s*(?:[가-힣A-Za-z0-9]+\s+){0,5}"
    r"(?:기업|회사|업체)(?:은|는|이|가|의)"
)
INDUSTRY_EXPLICIT_WORLD_SCOPE_MARKERS = (
    "전세계", "전 세계", "세계 전역", "세계 각국", "세계적으로", "worldwide",
)
INDUSTRY_ACCOUNTING_POLICY_RE = re.compile(
    r"회계\s*정책(?:에\s*따라|을|은)[^.!?。\n]{0,60}"
    r"(?:설명|적용|인식|측정|평가)(?:합니다|한다)|"
    r"(?:공정\s*가치|상각\s*후\s*원가)(?:로|에\s*따라)[^.!?。\n]{0,40}"
    r"(?:측정|평가)(?:합니다|한다)|"
    r"(?:재고\s*자산)(?:은|을)[^.!?。\n]{0,40}평가(?:합니다|한다)|"
    r"수익(?:으로|을)[^.!?。\n]{0,30}인식(?:합니다|한다)|"
    r"감사인(?:은|이)[^.!?。\n]{0,60}(?:의견을\s*표명|감사\s*절차를\s*수행)"
    r"(?:합니다|한다)|"
    r"유동성[^.!?。\n]{0,50}예측하고\s*관리(?:합니다|한다)|"
    r"영업\s*자금\s*수요[^.!?。\n]{0,50}충당(?:합니다|한다)"
)
INDUSTRY_GENERIC_PROBLEMS = frozenset({"문제", "위험", "과제", "어려움", "부족"})
INDUSTRY_CACHE_SOURCE_FIELDS = ("text", "industry", "problem", "geography_detail", "geography_evidence", "applicability_quote")
INDUSTRY_REQUIRED_FIELDS = (
    "anchor_id", "text", "industry", "problem", "geography", "geography_detail",
    "geography_evidence", "applicability_quote", "problem_present", "same_business", "geography_supported",
)
INDUSTRY_RESPONSE_OBSERVATION_FIELDS = (
    "검수입력기사", "응답기사", "빈제안기사", "제안근거", "검증생존", "검증탈락",
)
