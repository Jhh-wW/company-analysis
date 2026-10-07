"""산업 과제의 탐색·검수 한도와 닫힌 응답 계약."""

import re

INDUSTRY_PROMPT_VERSION = "industry-context-v2"
INDUSTRY_TOPIC_PREFIX = "industry_"
INDUSTRY_QUERY_COUNT = 4
INDUSTRY_COMPANY_QUERY_COUNT = 2
INDUSTRY_BODY_DIVISOR = 6
# 업종에 특정 문제를 미리 부여하지 않고 현재 문제·시장 변화의 본문을 찾는다.
INDUSTRY_QUERY_THEMES = ("산업 과제", "시장 변화")
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
