"""고객 유형·운영 역할의 관리·법률 문맥 제외 범위."""
import re

CUSTOMER_SLOT = "business_model:customer_type"
REVENUE_SLOT = "business_model:revenue_model"
VALUE_EXCHANGE_SLOT = "business_model:value_exchange"
REVENUE_RELATION_SLOTS = frozenset({REVENUE_SLOT, VALUE_EXCHANGE_SLOT})
OPERATING_ROLE_SLOT = "operations_partners:operating_role"
PRODUCT_ROLE_SLOT = "portfolio:product_role"
BUSINESS_SCOPE_SLOTS = frozenset({CUSTOMER_SLOT, *REVENUE_RELATION_SLOTS, OPERATING_ROLE_SLOT, PRODUCT_ROLE_SLOT})
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[가-힣)])[.!?]\s*|[;\n]+")
# 구체 제품 없이 회사의 포지셔닝만 밝힌 절은 제품 역할 칸을 채우지 않는다.
PRODUCT_POSITIONING_RE = re.compile(
    r"(?:다양한|새로운|혁신적인)솔루션(?:을|를)?제시"
    r"(?:합니다|한다|하며|하고|하여|하고있(?:다|습니다)|하고있다고밝히고있다)$"
    r"|^(?:(?:회사|당사|우리)(?:는|가))?"
    r"(?:변화하는[^.!?;\n]*산업[^.!?;\n]*|앞선변화로)?"
    r"(?:시장|산업)(?:을|를)?(?:이끌|선도)"
)
PRODUCT_NAMED_SOLUTION_RE = re.compile(
    r"(?<![가-힣A-Za-z0-9])(?!(?:다양한|새로운|혁신적인|종합|최고의|최적의)\s*)"
    r"[가-힣A-Za-z0-9_\-]+(?:솔루션|서비스)(?:은|는|이|가|을|를|로|이다|입니다|\s)"
)
CLAUSE_BOUNDARY_RE = re.compile(r"(?<![0-9]),(?![0-9])|(?<=으며)\s+|(?<=이며)\s+")
PRODUCT_ROLE_CLAUSE_BOUNDARY_RE = re.compile(
    CLAUSE_BOUNDARY_RE.pattern + r"|(?<=제시하며)\s+|(?<=제시하고)\s+|(?<=제시하여)\s+"
)
# 수익 계정의 포함·제외 정의만 제한한다. 실제 매출 종류의 구성은 그대로 둔다.
REVENUE_CLASSIFICATION_WINDOW_CHARS = 160
REVENUE_ACCOUNT_RE = re.compile(
    r"(?:기타|금융|영업외)(?:영업)?수익(?:항목|계정)?|수익(?:항목|계정)"
    r"|(?:이자|배당금)수익(?:은|는|이|가|에)")
# 각주가 계정 표제를 생략한 '기타에는'은 복수의 명시 계정명과 함께 판단한다.
REVENUE_IMPLICIT_ACCOUNT_RE = re.compile(r"기타(?:항목|계정)?(?:에는|는|에)")
REVENUE_ACCOUNT_ITEM_RE = re.compile(r"수입기술료|수입수수료|이자수익|배당금수익")
MIN_REVENUE_ACCOUNT_ITEMS = 2
REVENUE_CLASSIFICATION_RE = re.compile(
    rf"(?:수익|항목|계정|수입기술료|수입수수료).{{0,{REVENUE_CLASSIFICATION_WINDOW_CHARS}}}"
    r"(?:포함|제외|분류|계상|구성)(?:되|된|됩|됨|하|한|합|함)")
REVENUE_CLAUSE_BOUNDARY_RE = re.compile(
    CLAUSE_BOUNDARY_RE.pattern
    + r"|(?<=포함되고)\s+|(?<=제외되고)\s+|(?<=분류하고)\s+|(?<=계상하고)\s+"
    + r"|(?<=포함되며)\s+|(?<=제외되며)\s+|(?<=분류하며)\s+|(?<=계상하며)\s+")
