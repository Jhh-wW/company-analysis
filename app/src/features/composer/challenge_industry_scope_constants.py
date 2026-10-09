"""산업 주어의 설명과 회사 직접 과제를 구분하는 닫힌 문장 경계."""
from __future__ import annotations

import re
from typing import Final

DIRECT_CHALLENGE_CLAIM_SLOTS: Final[frozenset[str]] = frozenset({
    "", "current_challenges:issue", "current_challenges:initial_signal",
    "current_challenges:unresolved_gap", "current_challenges:next_check",
})
INDUSTRY_SUBJECT_MAX_CHARS: Final[int] = 100
INDUSTRY_ENVIRONMENT_MODIFIER_MAX_CHARS: Final[int] = 40
# 산업 배경을 먼저 적은 문장은 뒤의 실제 주체를 따로 읽는다.
# 원문·공개 문장을 자르지 않으며 이 표지는 회사의 제약을 승인하지 않는다.
INDUSTRY_ENVIRONMENT_PREFIX_RE = re.compile(
    rf"^\s*(?:이러한|이같은|이런)\s+[^.!?。;|\n]{{0,{INDUSTRY_SUBJECT_MAX_CHARS}}}?"
    rf"(?:산업|시장)\s*(?:환경|구조|상황|변화)[^.!?。;|\n]{{0,{INDUSTRY_ENVIRONMENT_MODIFIER_MAX_CHARS}}}?"
    r"(?:속에서|가운데|하에서)\s+(?P<body>.+)$"
)
# 일반 업계 집단은 특정 회사의 직접 주체와 다르다. 접두 수식어 없이
# 임의 기업명을 매체·사업자로 판별하지 않는다.
INDUSTRY_GROUP_SUBJECT_RE = re.compile(
    r"(?:전통|기존|일반|각|여러|다수(?:의)?|많은)\s*"
    r"(?:매체|기업|업체|사업자)(?:들)?(?:은|는|이|가)\s*"
)
# 새 산업 배경 분기에서 다른 명시 주어의 제약을 과잉제거하지 않는 경계다.
# 이 주어를 목표 회사로 승인하지 않고 기존 자기 원문·의미 검수에 남긴다.
INDUSTRY_CLAUSE_SUBJECT_RE = re.compile(
    rf"(?:^|[,，]\s*|(?<=고)\s+|(?<=며)\s+)"
    rf"(?P<subject>[가-힣A-Za-z0-9·&_-]{{2,{INDUSTRY_SUBJECT_MAX_CHARS}}}(?:은|는|이|가))\s*"
)
INDUSTRY_SCOPE_DIRECT_ISSUE_SLOTS: Final[frozenset[str]] = frozenset({
    "", "current_challenges:issue", "current_challenges:initial_signal",
})
# 산업·시장이 문장의 주어인 설명만 읽는다. 이름이나 업종을 판별하지 않는다.
INDUSTRY_SUBJECT_RE = re.compile(
    rf"^\s*(?P<subject>[^.!?。;|\n]{{1,{INDUSTRY_SUBJECT_MAX_CHARS}}}?"
    r"(?:산업|시장)(?:\s*(?:전반|전체|환경|상황|부문|분야))*)"
    r"(?:에서는|은|는|이|가)\s*"
)
# 자료를 서술 주어로 둔 인용 보고절도 그 안의 산업 주체로 대조한다.
# 배경과 자료 표지만 분리하며 자기 인용 원문이나 공개 문장은 바꾸지 않는다.
INDUSTRY_REPORT_PREFIX_RE = re.compile(
    r"^\s*(?P<context>(?:이러한|이같은|이런)\s+[^.!?。;|\n]{0,80}?"
    r"(?:속에서|가운데|상황에서)\s+)?"
    r"(?:공식\s*(?:자료|보고서)|공시\s*(?:자료|보고서)|보고서|같은\s*자료|공시)"
    r"(?:에\s*따르면|에서는|은|는)\s+"
    r"(?P<body>.+)$"
)
# 산업 수준의 전망은 실제 회사의 현재 사업 제약과 구별한다.
# 명시 보고절·산업 범위·미래 양태가 함께 있는 경우에만 이 경계를 적용한다.
INDUSTRY_REPORT_SCOPE_RE = re.compile(r"(?:산업|시장)\s*(?:환경|수준|전반|전체|부문|분야)")
INDUSTRY_REPORT_FUTURE_RE = re.compile(
    r"(?:향후|앞으로|미래)[^.!?。;|\n]{0,400}(?:전망|예상|가능성)"
)
# 회사가 자료를 발표했다는 행위만으로 시장의 어려움을 자기 피해로 만들지 않는다.
# 명시한 단일 주어와 완결된 보고 종결만 읽으며 인용 원문을 변경하지 않는다.
COMPANY_INDUSTRY_REPORT_RE = re.compile(
    rf"^\s*(?P<subject>[가-힣A-Za-z0-9·&()㈜_-]{{2,{INDUSTRY_SUBJECT_MAX_CHARS}}})"
    r"(?:은|는|이|가)\s+(?P<body>.+?)\s*"
    r"(?:직접\s*)?(?:(?:언급|발표|설명|보고|공시|기술|밝히)"
    r"(?:하고\s*있다|하고\s*있습니다|했다|하였다|한다|합니다|고\s*있다|고\s*있습니다)"
    r"|밝혔다|밝혔습니다)"
    r"[.!?。]?\s*$"
)
COMPANY_REPORTED_INDUSTRY_SCOPE_RE = re.compile(r"(?:산업|시장|업계|경기\s*상황)")
COMPANY_REPORTED_OWN_SCOPE_RE = re.compile(
    r"(?:당사|자사|자기\s*회사|우리\s*회사)"
)
# 실제 겪음·중단 등의 행위가 명시된 보고는 기존 회사 귀속 검수에 남긴다.
COMPANY_REPORTED_EXPERIENCE_RE = re.compile(
    r"(?:겪|입|받|중단|취소|지연)(?:고\s*있|었|았|됐|되었|했|하였|한다|했다)|"
    r"(?:손실|피해)(?:이|가|을|를)?\s*(?:발생|입|겪)|발생(?:했|하였)"
)
# 같은 후보에 회사 주체나 회사 소유의 사업 관계가 있으면 의미 검수에 남긴다.
# 자기 원문의 다른 문장에 있는 회사 주체를 후보에 대여하지 않는다.
COMPANY_SCOPE_RE = re.compile(
    r"(?:당사|회사|연결회사|연결실체|그룹)(?:에게|에|를|을|가|이|은|는|의)|"
    r"(?:당사|회사의)\s*(?:광고주|고객|제품|서비스|사업|공급|생산|판매)|"
    r"당사\s+(?=[가-힣A-Za-z])|"
    r"(?:주식회사|유한회사|\(주\)|㈜)[^.!?。;|\n]{0,40}?(?:은|는|이|가)"
)
# 산업 보고절의 선도·타회사 주어는 '회사'라는 낱말이 있어도 자기 관계가 아니다.
OTHER_COMPANY_SCOPE_RE = re.compile(
    r"(?:선도|다른|타|경쟁|외부|협력|고객|공급)(?:\s*회사)(?:에게|에|를|을|가|이|은|는|의)"
)
# 일반 기업 모집단의 제약은 실제 회사가 그 제약을 겪는다는 사실이 아니다.
GENERAL_BUSINESS_POPULATION_RE = re.compile(
    r"(?:대다수(?:의)?|대부분(?:의)?|여러|많은|다수(?:의)?|일반(?:적인)?|업계(?:의)?)\s*"
    r"(?:기업|회사|제조사|업체)(?:들)?(?:은|는|이|가|의|에|에게)"
)
COMPANY_PROBLEM_LINK_ONLY = frozenset({'때문', '로인해', '에따른', '하지만', '그러나', '그럼에도', '반면'})
COMPANY_EXPLICIT_CONSTRAINT_RE = re.compile(r"제약(?:을)?(?:받|겪)|제약(?:이)?존재")
# '회사가 영위하는 시장' 같은 관형절을 일반 시장 주어로 단정하지 않는다.
QUALIFIED_SUBJECT_RE = re.compile(r"(?:영위|생산|제공|판매|운영|납품|수행)하는")
# 회사 일반 업무 문장을 산업 설명 뒤에 붙여 직접 문제의 주체를 만들지 않는다.
# 완결된 주어·목적어·활동뿐인 짧은 절만 해당하며 다른 술어는 별도로 보존한다.
ROUTINE_COMPANY_ACTIVITY_RE = re.compile(
    r"^\s*(?:당사|회사|연결회사|연결실체|그룹)(?:은|는|이|가)\s+"
    r"(?P<object>[^.!?。;|\n]{1,80}?)(?:을|를)\s*"
    r"(?:제공|판매|생산|유통)(?:하고\s*있다|하고\s*있습니다|한다|합니다)[.!?。]?\s*$"
)
# 명시된 상태 접속 뒤 회사 주어가 다시 시작한 두 절을 별도로 읽는다.
# 원문/인용을 자르지 않고 후보의 문제 관계 판정에만 사용한다.
COMPANY_STATE_JOIN_RE = re.compile(
    r"(?:(?<=있으며)|(?<=있고))\s+"
    r"(?=(?:당사|회사|연결회사|연결실체|그룹)(?:은|는|이|가)\s+)"
)

