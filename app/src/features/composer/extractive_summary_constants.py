"""검증된 문장 중 표지에 먼저 실을 사실을 고르는 점수 기준."""

from typing import Final


SUMMARY_NUMERIC_SCORE: Final[int] = 3
SUMMARY_NAMED_ENTITY_SCORE: Final[int] = 2
SUMMARY_LOW_VALUE_SCORE: Final[int] = -3
SUMMARY_INTERPRETED_SCORE: Final[int] = -2
SUMMARY_FORMAL_ENDING_SCORE: Final[int] = -2

SUMMARY_NUMERIC_PATTERN: Final[str] = (
    r"(?<!\d)(?:\d[\d,]*(?:\.\d+)?\s*(?:억원|원|%|명|건|개|년|배)|\d{4}(?!\d))"
)
SUMMARY_NAMED_ENTITY_PATTERN: Final[str] = (
    r"'[^'\n]+'|\"[^\"\n]+\"|‘[^’\n]+’|“[^”\n]+”"
    r"|(?<![A-Za-z0-9_])[A-Z][A-Za-z0-9]*(?![A-Za-z0-9_])"
)
SUMMARY_FORMAL_ENDING_PATTERN: Final[str] = r"(?:습니다|입니다)\."
SUMMARY_LOW_VALUE_PATTERNS: Final[tuple[str, ...]] = (
    "인식한다",
    "인식하고",
    "계상",
    "회계처리",
    "기준서",
    "총평균법",
    "공정가치",
    "상각후원가",
    "대손충당금",
    "유동성을 예측",
    "영업외수익",
    "이자수익",
    "임대료수입",
    "승인될 예정",
    "주주총회",
)
