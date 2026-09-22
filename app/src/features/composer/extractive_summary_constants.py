"""검증된 문장 중 표지에 먼저 실을 사실을 고르는 점수 기준."""

from typing import Final


SUMMARY_NUMERIC_SCORE: Final[int] = 3
SUMMARY_NAMED_ENTITY_SCORE: Final[int] = 2
SUMMARY_LOW_VALUE_SCORE: Final[int] = -3
# 정렬 1순위는 등급이므로, 이 감점보다 큰 가점도 확인 우선을 뒤집지 못한다.
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
# 수익 구성의 두 항목이 모두 같은 경우만 비교한다. 숫자·시점·제품명은
# 지우지 않으며 다른 서술·추가 절·부정문에는 이 규칙을 적용하지 않는다.
SUMMARY_REVENUE_COMPOSITION_PATTERN: Final[str] = (
    r"(?P<subject>.+?)의 (?:주된 )?영업수익은 "
    r"(?P<service>.+?) 용역(?: 매출)?과 (?P<content>.+?) 매출"
    r"(?: 두 가지 형태)?로 구성된다\."
)
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
