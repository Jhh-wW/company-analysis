"""작성·비교 검수가 함께 쓰는 원문 예정 단계의 좁은 동작 문형."""

import re

SOURCE_CONTEXT_COMPLETED_RE = re.compile(r"(?:양산|상용화|출시|적용|개발)(?:\s*적용)?(?:을|를)?\s*(?:중|완료|하였|했|하고\s*있|되었습니다|되었다)")
SOURCE_CONTEXT_STAGE_RE = re.compile(r"양산|상용화|출시|적용|개발")
SOURCE_CONTEXT_PENDING_STAGE_RE = re.compile(r"(양산|상용화|출시|적용|개발)(?:\s*적용)?(?:(?!양산|상용화|출시|적용|개발)[^;|\n]){0,24}(?:예정|계획|준비|검토)")
SOURCE_CONTEXT_CLAUSE_RE = re.compile(r"[.。;,]\s*|(?:했고|했으며|이고|이며)\s*")
