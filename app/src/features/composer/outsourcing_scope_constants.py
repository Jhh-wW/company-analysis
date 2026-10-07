"""외주 공시를 직접 수행 부재로 확대하는 닫힌 문법 경계."""

import re

OUTSOURCING_SCOPE_PROBLEM = "scope_condition_unbound"
SENTENCE_RE = re.compile(r"[。.;\n]")
CLAUSE_RE = re.compile(r"[,，;]")
OUTSOURCING_RE = re.compile(r"외주|위탁")
DISCLOSURE_OMISSION_RE = re.compile(
    r"(?:생산\s*능력|생산\s*실적|설비|시설)(?:에\s*대(?:해|하여|해서))?"
    r"[^。.;\n]*(?:기재|공시)[^。.;\n]*(?:사항\s*(?:은|이)?\s*없|생략)"
)
# 부정 자체를 추측하지 않는다. 명시 직접/자체와 명시 수행 부정이 함께 있어야 한다.
DIRECT_NEGATIVE_RE = re.compile(
    r"(?P<object>[가-힣A-Za-z0-9·ㆍ/()\-\s]+?)(?:을|를|은|는)\s*"
    r"(?:직접|자체(?:적으로)?)\s*"
    r"(?P<action>수행|생산|운영|제조|처리)\s*하지\s*"
    r"않(?:는다|습니다|고\s*있(?:다|습니다))(?=$|[.,;。])"
)
SELF_SUBJECT_RE = re.compile(r"(?<![가-힣A-Za-z0-9])(?:당사|회사|자사)(?:은|는|가)\s*")
# 예외 증명의 앞 문맥은 버리지 않는다. 빈 앞부분이나 명시 현재 표지만 허용하고,
# 알려지지 않은 날짜·조건·주체 설명은 기존 의미 검수에 남긴다.
SUPPORT_PREFIX_RE = re.compile(r"\s*(?:(?:현재|지금|당기|보고\s*기간)\s*)?")
TOTAL_OUTSOURCING_TAIL_RE = re.compile(
    r"(?:을|를|은|는)\s*(?:(?:모두|전량|전체)\s*)"
    r"(?:(?:외주|위탁)(?:\s*업체)?(?:에|로)?\s*"
    r"(?:맡긴다|맡깁니다|맡기고\s*있(?:다|습니다)|"
    r"(?:처리|수행)?\s*(?:한다|합니다|하고\s*있(?:다|습니다)))"
    r"|(?:외부|외주|위탁)\s*업체(?:에|가)\s*"
    r"(?:맡긴다|맡깁니다|맡기고\s*있(?:다|습니다)|위탁한다|위탁합니다))"
    r"(?=$|[.,;。])"
)