REVENUE_ACTIVITY_WINDOW_CHARS = 80
# 인식·측정 정책만으로 거래의 대가 칸을 채우지 않는다. 실제 수금 조건은 보존한다.
REVENUE_POLICY_RE = re.compile(
    r"수익(?:의)?인식|(?:수익|매출)(?:은|는|을|으로)[^.!?;]{0,80}(?:인식|측정)|"
    r"유의적(?:인)?금융요소|실무적간편법|유효이자율법"
)
REVENUE_TRANSACTION_RE = re.compile(
    r"(?:고객|구매자|이용자)[^.!?;]{0,80}(?:판매했|제공했|공급했|납품했)"
    r"[^.!?;]{0,60}(?:대가|대금)[^.!?;]{0,20}\d[\d,.]*(?:억|만|천|백만)?(?:원|달러|유로)|"
    r"(?:고객|구매자|차주|가입자|계약자|이용자)[^.!?;]{0,80}"
    r"(?:대가|대금|이용료|구독료|수수료|보험료|이자)[^.!?;]{0,60}(?:받|수취|회수|청구|지급)|"
    r"(?:대가|대금|이용료|구독료|수수료|보험료|운용보수)[^.!?;]{0,40}"
    r"(?:현금|어음|월정액|연정액|건당|계약금|판매가격|납품가격|요율)|"
    r"(?:판매|제공|공급|납품|임대|구독)[^.!?;]{0,40}(?:대가|대금|이용료|수수료)"
    r"[^.!?;]{0,40}(?:받|수취|회수|청구)|"
    r"(?:기업|개인|고객|차주)(?:대출|여신)[^.!?;]{0,80}(?:이자|수수료)|"
    r"(?:보험계약자|보험가입자)[^.!?;]{0,80}보험료|"
    r"신탁[^.!?;]{0,80}(?:운용보수|수수료)"
)
# 금융업의 대출취급·보험판매처럼 실제 상품 활동을 나타내는 명사도 보존한다.
# 이자수익·수입수수료·수입기술료의 계정명만으로 활동을 추정하지 않는다.
REVENUE_ACTIVITY_RE = re.compile(
    rf"(?:상품|제품|장비|부품|설비|서비스|용역|콘텐츠|기술사용권|라이선스|대출|여신|신탁|보험|자산관리|결제|중개)"
    rf".{{0,{REVENUE_ACTIVITY_WINDOW_CHARS}}}"
    r"(?:판매|제공|공급|납품|취급|인수|운용|중개|임대|구독|허여)"
    rf"|(?:상품|제품|서비스|용역|콘텐츠).{{0,{REVENUE_ACTIVITY_WINDOW_CHARS}}}매출"
    rf"|(?:기업|개인|고객|차주)(?:대출|여신).{{0,{REVENUE_ACTIVITY_WINDOW_CHARS}}}(?:이자|수수료)"
)
CUSTOMER_ADMIN_RE = re.compile(
    r"신용(?:위험|등급|(?:을|를)?평가|정보|도)|매출채권|대손(?:충당|손실)|거래한도|채무불이행")
CUSTOMER_ADMIN_ACTION_RE = re.compile(
    r"관리|검토|평가|통제|담보|보증|한도|분산|정책|한해거래|수취|손실|이용|결정|사용|점검")
# 금융자산의 평상시 회수 보고는 제공물의 구매자·이용자 관계가 아니다.
# 외상판매나 실제 대출 제공 자체는 이 좁은 관리 문법에 포함하지 않는다.
CUSTOMER_RECOVERY_OBJECT_RE = re.compile(r"금융자산|매출채권|수취채권|미수금|대출채권")
CUSTOMER_RECOVERY_MANAGEMENT_RE = re.compile(
    r"회수(?:지연)?(?:현황|상태|대책)[^.!?;]{0,32}보고|"
    r"회수(?:지연|대책)[^.!?;]{0,32}(?:관리|정책|점검)|"
    r"지연사유[^.!?;]{0,32}(?:조치|보고|관리)"
)
# 고객 평가의 결과물과 수수료 수취가 같은 절에서 직접 연결된 유상서비스.
# 평가 뒤 거래한도를 정하거나 담보를 받는 내부 관리와 구분한다.
CUSTOMER_CREDIT_SERVICE_RE = re.compile(
    r"(?:고객(?:사)?|거래처)[^.!?;]{0,80}신용(?:을|를)?평가(?:해|하여|하고|하며)"
    r"[^.!?;]{0,80}(?:등급|평가결과|평가보고서)(?:을|를)?제공(?:하고|하며|하여|한다|합니다)"
    r"[^.!?;]{0,80}(?:평가)?수수료(?:를|을)?(?:받는다|받습니다|받고있|받았다|수취한다|수취하며|수취하고있)"
)
OPERATING_ADMIN_RE = re.compile(
    r"제조물책임(?:법|보험)|손해배상책임|보험계약|(?:공정거래위원회|공정위).*(?:조사|과징금)"
    r"|(?:제조|생산|위탁).*(?:조사결과|과징금|지연이자)|(?:제조|생산).*관련.*소송")
CAREER_PROFILE_RE = re.compile(r"학사|석사|박사|졸업|학력|주요경력|경력사항|경력|이전직장|전직장")
PERSONAL_POSITION_RE = re.compile(
    r"팀장|부장|본부장|공장장|담당임원|임원|이사|대표이사|수석|책임연구원"
    r"|(?:개발|제조|생산|공급|운영|공정|품질(?:관리)?)(?:부문|부서|본부|사업부|공장|팀)?"
    r"(?:을|를)?(?:담당|수행)")
