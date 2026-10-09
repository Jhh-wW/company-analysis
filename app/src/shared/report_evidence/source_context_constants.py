"""작성·비교 검수가 함께 쓰는 원문 예정 단계의 좁은 동작 문형."""

import re

SELF_SECTION_ORIGIN = "self_section_declaration"
SELF_SECTION_EXTRA_KEYS = frozenset({"declaration_part", "section_context_json"})
SELF_DECLARATION_RE = re.compile(
    r"^당사는\s+[^\n.!?]{1,1800}(?:으로|로)\s*구성(?:되어\s*있(?:는|습니다)|되어\s*있는)[^\n.!?]*[.]$"
)
SELF_DECLARATION_PART_RE = re.compile(r"하는\s+([가-힣A-Za-z0-9 ·-]{1,80}?(?:사업부문|부문))(?=과|,|으로|\s)")
SELF_DECLARATION_EXCLUDED_RE = re.compile(r"가상|예시|가정|향후|예정|계획|전망|구성하지|아니|과거|당시|타사|다른\s*회사|경쟁사|인용|라고|다고")

SOURCE_CONTEXT_COMPLETED_RE = re.compile(r"(?:양산|상용화|출시|적용|개발)(?:\s*적용)?(?:을|를)?\s*(?:중|완료|하였|했|하고\s*있|되었습니다|되었다)")
SOURCE_CONTEXT_STAGE_RE = re.compile(r"양산|상용화|출시|적용|개발")
SOURCE_CONTEXT_PENDING_STAGE_RE = re.compile(r"(양산|상용화|출시|적용|개발)(?:\s*적용)?(?:(?!양산|상용화|출시|적용|개발)[^;|\n]){0,24}(?:예정|계획|준비|검토)")
SOURCE_CONTEXT_CLAUSE_RE = re.compile(r"[.。;,]\s*|(?:했고|했으며|이고|이며)\s*")
