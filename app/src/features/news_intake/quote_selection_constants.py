"""원문 선택 ID의 후보 수·길이·응답 필드 계약."""

import re

QUOTE_SELECTION_VERSION = "news-quote-selection-v1"
QUOTE_ID_PREFIX = "quote-"
QUOTE_ID_HASH_CHARS = 20
QUOTE_MAX_CANDIDATES_PER_ARTICLE = 48
QUOTE_UNIT_CANDIDATE_BUDGET = 24
QUOTE_MAX_ADJACENT_SENTENCES = 16
QUOTE_MAX_CHARS = 1_000
QUOTE_BOUNDARY_PREVIEW_CHARS = 20
QUOTE_SOURCE_FIELDS = ("entity_evidence", "text", "time_evidence", "subject_evidence")
QUOTE_EXCERPT_SOURCE_FIELDS = ("text", "time_evidence", "subject_evidence")
QUOTE_FIELD_SUFFIX = "_quote_id"
QUOTE_SUBJECT_PREFIX = r"^\s*(?:(?:한편|또한|반면)\s+)?['\"‘’“”]?(?:\(주\))?"
QUOTE_KNOWN_NAME_BOUNDARY = r"(?:[\s,，:：'\"‘’“”()]|은|는|이|가|$)"
QUOTE_SENTENCE_RE = re.compile(r"[^\n]+?(?:[.!?。](?=\s|$)|(?=\n|$))")
QUOTE_EXPLICIT_BUSINESS_ACTION_RE = re.compile(
    r"(?:개발|공급|생산|판매|운영|체결|인수|설립|출시|수주)(?:했|한|하고|한다|해|하였|했다|한다|에\s*나섰)"
)
QUOTE_CLAUSE_SUBJECT_RE = re.compile(
    r"^\s*(?:(?:한편|또한|반면)\s+)?['\"‘’“”]?(?:\(주\))?"
    r"(?P<subject>[가-힣A-Za-z0-9]+(?:\s+[A-Za-z0-9.]+){0,4})"
    r"(?:\([^)]*\))?['\"‘’“”]?\s*(?:은|는|이|가)"
)
QUOTE_REVERSED_SUBJECT_RE = re.compile(
    r"(?:기업|회사|업체)(?:은|는|이|가)\s*(?P<subject>[가-힣A-Za-z0-9 ]+?)(?:이다|였다|입니다)(?:[.!?。]|$)"
)
QUOTE_COMPANY_PRONOUNS = frozenset({"회사", "이회사", "동사"})
QUOTE_PUBLISHER_RE = re.compile(r"(?:기사\s*제공|발행처|저작권|기자)\s*[:：©]?")
QUOTE_SUBSIDIARY_RE = re.compile(r"\s*의\s*(?:자회사|계열사)(?:인|\s)")
QUOTE_PAST_EMPLOYMENT_RE = re.compile(r"(?:과거|이전|전직)[^.。\n]*(?:근무|재직|출신)")
QUOTE_INDEPENDENT_TRANSITION_RE = re.compile(r"(?:독립\s*회사|개인\s*창업|독립해|독립한|퇴사\s*후)")
