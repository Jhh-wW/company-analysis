"""산업 주어의 설명과 회사 직접 과제를 구분하는 닫힌 문장 경계."""
from __future__ import annotations

import re
from typing import Final

DIRECT_CHALLENGE_CLAIM_SLOTS: Final[frozenset[str]] = frozenset({
    "", "current_challenges:issue", "current_challenges:initial_signal",
    "current_challenges:unresolved_gap", "current_challenges:next_check",
})
INDUSTRY_SUBJECT_MAX_CHARS: Final[int] = 100
# 산업·시장이 문장의 주어인 설명만 읽는다. 이름이나 업종을 판별하지 않는다.
INDUSTRY_SUBJECT_RE = re.compile(
    rf"^\s*(?P<subject>[^.!?。;|\n]{{1,{INDUSTRY_SUBJECT_MAX_CHARS}}}?"
    r"(?:산업|시장))(?:에서는|은|는|이|가)\s*"
)
# 같은 후보에 회사 주체나 회사 소유의 사업 관계가 있으면 의미 검수에 남긴다.
# 자기 원문의 다른 문장에 있는 회사 주체를 후보에 대여하지 않는다.
COMPANY_SCOPE_RE = re.compile(
    r"(?:당사|회사|연결회사|연결실체|그룹)(?:에게|에|를|을|가|이|은|는|의)|"
    r"(?:당사|회사의)(?:광고주|고객|제품|서비스|사업|공급|생산|판매)|"
    r"(?:주식회사|유한회사|\(주\)|㈜)[^.!?。;|\n]{0,40}?(?:은|는|이|가)"
)
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
