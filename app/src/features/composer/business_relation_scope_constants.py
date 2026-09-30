"""사업 관계의 자기 인용 대조에 쓰는 문법과 동의어."""
import re

BUSINESS_RELATION_SECTIONS = frozenset({"business_model", "operations_partners"})
BUSINESS_RELATION_PROBLEM = "scope_condition_unbound"
CLAUSE_BOUNDARY_RE = re.compile(r"(?<=[다요])[.!?]\s*|[;]\s*")
# 새 주어를 가진 연결절만 나눈다. 단순 역할 나열은 같은 주장으로 남긴다.
ACTOR_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?:하고|하며|되며|되고|이고|이며|하지만|반면)\s+(?=[^,\s;.!?]{1,24}(?:은|는|이|가)\s)"
)
SUBJECT_RE = re.compile(r"^\s*(?:또한\s*|그리고\s*)?([가-힣A-Za-z][가-힣A-Za-z0-9\s_-]{0,65}?)(?:은|는|이|가)(?=\s)")
GENERIC_SUBJECT_RE = re.compile(r"^(?:당사|회사|본사|해당회사|그회사|고객|고객사|기업|업체)$")
OTHER_ACTOR_RE = re.compile(r"경쟁(?:기업|회사|사)|타사|타법인|별도법인|다른(?:회사|기업|법인|업체)|협력(?:회사|기업|사)|고객사|거래처")
STATE_CLAUSE_BOUNDARY_RE = re.compile(r"(?:하고|하며|되며|되고|이고|이며|않고)\s*|[,，]")
CUSTOMER_CLAIM_RE = re.compile(
    r"(?:고객(?:사|층|유형|군)?(?:은|는|이|가|으로|로|을|를|에게|에)|"
    r"(?:을|를|에|에게)대상으로.{0,35}(?:판매|제공|공급)|"
    r"(?:을|를)(?:주요|주된)?고객(?:사)?(?:으로|로)|"
    r"(?:상대로|대상으로)(?:한|하|사업|판매|서비스))"
)
CUSTOMER_SOURCE_RELATION_RE = re.compile(
    r"고객|차주|가입자|계약자|구독자|이용자|회원|"
    r"(?:에|에게|으로|로)(?:공급|판매|제공)|(?:의|에서)(?:개발요청|주문|발주)|OEM|주문자상표"
)
# 산업명 자체가 아니라 후보가 고객이라고 명시한 유형만 대조한다.
CUSTOMER_FAMILIES = (
    ("완성차 고객", re.compile(r"완성차(?:업체|기업|제조사|메이커)|자동차(?:메이커|제조사|제조업체)")),
    ("최종 소비자", re.compile(r"최종소비자|일반소비자|일반소비층|개인소비자|일반개인")),
    ("기업 고객", re.compile(r"기업고객|법인고객|기업차주|법인차주|기업체|법인사업자")),
    ("개인 고객", re.compile(r"개인고객|개인차주|개인대출자|개인가입자")),
    ("대출 고객", re.compile(r"차주|대출자|대출고객|대출이용자|대출신청자")),
    ("보험 고객", re.compile(r"보험계약자|보험가입자|피보험자")),
    ("구독 고객", re.compile(r"구독자|구독고객|구독회원")),
    ("의료기관 고객", re.compile(r"병원|의료기관|의료시설")),
)
# 동의어는 같은 거래 관계만 묶는다. 판매와 제작, 라이선스와 구독은 서로 면제하지 않는다.
RELATION_FAMILIES = (
    ("OEM", re.compile(r"(?i)(?<![a-z])OEM(?![a-z])|주문자상표(?:부착)?(?:생산)?"),
     re.compile(r"(?i)(?<![a-z])OEM(?![a-z])|주문자상표(?:부착)?(?:생산)?")),
    ("위탁", re.compile(r"위탁(?:제조|제작|생산|운영|관리|개발)|외주(?:제작|생산|개발|화)|수탁(?:제조|생산|운영)|아웃소싱"),
     re.compile(r"위탁|외주|수탁|아웃소싱|(?i:contractmanufacturing)")),
    ("라이선스", re.compile(r"라이선스|라이센스|실시권|사용권(?:을|의)?(?:판매|제공|허여)|(?i:licensing)"),
     re.compile(r"라이선스|라이센스|실시권|사용권|(?i:licensing)")),
    ("구독", re.compile(r"구독(?:서비스|방식|모델|료|형|을|으로|에)|정기이용료"),
     re.compile(r"구독|정기이용료|월정액|연정액|(?i:subscription)")),
)
NEGATED_ACTION_RE = re.compile(r"(?:하지않|하지못|되지않|되지못|한적없|않았|불가|중단|해지)")
PENDING_ACTION_RE = re.compile(r"예정|할계획|계획(?:이다|입니다|중)|계획을세|검토중|할경우|하는경우|된다면|한다면")
ACTUAL_ACTION_RE = re.compile(r"(?:하|해|합|있|제공|판매|공급|고객|생산|제작|제조)")
SUBJECT_SUFFIX_RE = re.compile(r"(?:사업부문|사업부|부문|사업|분야)$")
