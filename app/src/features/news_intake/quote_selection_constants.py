"""원문 선택 ID의 후보 수·길이·응답 필드 계약."""

import re

QUOTE_SELECTION_VERSION = "news-quote-selection-v2"
QUOTE_ID_PREFIX = "quote-"
QUOTE_ID_HASH_CHARS = 20
QUOTE_ID_PATTERN = rf"^{re.escape(QUOTE_ID_PREFIX)}[0-9a-f]{{{QUOTE_ID_HASH_CHARS}}}$"
QUOTE_EMPTY_ID_PATTERN = rf"^({re.escape(QUOTE_ID_PREFIX)}[0-9a-f]{{{QUOTE_ID_HASH_CHARS}}})?$"
QUOTE_MAX_CANDIDATES_PER_ARTICLE = 48
QUOTE_UNIT_CANDIDATE_BUDGET = 24
QUOTE_MAX_ADJACENT_SENTENCES = 16
QUOTE_PRECEDING_CONTEXT_SENTENCES = 1
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
QUOTE_UI_PANEL_MARKERS = ("기사 듣기", "선호매체 추가", "URL공유", "가장작게", "가장크게")
QUOTE_UI_PANEL_MIN_MARKERS = 3
QUOTE_UI_PANEL_END_RE = re.compile(r"가장작게[^.!?。\n]{0,80}가장크게\s*")
QUOTE_UI_PREFIX_PROBLEM_RE = re.compile(
    r"(?:기능|서비스|이용자|고객|결제|설비|생산|납품|공급)[^.!?。\n]{0,48}"
    r"(?:장애|고장|지연|차질|중단|결함|불량|피해)"
)
QUOTE_PUBLISHER_RE = re.compile(r"(?:기사\s*제공|발행처|저작권|기자)\s*[:：©]?")
QUOTE_OPENING_BYLINE_MAX_CHARS = 80
QUOTE_OPENING_BYLINE_RE = re.compile(
    rf"^\s*\[[^\[\]\n.!?。]{{1,{QUOTE_OPENING_BYLINE_MAX_CHARS}}}\s+기자\]\s*"
)
QUOTE_PRIOR_SENTENCE_END_RE = re.compile(r"[.!?。](?=\s|$)")
QUOTE_PRE_BYLINE_CAPTION_RE = re.compile(
    r"^[^\n]*[.!?。]\s*\((?:사진|자료)\s*=[^()\n]+\)\s*$", re.M
)
QUOTE_OPENING_TARGET_SUBJECT = r"\s*(?:은|는|이|가)"
QUOTE_SELF_CONTAINED_SELECTION_GUIDE = (
    "뒤 문장이 약칭·회사·생략 주어로 이어지면, 명시한 대상 법인 소개와 그 회사의 "
    "같은 사업 사실이 함께 들어 있는 기존 연속 후보 ID를 고르세요. 다른 주체로 전환된 "
    "문장·다른 문단의 소개를 빌리지 말고, 그러한 자기완결 후보가 없으면 제외하세요. "
)
QUOTE_EVENT_DATE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{4}\.\d{1,2}\.\d{1,2}|"
    r"\d{4}/\d{2}/\d{2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일)(?!\d)"
)
QUOTE_EVENT_DATE_GUIDE = (
    "event_on은 스키마의 날짜 선택값 중 고르되, 선택한 text 내부의 해당 사건 연월일을 "
    "time_evidence_quote_id가 정확히 증명할 때만 날짜를 선택하세요. 연도만(예: 2026), "
    "행사명에 든 연도, 지난 2~3일처럼 불완전한 날짜만 있으면 event_on과 "
    "time_evidence_quote_id 모두 빈 문자열입니다. 다른 기사 날짜나 발행일을 빌리지 마세요. "
)
QUOTE_SUBSIDIARY_RE = re.compile(r"\s*의\s*(?:자회사|계열사)(?:인|\s)")
QUOTE_PAST_EMPLOYMENT_RE = re.compile(r"(?:과거|이전|전직)[^.。\n]*(?:근무|재직|출신)")
QUOTE_INDEPENDENT_TRANSITION_RE = re.compile(r"(?:독립\s*회사|개인\s*창업|독립해|독립한|퇴사\s*후)")
