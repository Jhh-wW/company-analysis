"""기사 본문 뒤 명시적 키워드·관련기사 목록의 닫힌 경계."""
import re

AUXILIARY_PANEL_RE = re.compile(
    r"(?<!\w)관련\s*키워드(?:\s*[:：]\s*|\s+)"
    r"(?P<keywords>[^\n.!?。]+?)\s+관련\s*기사(?:\s*[:：]\s*|\s+)"
)
AUXILIARY_MIN_KEYWORD_TOKENS = 3
NARRATIVE_PREFIX_RE = re.compile(r"(?:은|는|이|가|을|를|에서|에는|으로)\s*$")
NARRATIVE_CONNECTOR_RE = re.compile(r"(?:하고|하며|하여|하는|하거나|라는|이라고|이라고도|라고)\s")
NARRATIVE_KEYWORD_ENDINGS = ("과", "와", "을", "를", "의", "에서", "으로")
FEATURE_QUOTE_PAIRS = (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’"))
QUOTED_FEATURE_SUFFIX_RE = re.compile(r"^\s*(?:기능|서비스)(?:은|는|이|가|을|를|으로)")
COMPLETE_STATEMENT_RE = re.compile(
    r"(?:했다|한다|됐다|된다|있다|없다|이다|합니다|했습니다|됩니다|있습니다|없습니다|이었다|였다)"
    r"(?=[.!?。\s]|$)|\b(?:provides|provided|operates|operated|announced|offers|offered)\b", re.I,
)
SENTENCE_BOUNDARY_RE = re.compile(r"[.!?。\n]")
EXCLUDED_AUXILIARY_ONLY_BODY = "auxiliary_only_not_body"
EXCLUDED_AUXILIARY_EXCERPT = "grounded_auxiliary_not_body"
