"""교육 예시 원문 모드의 닫힌 스키마와 문맥 경계."""

import re

CONTEXT_VERSION = "source-practice-context-v1"
CONTEXT_KEYS = frozenset({
    "version", "document_id", "document_sha256", "mode", "text", "location",
    "text_sha256", "scope_location", "fragment_location", "fragment_range",
    "fragment_sha256",
})
MODES = frozenset({"example", "instruction"})
MAX_MARKER_CHARS = 5000
LOCATION_RE = re.compile(r"([0-9]{1,10})-([0-9]{1,10})")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
EXAMPLE_MARKER_RE = re.compile(
    r"예(?:를\s*들어|를\s*들면|컨대)[\s\S]{0,300}?"
    r"(?:해\s*보겠습니다|해보겠습니다|가정(?:합니다|한다|해)|이렇습니다|"
    r"요청(?:합니다|한다)|정의(?:합니다|한다))|"
    r"(?:프롬프트|요청|실습|프로젝트\s*규칙)의?\s*예시\s*[:：]"
)
INSTRUCTION_MARKER_RE = re.compile(
    r"(?:QA|검수)\s*에이전트[\s\S]{0,180}?"
    r"(?:검수\s*기준|검토시킵니다|보게\s*합니다|검수시킨다)|"
    r"(?:다섯|여러)\s*가지\s*관점으로\s*나눠\s*검토"
    , re.I
)
EXPLICIT_CASE_HEADING_RE = re.compile(r"^(?:실제\s*(?:회사|고객|도입|적용)\s*(?:적용\s*)?사례|고객\s*성공\s*사례)[\s:：]*$")
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。;])\s+|[\r\n]+")
ACTUAL_CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[.!?。;,，])\s+|[\r\n]+")
ACTUAL_SUBJECT_RE = re.compile(
    r"^(?:실제로?\s+)?(?:당사|우리\s*회사|회사|본사|당사\s*직원|"
    r"고객(?:사)?\s+[가-힣A-Za-z0-9]+(?:사|㈜|주식회사)|"
    r"[가-힣A-Za-z0-9]+(?:㈜|주식회사))\s*(?:는|은|이|가|에서는|에서)\s+"
)
ACTUAL_ACTION_RE = re.compile(
    r"[가-힣]+(?:했다|했(?:으며|고)|하였(?:다|습니다|으며|고)|했습니다|한다|합니다|하고\s*있(?:다|습니다)|되었(?:다|습니다))"
)
NON_ACTUAL_RE = re.compile(r"예를\s*들|가정|가령|(?:할|하는)\s*경우|(?:한다|했다고)\s*(?:해|가정)|하지\s*않|못했|예정|계획")
HYPOTHETICAL_MARKER_RE = re.compile(r"가정|가상|가령|해\s*보겠습니다")
EXPLICIT_ACTUAL_PREFIX_RE = re.compile(r"^실제로?\s+")
