"""공식 홈페이지의 현재 PG 사업자 소개를 옮기는 닫힌 범위."""

import re

HOMEPAGE_SELF_CONTEXT_SECTION = "competitive_position"
HOMEPAGE_SELF_CONTEXT_SLOT = "competitive_position:self_context"
HOMEPAGE_STATED_SLOT = "competitive_position:stated_differentiator"

# 회사 전체 거래액과 회사명의 가맹점 소개가 한 원문 구간에 있는 형태만 다룬다.
# 다른 시장·제품 순위의 명사구를 여기서 새로 자기선언으로 판정하지 않는다.
HOMEPAGE_PG_BUSINESS_INTRO_RE = re.compile(
    r"(?P<rank>국내\s+(?:PG|전자지급결제대행)\s+거래액\s+1위),\s*"
    r"가맹점\s*수\s*(?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)\+\s+"
    r"(?P<company>[가-힣A-Za-z0-9&·._()\s-]+?)와\s+함께하는\s+가맹점"
)
HOMEPAGE_ATTRIBUTED_INTRO_RE = re.compile(
    r"(?P<company>.+?)(?:은|는)\s+공식\s*홈페이지에서\s+자사를\s+"
    r"['\"“‘](?P<quote>[^'\"“”‘’\n]+)['\"”’]\s*"
    r"(?:사업자)?로\s+(?:직접\s+)?"
    r"(?:표현하고\s*있다|소개하고\s*있다|표현한다|소개한다)\."
)
