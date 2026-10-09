"""회사 사업의 명시 성장 제약과 연결 대응을 읽는 닫힌 문법."""
from __future__ import annotations

import re

ISSUE_SLOT = "current_challenges:issue"
RESPONSE_SLOT = "current_challenges:response"
SIGNAL_REASON = "direct_pattern:business_growth_constraint"
RESPONSE_REASON = "direct_pattern:business_constraint_response"

# 기존 5장 top 몫의 동점만 비교한다. 점수·최근/변경 순위·다른 의미 칸은 그대로다.
DIRECT_RELATION_REASONS_BY_SLOT = {
    ISSUE_SLOT: frozenset({SIGNAL_REASON, "direct_pattern:business_incident_row"}),
    RESPONSE_SLOT: frozenset({RESPONSE_REASON, "direct_pattern:business_incident_response"}),
}

# 다른 부문·다른 설명 항목을 넘어 제약과 대응을 합치지 않는다.
SECTION_START_RE = re.compile(r"(?:\(\d+\)|\d+\))\s*(?:신규사업[^.\n]{0,30}(?:전망|내용)|판매전략)")
SECTION_END_RE = re.compile(r"\(\d+\)|(?:^|\n)\s*\d+\)|\n\s*\[")
CONSTRAINT_RE = re.compile(
    r"(?P<object>[^.\n;!?]{2,100}?)의\s*성장(?:에는|에)\s*한계"
    r"(?:가\s*있다고\s*(?:판단|평가)하여|를\s*느껴)\s*,?"
)
CURRENT_RESPONSE_RE = re.compile(
    r"(?:진출(?:을|에\s*대한\s*진출을)?\s*추진하고\s*있(?:습니\s*다|다)|"
    r"개척에\s*주력하여[^.\n;!?]{0,100}구축하고\s*있(?:습니\s*다|다))"
    r"(?=[.\s]|$)"
)
SELF_RE = re.compile(r"(?:당사|회사|연결회사)(?:는|가)\s*")
LEGAL_FORM_RE = re.compile(r"주식회사|\(\s*주\s*\)|㈜")
NAMED_SUBJECT_RE = re.compile(
    r"(?P<suffix>[가-힣A-Za-z0-9&·._-]+\s*(?:주식회사|\(\s*주\s*\)|㈜))(?:은|는|이|가)\s*"
    r"|(?P<prefix>(?:주식회사|\(\s*주\s*\)|㈜)\s*[가-힣A-Za-z0-9&·._-]+?)(?:은|는|이|가)(?=\s)"
)
UNBOUND_PREFIX_RE = re.compile(
    r"과거|전기(?:에|에는|\s)|예전|이전에는|당시|지난해|향후|예정|계획|가정|경우|만약"
)
OTHER_SUBJECT_RE = re.compile(
    r"(?:고객사|고객|거래처|경쟁사|협력사|자회사|종속기업|종속회사|타사|다른\s*회사|산업|업계)(?:는|가|은|이)"
)
OTHER_BUSINESS_OWNER_RE = re.compile(r"(?:경쟁사|타사|다른\s*회사|산업|업계)(?:의|\s+전체의)\s*")
COMPLETED_CONSTRAINT_RE = re.compile(r"해소|해결|극복|완료")
UNRELATED_RESPONSE_RE = re.compile(r"이와\s*무관하게|별개의\s*사업|한편\s*다른\s*사업")
SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)|\n")
