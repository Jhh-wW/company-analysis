"""보도 계획을 실제 완료 원문으로 대체할 때 허용하는 좁은 문법."""

import re


NEWS_SUPERSESSION_RULE_VERSION = "news-explicit-completion-v1"
NEWS_SUPERSESSION_SOURCE_CATEGORIES = frozenset({"official_release", "news_report"})
NEWS_SUPERSESSION_FRAGMENT_KINDS = frozenset({"news", "뉴스"})
NEWS_SUPERSESSION_COMPLETED_KINDS = frozenset({"reported_fact", "company_statement"})
NEWS_SUPERSESSION_CONTENT_HASH_RE = re.compile(r"[0-9a-f]{64}")

# 소수점과 명칭 내부 마침표를 지우지 않는다. 분리할 수 없는 문장은 보존한다.
NEWS_SUPERSESSION_SENTENCE_RE = re.compile(r"[.!?。](?=\s|$)|\n+")
NEWS_SUPERSESSION_PLAN_RE = re.compile(
    r"(?P<core>.+?)(?P<action>발표|공개)할\s+(?:예정|계획)"
    r"(?:이다|입니다|이라고\s+(?:밝혔다|설명했다|전했다|발표했다))"
)
NEWS_SUPERSESSION_DONE_RE = re.compile(
    r"(?P<core>.+?)(?P<action>발표|공개|게시)(?:했다|하였다|했습니다|하였습니다)"
)

# 상대시점은 회사 주어 바로 뒤의 이 한 자리에서만 분리한다. 숫자 날짜,
# 「내년 초부터」, 기간·기한·조건 및 객체 안의 같은 글자는 건드리지 않는다.
NEWS_SUPERSESSION_LOCAL_TIME_RE = re.compile(
    r"^(?P<subject>[^\s,;:()\[\]\"'“”‘’]+(?:은|는|이|가))\s+"
    r"(?:이달\s+중|이번\s+달\s+중|이번\s+주\s+중|이날)\s+"
)
NEWS_SUPERSESSION_SUBJECT_RE = re.compile(
    r"^[^\s,;:()\[\]\"'“”‘’]+(?:은|는|이|가)\s+"
)

# 복합 술어의 완료를 마지막 어미 하나로 추론하지 않는다. 이 경우 원문을
# 더 작게 수집하거나 독립 검수로 나눌 수 있을 때까지 계획을 그대로 둔다.
NEWS_SUPERSESSION_COMPOUND_RE = re.compile(
    r"(?:하고|하며|하였고|했으며|하여|해서|했고|했지만|했으나|한\s+(?:뒤|후))\s"
)
# 정정·부정이 있는 기사에서 앞 문장 하나만 골라 완료 권위를 만들지 않는다.
# 이 표지는 삭제를 승인하지 않으며, 모호한 후보를 보존하는 보조 조건뿐이다.
NEWS_SUPERSESSION_UNCERTAIN_RE = re.compile(
    r"(?:하|되|있|없|맞)지\s*(?:않|못)|아니(?:다|라고|라는|었다|였)|아직"
    r"|(?:취소|철회|정정|번복|연기|중단|무산|실패)(?:했|하였|됐|되었|한|된)"
    r"|(?:할|될)\s*(?:예정|계획)|(?:예정|계획)(?:이다|이라고|입니다)"
)
