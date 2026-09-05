"""언론사 뉴스 도입의 닫힌 어휘와 상한."""

from __future__ import annotations

from typing import Final


DEFAULT_WINDOW_DAYS: Final[int] = 365
EXTENDED_WINDOW_DAYS: Final[int] = 1_095
MAX_CANDIDATES: Final[int] = 20
MAX_SECTIONS_PER_CANDIDATE: Final[int] = 3
ATTRIBUTION_CONTEXT_CHARS: Final[int] = 20
TITLE_DUPLICATE_SIMILARITY: Final[float] = 0.72
TITLE_DUPLICATE_MIN_CHARS: Final[int] = 8

PRESS_RELEASE_KEYWORDS: Final[tuple[str, ...]] = (
    "밝혔다",
    "출시",
    "선정",
    "계약",
    "수주",
    "체결",
    "공개",
)
INTERVIEW_KEYWORDS: Final[tuple[str, ...]] = ("인터뷰", "[인터뷰]", "［인터뷰］")
STOCK_KEYWORDS: Final[tuple[str, ...]] = ("주가", "목표주가", "52주")
UNVERIFIED_MARKERS: Final[tuple[str, ...]] = (
    "업계에 따르면",
    "알려졌다",
    "전망이다",
)
RUMOR_ONLY_MARKERS: Final[tuple[str, ...]] = ("업계에 따르면", "알려졌다")
NEWSROOM_PATH_MARKERS: Final[tuple[str, ...]] = (
    "/newsroom",
    "/news-room",
    "/press",
    "/media",
    "/news/",
)
CORPORATE_DESIGNATORS: Final[tuple[str, ...]] = (
    "주식회사",
    "(주)",
    "（주）",
    "㈜",
)
TITLE_PARTICLES: Final[tuple[str, ...]] = (
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "와",
    "과",
    "도",
    "로",
)
ATTRIBUTION_ROLE_MARKERS: Final[tuple[str, ...]] = (
    "대표이사",
    "공동대표",
    "각자대표",
    "부대표",
    "대표",
    "최고경영자",
    "ceo",
    "회장",
    "사장",
    "임원",
    "관계자는",
    "관계자",
)
QUOTE_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("「", "」"),
    ("“", "”"),
    ('"', '"'),
)

KIND_PRESS_RELEASE: Final[str] = "press_release"
KIND_INTERVIEW: Final[str] = "interview"
KIND_EVENT: Final[str] = "event"
KIND_OTHER: Final[str] = "other"
ALLOWED_KINDS: Final[tuple[str, ...]] = (
    KIND_PRESS_RELEASE,
    KIND_INTERVIEW,
    KIND_EVENT,
    KIND_OTHER,
)

PRIORITY_NEWSROOM: Final[int] = 1
PRIORITY_PRESS_RELEASE: Final[int] = 2
PRIORITY_INTERVIEW: Final[int] = 3
PRIORITY_OTHER: Final[int] = 4

JOURNALIST_NARRATION_SECTIONS: Final[frozenset[str]] = frozenset(
    {
        "identity",
        "business_model",
        "portfolio",
        "past_changes",
        "operations_partners",
        "competitive_position",
    }
)
ATTRIBUTED_QUOTE_ONLY_SECTIONS: Final[frozenset[str]] = frozenset(
    {"current_challenges", "future_strategy"}
)
DATED_QUOTE_ONLY_SECTIONS: Final[frozenset[str]] = frozenset({"culture"})
NON_EXTENDABLE_SECTIONS: Final[frozenset[str]] = frozenset(
    {"current_challenges", "future_strategy"}
)

SOURCE_KIND_NEWS: Final[str] = "news"
ORIGIN_NEWS_INTAKE: Final[str] = "news_intake"
NUMERIC_VALUE_PATTERN: Final[str] = r"[0-9０-９]|[%％]|(?:한|두|세|네|몇)\s*배"

EXCLUDED_COMPANY_NOT_MENTIONED: Final[str] = "company_not_mentioned"
EXCLUDED_RUMOR_ONLY: Final[str] = "rumor_only"
EXCLUDED_STOCK_ARTICLE: Final[str] = "stock_article"
EXCLUDED_INVALID_DATE: Final[str] = "invalid_date"
EXCLUDED_OUTSIDE_WINDOW: Final[str] = "outside_window"
EXCLUDED_INVALID_URL: Final[str] = "invalid_url"
EXCLUDED_DUPLICATE_RELEASE: Final[str] = "duplicate_press_release"
EXCLUDED_CANDIDATE_LIMIT: Final[str] = "candidate_limit"
EXCLUDED_INVALID_JSON: Final[str] = "classification_invalid_json"
EXCLUDED_UNKNOWN_ID: Final[str] = "classification_unknown_id"
EXCLUDED_DUPLICATE_ID: Final[str] = "classification_duplicate_id"
EXCLUDED_UNKNOWN_SECTION: Final[str] = "classification_unknown_section"
EXCLUDED_TOO_MANY_SECTIONS: Final[str] = "classification_too_many_sections"
EXCLUDED_UNKNOWN_KIND: Final[str] = "classification_unknown_kind"
EXCLUDED_READY_SECTION: Final[str] = "ready_section"
EXCLUDED_FETCH_FAILED: Final[str] = "fetch_failed"
EXCLUDED_NUMERIC_SENTENCE: Final[str] = "numeric_sentence"
EXCLUDED_UNVERIFIED_SENTENCE: Final[str] = "unverified_sentence"
EXCLUDED_QUOTE_REQUIRED: Final[str] = "quote_required"
EXCLUDED_ATTRIBUTION_REQUIRED: Final[str] = "attribution_required"
EXCLUDED_PUBLISHED_ON_REQUIRED: Final[str] = "published_on_required"
EXCLUDED_DUPLICATE_SENTENCE: Final[str] = "duplicate_sentence"
