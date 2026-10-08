"""회사명이 협력 상대로만 나온 관계 인용의 닫힌 주어 문맥 경계."""
import re

RELATION_CONTEXT_REASON = "preceding_actor_same_activity"
RELATION_TARGET_CASE = r"(?:와|과)(?:는|도)?(?![가-힣])"
RELATION_SENTENCE_PREFIX = r"^\s*(?:(?:한편|또한|반면)\s+)?[\"'‘’“”]?(?:(?:\(주\)|㈜|주식회사)\s*)?"
RELATION_ACTOR_RE = re.compile(
    r"^\s*(?:(?:\(주\)|㈜|주식회사)\s*)?(?P<actor>[가-힣A-Za-z0-9]+(?:\s+[A-Za-z0-9.]+){0,4})"
    r"(?:\([^)]*\))?\s*(?:은|는|이|가|도)\s+"
)
RELATION_CONTRAST_RE = re.compile(r"^\s*(?:한편|반면|그러나|다만|그런데)\b")
RELATION_INNER_ACTOR_RE = re.compile(r"(?:^|\s|[,，])(?P<actor>[가-힣A-Za-z0-9]{2,})(?:은|는|이|가|도)\s+")
RELATION_QUOTE_MARKERS = frozenset('"\'“”‘’')
RELATION_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
RELATION_TOKEN_MIN_CHARS = 3
RELATION_TOKEN_PARTICLES = ("에서는", "으로는", "에서", "으로", "에는", "까지", "부터", "은", "는", "을", "를", "에", "의", "이", "가", "도")
RELATION_GENERIC_TOKENS = frozenset({
    "회사", "기업", "업체", "사업", "시장", "범위", "고객", "계약", "협력", "진행", "확대",
    "현재", "이러한", "관련", "통하여", "그리고", "있다", "하고", "하며", "한다", "했다",
})
