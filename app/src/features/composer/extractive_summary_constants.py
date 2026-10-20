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
    r"(?P<subject>.+?)의 (?:주된 )?(?:영업)?(?:수익|매출)은 "
    r"(?P<streams>.+?)"
    r"(?:(?:으)?로 구성(?:된다|되어 있다)|에서 발생한다)\."
)
SUMMARY_REVENUE_ROUTE_SUFFIX: Final[str] = r"\s*(?P<count>두|세|네) 가지 (?:형태|경로)$"
SUMMARY_REVENUE_ROUTE_COUNTS: Final[dict[str, int]] = {"두": 2, "세": 3, "네": 4}
SUMMARY_REVENUE_STREAM_SEPARATOR: Final[str] = r"(?:과|와|및)\s+"
SUMMARY_REVENUE_MIN_STREAMS: Final[int] = 2
# 본문에서 허용한 회계 인식조건도 그 자체가 제공 가치·대가 요약은 아니다.
# 구체 거래·제공물이나 대금 수취가 함께 있으면 혼합 사실을 보존한다.
SUMMARY_RECOGNITION_ONLY_PATTERN: Final[str] = (
    r"수행\s*의무[^.!?\n]*(?:완료|이행)"
    r"|수익\s*인식\s*(?:시점|기준|조건|정책)"
    r"|(?:시점|기간|간편법)[^.!?\n]*수익[^.!?\n]*인식"
    r"|거래\s*가격[^.!?\n]*(?:배분|차감)"
)
SUMMARY_BUSINESS_EXCHANGE_PATTERN: Final[str] = (
    r"(?:상품|제품|장비|부품|서비스|용역|콘텐츠|소프트웨어|사용권|라이선스|대출|보험|신탁)"
    r"[^.!?\n]*(?:판매|제공|공급|납품|임대|중개|취급|구독|허여)(?:하|한|할|해|했|받|되)"
    r"|(?:대금|대가|이용료|구독료|수수료|보험료|운용보수)[^.!?\n]*(?:받|수취|회수|청구)"
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