# '시장에서'는 배경일 수 있으므로 그 조사만으로 회사의 실제 피해를 제외하지 않는다.
# 독주·강세·점유 현황·성장 전망의 완결 서술만 묶인 경우를 별도로 읽는다.
MARKET_OBSERVATION_SUBJECT_MAX_CHARS: Final[int] = 100
MARKET_BACKGROUND_RE = re.compile(
    rf"^\s*[^.!?。;|\n]{{1,{INDUSTRY_SUBJECT_MAX_CHARS}}}?(?:산업|시장)"
    r"(?:에서(?:는)?|\s*내(?:에서(?:는)?|에는)?)\s*(?P<body>.+?)\s*$"
)
_MARKET_LABEL = rf"[^.!?。;|\n]{{1,{MARKET_OBSERVATION_SUBJECT_MAX_CHARS}}}?"
_MARKET_DOMINANCE = (
    rf"{_MARKET_LABEL}(?:이|가)독주"
    r"(?:하는가운데|하고있으며|하고있고|하고있다|하고|하며|한다)"
)
_MARKET_STRENGTH = (
    rf"(?:{_MARKET_LABEL}(?:의)?강세(?:가|는)"
    r"(?:지속되고있으며|지속되고있고|지속되고있다|지속된다|나타나고있다)|"
    rf"{_MARKET_LABEL}(?:이|가)강세를보(?:인다|이며|이고))"
)
_MARKET_SHARE = (
    rf"{_MARKET_LABEL}(?:의)?점유율(?:이|은)"
    r"(?:높다|높으며|높고|높아지고|확대되고있다|확대되고있으며|확대되고있고)"
)
_MARKET_GROWTH = (
    rf"(?:{_MARKET_LABEL}(?:의)?성장(?:이|은)"
    r"(?:계속될것으로(?:예상된다|전망된다)|지속될것으로(?:예상된다|전망된다)|"
    r"예상된다|전망된다|계속되고있다|지속되고있다)|"
    rf"{_MARKET_LABEL}(?:이|가)성장(?:할것으로(?:예상된다|전망된다)|할전망이다))"
)
MARKET_OBSERVATION_ONLY_RE = re.compile(
    rf"(?:{_MARKET_DOMINANCE}|{_MARKET_STRENGTH}|{_MARKET_SHARE}|{_MARKET_GROWTH})"
    rf"(?:,?(?:{_MARKET_DOMINANCE}|{_MARKET_STRENGTH}|{_MARKET_SHARE}|{_MARKET_GROWTH}))*[.!?。]?"
)