PERSONAL_POSITION_MODIFIER_MAX_CHARS = 12
COMPOUND_PERSONAL_POSITION_RE = re.compile(
    rf"(?:개발|제조|생산|공급|운영|공정|품질)[가-힣]{{1,{PERSONAL_POSITION_MODIFIER_MAX_CHARS}}}"
    r"(?:담당|수행)(?=$|[|/(),])")
# 개인의 학위·이력과 함께 있어도 실제 현재 맡은 생산·운영 행동은 보존한다.
CURRENT_OPERATING_RESPONSIBILITY_RE = re.compile(
    r"(?:제품|부품|장비|설비|공장|제조|생산|공정).{0,45}"
    r"(?:생산|제조|운영|공정|품질관리)(?:을|를)?(?:직접)?"
    r"(?:담당|총괄|수행|책임)(?:한다|합니다|하고있|지고있|진다|집니다)"
)
PERSONAL_CAREER_PAST_CONTEXT_RE = re.compile(r"주요경력|경력사항|경력[:：]|이전직장|전직장")
PERSONAL_PAST_RESPONSIBILITY_RE = re.compile(
    r"(?:개발|제조|생산|공급|운영|공정|품질(?:관리)?)(?:을|를)?"
    r"(?:담당|총괄|수행|책임)(?:했다|했습니다|하였다|하였습니다|하였음|했던|하던)")
COMPANY_ACTION_SUBJECT_RE = re.compile(r"(?:회사|당사)(?:는|가|에서)")
OPERATING_PLAN_WINDOW_CHARS = 80
COMPANY_OPERATING_PLAN_RE = re.compile(
    rf"(?:제품|상품|부품|장비|설비|공장).{{0,{OPERATING_PLAN_WINDOW_CHARS}}}"
    r"(?:개발|제조|생산|공급|운영)(?:을|를)?(?:할|하려는|하려고|추진할)"
    rf".{{0,{OPERATING_PLAN_WINDOW_CHARS}}}(?:계획|예정)")
CUSTOMER_DEFINITION_RE = re.compile(r"(?:주요|핵심)?고객(?:사)?(?:는|은|이|가).+")
# 제공과 대가 수취가 같은 절에서 현재 동사로 연결된 고객 관계를 보존한다.
CUSTOMER_PROVISION_PAYMENT_RE = re.compile(
    r"고객(?:사)?[^.!?;,]{0,80}(?:에게|에)[^.!?;,]{0,80}"
    r"(?:공급|판매|납품|제공|수탁)하고[^.!?;,]{0,80}"
    r"(?:받는다|받습니다|수취한다|수취합니다|회수한다|회수합니다|청구한다|청구합니다)"
)
BUSINESS_SERVICE_RE = re.compile(
    r"(?:고객(?:사)?|거래처|수요처|구매자|이용자).{0,80}(?:에게|에|로부터|의).{0,80}"
    r"(?:공급|판매|납품|제공|수주|수탁)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중|서비스)"
    r"|(?:공급|판매|납품|제공|수주)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중).{0,80}(?:고객사|거래처|수요처)"
    r"|(?:주요|핵심)?고객(?:사)?(?:는|은|이|가).{0,80}(?:기업|업체|기관|개인|병원|공장|소비자|차주)(?:이다|입니다|이며|이고|와|과|및)")
# 경력의 '제조담당' 직함과 실제 '제조를 담당한다' 동사를 구분한다.
OPERATING_ACTION_RE = re.compile(
    r"(?:제품|상품|부품|장비|설비|시스템|서비스|사업|플랫폼|공장).{0,80}"
    r"(?:개발|제조|생산|공급|운영|제공)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중"
    r"|을(?:담당|수행)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중))"
    r"|보험(?:상품|계약)?.{0,80}(?:판매|인수)(?:한다|합니다|했다|했습니다|하였다|하며|하고있)"
    r"|(?:개발|제조|생산|공급|운영)(?:을|은|는|이|가)?(?:하고있|합니다|한다"
    r"|(?:담당|수행)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중))")
REJECT_BUSINESS_SLOT_SCOPE = "business_slot_scope_unsupported"
REMAINING_SUPPORT_RE = {
    CUSTOMER_SLOT: re.compile(r"고객사|거래처|수요처"),
    REVENUE_SLOT: re.compile(r"판매에서발생|수익원|과금|수수료|매출구조|수익의형태|매출(?:등)?으로구성|매출유형"),
    OPERATING_ROLE_SLOT: re.compile(r"생산|제조|운영한다"),
}
