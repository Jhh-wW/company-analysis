"""최종 산문의 문체와 지난 일정 표시에만 쓰는 닫힌 규칙."""

import re
from typing import Final


SENTENCE_ENDING_REPLACEMENTS: Final[dict[str, str]] = {
    "하고 있습니다.": "하고 있다.",
    "합니다.": "한다.",
    "됩니다.": "된다.",
    "입니다.": "이다.",
    "있습니다.": "있다.",
    "없습니다.": "없다.",
    "했습니다.": "했다.",
    "됐습니다.": "됐다.",
    "였습니다.": "였다.",
    "않습니다.": "않는다.",
}
SENTENCE_ENDING_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(re.escape(ending) for ending in sorted(
        SENTENCE_ENDING_REPLACEMENTS, key=len, reverse=True,
    ))
)
QUOTE_PAIRS: Final[dict[str, str]] = {
    "「": "」", "“": "”", "'": "'", '"': '"', "‘": "’",
}
PAST_DATED_FUTURE_TENSE: Final[str] = "past_dated_future_tense"
DISCLOSURE_ORIGINAL_MARKER: Final[str] = "(공시 원문 기준)"
DATED_PLAN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월"
    r"(?:\s*(?P<day>\d{1,2})\s*일자|(?!\s*\d))"
)
FUTURE_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<clause>[^.!?\n]*?(?:예정이다|예정입니다|할 계획이다))"
    r"(?P<period>\.|(?=\s*(?:\[\d+\]\s*)*(?:— 해석)?\s*$))"
)
