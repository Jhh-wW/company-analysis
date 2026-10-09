"""실제 활동을 말하지 않는 대응 문장의 닫힌 표현."""

import re

GENERIC_RESPONSE_ONLY = "challenge_business_relation_unbound"
GENERIC_RESPONSE_SENTENCE_RE = re.compile(r"[.!?。;\n]+")
_CONTEXT = (
    r"(?:(?:이러한|이같은|이와같은)?"
    r"(?:거시경제|경영환경|시장환경|경제환경|사업환경)"
    r"(?:(?:및|과|와)(?:거시경제|경영환경|시장환경|경제환경|사업환경))*"
    r"(?:의)?(?:어려움|변화|불확실성|악화)(?:에|을|를)"
    r"(?:대응하여|극복하기위해|고려하여),?)?"
)
_OWNER = r"(?:(?:회사|당사|자사)(?:는|가))?"
_GOAL = r"(?:지속가능한?성장|지속성장|기업가치제고|경쟁력강화|위기극복|지속가능성)"
_EFFORT = (
    r"(?:노력(?:해왔다|해왔다고|해오고있다|해오고있다고|하고있다|하고있다고|했다|했다고|중이다|중)|"
    r"최선을다(?:해왔다|해왔다고|하고있다|하고있다고))"
)
_ATTRIBUTION = r"(?:밝히고있다|밝혔다|설명했다|설명하고있다)?"
GENERIC_RESPONSE_ONLY_RE = re.compile(
    rf"{_CONTEXT}{_OWNER}{_GOAL}(?:을|를)?위해{_EFFORT}{_ATTRIBUTION}"
)
