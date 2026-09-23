"""9장 산문의 긍정 근거 범위. 원문이 선언한 특징·제약만 소재로 인정한다."""

import re

COMPETITIVE_SECTION_EVIDENCE_OFFCONTRACT = "competitive_section_evidence_offcontract"
COMPETITIVE_CLAUSE_RE = re.compile(r"[.!?。\n]+")
COMPETITIVE_ASSERTION_RE = re.compile(
    r"차별(?:점|화)|강점|경쟁력|경쟁\s*우위|핵심\s*역량|독자(?:적|적으로)?\s*(?:개발|기술|설계)"
    r"|(?:최초|유일|최다|최대|선도)[^.!?。\n]*(?:개발|제공|보유|구축|선정|인증|제품|기술)"
    r"|특허[^.!?。\n]*(?:보유|등록|출원)"
)
COMPETITIVE_LIMITATION_RE = re.compile(r"(?:경쟁|차별|강점|독자)[^.!?。\n]*(?:한계|제약|어려움|약점|부족)")
COMPETITIVE_NEGATED_DECLARATION_RE = re.compile(r"(?:차별점|강점|경쟁력)[^.!?。\n]*(?:밝히지\s*않|공시하지\s*않|확인할\s*수\s*없)")
# 일반 주어 하나나 표지 하나만 겹쳐 다른 원문 절을 빌리지 않는다.
COMPETITIVE_MIN_SUPPORT_TERMS = 2
