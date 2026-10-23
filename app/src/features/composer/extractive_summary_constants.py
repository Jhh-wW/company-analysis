"""검증된 문장 중 표지에 먼저 실을 사실을 고르는 점수 기준."""

from typing import Final


SUMMARY_NUMERIC_SCORE: Final[int] = 3
SUMMARY_NAMED_ENTITY_SCORE: Final[int] = 2
SUMMARY_LOW_VALUE_SCORE: Final[int] = -3
# 정렬 1순위는 등급이므로, 이 감점보다 큰 가점도 확인 우선을 뒤집지 못한다.
SUMMARY_INTERPRETED_SCORE: Final[int] = -2
SUMMARY_FORMAL_ENDING_SCORE: Final[int] = -2
# 같은 실적 장에서는 날짜가 든 보도보다 원값 검증을 마친 핵심 실적을 먼저 고른다.
SUMMARY_STRUCTURED_PERFORMANCE_SCORE: Final[int] = 5
SUMMARY_PERFORMANCE_SLOT: Final[str] = "past_changes:historical_performance"
SUMMARY_PERFORMANCE_SECTION: Final[str] = "past_changes"
SUMMARY_PERFORMANCE_ANCHOR_SECTION: Final[str] = "portfolio"

# 이미 검증된 후보끼리 역할을 비교한다. 숫자·영문 표지가 제품 기능보다
# 수익 분류를 앞세우거나 설립 목적이 현재 사업을 밀어내지 않게 한다.
SUMMARY_PRODUCT_ROLE_SLOT: Final[str] = "portfolio:product_role"
SUMMARY_IDENTITY_PURPOSE_PATTERN: Final[str] = (
    r"(?:목적으로|목적을\s*위해|위하여)\s*설립(?:된|한|되었|됐|하였|했)"
)
SUMMARY_CURRENT_BUSINESS_PATTERN: Final[str] = (
    r"(?:영위|운영|제조|판매|제공|공급|수령|수취|지급|개발|중개)"
    r"(?:한다|합니다|하고|하며|하는|하여|해\s*왔다)"
)
# 비용 항목만 나열한 사실은 수익 모델을 설명하지 않는다. 매출·수익원이나
# 실제 판매·수취도 함께 설명하는 혼합 사실은 기존 검증을 그대로 유지한다.
SUMMARY_COST_COMPOSITION_PATTERN: Final[str] = (
    r"비용\s*구조|영업\s*비용|판매관리비|매출원가|판관비"
    r"|지급\s*수수료|판매\s*수수료|광고선전비|복리후생비|인건비"
)
SUMMARY_REVENUE_FACT_PATTERN: Final[str] = (
    r"(?:매출(?:액)?|영업수익|수익(?:원)?)\s*(?:은|는|이|가|의|으로|에서)"
)

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
# 같은 정산 관계의 순서·종결어미만 다른 요약 후보를 비교한다.
# 조건·부정·다른 상대방·숫자·추가 사실은 이 닫힌 문법에 포함하지 않는다.
SUMMARY_SETTLEMENT_PATTERN: Final[str] = (
    r"(?:(?:사업의실질적작동방식을|전자(?:지불|지급)결제대행업의자금흐름구조를|대금흐름의구조를)보면,)?"
    r"(?:(?:{subject})는)?"
    r"(?:(?:전자(?:지불|지급)결제대행업|결제대행업)을통하여)?"
    r"최종소비자가결제한거래금액에대해(?:(?:{subject})는)?"
    r"원천사로부터대금을받은후,?"
    r"(?:수수료(?:와|및|·)부가가치세를제외하여가맹점(?:으로|에게|에)"
    r"|(?:(?:회사|당사)의)?가맹점(?:으로|에게|에)수수료(?:와|및|·)부가가치세를제외하여)"
    r"지급(?:한다|하고있습니다|하는구조를운영한다)[.]?"
)
SUMMARY_SETTLEMENT_BUSINESS_PATTERN: Final[str] = r"전자(?:지불|지급)결제대행업|결제대행업"
SUMMARY_FACT_SCOPE_FIELDS: Final[tuple[str, ...]] = (
    "legal_entity", "subject_scope", "time_state", "as_of", "fiscal_year",
    "event_date", "period_start", "period_end",
)
# 본문에서 허용한 회계 인식조건·채무 표시도 그 자체가 제공 가치·대가 요약은 아니다.
# 구체 거래·제공물이나 대금 수취가 함께 있으면 혼합 사실을 보존한다.
SUMMARY_RECOGNITION_ONLY_PATTERN: Final[str] = (
    r"수행\s*의무[^.!?\n]*(?:완료|이행)"
    r"|수익\s*인식\s*(?:시점|기준|조건|정책)"
    r"|(?:시점|기간|간편법)[^.!?\n]*수익[^.!?\n]*인식"
    r"|거래\s*가격[^.!?\n]*(?:배분|차감)"
    r"|(?:채무|부채|지급\s*의무)[^.!?\n]*(?:재무상태표|대차대조표)[^.!?\n]*"
    r"(?:예수금|미지급금)[^.!?\n]*(?:표시|계상)"
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
